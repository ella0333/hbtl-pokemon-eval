# Model options

Which models work with the benchmark, what they need to support, and how to configure them.

## The short version

Any **instruct model** served through an **OpenAI-compatible API** (`/v1/chat/completions`)
will work, as long as it meets two requirements: it must support **structured output**
(JSON schema responses) and it must support **custom system prompts**. The benchmark
relies on both.

## Supported backends

| Backend | Type | Structured output | Notes |
|---|---|---|---|
| [LM Studio](https://lmstudio.ai) | Local | Grammar-constrained | Recommended for local. Output literally cannot violate the schema. |
| [Ollama](https://ollama.com) | Local | Grammar-constrained | Same grammar enforcement as LM Studio. |
| [llama.cpp server](https://github.com/ggerganov/llama.cpp) | Local | Grammar-constrained | Direct llama.cpp HTTP server. |
| [vLLM](https://github.com/vllm-project/vllm) | Local/Cloud | Grammar-constrained | High-throughput serving. |
| [OpenAI](https://platform.openai.com) | Cloud (paid) | API-validated | Works well. |
| [Anthropic](https://console.anthropic.com) | Cloud (paid) | API-validated | Claude models, directly or through OpenRouter. |
| [OpenRouter](https://openrouter.ai) | Cloud (paid) | Varies by model | Routes to many providers. Check that your chosen model supports structured output. |
| Any OpenAI-compatible endpoint | Either | Varies | If it speaks `/v1/chat/completions`, it should work. |

## Model requirements

### Structured output

Every turn the model picks from a lettered menu, and its answer has to match a strict
JSON schema whose choice field only allows the letters on screen. On grammar-constrained
backends (LM Studio, Ollama, llama.cpp), the output literally cannot violate the schema.
On cloud APIs, the harness checks every answer and asks again, up to five times, when it
doesn't match.

**Models without structured output are not compatible.** There is no fallback to plain
JSON mode or to describing the format in the prompt, because results from those modes
wouldn't be comparable. Setup's connection check sends one request with the game's schema
and tells you if the model or provider can't handle it.

### System prompt support

The rules of the menu and the current area are given in a system prompt that changes
between indoors and outdoors. **Your model must properly support the system role in the
chat completions format.** Models that ignore or poorly handle custom system prompts will
not work. For example, older Gemma models (Gemma 1, Gemma 2) did not support system
prompts at all and would silently ignore them.

### Reasoning models are not required

When structured output is enabled, most backends turn off the model's native reasoning
mode, since the output must conform to the JSON schema. The benchmark handles this by
including a `thinking` field in every schema, so the model has a place to reason within
the structured answer itself. Any instruct model that meets the requirements above will
work.

## Configuration reference

All model settings live in `config.yaml` under `provider` and `model_params`, and setup
writes them for you.

```yaml
provider:
  kind: openai_compat
  base_url: https://openrouter.ai/api/v1
  api_key_env: MODEL_API_KEY       # env var name holding your API key ("" = none)
  model: openai/gpt-5.6-luna
  request_timeout: 300             # seconds before a request times out
model_params:
  temperature: 0.7
  top_p: 0.95
  max_tokens: 3000
prices:                            # used when the provider doesn't report cost
  input_per_million: 0.0
  output_per_million: 0.0
```

The API key itself goes in `.env`, never in `config.yaml`.

### The model identifier

The format depends on how you're connecting:

| Connection type | Format | Example |
|---|---|---|
| **Local** (LM Studio, Ollama) | Identifier as shown by the server | `qwen2.5-14b-instruct`, `unsloth/magistral-small-2509` |
| **Aggregator** (OpenRouter) | `publisher/model-name` | `openai/gpt-5.6-luna`, `anthropic/claude-sonnet-4` |
| **Direct API** (OpenAI, Anthropic) | Just the model name, no prefix | `gpt-5.6-luna`, `claude-sonnet-4` |

A common mistake is using `openai/gpt-5.6-luna` (the OpenRouter format) when connecting
directly to `api.openai.com`, which expects just `gpt-5.6-luna`. The harness handles this
automatically: if the API returns an "invalid model ID" error and your model name contains
a `/`, it strips the prefix and retries, and setup's connection check saves the corrected
name to `config.yaml`.

In LM Studio, the model identifier is shown in the Developer tab when the server is
running. In Ollama, it's the name you pulled (e.g. `qwen2.5:14b`).

### Provider-specific parameters

Some parameters (like `top_k` or `repeat_penalty`) are supported by local backends but not
by cloud APIs. If the API returns an "unknown parameter" error, the harness removes the
parameter and retries, and setup's connection check removes it from `config.yaml`.

### Busy providers

Free and busy endpoints regularly answer `429 rate-limited` or `503 overloaded`. The
harness waits and asks again (5 seconds, then 10, 20, 40 and 80) without counting it as
one of the model's answers. If the provider still isn't answering after that, the run
pauses and can be resumed later.
