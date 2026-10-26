import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from agent_zero.config import Config
from agent_zero.control import command
from agent_zero.processes import CURRENT_PID
from agent_zero.runtime import Runtime
from agent_zero.tools import tool_step, model_step


class BackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(root=Path(self.temp.name), cloud_enabled=True, cloud_free_only=True, session_budget_usd=0)
        self.runtime = Runtime(self.config)
        self.runtime.cloud.chat = AsyncMock(return_value={'role': 'assistant', 'content': 'fixture result'})

    async def asyncTearDown(self):
        await self.runtime.processes.interrupt_all()
        self.runtime.store.db.close()
        self.temp.cleanup()

    def child(self, parent=1):
        return self.runtime.processes.create('fixture child', parent=parent)['pid']

    async def auth(self):
        self.runtime.auth.setup('fixture kernel passphrase')
        result = await self.runtime.dispatch('sys_auth', {'passcode': 'fixture kernel passphrase'})
        return result['token']

    async def tools(self, name, args=None, actor=1):
        identity = CURRENT_PID.set(actor)
        try:
            rows, _ = await tool_step(self.runtime, {'tool_calls': [{'id': 'fixture', 'function': {'name': name, 'arguments': args or {}}}]})
            return json.loads(rows[0]['content'])
        finally:
            CURRENT_PID.reset(identity)

    def service(self, name='research', **extra):
        return self.runtime.services.syscall('sys_service_register', {'name': name, 'task': 'Summarize the supplied task', **extra}, actor=0)

    def job(self, name='daily', **extra):
        return self.runtime.scheduler.syscall('sys_job_create', {'name': name, 'task': 'fixture scheduled work', 'run_at': 100, **extra})

    async def done(self, pid):
        return await self.runtime.processes.wait(pid, timeout=2)

    async def test_mail_parent_child_round_trip_and_ack_ownership(self):
        child = self.child()
        result = await self.tools('sys_mail_send', {'recipient': 1, 'body': 'progress'}, child)
        message_id = result['result']['message_id']
        received = await self.tools('sys_mail_recv')
        self.assertEqual(received['result']['messages'][0]['sender'], child)
        self.assertEqual(received['result']['messages'][0]['body'], 'progress')
        self.assertEqual(self.runtime.mailboxes.count(1), 1)
        denied = await self.tools('sys_mail_ack', {'message_id': message_id}, child)
        self.assertIn('error', denied)
        await self.tools('sys_mail_ack', {'message_id': message_id})
        self.assertEqual(self.runtime.mailboxes.count(1), 0)
        self.runtime.cloud.chat.assert_not_called()

    async def test_mail_sibling_isolation_forgery_and_kernel_sender(self):
        a, b = self.child(), self.child()
        self.assertIn('error', await self.tools('sys_mail_send', {'recipient': b, 'body': 'cross sibling'}, a))
        self.assertIn('error', await self.tools('sys_mail_recv', {'pid': a}))
        self.assertIn('error', await self.tools('sys_mail_send', {'recipient': a, 'sender': 0, 'body': 'forge'}))
        token = await self.auth()
        result = await self.runtime.dispatch('sys_mail_send', {'recipient': a, 'body': 'operator notice'}, token)
        self.assertGreater(result['message_id'], 0)
        self.assertEqual(self.runtime.mailboxes.receive(a)[0]['sender'], 0)

    async def test_mail_bounds_and_large_receive_is_complete_valid_json(self):
        self.runtime.mailboxes.MAX_PENDING = 3
        for _ in range(3):
            self.runtime.mailboxes.send(0, 1, 'x' * 2048)
        with self.assertRaises(ValueError):
            self.runtime.mailboxes.send(0, 1, 'overflow')
        with self.assertRaises(ValueError):
            self.runtime.mailboxes.send(0, 1, '\0' * 2048)
        result = await self.tools('sys_mail_recv', {'limit': 20})
        self.assertEqual(len(result['result']['messages']), 1)
        self.assertTrue(result['result']['more'])
        self.assertEqual(len(result['result']['messages'][0]['body']), 2048)
        self.assertNotIn('truncated', result)

    async def test_mail_reap_preserves_messages_already_sent_to_parent(self):
        child = self.child()
        self.runtime.mailboxes.send(child, 1, 'finished progress')
        self.runtime.mailboxes.send(0, child, 'queued')
        process = self.runtime.processes.get(child)
        process.update(state='exited', exit_code=0)
        self.runtime.processes.save(process)
        self.runtime.processes.reap(child)
        self.assertEqual(self.runtime.mailboxes.count(child), 0)
        self.assertEqual(self.runtime.mailboxes.count(1), 1)

    async def test_service_registration_and_mutation_require_kernel(self):
        for method, args in [('sys_service_register', {'name': 'bad', 'task': 'task'}), ('sys_service_grant', {'name': 'bad', 'pid': 1}), ('sys_job_create', {'name': 'bad'}), ('sys_trace_list', {}), ('sys_mem_inspect', {'pid': 1}), ('sys_control_snapshot', {})]:
            with self.subTest(method=method), self.assertRaises(PermissionError):
                await self.runtime.dispatch(method, args)
            self.assertIn('error', await self.tools(method, args))
        self.assertEqual(self.runtime.services.list(), [])
        self.assertEqual(self.runtime.scheduler.list(), [])

    async def test_service_discovery_grants_and_parentage(self):
        self.service()
        child = self.child()
        self.assertEqual((await self.tools('sys_service_list', actor=child))['result']['services'], [])
        self.assertIn('error', await self.tools('sys_service_call', {'name': 'research'}, child))
        self.runtime.services.syscall('sys_service_grant', {'name': 'research', 'pid': child}, actor=0)
        result = await self.tools('sys_service_call', {'name': 'research', 'task': 'small request'}, child)
        worker = result['result']['pid']
        self.assertEqual(self.runtime.processes.get(worker)['ppid'], child)
        self.assertEqual((await self.done(worker))['result'], 'fixture result')
        # The worker's parent grant was not inherited.
        self.assertEqual(self.runtime.services.list(worker), [])

    async def test_service_is_single_instance_and_policy_can_disable_it(self):
        self.service()
        started, release = asyncio.Event(), asyncio.Event()
        async def blocked(*args):
            started.set()
            await release.wait()
            return {'role': 'assistant', 'content': 'done'}
        self.runtime.cloud.chat.side_effect = blocked
        result = self.runtime.services.syscall('sys_service_call', {'name': 'research'}, actor=1)
        await started.wait()
        with self.assertRaises(ValueError):
            self.runtime.services.syscall('sys_service_call', {'name': 'research'}, actor=1)
        with self.assertRaises(ValueError):
            self.runtime.services.syscall('sys_service_remove', {'name': 'research'}, actor=0)
        self.runtime.services.syscall('sys_service_enable', {'name': 'research', 'enabled': False}, actor=0)
        release.set()
        await self.done(result['pid'])
        with self.assertRaises(ValueError):
            self.runtime.services.syscall('sys_service_call', {'name': 'research'}, actor=1)

    async def test_service_keeps_capability_snapshot_and_model_cannot_override_it(self):
        self.service(capabilities=['file_read'])
        self.runtime.permissions['file_write'] = True
        self.assertIn('error', await self.tools('sys_service_call', {'name': 'research', 'capabilities': ['file_write']}))
        result = await self.tools('sys_service_call', {'name': 'research'})
        process = self.runtime.processes.get(result['result']['pid'])
        self.assertEqual(process['capabilities'], ['file_read'])
        self.assertEqual(process['model'], self.runtime.model)
        await self.done(process['pid'])

    async def test_kernel_started_service_queues_until_exit(self):
        token = await self.auth()
        await self.runtime.dispatch('sys_service_register', {'name': 'worker', 'task': 'fixture task'}, token)
        result = await self.runtime.dispatch('sys_service_start', {'name': 'worker'}, token)
        await asyncio.sleep(0)
        self.runtime.cloud.chat.assert_not_called()
        await self.runtime.dispatch('sys_exit', token=token)
        await self.done(result['pid'])
        self.runtime.cloud.chat.assert_awaited_once()

    async def test_scheduler_one_shot_runs_once_and_sends_completion_mail(self):
        self.job()
        self.runtime.scheduler.tick(100)
        self.runtime.scheduler.tick(100)
        pid = self.runtime.scheduler.runs()[0]['pid']
        await self.done(pid)
        self.runtime.scheduler.tick(101)
        self.assertEqual(len(self.runtime.scheduler.runs()), 1)
        self.assertEqual(self.runtime.scheduler.runs()[0]['status'], 'exited')
        self.assertIn('fixture result', self.runtime.mailboxes.receive(1)[0]['body'])
        self.runtime.cloud.chat.assert_awaited_once()

    async def test_schedule_interval_coalesces_missed_ticks_and_does_not_overlap(self):
        self.job(interval=60)
        started, release = asyncio.Event(), asyncio.Event()
        async def blocked(*args):
            started.set(); await release.wait()
            return {'role': 'assistant', 'content': 'done'}
        self.runtime.cloud.chat.side_effect = blocked
        self.runtime.scheduler.tick(10000)
        pid = self.runtime.scheduler.runs()[0]['pid']
        self.assertEqual(self.runtime.scheduler.get('daily')['next_run'], 10060)
        await started.wait()
        self.runtime.scheduler.tick(10100)
        self.assertEqual(len(self.runtime.scheduler.runs()), 1)
        release.set(); await self.done(pid)
        self.runtime.scheduler.tick(10100)
        self.assertEqual(len(self.runtime.scheduler.runs()), 2)
        await self.done(self.runtime.scheduler.runs()[0]['pid'])

    async def test_scheduler_holds_during_kernel_mode_and_notice_needs_no_model(self):
        self.job(kind='notice', task='local reminder')
        token = await self.auth()
        self.runtime.scheduler.tick(100)
        self.assertEqual(self.runtime.scheduler.runs(), [])
        await self.runtime.dispatch('sys_exit', token=token)
        self.runtime.scheduler.tick(100)
        self.assertEqual(self.runtime.scheduler.runs()[0]['status'], 'delivered')
        self.assertEqual(self.runtime.mailboxes.receive(1)[0]['body'], 'local reminder')
        self.runtime.cloud.chat.assert_not_called()

    async def test_scheduler_backpressure_retries_without_spawning_or_spending(self):
        self.job()
        self.runtime.permissions['agent_spawn'] = False
        self.runtime.scheduler.tick(100)
        self.assertEqual(self.runtime.scheduler.runs()[0]['status'], 'deferred')
        self.runtime.scheduler.tick(110)
        self.assertEqual(len(self.runtime.scheduler.runs()), 1)
        self.assertEqual(len(self.runtime.processes.records), 1)
        self.runtime.permissions['agent_spawn'] = True
        self.runtime.scheduler.tick(130)
        await self.done(self.runtime.scheduler.runs()[0]['pid'])
        self.runtime.cloud.chat.assert_awaited_once()

    async def test_scheduler_pause_remove_and_invalid_time_values(self):
        self.job()
        self.runtime.scheduler.syscall('sys_job_enable', {'name': 'daily', 'enabled': False})
        self.runtime.scheduler.tick(100)
        self.assertEqual(self.runtime.scheduler.runs(), [])
        self.runtime.scheduler.syscall('sys_job_remove', {'name': 'daily'})
        self.assertEqual(self.runtime.scheduler.list(), [])
        for extra in ({'run_at': float('nan')}, {'interval': float('inf')}, {'interval': 1}, {'kind': 'notice', 'task': '\0'*2048}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.job(**extra)

    async def test_restart_preserves_specs_mail_and_never_replays_claimed_work(self):
        self.service()
        self.job()
        self.runtime.mailboxes.send(0, 1, 'persisted')
        spec = self.runtime.scheduler.get('daily'); spec['enabled'] = False
        self.runtime.scheduler.save(spec)
        with self.runtime.store.db:
            self.runtime.store.db.execute("INSERT INTO scheduled_runs(job,due,claimed,status) VALUES('daily',100,100,'claimed')")
        self.runtime.store.db.close()
        self.runtime = Runtime(self.config)
        self.runtime.cloud.chat = AsyncMock(side_effect=AssertionError('replayed'))
        self.runtime.scheduler.tick(1000)
        self.assertEqual(self.runtime.scheduler.runs()[0]['status'], 'interrupted')
        self.assertEqual(self.runtime.mailboxes.receive(1)[0]['body'], 'persisted')
        self.assertEqual(self.runtime.services.list(1)[0]['name'], 'research')
        self.runtime.cloud.chat.assert_not_called()

    async def test_restart_marks_running_scheduled_child_interrupted(self):
        self.job()
        self.runtime.scheduler.tick(100)
        pid = self.runtime.scheduler.runs()[0]['pid']
        await self.runtime.processes.interrupt_all()
        self.runtime.store.db.close()
        self.runtime = Runtime(self.config)
        self.assertIn(self.runtime.scheduler.runs()[0]['status'], ('cancelled', 'interrupted'))
        self.assertEqual(self.runtime.scheduler.get('daily')['last_pid'], pid)
        self.assertFalse(self.runtime.scheduler.get('daily')['enabled'])

    async def test_memory_inspector_is_read_on_demand_and_kernel_only(self):
        self.runtime.memory.syscall('sys_mem_write', {'key': 'private-note', 'content': 'body fixture'}, actor=1)
        token = await self.auth()
        inspected = await self.runtime.dispatch('sys_mem_inspect', {'pid': 1}, token)
        self.assertEqual(inspected['pages'][0]['key'], 'private-note')
        self.assertNotIn('body fixture', json.dumps(inspected))
        self.assertEqual(inspected['stats']['private_pages'], 1)
        page = await self.runtime.dispatch('sys_mem_read', {'key': 'private-note'}, token)
        self.assertEqual(page['content'], 'body fixture')

    async def test_trace_correlates_process_model_and_tool_without_payloads(self):
        secret = 'private payload must stay out of traces'
        await self.tools('sys_mem_write', {'key': 'note', 'content': secret})
        process = self.runtime.processes.create('private task text')
        self.runtime.processes.start(process['pid'])
        await self.done(process['pid'])
        rows = self.runtime.trace.list(limit=100)
        self.assertNotIn(secret, json.dumps(rows))
        self.assertNotIn('private task text', json.dumps(rows))
        process_span = next(r for r in rows if r['operation'] == 'process.run')
        model_span = next(r for r in rows if r['kind'] == 'model')
        self.assertEqual(model_span['parent'], process_span['id'])
        self.assertGreaterEqual(model_span['duration_ms'], 0)
        self.assertTrue(any(r['operation'] == 'sys_mem_write' for r in rows))

    async def test_trace_captures_denials_cancellation_and_sanitized_errors(self):
        await self.tools('sys_mem_grant', {'space': 'missing', 'pid': 1, 'access': 'rw'})
        self.assertEqual(self.runtime.trace.list()[0]['status'], 'denied')
        started = asyncio.Event()
        async def blocked(*args):
            started.set(); await asyncio.Event().wait()
        self.runtime.cloud.chat.side_effect = blocked
        pid = self.child(); self.runtime.processes.start(pid)
        await started.wait()
        await self.runtime.processes.signal(pid, 'TERM')
        await self.done(pid)
        self.assertTrue(any(t['kind'] == 'model' and t['status'] == 'cancelled' for t in self.runtime.trace.list()))
        self.runtime.cloud.chat.side_effect = RuntimeError('sensitive provider body')
        with self.assertRaises(RuntimeError):
            await model_step(self.runtime, [{'role': 'user', 'content': 'secret prompt'}], self.runtime.model, None)
        trace = self.runtime.trace.list()[0]
        self.assertEqual(trace['error_type'], 'RuntimeError')
        self.assertNotIn('sensitive provider body', json.dumps(self.runtime.trace.list()))

    async def test_trace_retention_clear_and_recovery(self):
        self.runtime.trace.LIMIT = 3
        for _ in range(7):
            self.runtime.trace.event(1, 'fixture')
        self.assertEqual(len(self.runtime.trace.list()), 3)
        self.assertEqual(self.runtime.trace.clear()['cleared'], 3)
        with self.runtime.store.db:
            self.runtime.store.db.execute("INSERT INTO execution_traces(pid,kind,operation,status,started) VALUES(1,'model','fixture','running',100)")
        self.runtime.store.db.close()
        self.runtime = Runtime(self.config)
        self.assertEqual(self.runtime.trace.list()[0]['status'], 'interrupted')

    async def test_flush_clears_mail_and_run_results_but_preserves_policy(self):
        self.job(); self.service()
        self.runtime.scheduler.tick(100)
        await self.done(self.runtime.scheduler.runs()[0]['pid'])
        self.runtime.scheduler.reconcile()
        self.assertTrue(self.runtime.mailboxes.count(1))
        token = await self.auth()
        await self.runtime.dispatch('sys_mem_flush', {'scope': 'all'}, token)
        self.assertEqual(self.runtime.mailboxes.count(1), 0)
        self.assertEqual(self.runtime.scheduler.runs()[0]['result'], '')
        self.assertEqual(len(self.runtime.services.list()), 1)
        self.assertEqual(len(self.runtime.scheduler.list()), 1)

    async def test_public_snapshot_hides_service_tasks_job_tasks_and_mail_bodies(self):
        self.service(task='service secret')
        self.job(task='job secret')
        self.runtime.mailboxes.send(0, 1, 'mail secret')
        encoded = json.dumps(self.runtime.snapshot())
        for secret in ('service secret', 'job secret', 'mail secret'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(self.runtime.snapshot()['background']['root_mail_pending'], 1)

    async def test_control_parser_handles_timezone_and_rejects_ambiguous_or_shell_input(self):
        method, params = command('schedule at release 2026-10-26T09:00:00-05:00 "review release"')
        self.assertEqual(method, 'sys_job_create')
        self.assertEqual(params['task'], 'review release')
        self.assertEqual(command('schedule notice tea 60 "take a break"', now=100)[1]['run_at'], 160)
        self.assertEqual(command('service revoke helper 2'), ('sys_service_grant', {'name': 'helper', 'pid': 2, 'enabled': False}))
        for text in ('schedule at x 2026-10-26T09:00:00 task', 'schedule after x nan task', 'trace 1 extra', 'schedule after x -1 task', 'rm -rf /'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                command(text)

    async def test_real_graph_parent_invokes_service_waits_and_receives_child_mail(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        self.service()
        turns = {}
        async def provider(model, messages, record, tools):
            pid = CURRENT_PID.get()
            turns[pid] = turns.get(pid, 0) + 1
            turn = turns[pid]
            if pid != 1:
                name, args = ('sys_mail_send', {'recipient': 1, 'body': 'service progress'}) if turn == 1 else (None, None)
            elif turn == 1:
                name, args = 'sys_service_list', {}
            elif turn == 2:
                name, args = 'sys_service_call', {'name': 'research', 'task': 'fixture request'}
            elif turn == 3:
                name, args = 'sys_wait', {'pid': max(self.runtime.processes.records), 'timeout': 2}
            elif turn == 4:
                name, args = 'sys_mail_recv', {}
            elif turn == 5:
                name, args = 'sys_mail_ack', {'message_id': self.runtime.mailboxes.receive(1)[0]['id']}
            else:
                name, args = None, None
            if name:
                return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': f'{pid}-{turn}', 'function': {'name': name, 'arguments': args}}]}
            return {'role': 'assistant', 'content': 'service result received' if pid == 1 else 'worker complete'}
        self.runtime.cloud.chat.side_effect = provider
        async with AsyncSqliteSaver.from_conn_string(str(Path(self.temp.name)/'graph.sqlite')) as saver:
            self.runtime.graph = TrapGraph(self.runtime, saver)
            result = await self.runtime.chat({'text': 'use the research service'})
            self.assertEqual(result['message'], 'service result received')
            self.assertEqual(self.runtime.mailboxes.count(1), 0)
            child = max(self.runtime.processes.records)
            self.assertEqual(self.runtime.processes.get(child)['result'], 'worker complete')
            traces = self.runtime.trace.list(limit=100)
            start = next(t for t in traces if t['operation'] == 'sys_service_call')
            worker = next(t for t in traces if t['operation'] == 'process.run')
            self.assertEqual(worker['parent'], start['id'])
        self.runtime.graph = None
