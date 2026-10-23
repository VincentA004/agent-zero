# Verification

Checked 2026-10-23 on macOS / Apple Silicon, Python 3.14.4, Node 25.9.0.

- **52 Python tests passed**, with the optional LangGraph and Textual dependencies installed. No tests skipped in the full run.
- LangGraph conversation used a stub provider; checkpointed interrupt was reconstructed and resumed after authentication. Graph flush cleared SQLite checkpoints.
- Supervisor status/authentication and traps were verified to preempt a stalled inference task; the LangGraph version preserves the new trap checkpoint.
- Native Textual console was mounted in its headless test harness; authentication, a model command, and authority drop passed.
- Spoken authentication used mocked microphone and transcription; temporary recording cleanup passed. No live microphone capture was attempted. The separate real offline speech-model check is documented below.
- Python compilation and JavaScript syntax checks passed.
- Desktop (1440px) and phone (390px) layouts were checked. No horizontal overflow; the canvas rendered after view changes and with reduced-motion enabled; no JavaScript page errors.
- Initial browser checks covered preview chat and all administrative flows. The main interface was then redesigned as a voice-only orb: no text composer, transcript, or prompt suggestions remain.
- New browser checks covered missing-STT setup without requesting microphone access, optional metric visibility, Cardinal navigation, and absence of JavaScript errors.
- Synthetic browser audio passed through MediaRecorder and the voice RPC to a stubbed spoken response. No transcript was rendered; stop and Cardinal navigation discarded pending recordings and released every audio track. Actual microphone, STT model, and speech output were not used in those browser tests.
- Six deterministic JavaScript lifecycle tests passed: dormant nonmatch, phrase-only wake, same-breath request, sleep/stop, and focus-loss cancellation. Browser synthetic audio also verified wake -> conversation -> reply -> inactivity dormancy, foreground/kernel suspension, and microphone release.
- The rebuilt registry was verified in the UI: GLM recommended default, promotional Ling trial, and fixed free Gemma. Python tests verify retired selection migration and zero price ceilings/no paid fallback for the free model.
- Temporary test credentials and state were removed; final preview has cloud disabled, zero inference tokens/charges, and no configured passphrase.
- Cloud-model and Ollama inference, actual microphone accuracy, and dedicated audio engines remain unverified. A free Whisper tiny English model (~78 MB) was explicitly downloaded into the ignored project model directory for this feature; no paid inference calls were made.

Optional dependency versions used: LangGraph 1.2.16, langgraph-checkpoint-sqlite 3.1.2, Textual 8.2.8, psutil 7.2.2. The dependency extras are bounded to their tested major versions.

Screenshots from the local browser are in ignored `output/playwright/`: `jarvis-orb-only.png` and `jarvis-orb-mobile.png` show the current voice-first interface; earlier `jarvis-desktop.png`, `cardinal-desktop.png`, and `jarvis-mobile.png` record the original design.

## Actual offline speech check

The installed Whisper tiny English model recognized a generated local system-voice recording of “Hey Jarvis, tell me a short story.” The real Python wake endpoint returned `awake: true` with `tell me a short story.` as its command, and left conversation history empty. The isolated cold-load/transcription step took about 0.48 seconds on this machine; this is one generated recording, not a latency or microphone-accuracy benchmark. No microphone or audible playback was used.

The initial real check found a released faster-whisper/PyAV incompatibility. The voice extra now pins `av>=18.1,<19` because faster-whisper 1.2.1 calls `av.open(metadata_errors=...)`, removed in PyAV 19. This follows the upstream issue: https://github.com/SYSTRAN/faster-whisper/issues/1589 . Installed/tested versions: faster-whisper 1.2.1, PyAV 18.1.0, CTranslate2 4.8.3, sounddevice 0.5.6. Package discovery also explicitly includes only `agent_zero*`, so generated screenshot folders cannot break editable installation.

The configured project-local runtime was also checked over actual loopback HTTP: input readiness was true, `wake_probe` recognized the generated speech, history stayed empty, and inference usage/cost stayed zero. The final browser check verified microphone permission denial disables wake without repeated prompts, and the dormant/mobile views render correctly using synthetic streams.


## Local-agent completion pass (2026-10-23)

- Found existing `qwen3.5:9b` (6.6 GB disk) and `qwen3.5:0.8b` (1.0 GB disk); no Ollama model downloaded.
- Selected 9B in the private project config. A 4,096-context run reported 5,515,247,614 bytes (5.14 GiB) resident. First cold conversational request took 7.98 seconds; generated 18 tokens in 0.42 seconds. These are samples, not a performance guarantee.
- Real Qwen through LangGraph called `read_file` and correctly answered a temporary note’s codename/day (7.19 seconds including tool loop). A denied write was explained correctly, with no file created (1.93 seconds). Cloud spending stayed zero.
- macOS isolated worker successfully wrote/read/listed temporary sandbox files. The same profile denied an adjacent credential file read and a network connection to loopback Ollama.
- New tests cover step limits, local-only routing, tool errors, live preemption, graph tool state and model escalation, private spoken auth and cancellation, and cancellation closing the local HTTP connection.
- Browser inspection confirmed local Qwen status, no JARVIS text composer, ready offline input, LangGraph and OS worker isolation enabled. Screenshot: `output/playwright/jarvis-local-ready.png`.
- Built and opened `dist/Agent Zero.app`; dedicated Chrome profile, no automatic microphone capture. This is a checkout-dependent launcher, not a signed standalone distribution.
- Full suite: **64 Python tests and 7 JavaScript lifecycle tests pass** at this checkpoint. Syntax/compilation and dependency consistency checks pass. Paid inference and physical microphone capture were not used.

Final checks in this pass: **66 Python tests and 7 JavaScript lifecycle tests pass**. A generated audio file was transcribed offline, sent through the real local Qwen/LangGraph path, and the reply synthesized to an audio file: “7 plus 5 equals 12.” The complete file-based check took 7.09 seconds including speech synthesis, reported 5.14 GiB model residency, and incurred $0 cloud cost. No physical microphone was opened. Local HTTP cancellation closes the provider connection; cloud tool protocol tests use stubs only.


## Process supervisor and privilege pass (2026-10-23)

- Added supervisor-owned logical PIDs/PPIDs with PID 1 parent JARVIS, persisted process metadata, separate child histories/checkpoints, shared cloud gateway and bounded inference concurrency.
- Verified parent delegation, child execution, wait/result synthesis through actual LangGraph with stubbed cloud responses. No paid provider requests were made.
- Verified agent output cannot invoke model swaps, permissions, mounts, memory flushes, review decisions, authentication or administrative parsing. Forged caller/model/token arguments to spawn are rejected.
- Verified inherited capability subsets, live revocation, sibling/ancestor protection, no arbitrary shell workflow exec, subtree cancellation, orphan adoption, STOP/CONT, pre-start cancellation, reaping, queueing under kernel authority, restart-without-replay, and protected PID 1.
- `sys_proc_table` now lists logical agent records; `sys_trap_table` retains pending review requests. Both Cardinal interfaces expose process management separately from the trap queue.
- Full suite at this checkpoint: **85 Python tests and 7 JavaScript tests pass**. Cloud behavior is tested with stubs; the local Qwen measurements above remain valid for explicit fallback/Cardinal mode.

Final process checks: **87 Python tests and 7 JavaScript lifecycle tests pass**. Additional tests verify supervisor-owned getpid/getppid, kernel entry suspending children without replaying inference, and voice cancellation leaving background work intact. The intended cloud userspace route is configured with cloud execution still disabled; local Qwen remains available for Cardinal or explicit fallback.

Final budget check: **88 Python tests and 7 JavaScript tests pass**. Two child agents using the same mocked OpenRouter gateway were given a ceiling sufficient for only one request; exactly one provider call was issued and the other failed before networking. Browser checks confirm PID 1 is visible and unauthenticated model swaps, permission changes and process creation are rejected. Screenshot: `output/playwright/cardinal-process-tree.png`.


## Live free OpenRouter delegation (2026-10-23)

- Original environment credential failed HTTP 401. User entered a valid key through the hidden terminal setup command. Stored privately outside Git; no key appears in logs, prompts, model state or browser state.
- Gemma 4 26B A4B `:free` returned upstream 429 from Google AI Studio. Nemotron 3 Super free returned an unavailable upstream endpoint. Neither route was promoted to the live default. User suggested Nemotron 3 Ultra Free.
- Public catalog and endpoint metadata confirmed Ultra’s exact `nvidia/nemotron-3-ultra-550b-a55b:free` ID, zero pricing, and tool support. A tiny direct probe returned OK at reported cost 0.
- Fixed HTTP-200 upstream error envelopes (previously surfaced as missing `choices`). Only explicit 502/503 overloads get bounded retries in free-only mode; no paid fallback or quota retry.
- **Real LangGraph delegation passed:** parent PID 1 called `sys_spawn`; child PID 2, PPID 1, used the same Ultra free model with no capabilities, returned `17 × 19 = 323`, and exited code 0. Parent called `sys_wait` and replied “The child computed 17 × 19 = 323.” This was real cloud inference, not stubbed output.
- Complete test usage: **7,437 tokens; OpenRouter reported $0.00**. One separate tiny Ultra probe used 22 tokens at $0. Existing user conversation history was excluded via isolated temporary state. Report saved privately at `.agent-zero/delegation-check.json`.
- Runtime configured with free-only mode and budget $0. Paid selection blocked even for an authenticated operator until configuration is deliberately changed. All parent and child requests share this guard.
- **98 Python tests and 7 JavaScript lifecycle tests pass**, including secure setup, zero ceilings, no paid routing, record/model matching, unexpected-billing stop, and bounded provider overload handling. Syntax/compilation checks pass.

The same live test also passed through the **running daemon's Unix API**, using a separate test conversation: PID 1 spawned PID 2 (`math-child`), PID 2 returned `323` and exited 0, and the parent reported “The child computed 17 × 19 = 323.” This second live run used 7,417 tokens at reported cost $0. Its process record persists and is visible in Cardinal. Private report: `.agent-zero/live-daemon-delegation.json`.

## Parallel/process/security/recovery pass (2026-10-23)

All live calls stayed on `nvidia/nemotron-3-ultra-550b-a55b:free`, with free-only mode, zero price ceilings, no fallback and a $0 budget. Audio input was never opened.

- **Live parallel delegation:** parent JARVIS spawned two children in a tool batch. Client-side inference request windows overlapped. Children returned `323` and `899`; parent waited for both and returned sum `1222`. The harness also accepts normal reaping by verifying the parent's actual wait results.
- **Live process controls:** each control child made exactly one logical model call (explicit HTTP overload attempts may retry within that call). A deterministic gate after receipt of the provider response made STOP/CONT and TERM reproducible before result publication. STOP/CONT preserved the request without replay; TERM left the process cancelled with no assistant message. This does not claim that a cloud provider's hardware computation can be forcibly stopped; in-flight cancellation is covered by controlled local tests, and blocking cloud HTTP may finish remotely.
- **Privilege attacks:** forced malicious child tool output could not swap models, grant permissions, mount the runtime, authenticate, flush memory, approve reviews, read fixture credentials, kill PID 1 or spawn with greater authority. These backend tests do not rely on a model deciding to refuse.
- **Recovery:** a separate test host process was killed abruptly while a simulated provider call was blocked inside real LangGraph. SQLite WAL/checkpoints recovered; PID 1 returned idle, the unfinished child became interrupted, and no provider request or file action was replayed. Explicit re-exec of the interrupted record was rejected. A fresh task can be created deliberately.
- **Provider faults:** exhausted 503 retries remain bounded and can recover on a later request; 429 quota errors are not retried. Unexpected paid billing still blocks subsequent calls.

Found and fixed: duplicate STOP corrupting resume state; subtree TERM overwriting finished descendants' successful status; malformed tool entries crashing a batch; long tool results being cut into invalid JSON; and `flush all` retaining fork snapshots/task text/completed results. Also corrected a stopped waiter's resume state when its child finishes. Memory flush now cancels matching work before deleting graph checkpoints, preventing cancelled nodes from restoring erased state. Session flush addresses its own process/session; independent copied child memory requires `flush all`.

Final verification: **109 Python tests and 7 JavaScript tests pass**. Syntax/compilation and whitespace checks pass. Live checks used **13,157 tokens at OpenRouter-reported cost $0.00**. They ran in isolated temporary state, excluding user conversation history. Private report: `.agent-zero/runtime-checks.json`.

Repeat the live checks explicitly (consumes free request quota):

```sh
.venv/bin/python tests/live_runtime_checks.py --home .agent-zero
```

The live harness requires enabled free-only cloud mode and a $0 budget; regular unittest discovery does not execute it.

A forced provider-error test also found and fixed redaction ordering: API keys are now removed before error messages are shortened, so an echoed key cannot be partially exposed at the truncation boundary. The test uses fixture keys only.
