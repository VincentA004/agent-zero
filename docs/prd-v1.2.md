# Product Requirements Document (PRD): Agent Zero (v1.2)

---

## 1. Executive Summary & Objective

**Agent Zero** is a local/cloud AI runtime built on classic operating system isolation principles (Ring 0 Kernel vs. Ring 3 Userspace).

The runtime operates across two distinct execution domains:

1. **Ring 3 (Userspace / "JARVIS"):** An expressive, conversational companion running high-throughput, low-cost frontier open-weight models (GLM, DeepSeek, MiniMax, Qwen) routed exclusively through the **OpenRouter API**, paired with neural voice synthesis and sandboxed tool execution.
2. **Ring 0 (Kernel Mode / "Cardinal Console"):** A deterministic, fast administrative subsystem running locally via **Ollama** (or native MLX) within a **32GB Unified Memory budget**. It features a terminal UI (TUI) for hardware and state telemetry, an austere local voice engine, and direct capabilities to reconfigure, inspect, or hot-swap Ring 3 without agent self-tampering.

---

## 2. Core Architectural Model

```
+---------------------------------------------------------------------------------+
|                        USERSPACE / RING 3 ("JARVIS")                            |
|  - Engine: LangGraph stateful orchestration                                     |
|  - Routing Hub: OpenRouter API (OpenAI-compatible endpoints)                    |
|  - Reasoning: Low-cost Frontier Open-Weight Registry (GLM, DeepSeek, MiniMax)  |
|  - Audio: High-Fidelity Neural TTS (Kokoro-82M / Cartesia / ElevenLabs)         |
|  - Capabilities: Isolated workspace fs, standard web search, read-only tools   |
+----------------------------------------+----------------------------------------+
                                         |
                            Trap Vector  |  `sys_call` / Escalation Trigger
                          (Auth Barrier) |  (Passcode / Secret Key verification)
                                         v
+---------------------------------------------------------------------------------+
|                     KERNEL SPACE / RING 0 ("CARDINAL / DAEMON")                 |
|  - Host Process: `agentd` daemon (Python `asyncio`)                             |
|  - Local Engine: Ollama (port 11434 / MLX backend on Apple Silicon)             |
|  - Memory Constraint: <= 32GB Unified Memory budget (leaves 10GB+ system RAM)   |
|  - Controller: Textual / Rich Terminal UI (TUI)                                 |
|  - Audio: Ultra-low-latency local TTS (Piper TTS / robotic vocoder)             |
|  - IPC / Bus: Unix Domain Socket / Async Event Loop                             |
|  - Syscalls: Model hot-swap, sandbox mounts, process supervision, token logs   |
+---------------------------------------------------------------------------------+

```

---

## 3. Model Architecture & Registry Specifications

### 3.1 Ring 3 (Userspace Model Registry — OpenRouter)

All cloud inference is routed through a single integration point using the **OpenRouter API** (`[https://openrouter.ai/api/v1](https://openrouter.ai/api/v1)`) via `litellm` or `openai-python`.

The registry configuration (`registry.toml` or `models.json`) maps low-cost open-weight models:

| Model Identifier | OpenRouter ID | Primary Role | Cost Profile |
| --- | --- | --- | --- |
| **DeepSeek V4.1 Flash / 1017** | `deepseek/deepseek-v4.1-flash` | Primary conversation, high-speed tool dispatching, code generation | Extreme budget ($0.15–$0.30 / 1M) |
| **GLM 5.3 Flash** | `zhipu/glm-5.3-flash` | Scratchpad planning, agentic workflows, long-context caching | Budget tier / High throughput |
| **MiniMax Text 01 / M3** | `minimax/minimax-01` | Extended context document inspection, deep conversation | Ultra-low cost |
| **Xiaomi MiMo-V2.6 Flash** | `xiaomi/mimo-v2.6-flash` | Low-latency fallback for structured data tasks | Budget tier |

### 3.2 Ring 0 (Kernel SLM — Ollama on 32GB Unified Memory Budget)

Kernel Mode requires rapid, deterministic JSON parsing, fast Time-To-First-Token (TTFT), and a memory footprint that leaves **at least 8–12GB of RAM free** for the daemon, audio processing, and host operating system.

Inference runs locally via **Ollama** (`http://localhost:11434/api/chat` or `/v1`), which leverages native Metal/MLX on Apple Silicon.

```
+---------------------------------------------------------------------------------------+
|  Rank 1: qwen3-coder:30b (4-bit MoE, 3.3B active)                                    |
|  • Footprint: ~19 GB VRAM                                                              |
|  • Why: Dominates SWE-Bench and deterministic tool parsing. Fits inside 32GB RAM    |
|    with ~13GB remaining headroom.                                                     |
+---------------------------------------------------------------------------------------+
|  Rank 2: devstral:24b (Dense)                                                         |
|  • Footprint: ~14 GB VRAM                                                              |
|  • Why: 46.8% SWE-Bench score; purpose-built for agentic harness execution.              |
+---------------------------------------------------------------------------------------+
|  Rank 3: qwen2.5-coder:14b / nemotron-3.5-lightning:30b (Fast fallback)              |
|  • Footprint: ~9–12 GB VRAM                                                            |
|  • Why: Ultra-fast 60+ tok/s response time, zero memory pressure.                     |
+---------------------------------------------------------------------------------------+

```

*(Note: If Ollama's HTTP server overhead introduces latency in your loop, the engine abstraction should allow pointing directly to a resident `mlx-lm` Python process without altering the Syscall interface).*

---

## 4. System Architecture & Components

### 4.1 The Host Daemon (`agentd`)

* **Role:** The supervisory daemon and single source of truth for runtime state, hardware usage, and IPC.
* **Responsibilities:**
* Spawns and supervises the Userspace agent thread/process.
* Manages global configuration (`config.toml` or `system.json`).
* Enforces the privilege boundary and validates escalation tokens/passwords.
* Exposes an IPC interface (Unix Domain Socket or local loopback websocket) for Ring 3 and the TUI.



### 4.2 Ring 3: Userspace Agent Engine (LangGraph)

* **Role:** General user interaction, planning, and task execution.
* **Orchestration:** LangGraph state machine maintaining conversation state, scratchpad, and tool history.
* **Tool Isolation (Least Privilege):**
* All write/read operations are strictly confined to a dedicated working root (e.g., `~/.agent_zero/sandbox/`).
* No access to host environment secrets, API keys, or supervisor process controls.


* **Software Trap Mechanism:**
* When a command requires privileged execution (or the user issues an escalation trigger like `"System Call: Enter Kernel Mode"`), the agent yields a trap via LangGraph `interrupt()`.
* The graph halts state transitions and serializes state to the local checkpointer (SQLite) until Ring 0 authorizes or rejects the trap.



### 4.3 Ring 0: Kernel Mode & Cardinal TUI

* **Role:** Ground-truth administrative inspection, configuration, and manual overrides.
* **Interface:** Full-screen Terminal User Interface (TUI) built with `Textual`.
* *Telemetry Pane:* CPU/RAM (Unified Memory usage), active OpenRouter model, token burn rate, and context window utilization.
* *Process / Tool Matrix:* Table of mounted tools and permission toggles (`[X] Network`, `[ ] Shell_Exec`).
* *Console Input:* Command bar with dual-mode execution (direct CLI commands or natural language intent parsed by the local Ollama SLM).


* **Local Brain:** Ollama client running resident in memory to extract structured system calls from admin prompts.

### 4.4 Dual-Voice Audio Pipeline

* **Ring 3 Voice (Userspace):** Expressive, naturalistic neural voice (e.g., ElevenLabs API or local Kokoro-82M).
* **Ring 0 Voice (Kernel):** Crisp, robotic, ultra-low-latency synthesizer (Piper TTS running local `.onnx` or native system speech).
* **Audio Router:** Centralized audio output sink. Upon Ring 0 escalation:
1. Immediately cuts off and flushes Ring 3 audio streams.
2. Emits an escalation audio cue (mechanical chime / tone).
3. Routes all subsequent feedback through the Ring 0 voice until `sys_exit` is called.



---

## 5. Privilege Boundary & Syscall Interface

All Ring 0 operations are initiated via structured system calls.

| Syscall | Signature | Description |
| --- | --- | --- |
| `sys_auth` | `(passcode: str) -> AuthToken` | Validates credentials and transitions the terminal session into Ring 0. |
| `sys_exit` | `() -> void` | Drops privileges, restores normal audio routing, and resumes Ring 3 loop. |
| `sys_model_swap` | `(target_model_id: str) -> bool` | Hot-swaps the active OpenRouter model identifier in LangGraph. |
| `sys_mount_sandbox` | `(host_path: str, mount_as: str, mode: "ro" | "rw")` | Dynamically maps a host directory into the agent’s restricted file environment. |
| `sys_perm_toggle` | `(capability_id: str, enabled: bool) -> bool` | Enables/disables specific agent tool nodes (e.g., internet search, bash execution). |
| `sys_proc_table` | `() -> List[TaskState]` | Lists pending LangGraph checkpoints, interrupt states, or background sub-agents. |
| `sys_mem_flush` | `(scope: "ephemeral" | "session" | "all")` | Clears working memory or context window caches. |

---

## 6. Implementation Stack (V1 Defaults)

* **Core Runtime:** Python 3.11+ / `asyncio`
* **Agent Framework:** `langgraph` + `langchain-core`
* **Cloud Gateway:** `litellm` pointing to `[https://openrouter.ai/api/v1](https://openrouter.ai/api/v1)` via `OPENROUTER_API_KEY`
* **Local Engine:** `ollama` running `qwen3-coder:30b` (or `qwen2.5-coder:14b`)
* **TUI Engine:** `textual`
* **Storage / Checkpointing:** SQLite via `langgraph-checkpoint-sqlite`
* **Speech-to-Text (STT):** Local `faster-whisper`
* **Text-to-Speech (TTS):**
* Ring 3: Kokoro-82M (local high-quality) or Cartesia/ElevenLabs API
* Ring 0: Piper TTS (local low-latency `.onnx`)



---

## 7. Open Architectural Decisions (For Builder-Agent Collaboration)

The coding agent should prompt for preferences on the following implementation details:

1. **Ollama Execution Mode vs. MLX Direct:**
* *Option A (Ollama):* Standard background service on `localhost:11434`; zero-setup, uses standard OpenAI client SDK.
* *Option B (mlx-lm embedded):* Direct Python in-process binding via MLX; saves ~100ms HTTP loopback overhead on Apple Silicon.


2. **Authentication Factor:**
* *Option A:* Passphrase / PIN prompt inside the TUI upon escalation attempt.
* *Option B:* Spoken verbal passphrase processed via local STT before launching Ring 0.
* *Option C:* Time-based One-Time Password (TOTP) / Authenticator app code.


3. **Sandbox Depth for V1:**
* *Option A:* Pure Python path verification checking that all operations resolve within `~/.agent_zero/sandbox`.
* *Option B:* OS-level sandbox via containerized Docker runner or macOS `sandbox-exec`.


4. **TUI Presentation Mode:**
* *Option A:* Dedicated second terminal window or split-pane tmux session.
* *Option B:* Fullscreen takeover in the current terminal that yields back to a standard log upon `sys_exit`.



---

## 8. Milestone Roadmap

### Phase 1: The Core Trap & Daemon

* Implement `agentd` with state storage and IPC socket.
* Build a minimal LangGraph agent configured with OpenRouter (`deepseek/deepseek-v4.1-flash` or `zhipu/glm-5.3-flash`).
* Implement a `sys_call` tool triggering `interrupt()`, resuming execution via `Command(resume=...)` post-authentication.

### Phase 2: Ollama Kernel & The Cardinal TUI

* Stand up Ollama locally with `qwen3-coder:30b` (or `qwen2.5-coder:14b`).
* Build the `Textual` interface displaying unified memory usage, active OpenRouter model, and token telemetry.
* Connect `sys_model_swap` to dynamically switch userspace across OpenRouter models without restarting the daemon.

### Phase 3: Dual-Voice & Audio Router

* Integrate local STT (`faster-whisper`) on a push-to-talk or wake-phrase loop.
* Set up Piper TTS for Ring 0 voice synthesis and Kokoro-82M/cloud API for Ring 3.
* Implement the audio-cut interrupt mechanism when transitioning from Ring 3 to Ring 0.

### Phase 4: Sandboxing & Tool Verification

* Lock down Ring 3 file tools to a sandboxed directory.
* Wire up permission toggles in the TUI to dynamically enable/disable tools in the LangGraph graph.

---

### Immediate Verification Steps for Implementation

1. Verify Ollama inference locally:
```bash
ollama run qwen3-coder:30b "Extract JSON: Change model to deepseek-v4.1"

```


2. Verify OpenRouter access:
```bash
curl https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek/deepseek-v4.1-flash", "messages": [{"role": "user", "content": "ping"}]}'

```


3. Hand this PRD to your coding agent to scaffold the Phase 1 `agentd` daemon and LangGraph interrupt loop.