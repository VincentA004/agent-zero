"""Unix-inspired agent processes. Logical PIDs, not host OS PIDs or raw fork()."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import copy
import json
import time

CURRENT_PID = ContextVar("agent_pid", default=1)
TERMINAL = {"exited", "failed", "cancelled", "interrupted", "reaped"}


class ProcessTable:
    def __init__(self, runtime):
        self.runtime = runtime
        self.db = runtime.store.db
        self.db.execute("CREATE TABLE IF NOT EXISTS agent_processes(pid INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)")
        self.records = {pid: json.loads(data) for pid, data in self.db.execute("SELECT pid,data FROM agent_processes")}
        self.tasks = {}
        self.gates = {}
        self.done = {}
        self.slots = asyncio.Semaphore(runtime.config.max_parallel_inference)
        self.births = 0
        self.ready = asyncio.Event()
        if runtime.audio.mode == "userspace":
            self.ready.set()
        # Do not silently replay model calls, writes or paid work after a crash.
        for process in self.records.values():
            if process["pid"] != 1 and process["state"] not in TERMINAL:
                process.update(state="interrupted", error="Daemon restarted; execution was not replayed", exit_code=130)
                self.save(process)
        for process in self.records.values():
            if process["pid"] != 1 and (process["ppid"] not in self.records or self.records[process["ppid"]]["state"] in TERMINAL):
                process["ppid"] = 1
                self.save(process)
        root = self.records.get(1, {})
        root.update(pid=1, ppid=0, pgid=1, name="JARVIS", state="idle", task="Operator conversation",
                    model=runtime.model, session="main", depth=0, capabilities=[], created=root.get("created", time.time()),
                    result="", error="", exit_code=None)
        self.records[1] = root
        self.save(root)

    def save(self, process):
        process["updated"] = time.time()
        self.db.execute("INSERT OR REPLACE INTO agent_processes(pid,data) VALUES(?,?)", (process["pid"], json.dumps(process)))
        self.db.commit()

    def get(self, pid):
        if type(pid) is not int or pid < 1 or pid not in self.records or self.records[pid]["state"] == "reaped":
            raise ValueError("ESRCH: agent PID does not exist")
        return self.records[pid]

    def effective_caps(self, pid=None):
        pid = CURRENT_PID.get() if pid is None else pid
        process = self.get(pid)
        allowed = {name for name, enabled in self.runtime.permissions.items() if enabled}
        while process["pid"] != 1:
            allowed &= set(process["capabilities"])
            process = self.get(process["ppid"])
        return allowed

    def visible(self, actor, pid):
        if actor == 0:  # authenticated kernel
            return True
        process = self.get(pid)
        while True:
            if process["pid"] == actor:
                return True
            if process["ppid"] == 0:
                return False
            process = self.get(process["ppid"])

    def info(self, pid, actor=0):
        if not self.visible(actor, pid):
            raise PermissionError("EPERM: process is outside your subtree")
        process = self.get(pid)
        kernel_paused = not self.ready.is_set() and process["state"] in {"running", "runnable", "waiting"}
        return {**{k: copy.deepcopy(v) for k, v in process.items() if k != "snapshot"},
                "state": "suspended" if kernel_paused else process["state"], "kernel_paused": kernel_paused,
                "effective_capabilities": sorted(self.effective_caps(pid)),
                "engine": "local_fallback" if self.runtime.config.local_enabled else "cloud",
                "budget_owner_pid": 1}

    def table(self, actor=0):
        return [self.info(pid, actor) for pid in sorted(self.records)
                if self.records[pid]["state"] != "reaped" and self.visible(actor, pid)]

    def tree(self, actor=0):
        records = self.table(actor)
        indexed = {p["pid"]: {**p, "children": []} for p in records}
        roots = []
        for process in indexed.values():
            if process["ppid"] in indexed:
                indexed[process["ppid"]]["children"].append(process)
            else:
                roots.append(process)
        return roots

    def create(self, task, parent=1, name="", capabilities=None, snapshot=None, copy_memory=False):
        source = self.get(parent)
        if parent != 1 and source["state"] in TERMINAL:
            raise ValueError("ECHILD: parent is no longer alive")
        available = self.effective_caps(parent)
        if "agent_spawn" not in available:
            raise PermissionError("EPERM: agent_spawn capability is disabled")
        if not isinstance(task, str) or not task.strip() or len(task) > 8000:
            raise ValueError("Task must contain 1..8000 characters")
        if not isinstance(name, str) or len(name) > 60:
            raise ValueError("Process name must be at most 60 characters")
        if capabilities is not None and (not isinstance(capabilities, list) or any(not isinstance(c, str) for c in capabilities)):
            raise ValueError("Capabilities must be a list of names")
        requested = set(capabilities) if capabilities is not None else available
        if not requested <= available:
            raise PermissionError("EPERM: a child cannot gain capabilities its parent lacks")
        if source["depth"] >= self.runtime.config.max_agent_depth:
            raise ValueError("EAGAIN: maximum agent depth reached")
        live = [p for p in self.records.values() if p["pid"] != 1 and p["state"] not in TERMINAL]
        if len(live) >= self.runtime.config.max_child_agents or self.births >= self.runtime.config.max_agent_births:
            raise ValueError("EAGAIN: agent creation limit reached")
        if sum(p["state"] != "reaped" for p in self.records.values()) >= 64:
            raise ValueError("EAGAIN: reap finished processes before creating more")
        cursor = self.db.execute("INSERT INTO agent_processes(data) VALUES('{}')")
        pid = cursor.lastrowid
        process = dict(pid=pid, ppid=parent, pgid=source["pgid"], name=name or f"jarvis-{pid}", state="created",
                       task=task, model=self.runtime.model if parent == 1 else source["model"], session=f"proc-{pid}",
                       depth=source["depth"] + 1, capabilities=sorted(requested), created=time.time(),
                       result="", error="", exit_code=None, snapshot=copy.deepcopy(snapshot or []))
        self.records[pid] = process
        self.gates[pid] = asyncio.Event()
        self.gates[pid].set()
        self.done[pid] = asyncio.Event()
        self.births += 1
        self.save(process)
        self.runtime.memory.create_private(pid, copy_from=parent if copy_memory else None)
        self.runtime.store.audit("process.created", f"pid={pid} ppid={parent}")
        self.runtime.trace.event(pid, 'process.created')
        return self.info(pid)

    def start(self, pid, actor=0, workflow="task"):
        process = self.get(pid)
        if not self.visible(actor, pid):
            raise PermissionError("EPERM: process is outside your subtree")
        if workflow != "task":
            raise ValueError("ENOEXEC: only the registered task workflow is available")
        if process["state"] != "created":
            raise ValueError("EBUSY: exec requires a newly created process")
        process["state"] = "runnable"
        self.save(process)
        self.tasks[pid] = asyncio.create_task(self._run(pid), name=f"jarvis-pid-{pid}")
        self.tasks[pid].add_done_callback(lambda task: self._settle_unstarted(pid, task))
        return self.info(pid, actor)

    def _settle_unstarted(self, pid, task):
        if pid in self.done and not self.done[pid].is_set():
            process = self.records[pid]
            process.update(state="cancelled", error="Cancelled before execution", exit_code=143)
            self.save(process)
            self.done[pid].set()
            self.runtime.trace.event(pid, 'process.cancelled')
            self.tasks.pop(pid, None)
            for child in self.records.values():
                if child["ppid"] == pid and child["state"] != "reaped":
                    child["ppid"] = 1
                    self.save(child)

    async def checkpoint(self):
        pid = CURRENT_PID.get()
        while True:
            await self.ready.wait()
            if pid != 1:
                await self.gates[pid].wait()
            if self.ready.is_set() and self.runtime.audio.mode == "userspace":
                break
        if self.get(pid)["state"] in TERMINAL:
            raise PermissionError("Agent process has terminated")

    async def _run(self, pid):
        with self.runtime.trace.span(pid, 'process', 'process.run') as span:
            await self._execute(pid)
            state = self.records[pid]['state']
            span['status'] = {'failed': 'failed', 'cancelled': 'cancelled', 'waiting_review': 'paused'}.get(state, 'ok')

    async def _execute(self, pid):
        process = self.get(pid)
        context = CURRENT_PID.set(pid)
        try:
            await self.checkpoint()
            process["state"] = "running"
            self.save(process)
            messages = [{"role": "system", "content": self.runtime.agent_prompt()}, *process.pop("snapshot", []),
                        {"role": "user", "content": process["task"]}]
            self.runtime.store.message(process["session"], "user", process["task"])
            record = next(m for m in self.runtime.registry["cloud"] if m["id"] == process["model"])
            from .tools import run_loop
            operation = self.runtime.graph.converse(process["session"], process["model"], messages, record) if self.runtime.graph else run_loop(self.runtime, messages, process["model"], record)
            result = await operation
            await self.checkpoint()
            if result.get("escalation"):
                process.update(state="waiting_review", result="Human kernel review required")
                self.save(process)
                async with self.runtime.lock:
                    await self.runtime.create_trap(process["session"], result["escalation"])
            else:
                process.update(state="exited", result=result.get("response", ""), exit_code=0)
                self.runtime.store.message(process["session"], "assistant", process["result"])
        except asyncio.CancelledError:
            process.update(state="cancelled", error="Execution cancelled", exit_code=143)
        except Exception as exc:
            process.update(state="failed", error=str(exc)[:300], exit_code=1)
        finally:
            # Adopt unfinished descendants so no process loses its supervisor.
            for child in self.records.values():
                if child["ppid"] == pid and child["state"] != "reaped":
                    child["ppid"] = 1
                    self.save(child)
            CURRENT_PID.reset(context)
            self.save(process)
            self.runtime.scheduler.reconcile()
            self.done[pid].set()
            self.tasks.pop(pid, None)
            self.runtime.store.audit("process.finished", f"pid={pid} state={process['state']}")

    async def wait(self, pid, actor=0, timeout=0):
        process = self.get(pid)
        if not self.visible(actor, pid) or (actor and pid == actor):
            raise PermissionError("ECHILD: can only wait for descendants")
        if type(timeout) not in (int, float) or not 0 <= timeout <= 30:
            raise ValueError("Wait timeout must be 0..30 seconds")
        if process["state"] not in TERMINAL and process["state"] != "waiting_review" and timeout:
            previous = None
            if actor:
                previous = self.get(actor)["state"]
                self.get(actor)["state"] = "waiting"
                self.save(self.get(actor))
            try:
                await asyncio.wait_for(self.done[pid].wait(), timeout)
            except TimeoutError:
                pass
            finally:
                if actor:
                    caller = self.get(actor)
                    if caller["state"] == "waiting":
                        caller["state"] = previous
                        self.save(caller)
                    elif caller["state"] == "stopped" and caller.get("resume_state") == "waiting":
                        caller["resume_state"] = previous
                        self.save(caller)
        return self.info(pid, actor)

    async def signal(self, pid, signal, actor=0):
        process = self.get(pid)
        if not self.visible(actor, pid):
            raise PermissionError("EPERM: process is outside your subtree")
        if signal not in ("TERM", "KILL", "STOP", "CONT"):
            raise ValueError("Signal must be TERM, KILL, STOP or CONT")
        if pid == 1:
            if signal not in ("TERM", "KILL") or actor != 0:
                raise PermissionError("EPERM: PID 1 is protected; kernel may cancel its active turn")
            async with self.runtime.lock:
                await self.runtime.suspend_userspace(self.runtime.audio.mode, terminate_children=True)
            return self.info(1)
        if actor and actor == pid:
            raise PermissionError("EPERM: use sys_exit to terminate yourself")
        if process["state"] in TERMINAL:
            return self.info(pid, actor)
        targets = [self.records[p["pid"]] for p in self.table() if self.visible(pid, p["pid"])]
        if signal in ("TERM", "KILL"):
            tasks = []
            for child in targets:
                if child["state"] in TERMINAL:
                    continue
                task = self.tasks.get(child["pid"])
                if task:
                    task.cancel()
                    tasks.append(task)
                else:
                    child.update(state="cancelled", exit_code=143, error="Cancelled before exec")
                    self.save(child)
                    self.done[child["pid"]].set()
                    for orphan in self.records.values():
                        if orphan["ppid"] == child["pid"] and orphan["state"] != "reaped":
                            orphan["ppid"] = 1
                            self.save(orphan)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        else:
            for child in targets:
                if child["state"] in TERMINAL:
                    continue
                if signal == "STOP" and child["state"] != "stopped":
                    child["resume_state"] = child["state"]
                    self.gates[child["pid"]].clear()
                    child["state"] = "stopped"
                elif signal == "CONT" and child["state"] == "stopped":
                    self.gates[child["pid"]].set()
                    child["state"] = child.pop("resume_state", "runnable")
                self.save(child)
        self.runtime.store.audit("process.signal", f"pid={pid} signal={signal}")
        return self.info(pid, 0)

    def reap(self, pid, actor=0):
        process = self.get(pid)
        if pid == 1 or (actor and process["ppid"] != actor):
            raise PermissionError("ECHILD: only a parent or the kernel may reap a child")
        if process["state"] not in TERMINAL:
            raise ValueError("EBUSY: process has not exited")
        result = self.info(pid, actor)
        process.update(state="reaped", result="", snapshot=[])
        self.save(process)
        self.done.pop(pid, None)
        self.gates.pop(pid, None)
        self.runtime.memory.reap(pid)
        self.runtime.mailboxes.reap(pid)
        self.runtime.services.reap(pid)
        self.runtime.trace.event(pid, 'process.reaped')
        return result

    async def flush_memory(self, session, scope):
        # Called under kernel authority. Stop tasks before deleting checkpoints so
        # cancelled graph nodes cannot write erased state back into the saver.
        selected = [p for p in self.records.values() if p["pid"] != 1 and p["state"] != "reaped"
                    and (scope == "all" or p["session"] == session)]
        for process in selected:
            if process["state"] not in TERMINAL:
                await self.signal(process["pid"], "TERM", actor=0)
        for process in self.records.values():
            if process["pid"] == 1 or (scope != "all" and process["session"] != session):
                continue
            process.update(task="[cleared]", result="", error="", snapshot=[])
            self.save(process)

    async def interrupt_all(self):
        self.ready.clear()
        current = asyncio.current_task()
        tasks = [task for task in self.tasks.values() if task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def syscall(self, method, params, actor=0):
        if method not in PROCESS_SYSCALLS:
            raise PermissionError("EPERM: syscall is not available to agent processes")
        if method == "sys_getpid":
            return {"pid": actor or 1}
        if method == "sys_getppid":
            return {"ppid": self.get(actor or 1)["ppid"]}
        if method in ("sys_spawn", "sys_fork"):
            if actor and self.runtime.audio.mode != "userspace":
                raise PermissionError("EAGAIN: userspace is suspended")
            parent = params.get("ppid", 1) if actor == 0 else actor
            snapshot = self.runtime.context_history(self.get(parent)["session"]) if method == "sys_fork" else None
            child = self.create(params.get("task", ""), parent, params.get("name", ""), params.get("capabilities"), snapshot,
                                copy_memory=method == "sys_fork")
            return self.start(child["pid"], actor) if method == "sys_spawn" else child
        if method == "sys_exec":
            return self.start(params.get("pid"), actor, params.get("workflow", "task"))
        if method == "sys_proc_table":
            return self.table(actor)
        if method == "sys_proc_tree":
            return self.tree(actor)
        if method == "sys_proc_info":
            return self.info(params.get("pid"), actor)
        if method == "sys_wait":
            return await self.wait(params.get("pid"), actor, params.get("timeout", 0))
        if method == "sys_signal":
            return await self.signal(params.get("pid"), params.get("signal", "TERM"), actor)
        if method == "sys_reap":
            return self.reap(params.get("pid"), actor)
        raise ValueError("Unknown process syscall")


PROCESS_SYSCALLS = {"sys_getpid", "sys_getppid", "sys_spawn", "sys_fork", "sys_exec", "sys_proc_table", "sys_proc_tree", "sys_proc_info", "sys_wait", "sys_signal", "sys_reap"}
