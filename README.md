# Agent Zero

A Python supervisory runtime with two faces: **JARVIS**, a voice-only cyan floating intelligence orb, and **Cardinal**, a green/amber terminal-inspired administrative console. Built from the v1.2 PRD, with an 8–16 GB local inference target.

**Parent JARVIS and child agents use a Unix-inspired process supervisor and a shared cloud gateway.** Fresh configurations start in a free local preview; local Qwen is available independently for Cardinal and as an explicit userspace fallback. No API key, model download, microphone access, cloud speech, or inference request is needed to explore it. Python handles the runtime, authentication, storage, Unix IPC, agent graph, file tools, local inference adapter, voice routing, and native TUI. The browser uses plain HTML/CSS/JavaScript for visuals; there is no frontend build step or CDN.

## Start

This prepared checkout has the existing `qwen3.5:9b` and the free tiny English speech model. The agreed architecture uses cloud models for parent JARVIS and children, with local Qwen for Cardinal intent parsing. Cloud execution remains disabled until explicitly configured; local JARVIS is an optional fallback (`local_enabled: true`). No language model download was needed. To reopen that configured runtime:

```sh
.venv/bin/python -m agent_zero --home .agent-zero serve --graph
```

The model files and runtime state live in ignored `.agent-zero/`. Enable the **Hey Jarvis** ear control in the app and allow microphone access once per session. Cloud inference is disabled. Speech recognition runs locally; model conversation requires explicit cloud setup or local fallback configuration. You can also double-click `dist/Agent Zero.app`, or run `.venv/bin/python -m agent_zero --home .agent-zero desktop`. The launcher starts the graph-enabled daemon if needed and opens a dedicated Chrome app window. Chrome is installed on this Mac; other systems fall back to the default browser. This launcher depends on this checkout and its virtual environment; it is not a standalone signed installer.


Python 3.11+ on macOS or Linux:

```sh
python3 -m agent_zero serve
```

Open [the local interface](http://127.0.0.1:8420). The main screen is a voice interface with no text chat or transcript. Click the orb or microphone, or hold Space, to speak after offline STT is configured. A small settings dialog explains missing voice setup. Use the wake control to enable “Hey Jarvis” once per browser session; the microphone is not opened until you opt in and grant browser permission. In preview mode, spoken replies are deterministic and no conversation model is called.

Configure your own kernel passphrase in another terminal, then use **Authenticate** in Cardinal:

```sh
.venv/bin/python -m agent_zero --home .agent-zero init
```

The default state directory is `~/.agent-zero` (private permissions), containing `credentials.json` (salted scrypt hash), `state.sqlite`, `sandbox/`, and `agentd.sock`. `AGENT_ZERO_HOME` or `--home PATH` selects another directory. The socket is restricted to your OS user; HTTP binds exclusively to `127.0.0.1`. Stop the daemon with Ctrl+C.

## Optional Python features

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[agent,tui]'
.venv/bin/python -m agent_zero serve --graph
```

The `agent` extra enables parent/child LangGraph sessions and a bounded model → tools → model loop and real SQLite-backed `interrupt()` / `Command(resume=...)` traps. Without it, the zero-dependency preview uses a persisted trap queue. Do not switch a home directory containing pending graph checkpoints to the non-graph daemon. A single daemon owns each home directory.

For the native Textual interface, leave the daemon running and open another terminal:

```sh
.venv/bin/python -m agent_zero tui
```

Both consoles share the same Python daemon and state over IPC. The native console takes over its current terminal and restores it on exit. Ctrl+L drops authority; Ctrl+Q closes the console. Browser authority tokens are held in memory only and expire after five minutes; the daemon drops kernel mode on expiry. Authenticating a new operator invalidates the previous operator token.

## Parent JARVIS and child processes

PID 1 is parent JARVIS. Children inherit a subset of its file/delegation capabilities and its selected cloud model, share the same budget gateway, and have separate task histories and graph checkpoints. Models can spawn/fork, exec a registered task, inspect their subtree, wait, signal descendants and reap direct children. They cannot change models, permissions, mounts, credentials or review decisions. Only authenticated Cardinal commands can do that. A model asking for a Ring 0 method is rejected, even if it knows the method name.

Cardinal supports `ps`, `tree`, `spawn TASK`, `fork TASK`, `exec PID`, `wait PID`, `info PID`, `signal PID TERM|STOP|CONT`, and `reap PID`. Operator-created work queues while in kernel mode and starts after `exit`. The old review listing is now `sys_trap_table`; `sys_proc_table` lists real logical agent records. See [the complete process and syscall authority design](docs/process-model.md).

These are supervised logical agent processes, not host process IDs. Defaults allow four live children, depth two, twelve creations per daemon lifetime and two concurrent inference requests. STOP/CONT pause at model/tool boundaries; TERM cancels a subtree. Unfinished work is marked interrupted on restart rather than automatically replayed. No raw host fork or shell exec is exposed.

## Kernel commands

Authenticate before administrative actions. `status` and the dashboard are read-only and available without escalation.

| Command | Behavior |
| --- | --- |
| `status` | Inspect actual runtime state |
| `model z-ai/glm-5.3-flash` | Change the active cloud model; does not enable paid inference |
| `allow file_write` / `deny file_write` | Change the file tool capability |
| `flush session` / `flush all` | Clear conversation state (and graph checkpoints when enabled) |
| `exit` | Revoke authority and restore userspace when no traps remain |
| Other natural language | Ask the installed Ollama model for a JSON syscall **proposal**; never automatically executes |

Request a userspace trap using the browser's **Request userspace trap** control, or say “Enter kernel mode” through the configured voice interface. The underlying chat API also accepts `/kernel` or `System Call: Enter Kernel Mode`. The daemon pauses Ring 3, saves the request, and cancels userspace audio. Authenticate, approve or reject the review, then drop authority to resume. Approval records a review decision; it does not execute the requested privileged action. Issue the intended command explicitly. In the native console, `approve ID` and `reject ID` accept unambiguous ID prefixes.

The Python syscall layer also exposes `sys_mount_sandbox`, with a host directory, mount alias, and `ro`/`rw` mode. Mounts are session-only and cannot expose the runtime directory or any ancestor containing it. Shell and network tool adapters are not implemented and cannot be enabled.

## Models and billing

See [the dated model research](docs/model-research.md) for primary-source links, prices, corrections to the PRD, and memory caveats. A GPT-6 Luna research agent checked the candidates. The rebuilt registry records exact IDs, sources, roles, licenses, prices, and the date checked. GLM 5.3 Flash is the recommended default; Ling 3.0 Flash is a cheaper promotional trial; Gemma 4 26B A4B `:free` is a fixed free experiment with zero provider price ceilings. Retired persisted model IDs migrate to GLM and are audited. There is no automatic fallback from free to paid. The local shortlist is Qwen3.5 4B and 9B. Catalog evidence should be revisited; it is not permanent truth.

The portable conservative local default is `qwen3.5:4b` with **4,096 context** and one request at a time. This Mac already has 9B installed, so the prepared project config selects **Qwen3.5 9B** for Cardinal/local fallback. Set `local_enabled: true` to use an installed model for conversation; local mode takes priority over cloud mode and never falls back to a cloud service. `qwen3.5:9b` is an option nearer 12–16 GB. Model weight sizes exclude KV cache, runtime overhead, and audio models. `local_memory_gb` is checked against Ollama’s reported resident size; inference pauses below 1.5 GB available host RAM. This is **not an enforced OS memory limit** or a guarantee against transient allocation spikes. With 4K context, the installed Q4_K_M 9B model reported 5.14 GiB resident in a live test. Metrics are available in daemon status. No Ollama language model is downloaded by the daemon. An explicitly configured local conversation loads the already installed model as needed. Ollama must already be running and the exact model must already be installed before local conversation or natural-language kernel parsing can work. Direct MLX remains an interchangeable adapter follow-up; no speculative HTTP latency claims are assumed.

Paid cloud inference is **disabled** in this checkout and has not been exercised. To enable it later, manually copy `config.example.json` to your state directory as `config.json`, change `cloud_enabled` to `true`, and export `OPENROUTER_API_KEY` only in the daemon's terminal. Restart the daemon. No browser control enables billing. Credentials are never sent to the agent graph or browser.

The gateway enforces a maximum output length, reserves conservative estimated token charges before requests, and sends provider price ceilings. It retains reservations on ambiguous failures. The default $0.25 ceiling is **per daemon lifetime**, not a persistent account spending limit. Provider billing remains authoritative; also configure an OpenRouter key spending limit before paid use. Prices can change, so recheck catalog/provider quotes first. No automatic model fallback runs. Conversation requests are serialized independently of supervisor commands. Kernel authentication and traps cancel the local conversation task without waiting for a slow provider. Local conversation uses cancellable HTTP and closes its provider connection on interruption. Cloud requests still use blocking HTTP and may complete provider-side; its reservation is retained and a late response is not published.

## Voice

The main JARVIS application is an orb with microphone/mute/stop controls and optional compact telemetry. There is no text composer, chat transcript, or prompt panel. Cardinal retains its text-based administrative interface.

Manual browser microphone capture is explicit: click the orb or microphone to start, click again to finish, or hold Space and release to send. Clips stop after 15 seconds or about 1.3 seconds of silence following speech. Stop, Escape, leaving the view, or losing focus during capture releases the audio tracks and discards the recording. Audio is sent only to the loopback Python daemon, which transcribes it offline and deletes the temporary recording. No browser cloud speech recognition is used.

**Wake phrase:** enable the ear/wake control once per app session, grant microphone permission, and say **“Hey Jarvis”**. While the JARVIS view is visible and focused, the dimmed orb monitors short speech clips using the installed offline recognizer. Nonmatching ambient speech never reaches OpenRouter, conversation history, or audit logs. Silent clips are discarded in the browser. A leading “Hey Jarvis” wakes the orb; a request spoken in the same breath is preserved. After the reply, JARVIS listens for a follow-up; six seconds without speech returns it to dormant wake monitoring. Say “go to sleep” to return to dormancy, or “stop listening” to disable wake monitoring entirely. Stop and Escape also disable it. Switching to Cardinal, opening a settings/auth dialog, hiding the page, or losing focus releases the microphone; returning to the active JARVIS view resumes monitoring only if it was enabled.

This uses **local speech recognition**, not a dedicated acoustic wake-word model. Detection has clip/transcription latency and still needs real-world accent/noise testing; it is not a claim of Siri-level responsiveness. Wake speech is processed only on this machine, and generated replies are not recorded back into the wake detector because capture pauses during playback. No wake check invokes a cloud model.

For browser voice input, install the voice extra, set `stt_model_path` in the daemon config to a locally installed faster-whisper directory, and restart. The model directory must include `model.bin`, `config.json`, and `tokenizer.json`; nothing is downloaded by the running application automatically. The small Voice settings dialog shows the exact active config path. Conversation text remains in the local Python history store but is never rendered in the orb interface.

Speech output uses only an installed English voice (`localService`), starts in response to a user-initiated voice turn, and can be muted. Switching to Cardinal cancels browser speech. The Python `AudioRouter` provides generation-based cancellation and a free macOS system-voice fallback. The optional `LocalTranscriber` uses `faster-whisper` with `local_files_only=True`, requiring an explicitly installed model path.

Typed kernel authentication works. The browser auth dialog also has a **Speak passphrase locally** button. Its explicitly captured audio uses a separate credential endpoint, never chat, history, graph state, or TTS. Cancellation invalidates a pending verification. Recognition must match the configured passphrase exactly, including case and punctuation; typed auth remains available. The native console also offers explicit **F2 spoken authentication** when started with a locally installed faster-whisper model:

```sh
.venv/bin/python -m pip install -e '.[voice]'
.venv/bin/python -m agent_zero tui --voice-model /absolute/path/to/local/whisper-model
```

F2 loads the offline recognizer, records five seconds, deletes the temporary WAV, and submits the transcript directly to the same credential verifier. The transcript is never logged or sent to a conversation model. Recognition must match the passphrase exactly (a terminal sentence period is removed); a simple word-based phrase works best. This optional hardware flow has not been tested with a live microphone. Typed auth remains available. No recording begins at startup, and no model is downloaded automatically.

The browser capture/turn/playback pipeline has been tested with synthetic audio and a stubbed model response; real microphone quality and end-to-end wake accuracy still require hardware verification. Dedicated Kokoro/Piper synthesis remains deferred. Wake-triggered follow-up turns are implemented.

## Security boundary

“Ring 0” and “Ring 3” describe application authority domains; this software does not enter actual OS kernel mode. File tools restrict relative paths, reject symlinks and traversal, use descriptor-relative opens, enforce read-only mounts and file-size limits, and reject non-regular files. On macOS, attached file tools execute in a separate short-lived Python worker using a **deny-by-default OS sandbox**. Only the selected sandbox/mount roots are accessible for data, only writable roots may change, and network access is denied. Interpreter/library/module reads are allowed for startup. Tests verify that an adjacent credential file cannot be read and a loopback network connection cannot be opened. Other platforms retain descriptor-based confinement without this macOS policy. This isolates the file worker, not the entire daemon or Ollama service; arbitrary code and shell plugins are still excluded. Both local and cloud models can use the same bounded read/list/write tool loop. Every tool checks current permissions, write access starts disabled, and escalation only creates a human review request. `max_agent_steps` defaults to six (maximum twelve), with at most four calls per step. A denied tool returns an error for the model to explain or recover from; limits prevent infinite retry loops.

Loopback HTTP checks the Host, Origin, a custom request header, and a private HttpOnly SameSite browser-session cookie before mutation; there is no CORS permission or remote binding. Other processes running as your OS user are within the trust boundary. Kernel secrets are hashed, not logged, and never enter model prompts. Failed authentication is rate limited. SQLite stores conversation text unencrypted on the local machine. Read-only local telemetry/audit endpoints are not a separate multi-user privacy boundary.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check agent_zero/web/app.js
node --test tests/voice_state.test.mjs
```

The suite checks privilege enforcement, expiry, lockout, sandbox traversal/symlinks, mounted secrets, read-only mounts, permission toggles, trap persistence, cost rejection before networking, audio ownership, CSRF protections, and preview behavior. With the agent extra installed it also checks a real LangGraph interrupt surviving graph reconstruction and authenticated resume. The automated suite uses temporary state, stubbed inference, local sockets, and real macOS worker policy tests. Separate live integration checks exercised the already installed Qwen model reading a note and correctly reporting a denied write. They incur no inference cost.

See [implementation status](docs/implementation-status.md) for the remaining work.
