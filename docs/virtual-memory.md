# Virtual memory

Agent Zero's virtual memory is a supervisor-owned page store with private address spaces, kernel-managed shared regions, and a bounded resident working set per logical agent. It extends the existing conversation history and LangGraph checkpoints; it does not replace them or allocate host virtual memory.

## Addresses and authority

Every logical PID has a `private:PID` namespace. An agent addresses its own namespace as `private` (the default). Parent/child process visibility does not permit reading another process's private pages. The supervisor takes caller identity from `CURRENT_PID`, not tool arguments.

Shared regions have addresses such as `shared:project`. Only authenticated Cardinal calls can create/drop these regions or grant/revoke access. Grants name an exact PID and are `r` or `rw`. Read-only permits listing, searching and reading; read/write additionally permits writing and deleting pages. Agents cannot grant access, create shared regions, or impersonate an operator.

The authenticated operator can inspect or edit any private namespace. In Cardinal, an explicit `private:PID` address selects that process; `private` selects PID 1 unless a command provides a different PID.

## Page store and working set

Pages are UTF-8 strings with keys, revisions and update timestamps, persisted in the existing state SQLite database. A page write is write-through: it commits to backing storage before returning. Writes invalidate cached copies held by other processes. There are no provider calls, embeddings, or automatic summarization in this subsystem.

Bounds in this version:

| Resource | Limit |
| --- | --- |
| Page body | 4,096 UTF-8 bytes; escaped representation must fit the working set |
| Pages per namespace | 128 |
| Shared regions | 32 |
| Resident context per PID | 8,192 serialized UTF-8 bytes, including context framing |
| Resident pages per PID | 16, subject to the byte limit |
| Inventory response | 16 page addresses, with `next_offset` pagination |
| Search response | Up to 20 matching addresses |

A read pages data into the requesting PID's LRU cache. Agent writes also touch the working set. A miss increments the page-fault counter. If the cache exceeds its bounds, least-recently-used pages are evicted while their backing records remain intact. A subsequent read reloads them. Pages and grants survive daemon restart; resident caches and their counters start empty.

Before each model request, the supervisor rechecks resident-page access and injects a temporary user-role message marking the contents as untrusted data. This injected message never enters LangGraph state. Memory-read tool results contain the loaded address and revision rather than copying the page body into checkpoint history. Agent search results likewise retain addresses/revisions; the agent issues a read to load a matching page. Operator search can also show short previews.

The 8 KiB limit covers resident memory injection, not the entire provider prompt, conversation history, tool schemas, or a hard token limit. Models may quote or summarize data they have seen. Revocation evicts cached pages and denies future access; it cannot retract an earlier provider request or erase data copied into model-generated output, other pages, forked conversation history, or external provider logs. This is access control, not an information-flow or retroactive deletion guarantee.

## Process lifecycle

- **Spawn:** new, empty private namespace.
- **Fork:** eager independent copy of the parent's private pages. Subsequent writes do not affect the other copy. Shared grants and resident caches never inherit.
- **Exit:** private pages remain available for kernel inspection until reaping.
- **Reap:** deletes private pages, resident cache/counters and the PID's shared grants. Shared-region data remains.
- **Restart:** persisted pages/grants remain; unfinished work becomes interrupted and is not replayed. Startup also cleans namespaces/grants belonging to reaped records.
- **Session flush:** after stopping matching work, deletes that process's private pages and clears its working set. Other processes and shared backing pages remain.
- **Flush all:** after stopping work, deletes every private/shared page body and clears caches. Region definitions and grants remain as kernel policy.
- **Drop shared region:** deletes its pages and grants and invalidates every resident copy.

Memory pages use the same local, unencrypted SQLite storage boundary as the rest of runtime state. They are not accessible through the sandbox file worker. Normal status telemetry exposes aggregate counts and bytes, not memory contents or page keys. Policy changes are audited without page bodies.

## Syscalls

Agent-visible calls:

| Call | Parameters | Effect |
| --- | --- | --- |
| `sys_mem_write` | `key`, `content`, optional `space` | Persist a private or writable shared page |
| `sys_mem_read` | `key`, optional `space` | Page permitted data into the working set |
| `sys_mem_delete` | `key`, optional `space` | Delete a writable page and invalidate cached copies |
| `sys_mem_list` | Optional `space`, `offset` | List page metadata |
| `sys_mem_search` | `query`, optional `space` | Literal substring search over permitted keys/content |
| `sys_mem_stat` | None | Own private usage, grants, working set, faults and evictions |

Kernel-only calls add `sys_mem_create`, `sys_mem_grant`, `sys_mem_drop`, `sys_mem_spaces`, and the `sys_mem_command` command parser. The host RPC path requires operator authentication for all memory calls, including inspection. Kernel data calls may specify `pid`; model tool schemas reject it. Existing `sys_mem_flush` includes virtual pages in its scope.

## Cardinal workflow

In the Electron console or native TUI:

```text
mem spaces
mem stats 1
mem create project
mem write shared:project plan "Review the release checklist"
mem grant project 1 r
exit
```

JARVIS can now list/read `shared:project` but cannot modify it. A child needs its own explicit grant; access does not follow parentage. After authenticating again:

```text
mem grant project 2 rw
mem read private:2 notes
mem list private 2
mem revoke project 1
mem drop project
```

Use `mem search SPACE QUERY`, `mem delete SPACE KEY`, or `mem write SPACE KEY TEXT` for direct inspection and maintenance. Commands accept shell-style quoting for text, but execute no shell. `mem` alone shows PID 1's statistics. Agents should use `request_kernel` when a required region/grant is missing.

Implementation: [`memory.py`](../agent_zero/memory.py), integrated at process creation/reaping, authenticated runtime dispatch, the model tool adapter, and both Cardinal command interfaces. Tests use fixture pages and simulated inference, including an actual SQLite-backed LangGraph run.
