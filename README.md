# Agent Zero

I wanted to design my own agent runtime around ideas from operating systems: processes with identities, inherited capabilities, a supervisor that owns policy, and a small syscall interface between an agent and the resources it can use.

That led to two privilege rings. **JARVIS runs in Ring 3**, where it can converse, use permitted tools, and delegate tasks to child agents. **Cardinal is the Ring 0 control interface**, where I authenticate and change the runtime's policy. The model can request more authority, but the Python supervisor decides which calls are valid. A prompt cannot promote an agent into the kernel.

The interface follows the same split. JARVIS is a floating, voice-reactive orb. Asking to enter kernel mode lowers an authentication drawer over it; after verification, that same Electron window becomes Cardinal's green terminal console. Leaving Cardinal returns to the orb.

These rings are application authority domains. Agent Zero does not enter the host operating system's kernel, run a QEMU guest, or expose raw host process control.

## The runtime

Most of the system is Python. An asyncio supervisor owns authentication, process records, capabilities, provider access, storage, audio routing, and syscall dispatch. LangGraph provides the agent execution graph and durable interrupts; it does not decide who has authority. Electron hosts the window and microphone permission flow, with plain HTML, CSS, and JavaScript handling the orb and terminal visuals.

```text
                 Electron window
              JARVIS orb / Cardinal
                        |
       +----------------+----------------+
       |                                 |
  Ring 3: agents                  Ring 0: operator
  JARVIS (PID 1)                 Authenticated commands
       |                                 |
  Child agents                   Administrative dispatcher
       |                                 |
  Restricted tool / syscall adapter       |
       |                                 |
       +----------------+----------------+
                        |
                Python supervisor
                        |
       +----------------+----------------+
       |                |                |
  Shared inference  Sandboxed file    SQLite state
      gateway          worker        and checkpoints
```

There is one daemon per state directory. The desktop and optional Textual console connect to that daemon rather than creating independent runtimes. Local HTTP binds to `127.0.0.1`; the Unix socket is restricted to the current OS user.

## Two rings, different authority

| Domain | Principal | Authority |
| --- | --- | --- |
| Ring 3 / userspace | Parent JARVIS and child agents | Read permitted files, write when enabled, create bounded children, inspect their subtree, collect results, and request review |
| Ring 0 / supervision | Authenticated human operator | Select models, change capabilities, mount workspace directories, clear memory, inspect the full process table, and resolve reviews |

Cardinal's local Qwen model can translate natural language into a proposed syscall. It does not authenticate the operator or execute that proposal automatically. Python verifies an explicit command against the operator's token and the current policy.

Agent identity comes from a supervisor-owned context variable. The model cannot choose its caller PID, supply an operator token, or attach a more privileged identity to a tool call. The model tool adapter has a fixed allowlist and a separate process dispatcher; it never forwards arbitrary model-generated names into the administrative dispatcher. Knowing that `sys_model_swap` exists does not make it callable from JARVIS.

Operator tokens are held in UI memory, expire after five minutes, and are checked by the backend. A new authentication invalidates the previous operator token. Passphrases are stored as salted scrypt hashes and stay outside agent prompts, conversation history, and graph state.

## Agents as processes

Parent JARVIS is the persistent **PID 1** service. Each child has a PID, parent PID, task, execution state, inherited model, capability set, and separate conversation history and LangGraph checkpoints.

The process model borrows Unix lifecycle semantics:

| Call | Meaning in Agent Zero |
| --- | --- |
| `sys_getpid`, `sys_getppid` | Read the supervisor-assigned identity |
| `sys_spawn` | Create and start a child with a task |
| `sys_fork` | Create an inactive child with a snapshot of the parent's persisted conversation |
| `sys_exec` | Start a created child's registered task workflow |
| `sys_proc_tree`, `sys_proc_info` | Inspect processes within the caller's subtree |
| `sys_wait` | Poll or wait for a result, without reaping the process |
| `sys_signal` | Apply `STOP`, `CONT`, `TERM`, or `KILL` to a permitted child subtree |
| `sys_reap` | Release a terminated direct child's live process record and result |

For example, a parent can delegate a calculation and use the child's result in its next turn:

```text
PID 1 / JARVIS
  sys_spawn(task="Calculate 17 × 19", name="math-child") → PID 2

PID 2 / math-child
  model → tool loop → result: 323 → exited

PID 1 / JARVIS
  sys_wait(pid=2, timeout=30) → completed result: 323
  incorporate result into the spoken response
  sys_reap(pid=2)
```

These are logical asyncio processes. Fork copies conversation context rather than a running interpreter, provider request, authentication session, or set of file descriptors. Exec starts the registered agent workflow rather than an arbitrary host executable.

Children inherit their parent's selected model and can only narrow its capabilities. Every tool call intersects that inherited set with current operator permissions and ancestor restrictions, so a later policy change applies to existing descendants. A model swap changes the parent's selection; existing children retain theirs.

Defaults allow four live children, depth two, twelve child creations per daemon lifetime, and two concurrent inference requests shared across the tree. A wait does not occupy an inference slot. Finished parents' children are adopted by PID 1, and process IDs are not reused after reaping.

`STOP` and `CONT` are cooperative at model/tool boundaries. `TERM` cancels a subtree; `KILL` currently uses the same supervised cancellation. Neither signal can kill an arbitrary host PID. An in-flight cloud request may still finish remotely after local cancellation, but its late response is not published. On restart, unfinished children become interrupted rather than automatically replaying model requests or file writes.

## The syscall boundary

A syscall is a validated request to the supervisor. File operations appear to models as `list_files`, `read_file`, and `write_file`; process operations use the `sys_*` names above. Administrative calls are available only through the authenticated operator path.

| Ring 0 call | Responsibility |
| --- | --- |
| `sys_model_swap` | Select a registered cloud model, subject to billing policy |
| `sys_perm_toggle` | Change a runtime capability |
| `sys_mount_sandbox` | Mount a directory as read-only or read-write within the file boundary |
| `sys_mem_create`, `sys_mem_drop` | Create or destroy a shared memory region |
| `sys_mem_grant` | Grant an exact PID read-only/read-write access, or revoke it |
| `sys_mem_spaces`, `sys_mem_command` | Inspect namespaces and issue Cardinal memory commands |
| `sys_mem_flush` | Clear selected conversation state, virtual pages and graph checkpoints |
| `sys_proc_table` | Inspect the complete logical process table |
| `sys_trap_table`, `sys_resolve` | Inspect and resolve pending human reviews |
| `sys_parse` | Obtain a local-model command proposal without executing it |

The operator can also manage processes across the full tree. Agents remain limited to the process relationships enforced by the supervisor. `sys_auth` and explicit `voice_auth` belong to the human credential flow; `sys_exit` drops operator authority.

When an agent needs a policy change, it uses `request_kernel`. The transport also exposes `sys_call` to request review. That creates a **trap**:

1. The supervisor persists the request, interrupts the active parent turn, and suspends userspace work at execution boundaries.
2. The authentication drawer opens. Typed or explicitly captured offline speech goes directly to the credential verifier.
3. After authentication, Cardinal replaces the orb and exposes the request alongside process state, capabilities, and the event log.
4. The operator reviews the request and issues any policy change explicitly. Approving a review records a decision; it does not automatically execute the requested privileged action.
5. `exit` revokes authority and returns to userspace when no pending traps remain. Operator-created agent tasks queue until userspace resumes.

With the `agent` extra enabled, traps use LangGraph `interrupt()` and `Command(resume=...)` with SQLite checkpoints. The core preview has a persisted review queue. A state directory containing pending graph checkpoints must continue to use the graph-enabled daemon.

Cardinal provides direct commands such as:

```text
ps
tree
info 2
signal 2 STOP
signal 2 CONT
allow file_write
deny file_write
model nvidia/nemotron-3-ultra-550b-a55b:free
flush session
exit
```

`flush all` also cancels affected work and clears fork snapshots and cached child results before deleting checkpoints. Flushing one session does not erase independent copies held by its children. Pending reviews must be resolved before memory can be flushed.

The full authority and lifecycle specification is in [the process model](docs/process-model.md).

## Virtual memory

Each agent has a persistent private address space backed by SQLite. Pages hold facts, notes or task state that can outlive a conversation turn without occupying the model's active context. Spawn starts with empty private memory; fork copies the parent's private pages into an independent namespace. Even a parent cannot directly read its child's private pages through agent tools.

This follows the distinction between an address space and a working set: an agent can retain more information than it carries into any one model call. A page miss loads the requested data from backing storage, and eviction frees context space without deleting the stored page. The supervisor owns the mapping and access checks.

Agents use `sys_mem_write`, `sys_mem_read`, `sys_mem_list`, `sys_mem_search`, `sys_mem_delete`, and `sys_mem_stat`. Reading a page loads it into an LRU working set, bounded to 8 KiB of serialized context per PID. Eviction removes the resident copy while preserving backing data. Page bodies enter provider requests through a temporary, explicitly untrusted memory message; read results store addresses rather than duplicating bodies into graph checkpoints.

Shared memory is a kernel resource. Cardinal creates named regions and grants exact PIDs read-only or read/write access. Grants never inherit on spawn or fork, and every access checks current policy. For example:

```text
mem create project
mem write shared:project plan "Review the release checklist"
mem grant project 1 r
exit
```

JARVIS can then read `shared:project`; another agent needs its own grant. `mem revoke project 1` blocks subsequent access and evicts its cached pages. It cannot retract information a model already received or copied into output. These bounds apply to the memory working set, not the whole model prompt or host RAM.

After authenticating in Cardinal, inspect or change memory with:

```text
mem spaces
mem stats 1
mem list private:1
mem grant project 2 rw
mem read private:2 notes
mem revoke project 2
mem drop project
```

Each namespace holds up to 128 pages of at most 4 KiB each, with up to 32 shared regions. Pages and grants survive daemon restarts; resident caches start empty. Reaping a process removes its private pages and shared grants. A session flush clears its private pages and working set; `flush all` clears all page bodies while preserving shared-region definitions and grant policy.

See [virtual memory](docs/virtual-memory.md) for quotas, address syntax, paging, persistence, cleanup and the full command interface.

## Models and resource accounting

The intended split is **cloud inference for parent JARVIS and children**, with **local Qwen for Cardinal command proposals**. Local userspace conversation is an explicit fallback enabled by `local_enabled: true`; it takes priority over cloud conversation when enabled.

The registry's default cloud route is `nvidia/nemotron-3-ultra-550b-a55b:free`. Parent and children share one gateway, concurrency limit, token accounting, and budget. The example configuration uses `cloud_free_only: true` and a $0 budget: paid models are rejected before networking, provider price ceilings are zero, and there is no paid fallback. Unexpected reported nonzero billing blocks subsequent requests. Free-provider capacity and quotas can still prevent a request from succeeding.

The local registry includes Qwen3.5 4B and 9B. The portable default is `qwen3.5:4b`; the development Mac uses its already installed `qwen3.5:9b`. Local inference uses a 4,096-token context and checks reported resident memory and available host RAM against an 8–16 GB target. Those checks are admission guards, not OS-enforced allocation limits. Language models are never downloaded automatically.

Model records include sources, roles, pricing, and research dates. See [model research](docs/model-research.md) and [budget cloud research](docs/budget-cloud-research.md) for the selection history. The free NVIDIA endpoint has provider logging terms; inspect the registry notice before sending sensitive content. Paid-mode budgets, if enabled later, apply per daemon lifetime rather than acting as a persistent account spending limit.

## Voice and mode transitions

The main interface has no text chat or transcript. Enable wake monitoring once per app session, grant microphone permission, and say **“Hey Jarvis.”** You can also click the orb or hold Space to speak. Replies use an installed local English system voice.

“Hey Jarvis, enter kernel mode” is recognized locally as an explicit mode request and opens the authentication drawer. No conversation model receives the passphrase. On first use, the drawer can create and confirm a passphrase of at least 12 characters; later it offers typed or explicitly initiated spoken authentication. Spoken verification uses a separate offline endpoint and must match the configured phrase exactly.

Wake detection currently transcribes short clips with local faster-whisper rather than a dedicated acoustic wake model. Ambient nonmatches never enter conversation history or the cloud gateway. Temporary recordings are deleted after transcription. After a reply, the app listens for a follow-up; six seconds without speech ends the active conversation. “Go to sleep” returns to dormant monitoring if enabled; “stop listening,” Stop, and Escape disable it. Losing focus or opening an administrative dialog releases the microphone.

The current pipeline is clip-based. Streaming model-to-audio output, simultaneous spoken interruption, and dedicated Piper/Kokoro synthesis remain future work. Physical microphone quality and noisy-room wake accuracy require hardware testing.

## Running it

Use Python 3.11+ and Node.js 22.12+. macOS is the primary desktop target; the daemon and browser interface also support Linux, with a different file-isolation boundary.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[agent,tui,voice]'
npm ci
mkdir -p .agent-zero
cp config.example.json .agent-zero/config.json
.venv/bin/python -m agent_zero --home .agent-zero desktop
```

Copy the example configuration only for a new runtime directory. The initial configuration has cloud conversation disabled. To enable the free-only OpenRouter route, enter your key through the hidden terminal prompt, then restart the daemon:

```sh
.venv/bin/python -m agent_zero --home .agent-zero configure-openrouter
```

The setup command validates the key without inference and saves it privately outside Git. A saved key takes precedence over `OPENROUTER_API_KEY`. For voice input, set `stt_model_path` in `.agent-zero/config.json` to an installed faster-whisper directory containing `model.bin`, `config.json`, and `tokenizer.json`. The app does not download speech models at startup. Cardinal's natural-language parser separately requires Ollama and the configured Qwen model.

The desktop command starts or reuses the Python daemon. To run it in the foreground instead:

```sh
.venv/bin/python -m agent_zero --home .agent-zero serve --graph
```

The browser interface is available at `http://127.0.0.1:8420`. An optional native terminal client connects to the same daemon:

```sh
.venv/bin/python -m agent_zero --home .agent-zero tui
```

In Electron, click the Cardinal control or press Cmd+K / Ctrl+K to open the authentication drawer. Type `exit` in Cardinal to return to JARVIS. The terminal client uses Ctrl+L to drop authority and Ctrl+Q to close. A terminal can also set or replace the passphrase using `init`.

Build a macOS app bundle with:

```sh
.venv/bin/python -m agent_zero --home .agent-zero build-app --output 'dist/Agent Zero.app'
```

The destination must not exist. The bundle embeds Electron and is locally ad-hoc signed, but still uses this checkout and its Python virtual environment; it is not a standalone notarized installer.

State lives in the selected home directory: SQLite history, audit records and checkpoints, hashed credentials, private provider credentials, and the sandbox workspace. The default is `~/.agent-zero`; `--home` or `AGENT_ZERO_HOME` selects another directory. Local state, model files, API keys, and generated app bundles are ignored by Git.

## Where the boundary holds

On macOS, file tools execute in a separate short-lived Python worker under a deny-by-default OS sandbox. Paths are resolved relative to permitted directories, traversal and symlinks are rejected, mounts enforce read-only or read-write access, and file size and type are checked. The worker cannot access adjacent runtime credentials or open network connections. Other platforms retain descriptor-based file confinement without this macOS worker policy. The daemon and Ollama are outside the worker sandbox.

There is no arbitrary shell, code-execution, or network tool adapter, and those capabilities cannot be enabled by a model. Loopback HTTP mutations require the exact Host and Origin, a custom header, and an HttpOnly SameSite session cookie. Electron disables renderer Node integration, enables context isolation and sandboxing, restricts navigation to the runtime origin, and blocks new windows. Processes running as the same OS user remain within the local trust boundary. Conversation history is stored as unencrypted local SQLite data.

## Verification and code map

```sh
.venv/bin/python -m unittest discover -s tests -v
npm test
```

The automated suite uses temporary state, simulated inference, local sockets, and real macOS worker-policy checks. It covers privilege enforcement, process controls, restart recovery, graph interrupts, credential handling, billing rejection, file isolation, HTTP request validation, and voice lifecycle behavior.

An opt-in live check exercises real free-provider delegation with isolated conversation state:

```sh
.venv/bin/python -m agent_zero --home .agent-zero check-delegation
```

That check consumes free-request quota. It verifies parent spawn, child completion, result collection, and reported cost. Detailed results and remaining limitations are in [verification](docs/verification.md) and [implementation status](docs/implementation-status.md).

| Source | Responsibility |
| --- | --- |
| [`runtime.py`](agent_zero/runtime.py) | Supervisor, mode transitions, administrative dispatch |
| [`processes.py`](agent_zero/processes.py) | Process tree, inheritance, scheduling gates, lifecycle |
| [`tools.py`](agent_zero/tools.py), [`graph.py`](agent_zero/graph.py) | Restricted model tool adapter and LangGraph execution |
| [`memory.py`](agent_zero/memory.py) | Private/shared page store, kernel grants and bounded LRU paging |
| [`security.py`](agent_zero/security.py) | Passphrase verification, tokens, expiry and rate limits |
| [`sandbox.py`](agent_zero/sandbox.py), [`worker.py`](agent_zero/worker.py) | File boundary and isolated worker |
| [`engines.py`](agent_zero/engines.py) | Cloud/local adapters and shared provider accounting |
| [`server.py`](agent_zero/server.py), [`storage.py`](agent_zero/storage.py) | Loopback HTTP, Unix IPC and persisted state |
| [`audio.py`](agent_zero/audio.py), [`wake.py`](agent_zero/wake.py) | Offline transcription, audio ownership and deterministic mode phrases |
| [`desktop/`](desktop), [`web/`](agent_zero/web) | Electron shell and single-window interfaces |
