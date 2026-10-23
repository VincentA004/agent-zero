"""Race, malformed-output and failure cases for the agent supervisor."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from agent_zero.config import Config
from agent_zero.runtime import Runtime
from agent_zero.tools import tool_step


class ResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = Runtime(Config(root=self.root,cloud_enabled=True,cloud_free_only=True,session_budget_usd=0))
        self.manager = self.runtime.processes
        self.runtime.cloud.chat = AsyncMock(return_value={'content':'done'})

    async def asyncTearDown(self):
        await self.manager.interrupt_all()
        self.runtime.store.db.close()
        self.temp.cleanup()

    async def test_repeated_stop_then_cont_releases_once_with_correct_state(self):
        child = self.manager.create('Pause idempotently')
        await self.manager.signal(child['pid'],'STOP',actor=1)
        await self.manager.signal(child['pid'],'STOP',actor=1)
        await self.manager.signal(child['pid'],'CONT',actor=1)
        self.assertEqual(self.manager.get(child['pid'])['state'],'created')
        self.manager.start(child['pid'],actor=1)
        result=await self.manager.wait(child['pid'],timeout=2)
        self.assertEqual(result['state'],'exited')
        self.assertEqual(self.runtime.cloud.chat.await_count,1)

    async def test_subtree_cancel_preserves_finished_child_exit_status(self):
        parent=self.manager.create('Parent')
        child=self.manager.create('Child',parent=parent['pid'])
        self.manager.start(child['pid'])
        completed=await self.manager.wait(child['pid'],timeout=2)
        self.assertEqual(completed['state'],'exited')
        await self.manager.signal(parent['pid'],'TERM',actor=1)
        after=self.manager.info(child['pid'])
        self.assertEqual(after['state'],'exited')
        self.assertEqual(after['exit_code'],0)
        self.assertEqual(after['result'],'done')

    async def test_malformed_tool_entries_are_errors_and_batch_continues(self):
        malformed=[None,'not a call',{}, {'function':None}]
        responses,escalation=await tool_step(self.runtime,{'tool_calls':malformed})
        self.assertEqual(len(responses),4)
        self.assertFalse(escalation)
        for response in responses:
            self.assertIn('error',json.loads(response['content']))
        self.assertEqual(len(self.manager.table()),1)

    async def test_large_tool_result_remains_valid_bounded_json(self):
        (self.runtime.sandbox.root/'large.txt').write_text('A'*12000)
        responses,_=await tool_step(self.runtime,{'tool_calls':[{'function':{'name':'read_file','arguments':{'path':'large.txt'}}}]})
        content=responses[0]['content']
        self.assertLessEqual(len(content),4000)
        result=json.loads(content)
        self.assertTrue(result.get('truncated'))
        self.assertTrue(result['result'].startswith('AAAA'))

    async def test_flush_all_erases_fork_snapshots_and_completed_results(self):
        self.runtime.store.message('main','user','old conversation marker')
        fork=await self.manager.syscall('sys_fork',{'task':'Use the old conversation marker'},actor=1)
        child=self.manager.create('Completed old task')
        self.manager.start(child['pid'])
        await self.manager.wait(child['pid'],timeout=2)
        self.runtime.auth.setup('operator fixture passphrase')
        token=(await self.runtime.dispatch('sys_auth',{'passcode':'operator fixture passphrase'}))['token']
        await self.runtime.dispatch('sys_mem_flush',{'scope':'all'},token)
        retained=json.dumps(list(self.manager.records.values()))
        self.assertNotIn('old conversation marker',retained)
        self.assertNotIn('Completed old task',retained)
        self.assertNotIn('done',retained)
        self.assertIn(self.manager.get(fork['pid'])['state'],('cancelled','interrupted'))
        self.assertEqual(self.runtime.store.history('main'),[])

    async def test_stopped_waiter_restores_running_state_after_child_finishes(self):
        parent=self.manager.create('Waiting parent')
        child=self.manager.create('Child',parent=parent['pid'])
        self.manager.get(parent['pid'])['state']='running'
        waiter=asyncio.create_task(self.manager.wait(child['pid'],actor=parent['pid'],timeout=2))
        await asyncio.sleep(0)
        await self.manager.signal(parent['pid'],'STOP',actor=1)
        self.manager.get(child['pid']).update(state='exited',result='done',exit_code=0)
        self.manager.done[child['pid']].set()
        await waiter
        await self.manager.signal(parent['pid'],'CONT',actor=1)
        self.assertEqual(self.manager.get(parent['pid'])['state'],'running')

    async def test_flush_all_cancels_paused_graph_before_deleting_checkpoints(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        started=asyncio.Event()
        async def provider(*args):
            started.set()
            await asyncio.Event().wait()
        self.runtime.cloud.chat=AsyncMock(side_effect=provider)
        async with AsyncSqliteSaver.from_conn_string(str(self.root/'graph.sqlite')) as saver:
            self.runtime.graph=TrapGraph(self.runtime,saver)
            child=self.manager.create('Delete my in-flight graph')
            self.manager.start(child['pid'])
            await started.wait()
            self.runtime.auth.setup('operator fixture passphrase')
            token=(await self.runtime.dispatch('sys_auth',{'passcode':'operator fixture passphrase'}))['token']
            await self.runtime.dispatch('sys_mem_flush',{'scope':'all'},token)
            self.assertEqual(self.manager.get(child['pid'])['state'],'cancelled')
            self.assertFalse(self.manager.tasks)
            self.assertEqual([item async for item in saver.alist(None)],[])
            await self.runtime.dispatch('sys_exit',token=token)
            await asyncio.sleep(.01)
            self.assertEqual(self.runtime.cloud.chat.await_count,1)
            self.assertEqual([item async for item in saver.alist(None)],[])

    async def test_malicious_child_cannot_modify_kernel_or_read_host_secrets(self):
        from agent_zero.processes import CURRENT_PID
        child=self.manager.create('Malicious fixture',capabilities=[])
        secret=self.root/'openrouter.key'
        secret.write_text('LOCAL_FIXTURE_SECRET')
        before=(self.runtime.model,dict(self.runtime.permissions),dict(self.runtime.sandbox.mounts))
        attempts=[('sys_model_swap',{'target_model_id':self.runtime.registry['cloud'][0]['id']}),
                  ('sys_perm_toggle',{'capability_id':'file_write','enabled':True}),
                  ('sys_mount_sandbox',{'host_path':str(self.root),'mount_as':'root','mode':'rw'}),
                  ('sys_mem_flush',{'scope':'all'}),('sys_auth',{'passcode':'forged'}),
                  ('sys_resolve',{'trap_id':'forged','approved':True}),
                  ('read_file',{'path':'../openrouter.key'}),
                  ('sys_signal',{'pid':1,'signal':'TERM'}),
                  ('sys_spawn',{'task':'Escalate','capabilities':['file_write']})]
        context=CURRENT_PID.set(child['pid'])
        try:
            for name,args in attempts:
                results,_=await tool_step(self.runtime,{'tool_calls':[{'id':'fixture','function':{'name':name,'arguments':args}}]})
                self.assertIn('error',json.loads(results[0]['content']))
                self.assertNotIn('LOCAL_FIXTURE_SECRET',results[0]['content'])
        finally:
            CURRENT_PID.reset(context)
        self.assertEqual(before,(self.runtime.model,self.runtime.permissions,self.runtime.sandbox.mounts))
        self.assertEqual(self.runtime.auth.tokens,{})
        self.assertEqual(len(self.manager.table()),2)
        self.runtime.cloud.chat.assert_not_called()

    async def test_abrupt_host_process_restart_never_replays_child(self):
        import os
        import subprocess
        import sys
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        script=r'''
import asyncio,sys
from pathlib import Path
from agent_zero.config import Config
from agent_zero.runtime import Runtime
from agent_zero.graph import TrapGraph
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
async def main():
 root=Path(sys.argv[1])
 runtime=Runtime(Config(root=root,cloud_enabled=True,cloud_free_only=True,session_budget_usd=0))
 async def provider(*args):
  (root/'provider-started').write_text('exactly one simulated provider entry')
  await asyncio.Event().wait()
 runtime.cloud.chat=provider
 async with AsyncSqliteSaver.from_conn_string(str(root/'graph.sqlite')) as saver:
  runtime.graph=TrapGraph(runtime,saver)
  child=runtime.processes.create('Survive crash without replay')
  runtime.processes.start(child['pid'])
  await asyncio.Event().wait()
asyncio.run(main())
'''
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with (root/'subprocess.log').open('wb') as log:
                process=subprocess.Popen([sys.executable,'-c',script,str(root)],stdout=log,stderr=log)
                try:
                    async with asyncio.timeout(8):
                        while not (root/'provider-started').exists():
                            if process.poll() is not None:
                                self.fail((root/'subprocess.log').read_text())
                            await asyncio.sleep(.02)
                    process.kill()  # exact test child only; exercises SQLite WAL recovery
                    await asyncio.to_thread(process.wait,3)
                finally:
                    if process.poll() is None:
                        process.kill()
                        await asyncio.to_thread(process.wait,3)
            recovered=Runtime(Config(root=root,cloud_enabled=True,cloud_free_only=True,session_budget_usd=0))
            recovered.cloud.chat=AsyncMock(side_effect=AssertionError('Restart replayed a provider request'))
            try:
                async with AsyncSqliteSaver.from_conn_string(str(root/'graph.sqlite')) as saver:
                    recovered.graph=TrapGraph(recovered,saver)
                    self.assertEqual(recovered.processes.get(2)['state'],'interrupted')
                    self.assertEqual(recovered.processes.get(2)['exit_code'],130)
                    self.assertEqual(recovered.processes.get(1)['state'],'idle')
                    self.assertFalse(recovered.processes.tasks)
                    await asyncio.sleep(.02)
                    recovered.cloud.chat.assert_not_called()
                    state=await recovered.graph.graph.aget_state({'configurable':{'thread_id':'proc-2'}})
                    self.assertTrue(state.values)
                    self.assertIn('conversation',state.next)
                    with self.assertRaises(ValueError):
                        recovered.processes.start(2)
                    recovered.processes.reap(2,actor=1)
                    self.assertEqual(len(recovered.processes.table()),1)
            finally:
                recovered.store.db.close()
