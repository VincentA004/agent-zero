"""Opt-in live proof that a free cloud parent spawns and waits for a child."""
from __future__ import annotations
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import tempfile

from .config import Config
from .openrouter_setup import FREE_MODEL, load_key, verify_key
from .runtime import Runtime

PROMPT = (
    "This is an integration test of delegation. You MUST use sys_spawn to start exactly one child. "
    "Give that child the task: Compute 17 multiplied by 19 and return the number; do not delegate. "
    "Use sys_wait with timeout 30 on the returned PID to collect the child's completed result. "
    "If still running, wait again. Then give the operator a short final answer using the child's result. "
    "Do not answer directly without spawning and waiting. Do not request kernel authority or use files. "
    "Leave the child's record unreaped so this test can inspect it."
)


async def check(config):
    if not config.cloud_enabled or not config.cloud_free_only or config.local_enabled:
        raise ValueError("Configure free-only OpenRouter first; this check never enables paid inference")
    key = load_key(config)
    if not key:
        raise ValueError("Configure an OpenRouter API key locally first")
    await asyncio.to_thread(verify_key, key)
    # Isolated state and fresh graph: never sends existing user conversation history.
    with tempfile.TemporaryDirectory(prefix="agent-zero-live-check-") as temporary:
        root = Path(temporary)
        isolated = Config(root=root,cloud_enabled=True,cloud_free_only=True,model=FREE_MODEL,
                          session_budget_usd=0,max_output_tokens=512,max_agent_steps=8,
                          max_child_agents=1,max_agent_depth=1,max_agent_births=1)
        from .openrouter_setup import private_write
        private_write(root/'openrouter.key',key+'\n')
        key = ""
        runtime = Runtime(isolated)
        async with AsyncExitStack() as stack:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from .graph import TrapGraph
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(root/'graph.sqlite')))
            runtime.graph=TrapGraph(runtime,saver)
            try:
                async with asyncio.timeout(240):
                    response=await runtime.chat({'text':PROMPT})
                state=await runtime.graph.graph.aget_state({'configurable':{'thread_id':'main'}})
                calls=[call['function']['name'] for message in state.values.get('messages',[]) for call in message.get('tool_calls',[])]
                children=[p for p in runtime.processes.table() if p['pid']!=1]
                complete=(len(children)==1 and children[0]['ppid']==1 and children[0]['state']=='exited'
                          and '323' in children[0]['result'] and '323' in response.get('message','')
                          and 'sys_spawn' in calls and 'sys_wait' in calls and runtime.cloud.spent==0)
                report={'passed':complete,'model':runtime.model,'parent_pid':1,
                        'parent_tools':calls,'children':children,'parent_response':response,
                        'cost_usd':runtime.cloud.spent,'tokens':runtime.cloud.tokens}
                path=config.root/'delegation-check.json'
                private_write(path,json.dumps(report,indent=2)+'\n')
                print(json.dumps(report,indent=2))
                if not complete:
                    raise RuntimeError(f"Delegation check did not pass; inspect {path}")
                return report
            finally:
                await runtime.processes.interrupt_all()
                runtime.store.db.close()
