import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from agent_zero.config import Config
from agent_zero.processes import CURRENT_PID
from agent_zero.runtime import Runtime
from agent_zero.tools import TOOLS, tool_step


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = Runtime(Config(root=self.root, cloud_enabled=True))
        self.runtime.cloud.chat = AsyncMock(return_value={"role":"assistant", "content":"Child result"})
        self.manager = self.runtime.processes

    async def asyncTearDown(self):
        await self.manager.interrupt_all()
        self.runtime.store.db.close()
        self.temp.cleanup()

    async def wait_done(self, pid):
        return await asyncio.wait_for(self.manager.wait(pid, timeout=2), 3)

    async def test_spawn_wait_tree_and_reap(self):
        child = await self.manager.syscall('sys_spawn', {'task':'Find the answer','name':'research'}, actor=1)
        result = await self.wait_done(child['pid'])
        self.assertEqual(result['ppid'], 1)
        self.assertEqual(result['state'], 'exited')
        self.assertEqual(result['result'], 'Child result')
        self.assertEqual(self.manager.tree()[0]['children'][0]['pid'], child['pid'])
        self.assertEqual(self.manager.reap(child['pid'], actor=1)['exit_code'], 0)
        self.assertEqual(len(self.manager.table()), 1)
        self.assertEqual(self.runtime.cloud.chat.await_count, 1)

    async def test_fork_is_inert_until_exec_and_copies_conversation(self):
        self.runtime.store.message('main','user','Parent context')
        child = await self.manager.syscall('sys_fork', {'task':'Follow up'}, actor=1)
        await asyncio.sleep(0)
        self.runtime.cloud.chat.assert_not_called()
        self.assertEqual(child['state'], 'created')
        self.manager.start(child['pid'], actor=1)
        await self.wait_done(child['pid'])
        self.assertTrue(any(message.get('content')=='Parent context' for message in self.runtime.cloud.chat.call_args.args[1]))
        with self.assertRaises(ValueError):
            self.manager.start(child['pid'])

    async def test_non_registered_exec_cannot_run_commands(self):
        child = self.manager.create('Task')
        with self.assertRaises(ValueError):
            self.manager.start(child['pid'], workflow='/bin/sh')
        self.runtime.cloud.chat.assert_not_called()

    async def test_capabilities_cannot_increase(self):
        with self.assertRaises(PermissionError):
            self.manager.create('Task', capabilities=['file_write'])
        child = self.manager.create('Task', capabilities=['file_read'])
        with self.assertRaises(PermissionError):
            self.manager.create('Nested', parent=child['pid'])
        self.assertEqual(self.manager.effective_caps(child['pid']), {'file_read'})
        self.runtime.permissions['file_read'] = False
        self.assertEqual(self.manager.effective_caps(child['pid']), set())

    async def test_siblings_cannot_inspect_signal_or_wait_for_each_other(self):
        left = self.manager.create('Left')
        right = self.manager.create('Right')
        with self.assertRaises(PermissionError):
            self.manager.info(right['pid'], actor=left['pid'])
        with self.assertRaises(PermissionError):
            await self.manager.wait(right['pid'], actor=left['pid'])
        with self.assertRaises(PermissionError):
            await self.manager.signal(1, 'TERM', actor=left['pid'])
        with self.assertRaises(PermissionError):
            await self.manager.signal(right['pid'], 'TERM', actor=left['pid'])

    async def test_spawn_and_depth_limits(self):
        child = self.manager.create('First')
        grandchild = self.manager.create('Second', parent=child['pid'])
        with self.assertRaises(ValueError):
            self.manager.create('Too deep', parent=grandchild['pid'])
        self.runtime.config.max_child_agents = 2
        with self.assertRaises(ValueError):
            self.manager.create('Too many')

    async def test_term_cancels_entire_subtree_and_can_reap(self):
        started = asyncio.Event()
        async def slow(*args):
            started.set()
            await asyncio.Event().wait()
        self.runtime.cloud.chat = AsyncMock(side_effect=slow)
        parent = self.manager.create('Parent')
        self.manager.start(parent['pid'])
        await started.wait()
        child = self.manager.create('Child', parent=parent['pid'])
        self.manager.start(child['pid'])
        await self.manager.signal(parent['pid'], 'TERM', actor=1)
        self.assertEqual(self.manager.get(parent['pid'])['state'], 'cancelled')
        self.assertEqual(self.manager.get(child['pid'])['state'], 'cancelled')
        self.assertEqual(self.manager.get(child['pid'])['ppid'], 1)
        self.manager.reap(parent['pid'], actor=1)
        self.manager.reap(child['pid'], actor=1)

    async def test_immediate_cancel_before_task_runs_settles(self):
        child = self.manager.create('Never started')
        self.manager.start(child['pid'])
        await self.manager.signal(child['pid'], 'TERM', actor=1)
        self.assertEqual(self.manager.get(child['pid'])['state'], 'cancelled')
        self.assertTrue(self.manager.done[child['pid']].is_set())
        self.runtime.cloud.chat.assert_not_called()

    async def test_stop_cont_at_model_boundary(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def provider(*args):
            started.set()
            await finish.wait()
            return {'content':'Done'}
        self.runtime.cloud.chat = AsyncMock(side_effect=provider)
        child = self.manager.create('Pause me')
        self.manager.start(child['pid'])
        await started.wait()
        await self.manager.signal(child['pid'], 'STOP', actor=1)
        finish.set()
        await asyncio.sleep(.02)
        self.assertEqual(self.manager.get(child['pid'])['state'],'stopped')
        self.assertFalse(self.manager.done[child['pid']].is_set())
        await self.manager.signal(child['pid'], 'CONT', actor=1)
        self.assertEqual((await self.wait_done(child['pid']))['state'],'exited')

    async def test_kernel_spawn_is_queued_until_exit(self):
        self.runtime.auth.setup('test operator passphrase')
        token = (await self.runtime.dispatch('sys_auth', {'passcode':'test operator passphrase'}))['token']
        child = await self.runtime.dispatch('sys_spawn', {'task':'Queued task'}, token)
        await asyncio.sleep(.01)
        self.runtime.cloud.chat.assert_not_called()
        await self.runtime.dispatch('sys_exit', token=token)
        self.assertEqual((await self.wait_done(child['pid']))['state'], 'exited')

    async def test_all_host_process_syscalls_require_kernel_token(self):
        from agent_zero.processes import PROCESS_SYSCALLS
        for method in PROCESS_SYSCALLS | {'sys_trap_table'}:
            with self.subTest(method=method), self.assertRaises(PermissionError):
                await self.runtime.dispatch(method, {})

    async def test_ring_zero_calls_never_exposed_or_dispatched_by_models(self):
        forbidden = {'sys_model_swap','sys_perm_toggle','sys_mount_sandbox','sys_mem_flush','sys_resolve','sys_auth','sys_parse','sys_exit'}
        self.assertFalse(forbidden & {tool['function']['name'] for tool in TOOLS})
        original = self.runtime.model
        for method in forbidden:
            message = {'tool_calls':[{'function':{'name':method,'arguments':{'target_model_id':self.runtime.registry['cloud'][1]['id']}}}]}
            results, escalation = await tool_step(self.runtime, message)
            self.assertIn('error', json.loads(results[0]['content']))
            self.assertFalse(escalation)
        self.assertEqual(self.runtime.model, original)
        self.assertEqual(self.runtime.auth.tokens, {})
        with self.assertRaises(PermissionError):
            await self.manager.syscall('sys_model_swap', {}, actor=1)

    async def test_restart_marks_unfinished_children_interrupted_without_replay(self):
        child = self.manager.create('Do not replay')
        other = Runtime(Config(root=self.root, cloud_enabled=True))
        try:
            self.assertEqual(other.processes.get(child['pid'])['state'],'interrupted')
            self.assertEqual(other.processes.tasks,{})
            self.assertEqual(other.processes.get(1)['state'],'idle')
        finally:
            other.store.db.close()

    async def test_child_cannot_read_after_permission_revoked(self):
        child = self.manager.create('Restricted', capabilities=[])
        (self.runtime.sandbox.root/'note.txt').write_text('secret')
        context = CURRENT_PID.set(child['pid'])
        try:
            with self.assertRaises(PermissionError):
                self.runtime.file_tool('read','note.txt')
        finally:
            CURRENT_PID.reset(context)

    async def test_parallel_inference_is_bounded_and_budget_gateway_shared(self):
        active, peak = 0, 0
        release, entered = asyncio.Event(), asyncio.Event()
        async def provider(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
                return {'content':'Done'}
            finally:
                active -= 1
        self.runtime.cloud.chat = AsyncMock(side_effect=provider)
        children = [self.manager.create(f'Task {n}') for n in range(4)]
        for child in children:
            self.manager.start(child['pid'])
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(.01)
        self.assertEqual(peak, 2)
        release.set()
        await asyncio.gather(*(self.wait_done(child['pid']) for child in children))
        self.assertEqual(peak, 2)
        self.assertTrue(all(self.manager.info(child['pid'])['budget_owner_pid']==1 for child in children))

    async def test_agent_cannot_select_model_or_forge_parent_in_spawn(self):
        for arguments in ({'task':'Task','model':'another-model'}, {'task':'Task','ppid':0}, {'task':'Task','token':'forged'}):
            results, _ = await tool_step(self.runtime, {'tool_calls':[{'function':{'name':'sys_spawn','arguments':arguments}}]})
            self.assertIn('error', json.loads(results[0]['content']))
        self.assertEqual(len(self.manager.table()),1)

    async def test_child_escalation_requires_operator_and_remains_reapable(self):
        self.runtime.cloud.chat = AsyncMock(return_value={'tool_calls':[{'function':{'name':'request_kernel','arguments':{'reason':'Need a model swap'}}}]})
        child = self.manager.create('Request privilege')
        self.manager.start(child['pid'])
        await asyncio.wait_for(self.manager.done[child['pid']].wait(),2)
        self.assertEqual(self.manager.get(child['pid'])['state'],'waiting_review')
        self.assertEqual(self.runtime.audio.mode,'locked')
        self.assertEqual(len(self.runtime.store.pending()),1)
        self.runtime.auth.setup('test operator passphrase')
        token = (await self.runtime.dispatch('sys_auth', {'passcode':'test operator passphrase'}))['token']
        trap = self.runtime.store.pending()[0]
        await self.runtime.dispatch('sys_resolve', {'trap_id':trap['id'],'approved':True}, token)
        self.assertEqual(self.manager.get(child['pid'])['state'],'interrupted')
        self.manager.reap(child['pid'], actor=1)

    async def test_root_can_delegate_wait_and_synthesize_through_graph(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        def call(name, **args):
            return {'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}}]}
        async def provider(model, messages, *args):
            pid = CURRENT_PID.get()
            if pid != 1:
                return {'content':'Delegated finding'}
            tools = [m for m in messages if m.get('role')=='tool']
            if not tools:
                return call('sys_spawn',task='Focused task')
            first = json.loads(tools[0]['content'])['result']['pid']
            if len(tools) == 1:
                return call('sys_wait',pid=first,timeout=2)
            return {'content':'Combined delegated finding'}
        self.runtime.cloud.chat = AsyncMock(side_effect=provider)
        async with AsyncSqliteSaver.from_conn_string(str(self.root/'graph.sqlite')) as saver:
            self.runtime.graph = TrapGraph(self.runtime,saver)
            result = await self.runtime.chat({'text':'Delegate this'})
            self.assertEqual(result['message'],'Combined delegated finding')
            child = self.manager.table()[1]
            self.assertEqual(child['result'],'Delegated finding')
            self.assertEqual(child['model'],self.runtime.model)
            state = await self.runtime.graph.graph.aget_state({'configurable':{'thread_id':child['session']}})
            self.assertEqual(state.values['response'],'Delegated finding')

    async def test_identity_comes_from_supervisor_context(self):
        child = self.manager.create('Identity')
        context = CURRENT_PID.set(child['pid'])
        try:
            result, _ = await tool_step(self.runtime, {'tool_calls':[{'function':{'name':'sys_getpid','arguments':{}}},{'function':{'name':'sys_getppid','arguments':{}}}]})
            self.assertEqual(json.loads(result[0]['content'])['result']['pid'],child['pid'])
            self.assertEqual(json.loads(result[1]['content'])['result']['ppid'],1)
        finally:
            CURRENT_PID.reset(context)

    async def test_kernel_entry_suspends_child_without_replaying_inference(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def provider(*args):
            started.set()
            await release.wait()
            return {'content':'Completed once'}
        self.runtime.cloud.chat = AsyncMock(side_effect=provider)
        child = self.manager.create('Keep my work')
        self.manager.start(child['pid'])
        await started.wait()
        self.runtime.auth.setup('test operator passphrase')
        token = (await self.runtime.dispatch('sys_auth', {'passcode':'test operator passphrase'}))['token']
        release.set()
        await asyncio.sleep(.01)
        self.assertEqual(self.manager.info(child['pid'])['state'],'suspended')
        self.assertFalse(self.manager.done[child['pid']].is_set())
        await self.runtime.dispatch('sys_exit',token=token)
        self.assertEqual((await self.wait_done(child['pid']))['result'],'Completed once')
        self.assertEqual(self.runtime.cloud.chat.await_count,1)

    async def test_voice_stop_does_not_cancel_background_child(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def provider(*args):
            started.set()
            await release.wait()
            return {'content':'Background done'}
        self.runtime.cloud.chat = AsyncMock(side_effect=provider)
        child = self.manager.create('Background task')
        self.manager.start(child['pid'])
        await started.wait()
        await self.runtime.dispatch('voice_cancel')
        self.assertFalse(self.manager.done[child['pid']].is_set())
        release.set()
        self.assertEqual((await self.wait_done(child['pid']))['state'],'exited')

    async def test_children_share_one_pre_request_spend_ceiling(self):
        from unittest.mock import patch
        from agent_zero.engines import OpenRouter
        self.runtime.config.session_budget_usd = .0003
        self.runtime.cloud = OpenRouter(self.runtime.config)
        provider = {'choices':[{'message':{'role':'assistant','content':'Budgeted response'}}]}
        with patch.dict('os.environ', {'OPENROUTER_API_KEY':'test-fixture'}), patch('agent_zero.engines.request_json',return_value=provider) as request:
            children = [self.manager.create(f'Budget task {index}') for index in range(2)]
            for child in children:
                self.manager.start(child['pid'])
            results = await asyncio.gather(*(self.wait_done(child['pid']) for child in children))
        self.assertEqual(request.call_count,1)
        self.assertEqual(sorted(item['state'] for item in results),['exited','failed'])
        self.assertLessEqual(self.runtime.cloud.spent,self.runtime.config.session_budget_usd)
        self.assertTrue(any('ceiling' in item['error'] for item in results))
