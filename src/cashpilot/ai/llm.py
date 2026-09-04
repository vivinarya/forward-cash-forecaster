"""Minimal OpenAI-compatible chat client: stdlib only, opt-in, budget-capped, JSON-validated.

Notes on the deliberate boringness:
 * no `openai`/`litellm`/`langchain` dependency - it is 40 lines of urllib and one JSON schema check;
 * a hard call budget (`CASHPILOT_LLM_MAX_CALLS`) so a bad loop cannot burn money on stage;
 * failures are *expected* and handled: no key, timeout, non-JSON, rate limit -> returns None and
   the caller keeps its deterministic answer. `cashpilot run` must never fail because a model was down;
 * concurrency via threads, because triage is 20-80 small independent calls and serial latency on
   stage wifi would make the demo look broken.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..config import Settings


@dataclass
class LlmClient:
    settings: Settings
    calls: int = 0
    ok: int = 0
    failed: int = 0
    invalid: int = 0
    prompt_chars: int = 0
    completion_chars: int = 0
    ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.llm_enabled and self.settings.llm_api_key)

    def budget_left(self) -> int:
        return max(0, int(self.settings.llm_max_calls) - self.calls)

    def usage(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "model": self.settings.llm_model if self.enabled else None,
            "calls": self.calls,
            "ok": self.ok,
            "failed": self.failed,
            "invalid_json": self.invalid,
            "budget_remaining": self.budget_left(),
            "prompt_chars": self.prompt_chars,
            "completion_chars": self.completion_chars,
            "approx_tokens": int((self.prompt_chars + self.completion_chars) / 4),
            "wall_ms": round(self.ms, 1),
            "errors": self.errors[:5],
        }

    # ------------------------------------------------------------------ transport
    def _post(self, system: str, user: str) -> str | None:
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": self.settings.llm_model,
                "temperature": 0.0,
                "response_format": {"type": "json_object"},
                "max_tokens": 500,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.settings.llm_api_key}"},
            method="POST",
        )
        self.calls += 1
        self.prompt_chars += len(system) + len(user)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.settings.llm_timeout_s) as resp:
                raw = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            self.failed += 1
            self.errors.append(f"{type(exc).__name__}: {exc}"[:200])
            return None
        finally:
            self.ms += (time.perf_counter() - t0) * 1000
        try:
            text = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            self.failed += 1
            self.errors.append("unexpected_response_shape")
            return None
        self.completion_chars += len(text or "")
        return text

    def complete_json(self, system: str, user: str) -> dict | None:
        if not self.enabled or self.budget_left() <= 0:
            return None
        text = self._post(system, user)
        if text is None:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                self.invalid += 1
                return None
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                self.invalid += 1
                return None
        if not isinstance(payload, dict):
            self.invalid += 1
            return None
        self.ok += 1
        return payload

    def complete_many(self, prompts: list[tuple[str, str]], workers: int = 6) -> list[dict | None]:
        """Thread-pooled fan-out. Order is preserved so results map 1:1 onto inputs."""
        if not prompts or not self.enabled:
            return [None] * len(prompts)
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(prompts)))) as pool:
            return list(pool.map(lambda su: self.complete_json(*su), prompts))


def budget(client: LlmClient) -> bool:
    return client.enabled and client.budget_left() > 0
