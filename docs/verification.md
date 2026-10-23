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
