"""LLM providers behind one small interface. Each keeps its own native conversation history (no lossy conversion).

* anthropic / bedrock -> official Anthropic SDK (`anthropic`): `Anthropic` or `AnthropicBedrockMantle` (Bedrock signs
  with the AWS role assumed in the terminal). Claude refusal fallbacks are on by default: server-side
  (`fallbacks="default"`, beta server-side-fallback-2026-07-01) on the Anthropic API; the SDK's
  BetaRefusalFallbackMiddleware + one BetaFallbackState per conversation on Bedrock.
* openai / azure / openai_compatible -> official OpenAI SDK (`openai`), Chat Completions + function calling.

SDKs are optional extras: pip install 'spendlens[ai]'.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Config
from .tools import ToolResult, ToolSpec

ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_BEDROCK_FALLBACK = "anthropic.claude-opus-4-8"


class AIError(RuntimeError):
    """Configuration or provider problem shown to the user (not to the model)."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the model sent unparseable JSON
    raw_arguments: str = ""


@dataclass
class Turn:
    text: str
    calls: list[ToolCall]
    stop_reason: str
    model: str
    refused: bool = False
    notes: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class Provider(Protocol):
    label: str

    def start(self, system: str, tools: list[ToolSpec]) -> None: ...
    def send_user(self, text: str) -> Turn: ...
    def send_tool_results(self, results: list[ToolResult], final: bool = False) -> Turn: ...


# ------------------------------------------------------------------ Anthropic SDK (Claude API / Bedrock)


def _echo_content(content: list[Any]) -> list[Any]:
    """Assistant content to send back. After a refusal fallback, thinking / tool_use blocks before the last
    `fallback` block must be omitted (the `fallback` block itself stays in place)."""
    idx = max((i for i, b in enumerate(content) if getattr(b, "type", None) == "fallback"), default=-1)
    if idx < 0:
        return list(content)
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(content) if i >= idx or getattr(b, "type", None) not in drop]


class AnthropicProvider:
    def __init__(self, client: Any, model: str, cfg: Config, *, server_fallbacks: bool, fallback_state: Any = None,
                 label: str = "anthropic"):
        self.client, self.model, self.cfg = client, model, cfg
        self.server_fallbacks = server_fallbacks
        self.fallback_state = fallback_state  # BetaFallbackState for the middleware path (one per conversation)
        self.label = label
        self.messages: list[dict[str, Any]] = []
        self.system: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []

    def start(self, system: str, tools: list[ToolSpec]) -> None:
        self.system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self.tools = [{"name": t.name, "description": t.description, "input_schema": t.parameters, "strict": True}
                      for t in tools]

    def _request(self, final: bool = False) -> Turn:
        llm = self.cfg.llm
        kwargs: dict[str, Any] = {"model": self.model, "max_tokens": llm.max_tokens, "system": self.system,
                                  "tools": self.tools, "messages": self.messages}
        if final:  # out of tool steps: the model must answer in text
            kwargs["tool_choice"] = {"type": "none"}
        if llm.effort:
            kwargs["output_config"] = {"effort": llm.effort}
        if self.server_fallbacks:
            kwargs["betas"] = [ANTHROPIC_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        if self.fallback_state is not None:
            with self.fallback_state:
                resp = self.client.beta.messages.create(**kwargs)
        else:
            resp = self.client.beta.messages.create(**kwargs)
        return self._parse(resp)

    def _parse(self, resp: Any) -> Turn:
        content = list(resp.content or [])
        notes = []
        for b in content:
            if getattr(b, "type", None) == "fallback":
                frm, to = getattr(b, "from_", None), getattr(b, "to", None)
                notes.append(f"{getattr(frm, 'model', '?')} declined; {getattr(to, 'model', '?')} continued")
        usage = getattr(resp, "usage", None)
        turn = Turn(
            text="".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"),
            calls=[ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else None, json.dumps(b.input, default=str))
                   for b in content if getattr(b, "type", None) == "tool_use"],
            stop_reason=str(resp.stop_reason), model=str(getattr(resp, "model", self.model)), notes=notes,
            input_tokens=getattr(usage, "input_tokens", 0) or 0, output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        if turn.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            turn.refused = True
            turn.text = turn.text or ("The model declined this request"
                                      + (f" ({details.category})" if getattr(details, "category", None) else "") + ".")
            return turn  # a refused turn is not echoed back
        if turn.stop_reason == "max_tokens":
            turn.notes.append("response hit max_tokens; raise llm.max_tokens for longer answers")
        self.messages.append({"role": "assistant", "content": _echo_content(content)})
        return turn

    def send_user(self, text: str) -> Turn:
        self.messages.append({"role": "user", "content": text})
        return self._request()

    def send_tool_results(self, results: list[ToolResult], final: bool = False) -> Turn:
        # all results for one assistant turn go back in a single user message
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
            for r in results]})
        return self._request(final)


# ------------------------------------------------------------------ OpenAI SDK (OpenAI / Azure / compatible)


class OpenAIProvider:
    def __init__(self, client: Any, model: str, cfg: Config, *, family: str):
        self.client, self.model, self.cfg, self.family = client, model, cfg, family
        self.label = family
        self.messages: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []

    def start(self, system: str, tools: list[ToolSpec]) -> None:
        self.messages = [{"role": "system", "content": system}]
        self.tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                        "parameters": t.parameters}} for t in tools]

    def _request(self, final: bool = False) -> Turn:
        llm = self.cfg.llm
        kwargs: dict[str, Any] = {"model": self.model, "messages": self.messages, "tools": self.tools,
                                  "tool_choice": "none" if final else "auto"}  # final: out of tool steps, answer now
        # newer OpenAI/Azure models take max_completion_tokens; many compatible servers only know max_tokens
        kwargs["max_tokens" if self.family == "openai_compatible" else "max_completion_tokens"] = llm.max_tokens
        if llm.temperature is not None:
            kwargs["temperature"] = llm.temperature
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        calls = []
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = None
            calls.append(ToolCall(tc.id, tc.function.name, args if isinstance(args, dict) else None, raw))
        usage = getattr(resp, "usage", None)
        turn = Turn(text=msg.content or "", calls=calls, stop_reason=str(choice.finish_reason),
                    model=str(getattr(resp, "model", self.model)),
                    input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(usage, "completion_tokens", 0) or 0)
        if getattr(msg, "refusal", None):
            turn.refused, turn.text = True, msg.refusal
            return turn
        if turn.stop_reason == "length":
            turn.notes.append("response hit the token limit; raise llm.max_tokens for longer answers")
        entry: dict[str, Any] = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            entry["tool_calls"] = [{"id": c.id, "type": "function",
                                    "function": {"name": c.name, "arguments": c.raw_arguments}} for c in calls]
        self.messages.append(entry)
        return turn

    def send_user(self, text: str) -> Turn:
        self.messages.append({"role": "user", "content": text})
        return self._request()

    def send_tool_results(self, results: list[ToolResult], final: bool = False) -> Turn:
        for r in results:
            self.messages.append({"role": "tool", "tool_call_id": r.call_id,
                                  "content": ("ERROR: " if r.is_error else "") + r.content})
        return self._request(final)


# ------------------------------------------------------------------ factory


def _key(env_name: str | None, default_env: str, required: bool) -> str | None:
    name = env_name or default_env
    val = os.environ.get(name)
    if required and not val:
        raise AIError(f"Set the {name} environment variable (or llm.api_key_env to the name of the variable "
                      f"holding the key).")
    return val


def build_provider(cfg: Config) -> Provider:
    llm = cfg.llm
    p = llm.provider
    try:
        if p in ("anthropic", "bedrock"):
            import anthropic
        else:
            import openai
    except ImportError as e:
        raise AIError(f"The {p} provider needs the AI extras: pip install 'spendlens[ai]' ({e.name} missing).") from e

    if p == "anthropic":
        kwargs: dict[str, Any] = {}
        if key := _key(llm.api_key_env, "ANTHROPIC_API_KEY", required=False):
            kwargs["api_key"] = key  # else: ANTHROPIC_AUTH_TOKEN / `ant auth login` profile, resolved by the SDK
        if llm.base_url:
            kwargs["base_url"] = llm.base_url
        return AnthropicProvider(anthropic.Anthropic(**kwargs), llm.model, cfg, server_fallbacks=llm.fallbacks)
    if p == "bedrock":
        region = llm.region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not region:  # never guess: a wrong region sends the request (and its data) somewhere unintended
            raise AIError("Bedrock needs a region: set llm.region in finops.yaml or export AWS_REGION.")
        kwargs = {"aws_region": region}
        if cfg.aws.profile:
            kwargs["aws_profile"] = cfg.aws.profile
        state = None
        if llm.fallbacks:  # Bedrock has no server-side fallbacks: SDK middleware + per-conversation state
            kwargs["middleware"] = [anthropic.BetaRefusalFallbackMiddleware(
                [{"model": llm.fallback_model or DEFAULT_BEDROCK_FALLBACK}])]
            state = anthropic.BetaFallbackState()
        return AnthropicProvider(anthropic.AnthropicBedrockMantle(**kwargs), llm.model, cfg, server_fallbacks=False,
                                 fallback_state=state, label=f"bedrock ({region})")
    if p == "openai":
        kwargs = {"api_key": _key(llm.api_key_env, "OPENAI_API_KEY", required=True)}
        if llm.base_url:
            kwargs["base_url"] = llm.base_url
        return OpenAIProvider(openai.OpenAI(**kwargs), llm.model, cfg, family="openai")
    if p == "azure":
        if not llm.base_url:
            raise AIError("Azure OpenAI needs llm.base_url (https://<resource>.openai.azure.com).")
        client = openai.AzureOpenAI(azure_endpoint=llm.base_url, api_version=llm.api_version or "2024-10-21",
                                    api_key=_key(llm.api_key_env, "AZURE_OPENAI_API_KEY", required=True))
        return OpenAIProvider(client, llm.model, cfg, family="azure")
    if p == "openai_compatible":
        if not llm.base_url:
            raise AIError("openai_compatible needs llm.base_url, e.g. http://localhost:11434/v1 (Ollama).")
        key = _key(llm.api_key_env, "OPENAI_API_KEY", required=False) or "not-needed"
        return OpenAIProvider(openai.OpenAI(api_key=key, base_url=llm.base_url), llm.model, cfg,
                              family="openai_compatible")
    raise AIError(f"Unknown provider {p}")
