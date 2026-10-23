from __future__ import annotations

from typing import TypedDict


class TrapState(TypedDict, total=False):
    request: str
    approved: bool
    messages: list[dict]
    model: str
    record: dict
    response: str
    steps: int
    escalation: str


class TrapGraph:
    """Durable LangGraph interruption; the graph never receives kernel tokens."""

    def __init__(self, runtime, saver):
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
        self.runtime = runtime

        def gate(state: TrapState):
            result = interrupt({"request": state["request"], "authority": "human-kernel-session"})
            return {"approved": result is True}

        async def conversation(state: TrapState):
            from .tools import model_step
            message = await model_step(runtime, state["messages"], state["model"], state["record"])
            return {"messages": [*state["messages"], message],
                    "response": message.get("content", ""), "steps": state.get("steps", 0) + 1}

        async def tools(state: TrapState):
            from .tools import tool_step
            results, escalation = await tool_step(runtime, state["messages"][-1])
            return {"messages": [*state["messages"], *results], "escalation": escalation}

        def route_model(state):
            if state["messages"][-1].get("tool_calls"):
                return "tools"
            return END

        def route_tools(state):
            if state.get("escalation"):
                return END
            return "limit" if state["steps"] >= runtime.config.max_agent_steps else "conversation"

        def limit(state):
            return {"response": "I reached the tool-step limit. Please narrow the request before I continue."}

        builder = StateGraph(TrapState)
        builder.add_node("privilege_gate", gate)
        builder.add_node("conversation", conversation)
        builder.add_node("tools", tools)
        builder.add_node("limit", limit)
        builder.add_conditional_edges(START, lambda state: "privilege_gate" if state.get("request") else "conversation")
        builder.add_edge("privilege_gate", END)
        builder.add_conditional_edges("conversation", route_model)
        builder.add_conditional_edges("tools", route_tools)
        builder.add_edge("limit", END)
        self.graph = builder.compile(checkpointer=saver)
        self.saver = saver

    async def trap(self, session, request):
        await self.graph.ainvoke({"request": request}, {"configurable": {"thread_id": session}})
        trap_id = self.runtime.store.trap(session, request)
        await self.runtime.audio.transition("locked")
        self.runtime.store.audit("trap.pending", trap_id)
        return {"trap_id": trap_id, "status": "interrupted", "message": "LangGraph checkpoint saved. Kernel authentication required."}

    async def converse(self, session, model, messages, record):
        result = await self.graph.ainvoke({"request": "", "model": model, "messages": messages, "record": record, "steps": 0, "escalation": "", "response": ""}, {"configurable": {"thread_id": session}, "recursion_limit": self.runtime.config.max_agent_steps * 2 + 5})
        return {"response": result.get("response", ""), "escalation": result.get("escalation", "")}

    async def resume(self, session, approved):
        from langgraph.types import Command
        return await self.graph.ainvoke(Command(resume=approved), {"configurable": {"thread_id": session}})

    async def flush(self, session, scope):
        if self.runtime.store.pending():
            raise ValueError("Resolve pending traps before flushing graph state")
        if scope not in ("session", "ephemeral", "all"):
            raise ValueError("Invalid flush scope")
        if scope == "all":
            ids = {item.config["configurable"]["thread_id"] async for item in self.saver.alist(None)}
            for thread_id in ids:
                await self.saver.adelete_thread(thread_id)
        else:
            await self.saver.adelete_thread(session)
