# Implementation status

The prepared checkout supports cloud parent JARVIS and supervised children, local Qwen3.5 9B for Cardinal/fallback, and offline speech input. Nemotron 3 Ultra Free is live-tested and configured, with a $0 budget, free-only routing and paid swaps blocked. Local userspace fallback remains opt-in.

| Area | Implemented | Remaining limits |
| --- | --- | --- |
| Orchestration | PID 1 parent JARVIS, bounded child processes, spawn/fork/exec/wait/signals/reap, orphan adoption, separate LangGraph sessions, shared budget/concurrency, authenticated interrupt/resume | No automatic replay of reviewed privileged actions; interrupted inference is explicitly retried by the operator |
| Local inference | Existing Qwen3.5 9B verified, 4K context, thinking disabled for voice, resident-memory check, low-memory guard, cancellable HTTP | Resident budget is a guard, not an OS allocation ceiling; no direct MLX adapter |
| Tools | Model-visible list/read/write, live capability checks, writes disabled initially, explicit kernel review tool | No shell, arbitrary code, browser automation or network adapter |
| Isolation | Short-lived macOS sandbox-exec worker, filesystem confinement, read-only mounts, denied network and credential reads tested | The daemon and Ollama are outside the file-worker sandbox; non-macOS fallback has file confinement only |
| Authentication | First-use in-app setup, typed Electron/browser/TUI, explicitly initiated offline spoken browser/TUI, scrypt, rate limiting, expiring operator tokens | Real microphone accuracy requires user testing; recognized passphrase must match exactly |
| Voice | Offline STT, foreground Hey Jarvis wake matching, follow-up and sleep lifecycle, local system TTS, cancellation and focus/kernel pauses | Clip-based wake latency; no dedicated acoustic wake model or concurrent spoken barge-in; no incremental model-to-audio streaming |
| Desktop | Native ARM64 Electron .app, daemon startup/reuse, isolated profile, single-window JARVIS/Cardinal transition, drop-down typed/spoken authentication | Checkout/venv dependent, locally ad-hoc signed, no notarized standalone installer; hardware microphone verification remains manual |
| Cloud | Researched registry, native tools attached, billing opt-in, output/price/session guards, no paid fallback | Real zero-cost parent/child cloud delegation verified; paid inference not run; cloud HTTP may finish after local cancellation; budget resets with daemon |
| Telemetry | Host CPU/RAM, local inference duration/output tokens/resident memory in status, real daemon state | No full per-process profiler or long-running memory benchmark |
| Storage | SQLite history/audit/settings, graph checkpoints, flush commands | Local text unencrypted; retention and account-level billing reconciliation remain future hardening |

The core requested local application is usable. Hardware microphone quality, noisy-room wake accuracy, and optional paid-provider behavior are explicitly unverified. The installed macOS voice is the current output engine; Piper/Kokoro and streaming speech are future alternatives rather than prerequisites.

See [process model](process-model.md) for the explicit Ring 0/operator versus Ring 3/agent syscall allowlists. Local Qwen is never an authenticator or privileged executor.
