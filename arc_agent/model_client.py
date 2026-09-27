from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class ModelError(RuntimeError):
    pass


@dataclass
class Reply:
    message: dict[str, Any]
    usage: dict[str, Any]


class ChatClient:
    RETRYABLE = {408, 409, 429, 500, 502, 503, 504}

    def __init__(self, endpoint: str, api_key: str, model: str, timeout: int, max_tokens: int) -> None:
        if not all((endpoint, api_key, model)):
            raise ModelError("OPENAI_BASE_URL, OPENAI_API_KEY, and MODEL must be injected by the runner")
        self.endpoint, self.api_key, self.model = endpoint, api_key, model
        self.timeout, self.max_tokens = timeout, max_tokens
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                 *, model: str | None = None, max_tokens: int | None = None) -> Reply:
        selected = model or self.model
        body: dict[str, Any] = {"model": selected, "messages": messages, "stream": False,
                                "max_tokens": max_tokens or self.max_tokens}
        if "deepseek" in selected.lower():
            body["thinking"] = {"type": "enabled"}
        if tools:
            body["tools"] = tools
        data = json.dumps(body, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        for attempt in range(5):
            self.calls += 1
            req = request.Request(self.endpoint, data=data, headers=headers, method="POST")
            try:
                with request.urlopen(req, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                choices = payload.get("choices") or []
                if not choices or not isinstance(choices[0].get("message"), dict):
                    raise ModelError("Model response contained no assistant message")
                usage = payload.get("usage") or {}
                self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
                return Reply(choices[0]["message"], usage)
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:2500]
                if exc.code not in self.RETRYABLE or attempt == 4:
                    raise ModelError(f"Model HTTP {exc.code}: {detail}") from exc
            except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == 4:
                    raise ModelError(f"Model request failed: {exc}") from exc
            time.sleep(min(20, 1.5 ** attempt) + random.random() * 0.25)
        raise ModelError("Model retry budget exhausted")
