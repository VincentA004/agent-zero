# Scheduling, services, mailboxes and execution inspection

These features extend the existing Python supervisor and logical process tree. They use the same SQLite state database, model gateway, process quotas and capability checks. They do not install host cron jobs, launch arbitrary executables, or create independent model runtimes.

## Task scheduler

Cardinal can register one-shot jobs, fixed-interval jobs, explicit-offset timestamps, and inference-free mailbox reminders:

```text
schedule after review 300 "Review the release checklist"
schedule every monitor 3600 "Inspect the permitted workspace and report changes"
schedule at morning 2026-10-26T09:00:00-05:00 "Prepare a project summary"
schedule notice break 600 "Take a break"
schedule list
schedule runs review
schedule pause monitor
schedule resume monitor
schedule run review
schedule remove monitor
```

`after`, `every`, and `notice` use seconds. Intervals must be at least 60 seconds; delays/intervals are capped at one year. `at` requires a timezone offset or `Z`. These are fixed intervals and one-shot timestamps, not a calendar-aware cron expression system.

The daemon checks due jobs once per second. Automatic dispatch pauses in Cardinal/locked mode. Missed intervals coalesce to one dispatch, and a job never overlaps its previous worker. Jobs survive restarts; the daemon must be running to execute them and cannot wake a sleeping or shut-down Mac. There is no startup replay of a claimed occurrence. A due, unclaimed job can run after restart.

Dispatch commits a run claim and advances/disables its schedule before creating work. A crash between those actions can lose an occurrence; it favors avoiding duplicate provider requests over exactly-once execution. Definite local admission failures defer for 30 seconds. A recurring job's next due time advances from actual dispatch, avoiding catch-up bursts. Manual `run` consumes/advances that occurrence; an active worker causes deferral rather than duplication.

Agent jobs create PID 1 children with capability ceilings captured at registration and intersected with current permissions. They use the current parent model and shared gateway. No enabled conversation engine means deferral. Existing child/depth/concurrency/birth/unreaped-process limits still apply, including the twelve-child-creation default per daemon lifetime. Reaping releases process-table space but does not reset that birth quota.

Completed worker status and a bounded result are stored in run history and a best-effort completion message is sent to JARVIS's mailbox. A full mailbox can prevent that notification; the run record remains inspectable. `notice` sends a local kernel-origin mailbox message with no model call. The orb shows the pending-mail count; messages do not automatically wake inference or speak an alarm.

Up to 32 job definitions and roughly the latest 256 completed/deferred run records are retained, in addition to active runs. Removing a definition stops future dispatch but does not cancel an existing worker; use process signals for that. Pausing likewise affects future dispatch.

## Service registry

A service is a persistent, kernel-registered named agent workflow with a task template, capability ceiling, enable flag, caller grants and last worker PID:

```text
service register researcher "Summarize the supplied project material"
service list
service grant researcher 2
service revoke researcher 2
service start researcher "Review the current release notes"
service disable researcher
service enable researcher
service remove researcher
```

New services grant invocation to PID 1 only. Grants name exact PIDs and never inherit. Agents can use `sys_service_list` to discover their granted services and `sys_service_call(name, task?)` to invoke one. The resulting worker is a child of the caller, so existing `sys_wait`, process inspection, signals and reaping apply. Kernel-started workers are PID 1 children and queue until userspace resumes.

Each service permits one active worker at a time. A registration survives restart, but each invocation is a finite task with a fresh process/private namespace. It is not an infinitely looping or self-restarting daemon, and private memory is not automatically copied between invocations. Shared memory still needs explicit kernel grants. Up to 32 services can be registered.

Service calls cannot choose models, widen capabilities, grant callers or change the registration. Disabling blocks new invocations without killing an active worker. Removing an active service is rejected; stop its worker first. Existing process/model policies remain authoritative.

## Agent mailboxes

Each PID has a bounded persistent inbox. Agent tools are:

| Tool | Effect |
| --- | --- |
| `sys_mail_send(recipient, body)` | Send to self, an ancestor or a descendant |
| `sys_mail_recv(limit?)` | Read own pending messages without deleting them |
| `sys_mail_ack(message_id)` | Delete a handled message from own inbox |

Models cannot choose a sender or inspect another PID's inbox. Siblings cannot message each other directly; parent routing or a granted service provides an explicit relationship. The authenticated operator can inspect any inbox and send as kernel sender PID 0. Mail is untrusted task data, not authorization. Delivery never changes permissions or starts a provider request. Agents poll mail during an active turn; there is no automatic inference wakeup or message-driven exec.

Cardinal commands:

```text
mail recv
mail recv 2
mail send 2 "Please report progress"
mail ack 17
mail ack 18 2
```

Message bodies are capped at 2,048 UTF-8 bytes and an escaped-serialization limit; each inbox permits 64 pending messages, with 4,096 messages globally. Agent receive results have an additional tool-response bound and `more` flag. Acknowledge handled IDs before polling further messages. Messages persist until acknowledged or flushed. Reaping removes the recipient's inbox, but preserves messages it already sent to other inboxes. Mail is not inherited by spawned/forked agents. Session flush deletes messages involving that process; flush-all deletes all mail.

## Memory inspector

Cardinal's inspector selects a PID and displays private page metadata, backing-store usage, shared grants, resident addresses, page faults and evictions. Page bodies are fetched only after explicitly selecting a page. `inspect PID` exposes the same metadata through the native TUI/command input; existing `mem` commands inspect shared regions and edit pages.

`sys_mem_inspect` is kernel-only. Agent subtree visibility does not grant access to another agent's private pages. Inspector output is cleared when the UI leaves Cardinal, and pending reads are guarded against an expired/replaced token. The inspector does not load pages into an agent's working set; kernel reads are observational.

## Execution traces

The supervisor records nested spans for model requests, tool calls and process execution, plus process/service/scheduler lifecycle events. Cardinal displays recent spans with PID, parent span, operation, status and duration; `trace PID` filters a process and `trace clear` removes completed spans.

Trace records contain operation names, timings, statuses and error classes. They exclude prompts, tool arguments/results, memory contents, mailbox bodies and credentials. Tool names are recorded only if on the known tool allowlist; unknown names become `unknown`. This is execution metadata, not full model reasoning or deterministic replay.

Up to roughly 5,000 spans are retained; in-flight spans are preserved during trimming/clear. Daemon restart marks previously running spans interrupted. Cancellation and denials are recorded. Trace inspection/clearing requires operator authentication and is unavailable to model tools.

## State, authority and retention

The host RPC path requires kernel authentication for mailbox/service/scheduler/trace inspection and control. Model tools use separate explicit allowlists and supervisor-owned caller identity. Public status exposes only background counts and PID 1's pending-mail count. Full job tasks, service templates, messages and trace lists require authentication.

Memory flush also clears mailbox data and retained scheduled-run result bodies in its scope. Job/service definitions, enable flags, grants and trace metadata remain policy/diagnostic state; pause/remove jobs separately if future work should stop. SQLite data remains local and unencrypted, with the same OS-user trust boundary as existing runtime state.

Implementation: [`scheduler.py`](../agent_zero/scheduler.py), [`services.py`](../agent_zero/services.py), [`mailboxes.py`](../agent_zero/mailboxes.py), [`tracing.py`](../agent_zero/tracing.py), and the shared Cardinal [`control.py`](../agent_zero/control.py) parser.
