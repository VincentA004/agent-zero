# Agent Zero model options

Research checked 2026-10-23. Prices are OpenRouter's listed standard rates per million tokens at the time checked; individual providers, routing choices, cache rates, and prices can vary. OpenRouter pages are used for catalog IDs, price, context, and API capability claims. I did not treat vendor benchmark claims as evidence of Agent Zero performance.

## Cloud models on OpenRouter

| Candidate | OpenRouter model ID | Input / output per 1M | Context / max output | Tools and JSON schema | Notes |
|---|---|---:|---:|---|---|
| GLM 5.3 Flash | `z-ai/glm-5.3-flash` | $0.0052 / $0.1985 | 1,048,576 / 1,048,576 | Yes / yes | Lowest listed input and output price among the checked options; native text, image, and video inputs. [Catalog page](https://openrouter.ai/z-ai/glm-5.3-flash). |
| DeepSeek V4.1 Flash | `deepseek/deepseek-v4.1-flash` | $0.07 / $0.50 | 1,048,576 / 1,048,576 | Yes / yes | OpenRouter describes it as a 552B sparse MoE with 8B active on input and 16B on output. [Catalog page](https://openrouter.ai/deepseek/deepseek-v4.1-flash). |
| Xiaomi MiMo V2.6 Flash | `xiaomi/mimo-v2.6-flash` | $0.10 / $0.28 | 1,050,000 / 131,072 | Yes / yes | OpenRouter lists multimodal inputs including audio and video. [Catalog page](https://openrouter.ai/xiaomi/mimo-v2.6-flash). |
| MiniMax M3.1 Flash | `minimax/minimax-m3.1-flash` | $0.10 / $0.50 | 1,048,576 / 524,288 | Yes / yes | Current Flash option; OpenRouter says its free launch period ended Oct 22, 2026, so do not budget it as free. [Catalog page](https://openrouter.ai/minimax/minimax-m3.1-flash). |
| MiniMax M3 | `minimax/minimax-m3` | $0.23 / $0.96 | 1,048,576 / 262,144 | Yes / yes | Valid catalog entry, but M3.1 Flash is newer and cheaper on input. [Catalog page](https://openrouter.ai/minimax/minimax-m3). |
| Qwen3.6 35B A3B | `qwen/qwen3.6-35b-a3b` | $0.05 / $0.70 | 262,144 / 32,768 | Yes / yes | Open-weight Apache 2.0; 35B total, 3B active; 1M context requires YaRN extension. [Catalog page](https://openrouter.ai/qwen/qwen3.6-35b-a3b). |

For a comparable 100k input + 20k output request, listed token charges work out to roughly $0.0045 for GLM 5.3 Flash, $0.017 for DeepSeek V4.1 Flash, $0.0156 for MiMo V2.6 Flash, $0.020 for MiniMax M3.1 Flash, $0.042 for MiniMax M3, and $0.019 for Qwen3.6 35B A3B. This excludes caching and any other platform charges.

### ID corrections and selection

The PRD's candidate names are substantially valid, with these exact IDs: DeepSeek V4.1 Flash, GLM 5.3 Flash, and MiMo V2.6 Flash. MiniMax M3 exists under its own ID, but MiniMax M3.1 Flash is the newer Flash variant currently listed. The IDs above are OpenRouter's IDs, not vendor-native API names. Treat any model alias (for example a `latest` route) as mutable.

For a cheap default to try in Agent Zero, start with **GLM 5.3 Flash**: its listed price is dramatically lower and its OpenRouter entry explicitly advertises function calling and schema-constrained JSON. Keep **DeepSeek V4.1 Flash** as a comparison option for a more expensive alternative, and **MiMo V2.6 Flash** if its multimodal inputs matter. This is a shortlist based on verifiable catalog capability and cost, not a quality ranking; the provider's published benchmarks are not a substitute for an Agent Zero harness evaluation.

All three short-listed entries have long contexts, but that does not mean a long prompt is cheap: output tokens cost more than input, particularly on DeepSeek. OpenRouter confirms tool and JSON-schema API support, but tool success and behavior can differ among providers. Pin or exclude a provider when repeatability matters, and retest when switching routes.

## Local models on an assumed 32 GB Apple Silicon Mac

I interpret “32 GB unified memory budget” conservatively as **32 GB total physical unified memory**, including macOS, Agent Zero, other applications, model weights, and KV cache. The user clarified a target of **8–16 GB for local inference**. That allowance must cover weights, KV cache, and runtime overhead. A 4B model is the conservative baseline near 8 GB; a 9B model is a candidate nearer 12–16 GB. Context length is a maximum capability, not a safe local context target.

| Local candidate | Published Ollama artifact size | Recommendation for 32 GB total |
|---|---:|---|
| `qwen3.5:4b` | 3.3–4.0 GB | Safest fit if only 8–12 GB is free before loading; leaves more room for KV cache and runtime overhead. |
| `qwen3.5:9b` | 6.6–7.6 GB | Better capability when at least about 12 GB is free before loading, with modest context and no large competing workloads. |
| Direct MLX `mlx-community/Qwen3.5-9B-MLX-4bit` | 5.98 GB files | Direct-MLX candidate for 8–16 GB; model page demonstrates use via `mlx-vlm`, separate from Ollama's API. |
| `gemma4:12b` | 7.7–8.0 GB | A 12B choice for the 16 GB end of the target, with modest context. Google documents native function calling. |
| `qwen3:14b` | 9.3 GB | A 14B choice for the 16 GB end of the target, with modest context; older model family and 40K advertised context. |
| `qwen3.5:9b-mlx` | 8.9 GB | Alternative Ollama registry artifact labelled MLX; leaves very little headroom at 8–12 GB free. |
| `qwen3.5:27b` | 17–20 GB | Tight. Weights alone may consume nearly all available memory after OS/apps; only consider with a small context and a quiet machine. |
| `qwen3.5:27b-mlx` | 20 GB | Tighter still at 32 GB total; not the safe default. |
| `qwen3.5:35b` | 22 GB | Avoid under this assumption. Ollama's own MLX launch post says the 35B-A3B coding model requires a Mac with **more than 32 GB** unified memory. |

Ollama publishes these sizes and advertised context values in its [Qwen3.5 library](https://ollama.com/library/qwen3.5), [Qwen3 library](https://ollama.com/library/qwen3), and [Gemma 4 library](https://ollama.com/library/gemma4). The 128K–256K catalog context numbers must not be interpreted as fitting in the listed weight size. KV-cache use depends on architecture, context actually allocated, and number of concurrent sessions; reserve several GB for it. At 8 GB free, start with 4B. Consider 9B at 12 GB free; consider 12B or 14B only near 16 GB free and with a short context, then measure memory pressure.

### Ollama Metal versus MLX

These are two separate ways to run locally:

- **Ollama on Apple Silicon** is an app/server with its own model registry and API. Ollama's recent macOS releases describe an **MLX-powered engine** for Apple Silicon; model tags ending in `-mlx` are specific published Ollama artifacts. Use Ollama's model size and engine support as listed for the exact tag, rather than assuming every Ollama artifact is a generic GGUF/Metal model. See [Ollama's MLX announcement](https://ollama.com/blog/mlx) and [GGUF support note](https://ollama.com/blog/improved-performance-and-model-support-with-gguf), which describes GGUF via llama.cpp alongside the MLX engine.
- **Direct MLX inference** means loading an MLX-converted model through an Apple MLX package such as [`mlx-lm`](https://github.com/ml-explore/mlx-lm) or `mlx-vlm`. For example, the [`mlx-community/Qwen3.5-9B-MLX-4bit` artifact](https://huggingface.co/mlx-community/Qwen3.5-9B-MLX-4bit) is listed at 5.98 GB and demonstrates an `mlx-vlm` path. This is a separate runtime/API from Ollama; an Ollama `-mlx` tag is not automatically interchangeable with an MLX-LM checkpoint.

Ollama's API supports tool calls and JSON-schema structured output at the runtime level ([tool calling](https://ollama.com/blog/tool-support), [structured outputs](https://ollama.com/blog/structured-outputs)). The model still needs a suitable chat template and training for reliable tool use. Qwen's official [Qwen3.5-9B model card](https://huggingface.co/Qwen/Qwen3.5-9B) documents serving tool-call configuration, and Google's [Gemma 4 function-calling guide](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4) documents native function calling for 12B. For Agent Zero, start with **Ollama `qwen3.5:4b`** if near an 8 GB budget; use Ollama `qwen3.5:9b` or the direct MLX 9B Q4 artifact nearer 12–16 GB. Consider 12B/14B only at the top end with a short context.

## Unknowns to carry into an Agent Zero evaluation

- No Agent Zero task set or live calls were run, so relative tool reliability, coding quality, latency, uptime, and final cost are unknown.
- OpenRouter prices and served providers can change. Recheck the model pages and provider table immediately before setting defaults.
- Context claims are provider/model ceilings. Budget cost and memory for the actual prompt, completion, KV cache, and concurrency.
- Model names in product briefs are not a stable API contract. Store the exact provider ID, date checked, and routing policy with the configuration.
- Local recommendations assume 32 GB total memory and an 8–16 GB model/runtime footprint. Exact KV-cache use and throughput remain hardware- and context-dependent.
