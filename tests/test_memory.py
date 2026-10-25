import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from agent_zero.config import Config
from agent_zero.memory import memory_command
from agent_zero.processes import CURRENT_PID
from agent_zero.runtime import Runtime
from agent_zero.tools import model_step, tool_step


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(root=Path(self.temp.name), cloud_enabled=True)
        self.runtime = Runtime(self.config)
        self.runtime.cloud.chat = AsyncMock(return_value={'role': 'assistant', 'content': 'done'})
        self.mem = self.runtime.memory

    async def asyncTearDown(self):
        await self.runtime.processes.interrupt_all()
        self.runtime.store.db.close()
        self.temp.cleanup()

    def call(self, method, actor=1, **params):
        return self.mem.syscall('sys_mem_' + method, params, actor)

    def child(self):
        return self.runtime.processes.create('fixture task')['pid']

    def shared(self, pid, access='rw'):
        self.call('create', actor=0, name='team')
        self.call('grant', actor=0, space='team', pid=pid, access=access)
        return 'shared:team'

    async def test_private_isolation_includes_parents_siblings_and_forged_pid(self):
        a, b = self.child(), self.child()
        self.call('write', actor=a, key='secret', content='only A')
        for actor in (1, b):
            with self.subTest(actor=actor), self.assertRaises(PermissionError):
                self.call('read', actor=actor, space=f'private:{a}', key='secret')
        with self.assertRaises(ValueError):
            self.call('read', actor=b, pid=a, key='secret')
        self.assertEqual(self.call('read', actor=0, pid=a, key='secret')['content'], 'only A')

    async def test_model_adapter_rejects_identity_and_kernel_call_forgery(self):
        child = self.child()
        self.call('write', actor=child, key='secret', content='hidden fixture')
        calls = [
            {'name': 'sys_mem_read', 'arguments': {'pid': child, 'key': 'secret'}},
            {'name': 'sys_mem_read', 'arguments': {'space': f'private:{child}', 'key': 'secret'}},
            {'name': 'sys_mem_grant', 'arguments': {'space': 'team', 'pid': 1, 'access': 'rw'}},
            {'name': 'sys_mem_command', 'arguments': {'text': 'mem create stolen'}},
        ]
        results, _ = await tool_step(self.runtime, {'tool_calls': [{'id': str(i), 'function': c} for i, c in enumerate(calls)]})
        self.assertTrue(all('error' in json.loads(r['content']) for r in results))
        self.assertNotIn('hidden fixture', json.dumps(results))
        self.assertEqual(self.call('spaces', actor=0)[-1]['kind'], 'private')

    async def test_shared_read_write_and_kernel_only_policy(self):
        child = self.child()
        space = self.shared(child, 'r')
        self.call('write', actor=0, space=space, key='plan', content='kernel plan')
        self.assertEqual(self.call('read', actor=child, space=space, key='plan')['content'], 'kernel plan')
        for method, params in [('write', {'content': 'change'}), ('delete', {})]:
            with self.assertRaises(PermissionError):
                self.call(method, actor=child, space=space, key='plan', **params)
        with self.assertRaises(PermissionError):
            self.call('read', space=space, key='plan')
        with self.assertRaises(PermissionError):
            self.call('grant', actor=child, space=space, pid=1, access='rw')
        self.call('grant', actor=0, space=space, pid=child, access='rw')
        self.call('write', actor=child, space=space, key='plan', content='child plan')
        self.assertEqual(self.call('read', actor=0, space=space, key='plan')['content'], 'child plan')

    async def test_revoke_evicts_and_blocks_every_future_access(self):
        space = self.shared(1)
        self.call('write', space=space, key='secret', content='shared fixture')
        self.assertIn('shared fixture', self.mem.resident_context(1)['content'])
        self.call('grant', actor=0, space=space, pid=1, access='none')
        self.assertIsNone(self.mem.resident_context(1))
        for method, params in [('read', {'key': 'secret'}), ('list', {}), ('search', {'query': 'fixture'}), ('write', {'key': 'x', 'content': 'x'}), ('delete', {'key': 'secret'})]:
            with self.subTest(method=method), self.assertRaises(PermissionError):
                self.call(method, space=space, **params)

    async def test_shared_write_invalidates_other_readers_and_drop_removes_grants(self):
        child = self.child()
        space = self.shared(1)
        self.call('grant', actor=0, space=space, pid=child, access='r')
        self.call('write', space=space, key='plan', content='old')
        self.call('read', actor=child, space=space, key='plan')
        self.call('write', space=space, key='plan', content='new')
        self.assertIsNone(self.mem.resident_context(child))
        self.assertEqual(self.call('read', actor=child, space=space, key='plan')['content'], 'new')
        self.call('drop', actor=0, space=space)
        self.assertEqual(self.call('stat', actor=child)['shared_grants'], [])
        self.assertIsNone(self.mem.resident_context(child))

    async def test_fork_copies_private_pages_spawn_is_empty_grants_never_inherit(self):
        self.call('write', key='project', content='original')
        space = self.shared(1)
        fork = await self.runtime.processes.syscall('sys_fork', {'task': 'fork'}, actor=1)
        spawned = self.child()
        self.assertEqual(self.call('read', actor=fork['pid'], key='project')['content'], 'original')
        self.assertEqual(self.call('stat', actor=spawned)['private_pages'], 0)
        self.call('write', actor=fork['pid'], key='project', content='independent')
        self.assertEqual(self.call('read', key='project')['content'], 'original')
        for pid in (fork['pid'], spawned):
            self.assertEqual(self.call('stat', actor=pid)['shared_grants'], [])
            with self.assertRaises(PermissionError):
                self.call('list', actor=pid, space=space)

    async def test_lru_eviction_pages_back_in_without_losing_backing_data(self):
        for key in ('a', 'b', 'c'):
            self.call('write', key=key, content=key * 3500)
        stats = self.call('stat')
        self.assertEqual(stats['private_pages'], 3)
        self.assertEqual(stats['resident_pages'], 2)
        self.assertGreater(stats['evictions'], 0)
        self.assertNotIn(('private:1', 'a'), self.mem.cache[1])
        faults = stats['page_faults']
        self.assertEqual(self.call('read', key='a')['content'], 'a' * 3500)
        self.assertEqual(self.call('stat')['page_faults'], faults + 1)
        self.assertLessEqual(len(self.mem.resident_context(1)['content'].encode()), self.mem.RESIDENT_BYTES)

    async def test_escaped_and_unicode_pages_obey_real_serialized_context_budget(self):
        for key, value in [('null', '\0' * 900), ('unicode', '界' * 1300), ('ascii', 'a' * 4000)]:
            self.call('write', key=key, content=value)
        self.assertLessEqual(len(self.mem.resident_context(1)['content'].encode()), self.mem.RESIDENT_BYTES)
        for content in ('界' * 1400, 'a' * 4097, '\0' * 4096, '', None):
            with self.assertRaises(ValueError):
                self.call('write', key='invalid', content=content)
        with self.assertRaises(ValueError):
            self.call('write', key='../escape', content='x')

    async def test_page_quota_paginated_inventory_search_and_delete(self):
        self.mem.MAX_PAGES = 20
        for i in range(20):
            self.call('write', key=f'page-{i:02}', content=f'entry {i}')
        first = self.call('list')
        second = self.call('list', offset=first['next_offset'])
        self.assertEqual(len(first['pages']) + len(second['pages']), 20)
        self.assertIsNone(second['next_offset'])
        self.assertEqual(self.call('search', query='entry 19')['matches'][0]['key'], 'page-19')
        with self.assertRaises(ValueError):
            self.call('write', key='overflow', content='x')
        self.call('write', key='page-00', content='updated')
        self.call('delete', key='page-01')
        self.call('write', key='replacement', content='allowed')
        with self.assertRaises(ValueError):
            self.call('list', offset=-1)

    async def test_restart_preserves_pages_and_grants_but_not_residency(self):
        child = self.child()
        self.call('write', key='durable', content='stored across restart')
        self.shared(child, 'r')
        self.runtime.store.db.close()
        self.runtime = Runtime(self.config)
        self.mem = self.runtime.memory
        self.assertEqual(self.call('stat')['resident_pages'], 0)
        self.assertEqual(self.call('read', key='durable')['content'], 'stored across restart')
        self.assertEqual(self.call('stat', actor=child)['shared_grants'], [{'space': 'shared:team', 'access': 'r'}])
        self.assertEqual(self.runtime.processes.get(child)['state'], 'interrupted')

    async def test_kernel_flush_scope_and_reap_cleanup(self):
        child = self.child()
        self.call('write', key='root', content='root')
        self.call('write', actor=child, key='child', content='child')
        space = self.shared(child)
        self.call('write', actor=child, space=space, key='team', content='shared')
        self.runtime.auth.setup('fixture kernel phrase')
        auth = await self.runtime.dispatch('sys_auth', {'passcode': 'fixture kernel phrase'})
        await self.runtime.dispatch('sys_mem_flush', {'scope': 'session'}, auth['token'])
        self.assertEqual(self.call('stat')['private_pages'], 0)
        self.assertEqual(self.call('stat', actor=child)['private_pages'], 1)
        self.assertEqual(self.call('read', actor=0, space=space, key='team')['content'], 'shared')
        await self.runtime.dispatch('sys_mem_flush', {'scope': 'all'}, auth['token'])
        self.assertEqual(self.mem.summary()['pages'], 0)
        self.assertEqual(len(self.call('stat', actor=child)['shared_grants']), 1)
        self.runtime.processes.reap(child)
        self.assertNotIn(f'private:{child}', [s['space'] for s in self.call('spaces', actor=0)])
        self.assertFalse(self.mem.db.execute('SELECT 1 FROM memory_grants WHERE pid=?', (child,)).fetchone())

    async def test_rpc_requires_auth_and_content_does_not_leak_into_status_or_audit(self):
        secret = 'sensitive private fixture'
        self.call('write', key='note', content=secret)
        self.assertNotIn(secret, json.dumps(self.runtime.snapshot()))
        for method in ('sys_mem_read', 'sys_mem_stat', 'sys_mem_create', 'sys_mem_command'):
            with self.assertRaises(PermissionError):
                await self.runtime.dispatch(method, {'key': 'note', 'name': 'stolen', 'text': 'mem create stolen'})

    async def test_model_gets_only_its_resident_pages_without_mutating_graph_input(self):
        child = self.child()
        self.call('write', key='root', content='root memory')
        self.call('write', actor=child, key='child', content='child memory')
        messages = [{'role': 'user', 'content': 'task'}]
        context = CURRENT_PID.set(child)
        try:
            await model_step(self.runtime, messages, self.runtime.model, None)
        finally:
            CURRENT_PID.reset(context)
        sent = self.runtime.cloud.chat.call_args.args[1]
        self.assertEqual(len(messages), 1)
        self.assertEqual(sent[-1]['role'], 'user')
        self.assertIn('child memory', sent[-1]['content'])
        self.assertNotIn('root memory', str(sent))

    async def test_real_graph_does_not_checkpoint_automatically_injected_pages(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        self.call('write', key='resident', content='resident-only fixture')
        self.runtime.cloud.chat.side_effect = [
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'page', 'function': {
                'name': 'sys_mem_read', 'arguments': {'key': 'resident'}}}]},
            {'role': 'assistant', 'content': 'done'},
        ]
        async with AsyncSqliteSaver.from_conn_string(str(Path(self.temp.name) / 'graph.sqlite')) as saver:
            self.runtime.graph = TrapGraph(self.runtime, saver)
            await self.runtime.chat({'text': 'hello'})
            checkpoint = await saver.aget_tuple({'configurable': {'thread_id': 'main'}})
            self.assertNotIn('resident-only fixture', json.dumps(checkpoint.checkpoint))
            self.assertIn('loaded', json.dumps(checkpoint.checkpoint))
            self.assertIn('resident-only fixture', str(self.runtime.cloud.chat.call_args.args[1]))
        self.runtime.graph = None

    async def test_kernel_command_parser_and_authenticated_shared_workflow(self):
        self.assertEqual(memory_command('mem grant team 1 r'), ('sys_mem_grant', {'space': 'team', 'pid': 1, 'access': 'r'}))
        self.runtime.auth.setup('fixture kernel phrase')
        auth = await self.runtime.dispatch('sys_auth', {'passcode': 'fixture kernel phrase'})
        for text in ('mem create team', 'mem write shared:team plan "kernel project plan"', 'mem grant team 1 r'):
            await self.runtime.dispatch('sys_mem_command', {'text': text}, auth['token'])
        await self.runtime.dispatch('sys_exit', token=auth['token'])
        results, _ = await tool_step(self.runtime, {'tool_calls': [{'id': 'read', 'function': {'name': 'sys_mem_read', 'arguments': {'space': 'shared:team', 'key': 'plan'}}}]})
        self.assertTrue(json.loads(results[0]['content'])['result']['loaded'])
        self.assertNotIn('kernel project plan', results[0]['content'])
        self.assertIn('kernel project plan', self.mem.resident_context(1)['content'])
