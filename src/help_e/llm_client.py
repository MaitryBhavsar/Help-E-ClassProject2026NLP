"""Unified OpenAI-compatible LLM client with JSON-schema enforcement and per-call logging.

Every LLM call in HELP-E goes through ``LLMClient.generate_structured`` or
``LLMClient.generate_text``. Structured calls:
  1. Try Outlines-style constrained decoding when available.
  2. Otherwise send the schema inline and use the OpenAI-style
     ``response_format={"type": "json_object"}`` JSON mode.
  3. Repair malformed JSON with ``json_repair`` before validating.
  4. Retry once on schema-validation failure with a targeted error hint.

Every call is seeded from ``hash((profile_id, session_id, system, turn_id,
call_role))`` so reruns reproduce exactly (§11.7). The raw request, response,
and parsed output are appended to
``help_e/logs/{profile_id}/{session_id}/{turn_id}.jsonl``.

The transport is the OpenAI-compatible ``/v1/chat/completions`` endpoint. In
the current setup the main endpoint (11436) is vLLM with Llama 3.3 70B, and
the simulator endpoint (11438) is Ollama with gpt-oss:20b. Both expose the
same OpenAI-style API, so this client is backend-agnostic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

try:  # optional; fallback path is fine without it
    from json_repair import repair_json  # type: ignore
except ImportError:  # pragma: no cover — fallback when the package isn't installed
    def repair_json(s: str, return_objects: bool = False) -> Any:  # type: ignore[misc]
        return s

try:  # optional; Outlines binding is a future hook
    import outlines  # type: ignore  # noqa: F401
    _OUTLINES_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OUTLINES_AVAILABLE = False

from jsonschema import Draft202012Validator, ValidationError

from . import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output-token rate limiter (per-tenant)
# ---------------------------------------------------------------------------


class _OutputTokenLimiter:
    """Token bucket throttle so a tenant's output-token budget is not
    exceeded.

    Lightning AI's serverless tier caps OUTPUT tokens at 200/sec per
    tenant. When we burst-fire several calls in a tight Python loop
    (e.g. v6's 3 chatbot calls per turn at max_tokens=800/2000/1400),
    a single Python second can demand thousands of tokens → server
    returns HTTP 500 or content-corrupted HTTP 200.

    This limiter:
      - Per-tenant token bucket; refills at `max_tokens_per_sec`
        tokens/sec, capped at `burst_seconds * rate` so a few quick
        calls in a row are still allowed.
      - Before each call: reserve `expected_max_tokens` (the call's
        `max_tokens` cap — pessimistic upper bound). If the bucket has
        less than that, sleep for the deficit / rate seconds, then
        deduct.
      - The reservation amount is capped at the bucket capacity so
        oversized requests (e.g. 2000 tok call against a 800-cap
        bucket) don't loop forever.
      - After each call: optional `record(actual)` could refund excess
        but we keep it pessimistic and let the steady refill rate
        compensate over time.

    Per-tenant: keyed by `(url, api_key_prefix)`. Local vLLM (no
    key/quota) is identified by `max_tokens_per_sec is None` and
    skipped entirely.
    """

    def __init__(self, max_tokens_per_sec: Optional[int] = 200,
                 burst_seconds: float = 4.0,
                 max_concurrent: int = 2,
                 min_interval_s: float = 2.0):
        self.rate = max_tokens_per_sec
        self.capacity = (
            float(max_tokens_per_sec) * burst_seconds
            if max_tokens_per_sec else 0.0
        )
        self.max_concurrent = max_concurrent
        self.min_interval_s = min_interval_s
        # tenant key -> [last_ts_monotonic, balance_tokens]
        self._buckets: dict[str, list[float]] = {}
        # tenant key -> Semaphore(max_concurrent)
        self._semaphores: dict[str, threading.Semaphore] = {}
        # tenant key -> last_request_start_ts (for min-interval enforcement)
        self._last_request_ts: dict[str, float] = {}
        self._lock = threading.Lock()

    def _refill_locked(self, tenant_key: str, now: float) -> list[float]:
        """Refill bucket since last touch. Caller must hold self._lock."""
        bucket = self._buckets.get(tenant_key)
        if bucket is None:
            bucket = [now, self.capacity]
            self._buckets[tenant_key] = bucket
            return bucket
        elapsed = now - bucket[0]
        if elapsed > 0:
            bucket[1] = min(self.capacity, bucket[1] + elapsed * self.rate)
            bucket[0] = now
        return bucket

    def reserve(self, tenant_key: str, expected_tokens: int) -> float:
        """Block until enough tokens are available in the bucket, then
        deduct. Returns total seconds slept for diagnostics.
        """
        if self.rate is None or expected_tokens <= 0:
            return 0.0
        # Cap reservation at bucket capacity so a single huge request
        # (e.g. mind1_v6 max_tokens=2500 vs cap 800) doesn't loop forever.
        # Real generation will span multiple seconds anyway; the rate
        # naturally smooths over.
        reserve_amt = min(float(expected_tokens), self.capacity)
        slept_total = 0.0
        while True:
            wait = 0.0
            with self._lock:
                now = time.monotonic()
                bucket = self._refill_locked(tenant_key, now)
                if bucket[1] >= reserve_amt:
                    bucket[1] -= reserve_amt
                    return slept_total
                deficit = reserve_amt - bucket[1]
                wait = deficit / self.rate + 0.02
            # Sleep OUTSIDE the lock so concurrent callers can refill /
            # progress.
            time.sleep(wait)
            slept_total += wait

    def record(self, tenant_key: str, actual_tokens: int) -> None:
        """Currently a no-op — the natural refill rate compensates for
        any over-reservation (max_tokens vs actual). Kept as a public
        API in case we want to refund excess in the future.
        """
        return None

    def acquire_slot(self, tenant_key: str) -> None:
        """Block until (a) fewer than `max_concurrent` requests are
        in-flight on this tenant, AND (b) at least `min_interval_s`
        has passed since the last call started.

        Used in tandem with `reserve(...)`: this enforces concurrency +
        spacing; reserve enforces output-token rate.
        """
        if self.max_concurrent <= 0:
            return
        # Lazily create the semaphore for this tenant.
        with self._lock:
            sem = self._semaphores.get(tenant_key)
            if sem is None:
                sem = threading.Semaphore(self.max_concurrent)
                self._semaphores[tenant_key] = sem
        # Acquire the slot (may block if already at max_concurrent).
        sem.acquire()
        # Enforce min-interval since last request started.
        if self.min_interval_s > 0:
            with self._lock:
                last = self._last_request_ts.get(tenant_key, 0.0)
                now = time.monotonic()
                wait = (last + self.min_interval_s) - now
                # Update the last-ts to NOW (claims this slot before sleeping
                # so concurrent acquirers see the updated time).
                self._last_request_ts[tenant_key] = max(now, last + self.min_interval_s)
            if wait > 0:
                time.sleep(wait)

    def release_slot(self, tenant_key: str) -> None:
        """Release a slot acquired by `acquire_slot`."""
        if self.max_concurrent <= 0:
            return
        with self._lock:
            sem = self._semaphores.get(tenant_key)
        if sem is not None:
            sem.release()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class CallContext:
    """Identifies a single LLM call for seeding and logging."""

    profile_id: str
    session_id: int
    system: str  # "v1", "v2", "v3", "v4", "v5"
    turn_id: int
    call_role: str  # one of config.CALL_ROLES

    def seed(self) -> int:
        payload = "|".join([
            config.SEED_SALT,
            self.profile_id, str(self.session_id), self.system,
            str(self.turn_id), self.call_role,
        ])
        return int(hashlib.sha256(payload.encode()).hexdigest(), 16) % (2**31 - 1)

    def log_path(self) -> Path:
        d = config.LOG_ROOT / self.profile_id / f"session_{self.session_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"turn_{self.turn_id:03d}.jsonl"


@dataclass
class LLMClient:
    """One client, two backends. Routes per-call based on ``ctx.call_role``:
    simulator roles (Mind-1/2/3) hit the simulator endpoint; everything
    else hits the main endpoint.
    """

    main_model: str = config.MAIN_MODEL_NAME
    main_url: str = config.MAIN_OLLAMA_URL
    sim_model: str = config.SIM_MODEL_NAME
    sim_url: str = config.SIM_OLLAMA_URL
    small_model: str = config.SMALL_MODEL_NAME
    small_url: str = config.SMALL_MODEL_URL
    judge_model: str = config.JUDGE_MODEL_NAME
    judge_url: str = config.JUDGE_OLLAMA_URL
    timeout_s: int = config.REQUEST_TIMEOUT_S
    use_outlines: bool = False  # future hook; default off
    max_retries: int = 1  # one retry on schema mismatch, then log-and-default

    _session: requests.Session = field(default_factory=requests.Session)
    # Per-tenant output-token rate limiter (token bucket + per-tenant
    # max_concurrent + min_interval). See `_OutputTokenLimiter` docs.
    _output_limiter: _OutputTokenLimiter = field(
        default_factory=lambda: _OutputTokenLimiter(
            max_tokens_per_sec=200,     # token bucket on (Lightning tier)
            burst_seconds=4.0,          # 800-token burst capacity
            max_concurrent=2,           # at most 2 in-flight per tenant
            min_interval_s=1.0,         # 1-sec min gap between request starts
        )
    )
    # GLOBAL concurrency cap — at most N in-flight LLM calls TOTAL
    # across ALL tenants combined. Set to 2 to enforce the user's
    # "max 2 parallel calls" constraint regardless of tenant routing.
    # Acquired before tenant-level acquire, released after the call.
    _global_semaphore: threading.Semaphore = field(
        default_factory=lambda: threading.Semaphore(2)
    )
    # Slow-call alarm: log a WARNING when any successful call exceeds
    # this many seconds. The log line is grep-able by monitors so the
    # user gets early notice of Lightning AI silent-slow windows.
    SLOW_CALL_THRESHOLD_S: float = 90.0

    # -- routing -----------------------------------------------------------

    def _route(self, call_role: str) -> tuple[str, str, str]:
        """Return (url, model, api_key) for this call_role.

        Four tiers (priority order):
          1. SMALL_MODEL_ROLES → cheap 20B-class extractor.
          2. JUDGE_ROLES       → dedicated judge endpoint (separate
             billing pool so judges don't compete with chatbot 429s).
          3. SIMULATOR_ROLES   → simulator endpoint.
          4. everything else   → main chatbot endpoint.
        """
        if call_role in config.SMALL_MODEL_ROLES:
            return self.small_url, self.small_model, config.SIM_API_KEY
        if call_role in config.JUDGE_ROLES:
            return self.judge_url, self.judge_model, config.JUDGE_API_KEY
        if call_role in config.SIMULATOR_ROLES:
            return self.sim_url, self.sim_model, config.SIM_API_KEY
        return self.main_url, self.main_model, config.MAIN_API_KEY

    # Back-compat: some older code referenced client.model / client.url.
    @property
    def model(self) -> str:
        return self.main_model

    @property
    def url(self) -> str:
        return self.main_url

    # -- health ------------------------------------------------------------

    def ping(self) -> dict[str, bool]:
        """Probe each backend via /v1/models with its own API key.

        Returns {endpoint_name: reachable_bool} for main / sim / judge.
        """
        out: dict[str, bool] = {}
        endpoints = (
            ("main", self.main_url, config.MAIN_API_KEY),
            ("sim", self.sim_url, config.SIM_API_KEY),
            ("judge", self.judge_url, config.JUDGE_API_KEY),
        )
        for name, url, key in endpoints:
            try:
                r = self._session.get(
                    f"{url}/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=5,
                )
                out[name] = r.ok
            except requests.RequestException:
                out[name] = False
        return out

    # -- structured call ---------------------------------------------------

    def generate_structured(
        self,
        *,
        ctx: CallContext,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        validator_extras: Optional[callable] = None,  # type: ignore[valid-type]
    ) -> dict:
        """Run a structured-output call and return a schema-validated dict.

        ``validator_extras`` is an optional callable taking the parsed dict and
        raising ``ValueError`` if post-parse business rules fail (§18.1 rules
        like "main_problem ∈ active_problems", §18.4 "chosen_techniques ⊆
        candidate_list").
        """

        temperature = config.TEMPERATURE_BY_ROLE.get(ctx.call_role, 0.2)
        seed = ctx.seed()
        url, model, api_key = self._route(ctx.call_role)
        jsonschema_validator = Draft202012Validator(schema)

        # Per-role retry budget overrides the class default. Useful for
        # roles like response_v6 whose stricter validators (banned-opener,
        # candidate-subset) need more chances to learn from error hints.
        role_retries = getattr(config, "MAX_RETRIES_BY_ROLE", {}).get(
            ctx.call_role, self.max_retries,
        )
        # Per-role max_tokens cap (sized from observed-max + buffer; see
        # config.MAX_TOKENS_BY_ROLE). Stops vLLM generation early so
        # latency tracks the real output size rather than the model
        # default ceiling.
        role_max_tokens = getattr(config, "MAX_TOKENS_BY_ROLE", {}).get(
            ctx.call_role,
            getattr(config, "DEFAULT_MAX_TOKENS", 4096),
        )

        last_error: Optional[str] = None
        for attempt in range(role_retries + 1):
            error_hint = (
                f"\n\nYour previous response failed validation with: {last_error}\n"
                f"Return only valid JSON matching the schema exactly."
                if attempt > 0 and last_error
                else ""
            )

            request_payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt + error_hint},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": temperature,
                "seed": seed + attempt,  # perturb on retry
                "max_tokens": role_max_tokens,
                "stream": False,
            }

            t0 = time.monotonic()
            try:
                raw_text = self._post_chat(request_payload, url, api_key)
            except requests.Timeout as e:
                # Timeouts mean the backend is hung/saturated; retrying just
                # burns another timeout_s. Log and break so the caller's
                # fallback kicks in immediately.
                last_error = f"timeout: {e}"
                self._log(ctx, request_payload, None, last_error,
                          time.monotonic() - t0, attempt, parsed=None)
                break
            except requests.RequestException as e:
                last_error = f"transport error: {e}"
                self._log(ctx, request_payload, None, str(e), time.monotonic() - t0,
                          attempt, parsed=None)
                # On 429 (rate-limited), sleep with exponential backoff
                # before the next attempt. Without this, retries hammer
                # the same rate-limit and burn the retry budget instantly
                # on hosted endpoints. 5xx is treated as transient too.
                msg = str(e)
                if "429" in msg or "rate" in msg.lower() or " 5" in msg[:60]:
                    backoff_s = min(60.0, 2.0 * (2 ** attempt))
                    log.warning("rate-limited (%s); sleeping %.1fs before retry %d",
                                ctx.call_role, backoff_s, attempt + 1)
                    time.sleep(backoff_s)
                continue

            parsed, parse_err = self._parse_json(raw_text)
            latency = time.monotonic() - t0

            if parse_err is not None:
                last_error = f"JSON parse: {parse_err}"
                self._log(ctx, request_payload, raw_text, last_error, latency,
                          attempt, parsed=None)
                continue

            try:
                jsonschema_validator.validate(parsed)
                if validator_extras is not None:
                    validator_extras(parsed)
            except (ValidationError, ValueError) as e:
                last_error = f"schema/business: {e}"
                self._log(ctx, request_payload, raw_text, last_error, latency,
                          attempt, parsed=parsed)
                continue

            # success
            self._log(ctx, request_payload, raw_text, None, latency, attempt,
                      parsed=parsed)
            return parsed

        # All retries exhausted — raise; the caller decides whether to supply
        # a safe default (§18.1 extraction does; others re-raise).
        raise LLMStructuredError(
            f"{ctx.call_role} failed after {self.max_retries + 1} attempts: "
            f"{last_error}"
        )

    # -- free-text call ----------------------------------------------------

    def generate_text(
        self,
        *,
        ctx: CallContext,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Free-form text output. Used only where JSON would be overkill
        (none of the §18 calls today — every §18 call is structured)."""

        temperature = config.TEMPERATURE_BY_ROLE.get(ctx.call_role, 0.4)
        seed = ctx.seed()
        url, model, api_key = self._route(ctx.call_role)
        role_max_tokens = getattr(config, "MAX_TOKENS_BY_ROLE", {}).get(
            ctx.call_role,
            getattr(config, "DEFAULT_MAX_TOKENS", 4096),
        )

        request_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "seed": seed,
            "max_tokens": role_max_tokens,
            "stream": False,
        }
        t0 = time.monotonic()
        raw = self._post_chat(request_payload, url, api_key)
        latency = time.monotonic() - t0
        self._log(ctx, request_payload, raw, None, latency, 0, parsed=None)
        return raw.strip()

    # -- plumbing ----------------------------------------------------------

    @staticmethod
    def _tenant_key(url: str, api_key: Optional[str]) -> Optional[str]:
        """Identifier for output-token rate limiting. Returns None for
        endpoints that don't need throttling (local vLLM)."""
        if not url or "localhost" in url or "127.0.0.1" in url:
            return None
        # Throttle by (url + key prefix) so two Lightning AI accounts on
        # the same URL get independent quotas.
        prefix = (api_key or "")[:8]
        return f"{url}|{prefix}"

    def _post_chat(self, payload: dict, url: str, api_key: Optional[str] = None) -> str:
        """Hit the OpenAI-compatible /v1/chat/completions endpoint.

        ``api_key`` is the per-route key from `_route` (different
        Lightning AI accounts have different keys). Falls back to the
        global ``config.API_KEY`` for back-compat with single-key callers
        (e.g. tests that bypass `_route`).

        Per-tenant output-token throttling: before sending, reserve
        `payload.max_tokens` against a 1-second sliding window (200
        tok/sec cap matches Lightning AI serverless tier). After the
        response, record the actual `completion_tokens` so the trailing
        window converges on real usage. Local vLLM (no throttling) is
        identified by a None tenant key.
        """
        tenant = self._tenant_key(url, api_key)
        expected_max = int(payload.get("max_tokens") or 0)

        # GLOBAL concurrency cap (max 2 in-flight TOTAL across all
        # tenants). Acquired BEFORE tenant-level acquire so the global
        # cap is honored even when tenants have headroom.
        self._global_semaphore.acquire()
        # Then per-tenant slot + min-interval (Lightning has separate
        # per-tenant limits beyond the global cap).
        if tenant is not None:
            self._output_limiter.acquire_slot(tenant)

        call_start = time.monotonic()
        try:
            if tenant is not None and expected_max > 0:
                slept = self._output_limiter.reserve(tenant, expected_max)
                if slept > 0.5:
                    log.debug("rate-throttle %s slept %.2fs (reserve=%d tok)",
                              tenant, slept, expected_max)

            r = self._session.post(
                f"{url}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key or config.API_KEY}"},
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            body = r.json()
            elapsed = time.monotonic() - call_start
            # Slow-call alarm — early warning for Lightning silent-slow
            # congestion windows. Logged at WARNING so monitors catch it.
            if elapsed >= self.SLOW_CALL_THRESHOLD_S:
                role = payload.get("model", "?")
                log.warning(
                    "SLOW-CALL: %s on %s took %.1fs (>= %.0fs threshold)",
                    role, url, elapsed, self.SLOW_CALL_THRESHOLD_S,
                )
            if tenant is not None:
                actual = (body.get("usage") or {}).get("completion_tokens")
                if isinstance(actual, int) and actual >= 0:
                    self._output_limiter.record(tenant, actual)
            choices = body.get("choices") or []
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content", "") or ""
        finally:
            if tenant is not None:
                self._output_limiter.release_slot(tenant)
            self._global_semaphore.release()

    # Match a fenced ```json ... ``` (or generic ``` ... ```) block,
    # tolerating leading prose and optional language tag.
    _FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
    # Fallback: find the FIRST balanced JSON object/array substring in
    # the text (covers "Sure, here's the JSON: { ... }" preambles).
    _OBJ_SLICE_RE = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)

    @classmethod
    def _strip_to_json_payload(cls, text: str) -> str:
        """Best-effort extraction of the JSON substring from an LLM
        response that may be wrapped in markdown fences or surrounded by
        chatty prose. Returns the candidate substring; if nothing matches
        the original text is returned unchanged.
        """
        if not text:
            return text
        m = cls._FENCE_RE.search(text)
        if m:
            return m.group(1)
        # No fence — try to slice from first { or [ to last } or ].
        # Greedy regex above; falls through if there's no JSON-shaped
        # substring at all.
        m = cls._OBJ_SLICE_RE.search(text)
        if m:
            return m.group(1)
        return text

    @classmethod
    def _parse_json(cls, text: str) -> tuple[Optional[dict], Optional[str]]:
        if not text:
            return None, "empty response"
        # Quick path: already-clean JSON.
        try:
            return json.loads(text), None
        except json.JSONDecodeError as first:
            # First, see if the LLM wrapped the JSON in a code fence or
            # added a chatty preamble. Strip to the JSON-shaped payload
            # before trying repair.
            payload = cls._strip_to_json_payload(text)
            if payload is not text:
                try:
                    return json.loads(payload), None
                except json.JSONDecodeError:
                    pass
            # Fall back to json_repair on the (possibly trimmed) payload.
            try:
                repaired = repair_json(payload, return_objects=True)
                if isinstance(repaired, dict):
                    return repaired, None
                # json_repair may return a string if it can't find structure
                if isinstance(repaired, str):
                    return json.loads(repaired), None
                return None, f"non-dict after repair ({type(repaired).__name__})"
            except Exception as second:
                return None, f"{first}; repair: {second}"

    @staticmethod
    def _log(
        ctx: CallContext,
        request: dict,
        raw_response: Optional[str],
        error: Optional[str],
        latency_s: float,
        attempt: int,
        parsed: Optional[dict],
    ) -> None:
        record = {
            "ts": time.time(),
            "profile_id": ctx.profile_id,
            "session_id": ctx.session_id,
            "system": ctx.system,
            "turn_id": ctx.turn_id,
            "call_role": ctx.call_role,
            "attempt": attempt,
            "model": request.get("model"),
            "temperature": request.get("temperature"),
            "seed": request.get("seed"),
            "latency_s": round(latency_s, 3),
            "error": error,
            "raw_response": raw_response,
            "parsed": parsed,
        }
        with ctx.log_path().open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


class LLMStructuredError(RuntimeError):
    """Raised by ``generate_structured`` when all retries exhaust."""


# Module-level singleton so callers can ``from llm_client import client``
# without every module wiring its own instance. Override via set_client() at
# test time.
_default_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def set_client(client: LLMClient) -> None:
    global _default_client
    _default_client = client
