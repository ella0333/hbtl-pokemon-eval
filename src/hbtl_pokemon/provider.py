"""OpenAI-compatible model client with strict JSON schema output.

Follows the same conventions as the standalone harnesses in the module
family: the schema is always sent as a strict json_schema response format
(forced tool use on the Anthropic API), a model id with a rejected publisher
prefix is retried without it, and a rejected parameter is renamed or dropped.
There is no fallback to plain JSON mode or prompting. A model that can't
return schema-valid JSON is not compatible with the benchmark.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from urllib.parse import urlparse

import httpx

# Hosts that speak the Anthropic Messages API instead of OpenAI chat completions.
_ANTHROPIC_HOSTS = {"api.anthropic.com"}

# Regexes to extract the offending parameter from provider error messages.
_BAD_PARAM_RE = re.compile(
    r"[Uu]n(?:known|supported) (?:parameter|value):?\s*'?\"?(\w+)"
)
_BACKTICK_PARAM_RE = re.compile(r"`(\w+)`")

# Parameters that have a known alternate name on certain providers.
_PARAM_ALIASES: dict[str, str] = {
    "max_tokens": "max_completion_tokens",
}

# Never remove these from the payload, even on error.
_ESSENTIAL_PARAMS = {"model", "messages", "stream", "response_format"}

# Maximum number of parameter-fix retries per call.
_MAX_PARAM_RETRIES = 6

# How many model answers a single decision gets before it is given up.
MAX_GENERATE_ATTEMPTS = 5

# Waiting out rate limits and server errors. These waits never use up one of
# the answer attempts, since the model never answered.
TRANSIENT_RETRIES = 5
TRANSIENT_BACKOFF = 5.0
TRANSIENT_BACKOFF_MAX = 120.0
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
_TRANSIENT_WORDS = ("overloaded", "rate limit", "rate-limit", "temporarily",
                    "timeout", "timed out", "try again")

# Regex to extract a JSON object from potentially wrapped text.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Harmony-style channel tags some models leak into output.
_HARMONY_RE = re.compile(r"<\|[^|]*\|>")

# Placeholder content pattern.
_PLACEHOLDER_RE = re.compile(r"^[\[\(]?[.…]{2,}[\]\)]?\.?$")


class CallCapReached(Exception):
    """Raised when the run has used its whole model-call budget."""


def say(text: str) -> None:
    """Print a status line, ASCII-safe on any console."""
    line = f"[system] {text}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        import sys
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, errors="replace").decode(enc), flush=True)


def is_anthropic_host(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _ANTHROPIC_HOSTS)


def _parse_json(content: str) -> dict | None:
    """Parse JSON from model output, with regex fallback for chatty models."""
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(content)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def validate(content: str, schema: dict) -> tuple[dict | None, str]:
    """Check a reply against the schema. Returns (parsed, "") or (None, reason)."""
    parsed = _parse_json(content or "")
    if parsed is None:
        return None, "not valid JSON"
    for key, val in list(parsed.items()):
        if isinstance(val, str):
            parsed[key] = _HARMONY_RE.sub("", val).strip()
    props = schema.get("properties", {})
    for field in schema.get("required", []):
        if field not in parsed:
            return None, f"missing field '{field}'"
    for field, spec in props.items():
        if field not in parsed:
            continue
        val = parsed[field]
        if spec.get("type") == "string" and not isinstance(val, str):
            return None, f"'{field}' is not a string"
        if "enum" in spec and val not in spec["enum"]:
            return None, f"'{field}' is {val!r}, not one of {spec['enum']}"
        if field != "thinking" and isinstance(val, str) and (
                not val.strip() or _PLACEHOLDER_RE.match(val.strip())):
            return None, f"'{field}' is empty"
        if "maxLength" in spec and isinstance(val, str) and len(val) > spec["maxLength"]:
            parsed[field] = val[:spec["maxLength"]]
    return parsed, ""


class Provider:
    """One model endpoint, with the run's call cap and token and cost totals."""

    def __init__(self, config: dict, call_log=None):
        self.config = config
        p = config["provider"]
        self.base_url = p["base_url"].rstrip("/")
        self.model = p["model"]
        self.api_key = os.environ.get(p.get("api_key_env") or "", "") if p.get("api_key_env") else ""
        self.timeout = float(p.get("request_timeout", 300))
        self.params = dict(config.get("model_params", {}) or {})
        prices = config.get("prices", {}) or {}
        self.price_in = float(prices.get("input_per_million", 0) or 0)
        self.price_out = float(prices.get("output_per_million", 0) or 0)
        self.max_calls = int(config.get("run", {}).get("call_cap", 0) or 0)
        self.is_anthropic = is_anthropic_host(self.base_url)
        self.no_think = bool((p.get("quirks") or {}).get("no_think_suffix")) or "qwen" in self.model.lower()

        self.blocked_params: set[str] = set()
        self.param_renames: dict[str, str] = {}
        self.model_override: str | None = None
        self.call_log = call_log  # callable(dict) or None

        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.reasoning_tokens = 0
        self.cost = 0.0
        self.reported_cost_calls = 0

    # ── payloads ────────────────────────────────────────────────────────

    def _openai_payload(self, messages: list, schema: dict) -> dict:
        candidates = {
            "temperature": self.params.get("temperature", 0.7),
            "top_p": self.params.get("top_p", 0.95),
            "max_tokens": self.params.get("max_tokens", 3000),
        }
        if self.params.get("top_k"):
            candidates["top_k"] = self.params["top_k"]
        if self.params.get("repeat_penalty"):
            candidates["repeat_penalty"] = self.params["repeat_penalty"]
        payload = {"model": self.model_override or self.model,
                   "messages": messages, "stream": False}
        for key, value in candidates.items():
            if key not in self.blocked_params:
                payload[key] = value
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "strict": True, "schema": schema},
        }
        for old, new in self.param_renames.items():
            if old in payload:
                payload[new] = payload.pop(old)
        return payload

    def _anthropic_payload(self, messages: list, schema: dict) -> dict:
        system_parts: list[str] = []
        merged: list[dict] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
                continue
            content = msg["content"]
            if isinstance(content, list):
                blocks = []
                for part in content:
                    if part.get("type") == "image_url":
                        uri = part["image_url"]["url"]
                        media, data = uri[5:].split(";base64,", 1)
                        blocks.append({"type": "image", "source": {
                            "type": "base64", "media_type": media, "data": data}})
                    else:
                        blocks.append({"type": "text", "text": part.get("text", "")})
                content = blocks
            if merged and merged[-1]["role"] == msg["role"] and isinstance(content, str) \
                    and isinstance(merged[-1]["content"], str):
                merged[-1]["content"] += "\n\n" + content
            else:
                merged.append({"role": msg["role"], "content": content})
        if not merged or merged[0]["role"] != "user":
            merged.insert(0, {"role": "user", "content": "(continue)"})
        payload = {
            "model": self.model_override or self.model,
            "messages": merged,
            "max_tokens": self.params.get("max_tokens", 3000),
            "stream": False,
            "tools": [{"name": "response",
                       "description": "Respond with the required structured data.",
                       "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": "response"},
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if "temperature" not in self.blocked_params:
            payload["temperature"] = self.params.get("temperature", 0.7)
        return payload

    def _request(self, messages: list, schema: dict) -> tuple[str, dict, dict]:
        if self.is_anthropic:
            url = f"{self.base_url}/v1/messages"
            headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
            if self.api_key:
                headers["x-api-key"] = self.api_key
            return url, headers, self._anthropic_payload(messages, schema)
        base = self.base_url if self.base_url.endswith("/v1") else self.base_url + "/v1"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return f"{base}/chat/completions", headers, self._openai_payload(messages, schema)

    # ── fixes ───────────────────────────────────────────────────────────

    def _try_fix_param(self, error_text: str, payload: dict) -> bool:
        m = _BAD_PARAM_RE.search(error_text) or _BACKTICK_PARAM_RE.search(error_text)
        if not m:
            return False
        bad = m.group(1)
        if bad in _ESSENTIAL_PARAMS:
            return False
        alias = _PARAM_ALIASES.get(bad)
        if alias and bad not in self.param_renames:
            self.param_renames[bad] = alias
            if bad in payload:
                payload[alias] = payload.pop(bad)
            say(f"Parameter '{bad}' not supported; using '{alias}' instead")
            return True
        if bad not in self.blocked_params:
            self.blocked_params.add(bad)
            payload.pop(bad, None)
            payload.pop(self.param_renames.get(bad, ""), None)
            say(f"Parameter '{bad}' not supported; removed")
            return True
        return False

    # ── one HTTP exchange, with fixes and transient waits ───────────────

    async def _post(self, client: httpx.AsyncClient, messages: list, schema: dict):
        """Returns (data, None) on a 200, or (None, error_text)."""
        url, headers, payload = self._request(messages, schema)
        waits = 0
        fixes = 0
        while True:
            try:
                resp = await client.post(url, json=payload, headers=headers, timeout=self.timeout)
                status, text = resp.status_code, resp.text
            except httpx.HTTPError as e:
                status, text = 0, f"{type(e).__name__}: {e}"

            lower = text.lower()
            if status in (400, 404, 422) and fixes < _MAX_PARAM_RETRIES:
                if ("invalid model" in lower or "not_found" in lower
                        or "could not resolve" in lower or "not a valid model" in lower):
                    current = payload.get("model", "")
                    if "/" in current:
                        stripped = current.split("/", 1)[1]
                        say(f"Model '{current}' rejected; retrying as '{stripped}'")
                        self.model_override = stripped
                        payload["model"] = stripped
                        fixes += 1
                        continue
                if any(w in lower for w in ("unknown parameter", "unsupported parameter",
                                            "unsupported value", "deprecated", "not supported")):
                    if self._try_fix_param(text[:500], payload):
                        fixes += 1
                        continue

            if status == 200:
                try:
                    data = resp.json()
                except ValueError:
                    data = None
                if isinstance(data, dict) and "error" in data and "choices" not in data \
                        and "content" not in data:
                    err = data["error"]
                    text = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    status = int(err.get("code", 0) or 0) if isinstance(err, dict) and str(err.get("code", "")).isdigit() else 0
                    lower = text.lower()
                elif data is not None:
                    return data, None

            transient = status == 0 or status in _TRANSIENT_STATUS or any(w in lower for w in _TRANSIENT_WORDS)
            if transient and waits < TRANSIENT_RETRIES:
                delay = min(TRANSIENT_BACKOFF * (2 ** waits), TRANSIENT_BACKOFF_MAX)
                delay *= 1 + random.uniform(-0.25, 0.25)
                waits += 1
                say(f"Provider busy or unreachable ({status or 'no response'}); "
                    f"waiting {delay:.0f}s before asking again")
                await asyncio.sleep(delay)
                continue
            return None, f"HTTP {status}: {text[:300]}"

    def _content_and_usage(self, data: dict) -> tuple[str, dict]:
        usage = data.get("usage") or {}
        if self.is_anthropic:
            content = ""
            for block in data.get("content", []):
                if block.get("type") == "tool_use":
                    content = json.dumps(block.get("input", {}))
                    break
                if block.get("type") == "text":
                    content = block.get("text", "")
            tin = int(usage.get("input_tokens", 0) or 0)
            tout = int(usage.get("output_tokens", 0) or 0)
            return content, {"in": tin, "out": tout, "reasoning": 0, "cost": None}
        try:
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            if not content and msg.get("reasoning_content"):
                content = msg["reasoning_content"]
        except (KeyError, IndexError, TypeError):
            content = ""
        details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        cost = usage.get("cost")
        return content, {
            "in": int(usage.get("prompt_tokens", 0) or 0),
            "out": int(usage.get("completion_tokens", 0) or 0),
            "reasoning": int(details.get("reasoning_tokens", 0) or 0),
            "cost": float(cost) if isinstance(cost, (int, float)) else None,
        }

    def _add_usage(self, usage: dict) -> float:
        self.tokens_in += usage["in"]
        self.tokens_out += usage["out"]
        self.reasoning_tokens += usage["reasoning"]
        if usage["cost"] is not None:
            cost = usage["cost"]
            self.reported_cost_calls += 1
        else:
            cost = usage["in"] / 1e6 * self.price_in + usage["out"] / 1e6 * self.price_out
        self.cost += cost
        return cost

    # ── public ──────────────────────────────────────────────────────────

    async def generate(self, client: httpx.AsyncClient, messages: list, schema: dict,
                       turn: int) -> tuple[dict | None, str]:
        """Ask for one decision. Returns (parsed, "") or (None, error).

        Every request the model answered counts as a call, including answers
        that were rejected and retried.
        """
        if self.no_think and messages and messages[-1]["role"] == "user" \
                and isinstance(messages[-1]["content"], str):
            messages = messages[:-1] + [{"role": "user",
                                         "content": messages[-1]["content"] + " /no_think"}]
        last_error = ""
        for attempt in range(1, MAX_GENERATE_ATTEMPTS + 1):
            if self.max_calls and self.calls >= self.max_calls:
                raise CallCapReached(f"call cap of {self.max_calls} reached")
            started = time.time()
            data, error = await self._post(client, messages, schema)
            if data is None:
                self._log(turn, attempt, messages, schema, None, None, error, started)
                return None, error
            self.calls += 1
            content, usage = self._content_and_usage(data)
            cost = self._add_usage(usage)
            parsed, reason = validate(content, schema)
            self._log(turn, attempt, messages, schema, data, {**usage, "cost": cost},
                      reason, started)
            if parsed is not None:
                return parsed, ""
            last_error = f"reply rejected: {reason}"
            say(f"Response rejected ({reason}), asking again")
        return None, last_error

    def _log(self, turn, attempt, messages, schema, data, usage, error, started):
        if not self.call_log:
            return
        self.call_log({
            "turn": turn,
            "attempt": attempt,
            "call": self.calls if data is not None else None,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
            "seconds": round(time.time() - started, 2),
            "model": self.model_override or self.model,
            "request": {"messages": messages, "schema": schema},
            "response": data,
            "usage": usage,
            "error": error or None,
        })

    def totals(self) -> dict:
        return {
            "calls": self.calls,
            "tokens": {"in": self.tokens_in, "out": self.tokens_out,
                       "reasoning": self.reasoning_tokens},
            "cost_usd": round(self.cost, 6),
            "reported_cost_calls": self.reported_cost_calls,
            "cost_source": ("provider" if self.reported_cost_calls == self.calls and self.calls
                            else "config prices" if not self.reported_cost_calls
                            else "mixed"),
        }

    def restore(self, totals: dict) -> None:
        self.calls = int(totals.get("calls", 0))
        t = totals.get("tokens", {})
        self.tokens_in = int(t.get("in", 0))
        self.tokens_out = int(t.get("out", 0))
        self.reasoning_tokens = int(t.get("reasoning", 0))
        self.cost = float(totals.get("cost_usd", 0.0))
        self.reported_cost_calls = int(totals.get("reported_cost_calls", 0))
