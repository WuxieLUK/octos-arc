from __future__ import annotations

import json
import base64
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Any

from arcbench_agent_runtime import AgentRuntime

from .config import Config
from .model_client import ChatClient
from .prompts import SYSTEM_PROMPT, user_prompt
from .requirements_tree import load_tree
from .validator import AppValidator
from .workspace_tools import TOOLS, WorkspaceTools


class Agent:
    def __init__(self, config: Config, runtime: AgentRuntime) -> None:
        self.config, self.runtime = config, runtime
        self.client = ChatClient(config.endpoint, config.api_key, config.model,
                                 config.request_timeout, config.max_tokens)
        self.tree: RequirementTree | None = None
        self.tools: WorkspaceTools | None = None
        self.started = 0.0

    def run(self) -> None:
        if self.config.task_type not in {"web", "web_app"}:
            raise ValueError(f"This build targets the web starter; unsupported task type: {self.config.task_type}")
        self._copy_template()
        self.tree = load_tree(self.config.requirement_dir)
        self.runtime.traceability.init_store()
        validator = AppValidator(self.config.output_dir)
        self.tools = WorkspaceTools(self.config.requirement_dir, self.config.output_dir, validator,
                                    self.runtime.traceability, set(self.tree.nodes), self.config.max_file_bytes,
                                    self.tree.nodes)
        self.started = time.monotonic()
        files = self.tools.tool_list_files("output")["files"]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(self.config.task_type, self.tree.prompt_document(), files)},
        ]
        validations = 0
        for turn in range(self.config.max_tool_rounds):
            self._check_deadline()
            reply = self.client.complete(messages, TOOLS)
            message = reply.message
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                if self.tools.last_validation and self.tools.last_validation.get("phase") == "startup" and self.tools.last_validation.get("ok"):
                    return
                if validations >= 3:
                    raise RuntimeError("The agent stopped before startup validation passed")
                result = self.tools.tool_validate_application("startup")
                validations += 1
                messages.append({"role": "user", "content": "A final local startup check was run. Diagnose and fix its result, then validate again.\n" + json.dumps(result, ensure_ascii=False)})
                continue
            for call in calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                    result = self._execute(name, arguments)
                except (json.JSONDecodeError, ValueError) as exc:
                    result = {"ok": False, "error": f"Invalid tool call: {exc}"}
                call_id = call.get("id")
                if call_id:
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "name": name, "content": json.dumps(result, ensure_ascii=False)[:14000]})
                else:
                    messages.append({"role": "user", "content": f"Tool {name} result:\n" + json.dumps(result, ensure_ascii=False)[:14000]})
                if name == "finish_generation" and result.get("ok"):
                    return
                if name == "validate_application":
                    validations += 1
        raise RuntimeError(f"Tool-call limit reached ({self.config.max_tool_rounds}) before successful completion")

    def _execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "finish_generation":
            validation = self.tools.last_validation if self.tools else None
            if not validation or validation.get("phase") != "startup" or not validation.get("ok"):
                return {"ok": False, "error": "Run successful startup validation before finishing", "last_validation": validation}
            checkpoint_warning = None
            try:
                self.runtime.git.ensure_repo(create_initial_commit=False)
                self.runtime.git.commit("Generated application: validated startup")
                self.runtime.events.notify_commit_history_changed("generation_complete", preview=True)
            except Exception as exc:
                checkpoint_warning = f"Checkpoint unavailable: {type(exc).__name__}: {str(exc)[:300]}"
            return {"ok": True, "summary": str(args.get("summary") or "Application generated and startup-validated")[:500],
                    "checkpoint_warning": checkpoint_warning,
                    "model_usage": {"requests": self.client.calls, "prompt_tokens": self.client.prompt_tokens,
                                    "completion_tokens": self.client.completion_tokens}}
        if name == "inspect_reference_image":
            if not self.config.vision_model:
                return {"ok": False, "error": "No VISION_MODEL or VISUAL_MODEL was injected; implement from written requirements and skip image analysis."}
            path = Path(str(args.get("path", "")))
            if path.is_absolute():
                return {"ok": False, "error": "Absolute paths are not allowed"}
            root = self.config.requirement_dir.resolve()
            image_path = (root / path).resolve()
            try:
                image_path.relative_to(root)
            except ValueError:
                return {"ok": False, "error": "Reference path escapes requirements directory"}
            if not image_path.is_file() or image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                return {"ok": False, "error": "Reference image not found or unsupported format"}
            if image_path.stat().st_size > 8_000_000:
                return {"ok": False, "error": "Reference image exceeds 8 MB"}
            media = mimetypes.guess_type(image_path.name)[0] or "image/png"
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            answer = self.client.complete([{"role": "user", "content": [
                {"type": "text", "text": str(args.get("question") or "Describe the visible layout and controls.")[:2000]},
                {"type": "image_url", "image_url": {"url": f"data:{media};base64,{encoded}"}},
            ]}], model=self.config.vision_model, max_tokens=3000)
            return {"ok": True, "analysis": answer.message.get("content") or ""}
        assert self.tools is not None
        return self.tools.execute(name, args)

    def _check_deadline(self) -> None:
        if time.monotonic() - self.started > self.config.total_timeout:
            raise TimeoutError("Agent time budget exhausted")

    def _copy_template(self) -> None:
        source = Path(__file__).resolve().parents[1] / "template"
        if not source.is_dir():
            raise FileNotFoundError(f"Bundled web template is missing: {source}")
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            if not item.is_file() or any(part in {"node_modules", "dist", ".git", "__pycache__"} for part in item.parts):
                continue
            relative = item.relative_to(source)
            destination = self.config.output_dir / relative
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)
