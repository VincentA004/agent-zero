"""Opt-in live free-only checks. Not discovered by unittest and never records audio."""
from __future__ import annotations
import argparse
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import tempfile
import time

from agent_zero.config import Config
from agent_zero.graph import TrapGraph
from agent_zero.openrouter_setup import FREE_MODEL, load_key, private_write, verify_key
from agent_zero.processes import CURRENT_PID
from agent_zero.runtime import Runtime

PARALLEL_PROMPT = (
    'Run a parallel delegation integration test. Your FIRST response MUST contain TWO sys_spawn tool calls '
    'in the SAME tool-call batch, each with capabilities: []. '
    'Name the first child multiply-a and task it to compute 17 times 19; return the number without delegating. '
    'Name the second multiply-b and task it to compute 29 times 31; return the number without delegating. '
    'Start BOTH before waiting. Use sys_wait(timeout=30) for BOTH children until they finish. '
    'Then report both numbers and their sum. Do not use files or request kernel authority. '
    'Leave records unreaped for the test harness to inspect.'
)


def waits_from(state):
    results=[]
    for message in state.values.get('messages',[]):
        if message.get('role')=='tool' and message.get('tool_name')=='sys_wait':
            value=json.loads(message['content']).get('result')
            if isinstance(value,dict) and value.get('state')=='exited':
                results.append(value)
    return results


async def run(config):
    if not config.cloud_enabled or not config.cloud_free_only or config.local_enabled or config.session_budget_usd != 0:
        raise ValueError('Live checks require enabled cloud, free-only mode, no local fallback and a $0 budget')
    key=load_key(config)
    if not key:
        raise ValueError('Configure the OpenRouter key locally first')
    await asyncio.to_thread(verify_key,key)
    report={'model':FREE_MODEL,'parallel':{},'controls':{},'passed':False}
    runtime=None
    with tempfile.TemporaryDirectory(prefix='agent-zero-live-runtime-') as directory:
        root=Path(directory)
        private_write(root/'openrouter.key',key+'\n')
        key=''
        runtime=Runtime(Config(root=root,cloud_enabled=True,cloud_free_only=True,model=FREE_MODEL,
                               session_budget_usd=0,max_output_tokens=512,max_agent_steps=8,
                               max_child_agents=2,max_agent_depth=1,max_agent_births=4,max_parallel_inference=2))
        original=runtime.cloud.chat
        spans=[]
        async def measured(*args):
            span={'pid':CURRENT_PID.get(),'start':time.monotonic(),'end':None}
            spans.append(span)
            try:
                return await original(*args)
            finally:
                span['end']=time.monotonic()
        runtime.cloud.chat=measured
        async with AsyncExitStack() as stack:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            saver=await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(root/'graph.sqlite')))
            runtime.graph=TrapGraph(runtime,saver)
            try:
                async with asyncio.timeout(240):
                    response=await runtime.chat({'text':PARALLEL_PROMPT})
                state=await runtime.graph.graph.aget_state({'configurable':{'thread_id':'main'}})
                waits={item['pid']:item for item in waits_from(state)}
                child_ids=sorted(pid for pid in runtime.processes.records if pid!=1)
                child_spans=[s for s in spans if s['pid'] in child_ids]
                overlap=any(a['pid']!=b['pid'] and max(a['start'],b['start']) < min(a['end'],b['end'])
                            for a in child_spans for b in child_spans)
                correct=(len(child_ids)==2 and set(child_ids)<=set(waits)
                         and sorted('323' if '323' in waits[pid]['result'] else '899' if '899' in waits[pid]['result'] else '' for pid in child_ids)==['323','899']
                         and '1222' in response.get('message','').replace(',','') and overlap)
                report['parallel']={'passed':correct,'children':list(waits.values()),'request_windows_overlap':overlap,
                                    'parent_response':response,'client_request_spans':spans.copy()}
                print(json.dumps({'parallel_passed':correct,'overlap':overlap,'parent':response}),flush=True)
                if not correct:
                    raise RuntimeError('Live parallel delegation did not pass')
                for action in ('stop_resume','cancel'):
                    arrived,release=asyncio.Event(),asyncio.Event()
                    async def controlled(*args):
                        value=await measured(*args)
                        arrived.set()
                        await release.wait()  # deterministic boundary before result publication
                        return value
                    runtime.cloud.chat=controlled
                    child=runtime.processes.create('Reply with READY only. Do not delegate or use tools.',capabilities=[])
                    pid=child['pid']
                    runtime.processes.start(pid,actor=1)
                    await asyncio.wait_for(arrived.wait(),100)
                    if action=='stop_resume':
                        await runtime.processes.signal(pid,'STOP',actor=1)
                        await runtime.processes.signal(pid,'STOP',actor=1)
                        release.set()
                        await asyncio.sleep(.03)
                        stopped=(runtime.processes.info(pid)['state']=='stopped' and not runtime.processes.done[pid].is_set())
                        await runtime.processes.signal(pid,'CONT',actor=1)
                        await asyncio.wait_for(runtime.processes.done[pid].wait(),10)
                        result=runtime.processes.info(pid)
                        passed=stopped and result['state']=='exited' and 'READY' in result['result']
                    else:
                        await runtime.processes.signal(pid,'TERM',actor=1)
                        release.set()
                        await asyncio.sleep(.03)
                        result=runtime.processes.info(pid)
                        passed=(result['state']=='cancelled' and result['result']==''
                                and all(m['role']!='assistant' for m in runtime.store.history(result['session'])))
                    calls=sum(s['pid']==pid for s in spans)
                    passed=passed and calls==1
                    report['controls'][action]={'passed':passed,'model_calls':calls,'process':result,
                                               'control_boundary':'after real provider response, before publishing'}
                    print(json.dumps({action:passed,'model_calls':calls,'state':result['state']}),flush=True)
                    if not passed:
                        raise RuntimeError(f'Live {action} check failed')
                report['passed']=runtime.cloud.spent==0
            except Exception as exc:
                report['error']=f'{type(exc).__name__}: {str(exc)[:300]}'
            finally:
                await runtime.processes.interrupt_all()
                report.update(cost_usd=runtime.cloud.spent,tokens=runtime.cloud.tokens)
                private_write(config.root/'runtime-checks.json',json.dumps(report,indent=2)+'\n')
                runtime.store.db.close()
    print(json.dumps({'passed':report['passed'],'cost_usd':report['cost_usd'],'tokens':report['tokens'],
                      'error':report.get('error'),'report':str(config.root/'runtime-checks.json')}),flush=True)
    if not report['passed']:
        raise RuntimeError('Live runtime checks did not pass; inspect the saved report')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(run(Config.load(args.home)))
