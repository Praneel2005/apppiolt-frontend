"""LLM Adapter for AppPilot (Gemini + FakeLLM for tests).

Features:
- Provider-agnostic interface (`LLMProvider`)
- Gemini implementation via `google-genai` SDK
- Flattened schema bridge (`PlanFlat`) for Gemini structured-output compatibility
- Persistent disk cache (`.llm_cache`) keyed by SHA-256 of (model, messages, schema)
- Token-bucket rate limiter with exponential backoff for free-tier quotas
- Per-call latency and token telemetry
- `FakeLLM` for offline, deterministic testing without API keys
"""
from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Generic, Literal, Type, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from contracts.actions import Intent, Plan, ToolCall, ToolName

logger = logging.getLogger("agent.llm")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = ROOT / ".llm_cache"

T = TypeVar("T", bound=BaseModel)


# ------------------------------------------------------------------------------
# Flattened schemas for Gemini structured output compatibility
# (Gemini rejects arbitrary free-form dicts in response_schema)
# ------------------------------------------------------------------------------
class ToolCallFlat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tool: ToolName
    args_json: str = Field("{}", description="JSON string of tool arguments")
    expect_json: str = Field("{}", description="JSON string of expected UI state postconditions")


class IntentFlat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Literal[
        "navigate",
        "set_state",
        "read_view",
        "analyze_trend",
        "compare_periods",
        "explain_change",
        "multi_step",
        "clarify_needed",
        "out_of_scope",
        "unsafe_action",
    ]
    slots_json: str = Field("{}", description="JSON string of slots (metric, dimension, etc.)")
    ambiguity: str | None = None


class PlanFlat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: IntentFlat
    steps: list[ToolCallFlat] = Field(default_factory=list)
    clarifying_question: str | None = None
    allow_replan: bool = False


def plan_flat_to_plan(flat: PlanFlat) -> Plan:
    """Convert Gemini-compatible PlanFlat to official contracts.actions.Plan."""
    try:
        slots = json.loads(flat.intent.slots_json) if flat.intent.slots_json else {}
    except Exception:
        slots = {}

    intent = Intent(
        name=flat.intent.name,
        slots=slots if isinstance(slots, dict) else {},
        ambiguity=flat.intent.ambiguity,
    )

    steps: list[ToolCall] = []
    for s in flat.steps:
        try:
            args = json.loads(s.args_json) if s.args_json else {}
        except Exception:
            args = {}

        try:
            expect = json.loads(s.expect_json) if s.expect_json else {}
        except Exception:
            expect = {}

        steps.append(
            ToolCall(
                tool=s.tool,
                args=args if isinstance(args, dict) else {},
                expect=expect if isinstance(expect, dict) else {},
            )
        )

    return Plan(
        intent=intent,
        steps=steps,
        clarifying_question=flat.clarifying_question,
        allow_replan=flat.allow_replan,
    )


def plan_to_plan_flat(plan: Plan) -> PlanFlat:
    """Convert contracts.actions.Plan to Gemini-compatible PlanFlat."""
    return PlanFlat(
        intent=IntentFlat(
            name=plan.intent.name,
            slots_json=json.dumps(plan.intent.slots, ensure_ascii=False),
            ambiguity=plan.intent.ambiguity,
        ),
        steps=[
            ToolCallFlat(
                tool=s.tool,
                args_json=json.dumps(s.args, ensure_ascii=False),
                expect_json=json.dumps(s.expect, ensure_ascii=False),
            )
            for s in plan.steps
        ],
        clarifying_question=plan.clarifying_question,
        allow_replan=plan.allow_replan,
    )


# ------------------------------------------------------------------------------
# Telemetry and Rate Limiting
# ------------------------------------------------------------------------------
class CallLog(BaseModel):
    timestamp: float
    model: str
    schema_name: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False


class TokenBucket:
    """Simple thread-safe token bucket rate limiter."""

    def __init__(self, requests_per_minute: float = 15.0):
        self.capacity = max(1.0, requests_per_minute)
        self.tokens = self.capacity
        self.refill_rate = self.capacity / 60.0  # tokens per second
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)

            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.refill_rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
            else:
                self.tokens -= 1.0


# ------------------------------------------------------------------------------
# Persistent Disk Cache
# ------------------------------------------------------------------------------
class DiskCache:
    def __init__(self, directory: Path | str = DEFAULT_CACHE_DIR):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, model: str, schema_name: str, messages: list[dict], system_prompt: str) -> str:
        payload = json.dumps(
            {"model": model, "schema": schema_name, "messages": messages, "sys": system_prompt},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, model: str, schema_name: str, messages: list[dict], system_prompt: str) -> str | None:
        k = self._key(model, schema_name, messages, system_prompt)
        p = self.dir / f"{k}.json"
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    def set(self, model: str, schema_name: str, messages: list[dict], system_prompt: str, response_json: str) -> None:
        k = self._key(model, schema_name, messages, system_prompt)
        p = self.dir / f"{k}.json"
        try:
            p.write_text(response_json, encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write to LLM cache: %s", e)


# ------------------------------------------------------------------------------
# LLM Provider Base
# ------------------------------------------------------------------------------
class LLMProvider(abc.ABC):
    def __init__(self, model_name: str, cache_dir: Path | str | None = None):
        self.model_name = model_name
        self.cache = DiskCache(cache_dir or DEFAULT_CACHE_DIR)
        self.call_logs: list[CallLog] = []

    @abc.abstractmethod
    async def generate_structured(
        self,
        schema: Type[T],
        messages: list[dict[str, str]],
        system_prompt: str = "",
    ) -> T:
        """Generates a structured Pydantic response matching `schema`."""
        pass


# ------------------------------------------------------------------------------
# Gemini Provider
# ------------------------------------------------------------------------------
class GeminiProvider(LLMProvider):
    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
        rpm: float = 15.0,
    ):
        model = model_name or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        super().__init__(model, cache_dir)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or ""
        self.limiter = TokenBucket(rpm)
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY is not set. Check your .env file.")
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def generate_structured(
        self,
        schema: Type[T],
        messages: list[dict[str, str]],
        system_prompt: str = "",
    ) -> T:
        # Check if caller wants Plan directly; if so, map to PlanFlat for Gemini compatibility
        is_plan = schema is Plan
        target_schema = PlanFlat if is_plan else schema
        schema_name = "PlanFlat" if is_plan else schema.__name__

        # 1. Check cache
        cached = self.cache.get(self.model_name, schema_name, messages, system_prompt)
        if cached is not None:
            log = CallLog(
                timestamp=time.time(),
                model=self.model_name,
                schema_name=schema_name,
                latency_ms=0.0,
                cached=True,
            )
            self.call_logs.append(log)
            loaded_json = json.loads(cached)
            parsed_flat = target_schema.model_validate(loaded_json)
            if is_plan:
                return plan_flat_to_plan(parsed_flat)  # type: ignore
            return parsed_flat  # type: ignore

        # 2. Rate limiting
        await self.limiter.acquire()

        # 3. Build contents
        contents = []
        if system_prompt:
            contents.append(f"System: {system_prompt}\n")
        for m in messages:
            contents.append(f"{m.get('role', 'user').title()}: {m.get('content', '')}")
        prompt = "\n\n".join(contents)

        client = self._get_client()
        t0 = time.time()

        # Retry once on transient parse or network failures
        last_err = None
        for attempt in range(2):
            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=self.model_name,
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                            "response_schema": target_schema,
                        },
                    ),
                )
                latency_ms = (time.time() - t0) * 1000.0

                raw_text = response.text or "{}"
                parsed_final = None
                if is_plan:
                    try:
                        parsed_final = Plan.model_validate_json(raw_text)
                    except Exception:
                        parsed_final = None

                if parsed_final is None:
                    parsed_flat = target_schema.model_validate_json(raw_text)
                    parsed_final = plan_flat_to_plan(parsed_flat) if is_plan else parsed_flat

                # Save to cache
                self.cache.set(self.model_name, schema_name, messages, system_prompt, raw_text)

                in_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) if hasattr(response, "usage_metadata") else 0
                out_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) if hasattr(response, "usage_metadata") else 0

                self.call_logs.append(
                    CallLog(
                        timestamp=time.time(),
                        model=self.model_name,
                        schema_name=schema_name,
                        latency_ms=latency_ms,
                        input_tokens=in_tokens or 0,
                        output_tokens=out_tokens or 0,
                        cached=False,
                    )
                )

                return parsed_final  # type: ignore

            except Exception as e:
                last_err = e
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                raise last_err from None


# ------------------------------------------------------------------------------
# Groq Provider (OpenAI-compatible REST via httpx)
# ------------------------------------------------------------------------------
class GroqProvider(LLMProvider):
    """Groq API provider (Llama 3.3 70B, Llama 3.1 8B).
    Ultra-fast inference with generous free-tier limits (30 RPM, 14,400 RPD).
    """

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
        cache_dir: Path | str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
    ):
        model = model_name or os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"
        super().__init__(model_name=model, cache_dir=cache_dir)
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        # Rate limiter: Groq free tier is 30 RPM
        self.limiter = TokenBucket(requests_per_minute=30.0)

    async def generate_structured(
        self,
        schema: Type[T],
        messages: list[dict[str, str]],
        system_prompt: str = "",
    ) -> T:
        import httpx

        t0 = time.time()
        schema_name = schema.__name__

        # Handle Plan schema flattening
        is_plan = schema is Plan
        target_schema = PlanFlat if is_plan else schema

        # 1. Check disk cache
        cached = self.cache.get(self.model_name, schema_name, messages, system_prompt)
        if cached:
            latency_ms = (time.time() - t0) * 1000.0
            self.call_logs.append(
                CallLog(
                    timestamp=time.time(),
                    model=self.model_name,
                    schema_name=schema_name,
                    latency_ms=latency_ms,
                    cached=True,
                )
            )
            if is_plan:
                try:
                    return Plan.model_validate_json(cached)  # type: ignore
                except Exception:
                    flat = PlanFlat.model_validate_json(cached)
                    return plan_flat_to_plan(flat)  # type: ignore
            return schema.model_validate_json(cached)

        if not self.api_key:
            raise ValueError("GROQ_API_KEY is not configured in .env or environment.")

        schema_json = json.dumps(target_schema.model_json_schema(), indent=2)

        groq_sys_prompt = (
            f"{system_prompt}\n\n"
            f"You MUST respond ONLY with a valid JSON object matching this schema:\n"
            f"```json\n{schema_json}\n```\n"
            f"Do not include any introductory text or markdown formatting except the raw JSON object."
        )

        chat_messages = [{"role": "system", "content": groq_sys_prompt}]
        for m in messages:
            chat_messages.append({"role": m["role"], "content": m["content"]})

        last_err = None
        for attempt in range(4):
            await self.limiter.acquire()
            try:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": self.model_name,
                    "messages": chat_messages,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }

                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )

                if resp.status_code == 429:
                    err_data = resp.json().get("error", {})
                    msg = err_data.get("message", "")
                    # Extract suggested wait time e.g. "Please try again in 18.01s"
                    wait_s = 5.0
                    import re
                    m = re.search(r"try again in ([0-9.]+)s", msg)
                    if m:
                        wait_s = float(m.group(1)) + 1.0
                    if wait_s > 2.0:
                        logger.warning("Groq rate limit wait %.1fs exceeds 2.0s threshold; aborting to trigger failover", wait_s)
                        raise RuntimeError(f"Rate limit backoff {wait_s:.1f}s exceeds 2.0s threshold")
                    logger.warning(f"Groq 429 rate limit hit, backing off {wait_s:.1f}s...")
                    await asyncio.sleep(wait_s)
                    continue

                if resp.status_code != 200:
                    err_text = resp.text
                    raise RuntimeError(f"Groq API error ({resp.status_code}): {err_text}")

                data = resp.json()
                raw_text = data["choices"][0]["message"]["content"]
                parsed_final = None
                if is_plan:
                    try:
                        parsed_final = Plan.model_validate_json(raw_text)
                    except Exception:
                        parsed_final = None

                if parsed_final is None:
                    parsed_flat = target_schema.model_validate_json(raw_text)
                    parsed_final = plan_flat_to_plan(parsed_flat) if is_plan else parsed_flat

                # Save to cache
                self.cache.set(self.model_name, schema_name, messages, system_prompt, raw_text)

                usage = data.get("usage", {})
                in_tokens = usage.get("prompt_tokens", 0)
                out_tokens = usage.get("completion_tokens", 0)
                latency_ms = (time.time() - t0) * 1000.0

                self.call_logs.append(
                    CallLog(
                        timestamp=time.time(),
                        model=self.model_name,
                        schema_name=schema_name,
                        latency_ms=latency_ms,
                        input_tokens=in_tokens,
                        output_tokens=out_tokens,
                        cached=False,
                    )
                )

                return parsed_final  # type: ignore

            except Exception as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(2.0)
                    continue
                raise last_err from None


# ------------------------------------------------------------------------------
# Chained Provider for Failover (WP6)
# ------------------------------------------------------------------------------
class ChainedLLMProvider(LLMProvider):
    """Resilient provider chain: tries primary; on failure or rate-limit, tries fallback."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None):
        super().__init__(primary.model_name, primary.cache.dir)
        self.primary = primary
        self.fallback = fallback

    async def generate_structured(
        self,
        schema: Type[T],
        messages: list[dict[str, str]],
        system_prompt: str = "",
    ) -> T:
        try:
            return await self.primary.generate_structured(schema, messages, system_prompt)
        except Exception as e:
            if self.fallback:
                logger.warning(
                    "Primary provider '%s' failed (%s). Failing over to '%s'...",
                    self.primary.model_name,
                    e,
                    self.fallback.model_name,
                )
                return await self.fallback.generate_structured(schema, messages, system_prompt)
            raise


# ------------------------------------------------------------------------------
# Fake LLM Provider for Tests
# ------------------------------------------------------------------------------
class FakeLLM(LLMProvider):
    """Deterministic fake provider for test suites. Does not make external network calls."""

    def __init__(self, model_name: str = "fake-model", cache_dir: Path | str | None = None):
        super().__init__(model_name, cache_dir)
        self.canned_responses: list[Any] = []
        self.handlers: list[tuple[Callable[[list[dict[str, str]]], bool], Any]] = []

    def queue_response(self, response: Any) -> None:
        """Queue a predetermined response object or JSON dict."""
        self.canned_responses.append(response)

    def register_handler(self, matcher: Callable[[list[dict[str, str]]], bool], response: Any) -> None:
        """Register a handler that returns response if matcher(messages) is True."""
        self.handlers.append((matcher, response))

    async def generate_structured(
        self,
        schema: Type[T],
        messages: list[dict[str, str]],
        system_prompt: str = "",
    ) -> T:
        t0 = time.time()
        schema_name = schema.__name__

        # 1. Check handlers
        for matcher, resp in self.handlers:
            if matcher(messages):
                return self._coerce(schema, resp)

        # 2. Check queued responses
        if self.canned_responses:
            resp = self.canned_responses.pop(0)
            log = CallLog(
                timestamp=time.time(),
                model=self.model_name,
                schema_name=schema_name,
                latency_ms=(time.time() - t0) * 1000.0,
                cached=False,
            )
            self.call_logs.append(log)
            return self._coerce(schema, resp)

        # 3. Default fallback dummy creation
        return self._create_dummy(schema)

    def _coerce(self, schema: Type[T], obj: Any) -> T:
        if isinstance(obj, schema):
            return obj
        if isinstance(obj, dict):
            return schema.model_validate(obj)
        if isinstance(obj, str):
            return schema.model_validate_json(obj)
        raise ValueError(f"Cannot coerce {type(obj)} to {schema}")

    def _create_dummy(self, schema: Type[T]) -> T:
        if schema is Plan:
            return Plan(  # type: ignore
                intent=Intent(name="navigate"),
                steps=[ToolCall(tool="navigate", args={"page_id": "ops.dashboard"})],
            )
        if schema is PlanFlat:
            return PlanFlat(  # type: ignore
                intent=IntentFlat(name="navigate", slots_json="{}"),
                steps=[ToolCallFlat(tool="navigate", args_json='{"page_id": "ops.dashboard"}')],
            )
        # Attempt minimal default instantiation
        try:
            return schema.model_validate({})
        except Exception:
            raise NotImplementedError(f"No fake response configured for schema {schema.__name__}")


# ------------------------------------------------------------------------------
# Factory Function
# ------------------------------------------------------------------------------
def get_llm(
    provider_name: str | None = None,
    model_name: str | None = None,
    cache_dir: Path | str | None = None,
) -> LLMProvider:
    provider = (provider_name or os.environ.get("LLM_PROVIDER") or "gemini").strip("\"'").lower()
    if provider == "fake":
        return FakeLLM(model_name or "fake-model", cache_dir)
    if provider == "gemini":
        return GeminiProvider(model_name, cache_dir=cache_dir)
    if provider == "groq":
        return GroqProvider(model_name, cache_dir=cache_dir)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
