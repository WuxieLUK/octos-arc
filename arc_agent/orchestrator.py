from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Any

from arcbench_agent_runtime import AgentRuntime

from .config import Config
from .model_client import ChatClient
from .prompts import SYSTEM_PROMPT, user_prompt
from .requirements_tree import RequirementTree, load_tree
from .validator import AppValidator
from .workspace_tools import TOOLS, WorkspaceTools


class Agent:
    def __init__(self, config: Config, runtime: AgentRuntime) -> None:
        self.config, self.runtime = config, runtime
        self.client = ChatClient(config.endpoint, config.api_key, config.model,
                                 config.request_timeout, config.max_tokens)
        self.vision_client = None
        if config.vision_model and config.vision_endpoint and config.vision_api_key:
            self.vision_client = ChatClient(config.vision_endpoint, config.vision_api_key,
                                            config.vision_model, config.request_timeout, config.max_tokens)
        elif config.model.lower() in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
            self.vision_client = self.client
        self.tree: RequirementTree | None = None
        self.tools: WorkspaceTools | None = None
        self.started = 0.0
        self.run_notes: list[str] = []

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
            messages.append(reply.message)
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
                arguments: dict[str, Any] = {}
                try:
                    arguments = self._parse_tool_arguments(fn.get("arguments"))
                    result = self._execute(name, arguments)
                except Exception as exc:
                    result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:700]}"}
                self.run_notes.append(self._tool_note(name, arguments, result))
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
            if turn >= 3 and (turn + 1) % 4 == 0:
                messages = self._compact_messages(messages)
        raise RuntimeError(f"Tool-call limit reached ({self.config.max_tool_rounds}) before successful completion")

    @staticmethod
    def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
        arguments = json.loads(raw or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        return arguments

    def _compact_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        files = self.tools.tool_list_files("output").get("files", []) if self.tools else []
        notes = "\n".join(self.run_notes[-16:]) or "No tools have run yet."
        validation = self.tools.last_validation if self.tools else None
        validation_note = json.dumps(validation, ensure_ascii=False)[:6000] if validation else "No validation has run yet."
        checkpoint = (
            "Context checkpoint: previous tool-call transcripts were compacted. Their changes remain in the current workspace. "
            "Read files again when details are needed; do not recreate or discard existing work.\n\n"
            "Recent tool actions/results:\n" + notes +
            "\n\nLatest validation result:\n" + validation_note +
            "\n\nCurrent output files:\n" + "\n".join(files[:300])
        )
        return [messages[0], messages[1], {"role": "user", "content": checkpoint[:16000]}]

    @staticmethod
    def _tool_note(name: str, arguments: dict[str, Any], result: dict[str, Any]) -> str:
        if name == "write_file":
            detail = f"{arguments.get('path', '?')} ({result.get('bytes', len(str(arguments.get('content', '')).encode('utf-8')))} bytes)"
        elif name == "read_file":
            detail = f"{arguments.get('path', '?')} ({result.get('characters', 0)} chars; truncated={result.get('truncated', False)})"
        elif name == "list_files":
            detail = f"{arguments.get('domain', '?')}: {len(result.get('files', []))} files"
        elif name == "get_requirement":
            detail = f"{arguments.get('req_id', '?')}: {len(result.get('scenarios', []))} scenarios retrieved"
        elif name == "inspect_reference_image":
            detail = str(result.get("analysis") or result.get("error") or "no visual result")[:900]
        elif name == "validate_application":
            commands = result.get("commands", [])
            detail = f"{result.get('phase', 'validation')} ok={result.get('ok')} checks={result.get('checks', {})}"
            for command in commands:
                if command.get("returncode"):
                    detail += f"; failed {command.get('cwd')} exit={command.get('returncode')} "
                    detail += str(command.get("stderr") or command.get("stdout") or "")[-900:]
        elif name == "record_interface":
            detail = f"{arguments.get('req_id', '?')} {arguments.get('content', '')[:300]}"
        else:
            detail = str(result.get("summary") or result.get("error") or result.get("ok", ""))[:500]
        return f"{name}: {'ok' if result.get('ok', True) else 'FAILED'} - {detail}"

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
                    "model_usage": self._model_usage()}
        if name == "inspect_reference_image":
            if not self.vision_client:
                return {"ok": False, "error": "No usable VISUAL_MODEL/VISION_MODEL endpoint and key were injected; use written requirements."}
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
            try:
                answer = self.vision_client.complete([{"role": "user", "content": [
                    {"type": "text", "text": str(args.get("question") or "Describe the visible layout and controls.")[:2000]},
                    {"type": "image_url", "image_url": {"url": f"data:{media};base64,{encoded}"}},
                ]}], model=self.config.vision_model, max_tokens=3000)
            except Exception as exc:
                detail = str(exc).replace(self.config.vision_api_key, "[redacted]")[:500]
                return {"ok": False, "error": f"Vision request failed: {detail}. Continue from written requirements."}
            return {"ok": True, "analysis": answer.message.get("content") or ""}
        assert self.tools is not None
        return self.tools.execute(name, args)

    def _model_usage(self) -> dict[str, int]:
        clients = [self.client] + ([self.vision_client] if self.vision_client else [])
        return {key: sum(getattr(client, attr) for client in clients)
                for key, attr in (("requests", "calls"), ("prompt_tokens", "prompt_tokens"),
                                  ("completion_tokens", "completion_tokens"))}

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
