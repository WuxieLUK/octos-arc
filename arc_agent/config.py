from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int, floor: int = 1) -> int:
    try:
        return max(floor, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    requirement_dir: Path
    output_dir: Path
    task_type: str
    endpoint: str
    api_key: str
    model: str
    vision_endpoint: str
    vision_api_key: str
    vision_model: str
    request_timeout: int = 600
    total_timeout: int = 46 * 60 * 60
    max_tool_rounds: int = 40
    max_tokens: int = 32768
    max_file_bytes: int = 1_000_000

    @classmethod
    def from_env(cls, requirement_dir: Path, output_dir: Path, task_type: str = "web") -> "Config":
        endpoint = _chat_endpoint(os.getenv("OPENAI_BASE_URL", ""))
        visual_endpoint = _chat_endpoint(os.getenv("VISUAL_BASE_URL", "")) or endpoint
        visual_key = os.getenv("VISUAL_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        return cls(
            requirement_dir=requirement_dir.expanduser().resolve(),
            output_dir=output_dir.expanduser().resolve(),
            task_type=task_type,
            endpoint=endpoint,
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            model=os.getenv("MODEL", "").strip(),
            vision_endpoint=visual_endpoint,
            vision_api_key=visual_key,
            vision_model=(os.getenv("VISUAL_MODEL") or os.getenv("VISION_MODEL") or "").strip(),
            request_timeout=_int("ARC_AGENT_REQUEST_TIMEOUT", 600, 15),
            total_timeout=_int("ARC_AGENT_TIME_BUDGET", 46 * 60 * 60, 300),
            max_tool_rounds=_int("ARC_AGENT_MAX_TOOL_ROUNDS", 40, 1),
            max_tokens=_int("ARC_AGENT_MAX_OUTPUT_TOKENS", 32768, 1024),
            max_file_bytes=_int("ARC_AGENT_MAX_FILE_BYTES", 1_000_000, 1024),
        )


def _chat_endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if endpoint and not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    return endpoint
