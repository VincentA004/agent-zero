# Agent processes and syscall authority

Agent Zero uses Unix-inspired **logical agent processes** supervised by Python asyncio. These PIDs are not host OS PIDs. There is no raw `os.fork()`, shell `exec`, QEMU VM, or duplicated local model per clone. The separate macOS file worker remains the OS sandbox boundary for file tools.

The process hierarchy borrows parent/child identities and inheritance from [POSIX fork](https://pubs.opengroup.org/onlinepubs/9799919799/functions/fork.html). Each agent uses a distinct SQLite-backed LangGraph thread, following [LangGraph checkpoint thread identity](https://langchain-ai.github.io/langgraph/reference/checkpoints/).

## Authority domains

| Domain | Calls | Who can invoke |
| --- | --- | --- |
| Ring 0 administration | `sys_model_swap`, `sys_perm_toggle`, `sys_mount_sandbox`, `sys_mem_flush`, `sys_resolve`, `sys_trap_table`, `sys_parse` | Authenticated operator only |
| Authentication | `sys_auth`, explicit `voice_auth`, `sys_exit` (drop operator authority) | Human credential flow; never model tools |
| Agent lifecycle | `sys_getpid`, `sys_getppid`, `sys_spawn`, `sys_fork`, `sys_exec`, `sys_proc_tree`, `sys_proc_info`, `sys_wait`, `sys_signal`, `sys_reap` | Model tools restricted to the caller's subtree; authenticated operator can manage the whole tree |
| Process listing | `sys_proc_table` | Authenticated operator; agent tree tool exposes only its subtree |
| Review request | `request_kernel` tool / `sys_call` transport | May request human review, never grant authority or execute an admin action |

The model tool adapter has a fixed allowlist and calls a separate process dispatcher with a supervisor-owned ContextVar identity. It never forwards arbitrary model-generated method names to the administrative dispatcher. Models cannot supply caller PIDs, operator tokens, alternate models, or privileged capabilities. Knowledge of a syscall name does not grant access. Local Qwen interprets Cardinal intent as a proposal; Python validates authority and dispatches explicit commands. Being visually in the kernel UI alone does not grant authority: an unexpired token is required server-side.

## Process semantics

- **PID 1 / JARVIS** is the persistent parent conversational service. Children inherit its selected cloud model and share the same OpenRouter gateway and spending ceiling. Existing children keep their model when the operator changes the parent's selection.
- Cloud is the intended userspace engine. Explicit `local_enabled: true` is a local fallback/testing mode. Qwen remains available independently for Cardinal parsing. No inference route is silently enabled and no cloud key/billing changes occur through model tools.
- `sys_spawn(task, name?, capabilities?)` creates and starts a child. `sys_fork(task, name?)` copies the parent's persisted conversation into a new **created** child. It copies no live provider request, file descriptors, authentication state, or mutable graph checkpoint. `sys_exec(pid)` starts only the registered `task` workflow; arbitrary executables are rejected.
- Child sessions are `proc-PID`, with their own history and LangGraph checkpoints. PIDs increase and are not reused after reaping. Model identity is inherited; there is no model argument to spawn/fork/exec.
- Child capabilities can only narrow the parent's effective capabilities. Every tool intersects them with current operator permissions and ancestor restrictions. Children do not inherit auth tokens. `agent_spawn` can be disabled only by the authenticated operator.
- Defaults: four live children, maximum depth two, twelve child creations per daemon lifetime, and two concurrent inference requests across parent and children. Waits do not hold inference slots. Tool-step limits still apply independently. Unreaped process count is capped at 64.
- `sys_wait(pid, timeout=0)` polls or waits up to 30 seconds and returns status/result. Agent callers may wait only within their subtree and never on themselves or ancestors. It does not reap automatically.
- `sys_signal(pid, TERM|KILL)` cancels a child subtree. KILL currently uses the same supervised task cancellation as TERM; neither kills an arbitrary host PID. Local HTTP connections close; a previously issued blocking cloud request may still finish and incur charges, whose reservation remains counted.
- STOP/CONT are cooperative at model/tool boundaries; duplicate STOP signals are idempotent. An in-flight provider request may finish while stopped, but no subsequent model/tool step proceeds before CONT. This is not host SIGSTOP/SIGCONT.
- A finished parent's unreaped children are adopted by PID 1. `sys_reap` releases terminal metadata/results from the live table; only the direct parent or authenticated operator may reap. Completed results remain available until reaped; cancelling a subtree preserves already finished descendants' exit status and results. Audit and conversation history have separate retention/flush behavior.
- Parent JARVIS cannot be reaped or terminated by a model. The authenticated operator may cancel its current turn; PID 1 remains the service for the next conversation.
- Entering kernel mode interrupts the active parent voice turn and suspends children at model/tool boundaries. In-flight child provider requests may complete, but no tools execute or results publish until userspace resumes. Stopping voice interaction alone leaves background children running. Operator-created tasks while in kernel mode queue until authority is dropped. Review approval never replays privileged actions; the operator changes policy explicitly, then starts fresh work if needed.
- On restart, unfinished children become **interrupted**. Their records/checkpoints remain inspectable, but no paid request or file write is automatically replayed.

## Cardinal commands

`ps`, `tree`, `info PID`, `spawn TASK`, `fork TASK`, `exec PID`, `wait PID`, `signal PID TERM|KILL|STOP|CONT`, and `reap PID` work in both consoles after authentication. Spawned/exec'd tasks remain queued until `exit` drops kernel authority. Browser process controls also offer exec, stop/continue, cancellation and reaping. The trap queue is now `sys_trap_table`; `sys_proc_table` lists actual agent process records.

`sys_mem_flush all` also cancels pending/running agent work and clears process task text, fork snapshots and cached results before removing graph checkpoints. Session/ephemeral flush clears only the selected session/process; copies held by independent children require `all`. Pending human review requests must be resolved before flushing.
