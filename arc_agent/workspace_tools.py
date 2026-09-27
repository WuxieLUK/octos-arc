from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .validator import AppValidator


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = [
    _schema("list_files", "List workspace files (requirements or generated application).", {
        "domain": {"type": "string", "enum": ["requirements", "output"]},
        "path": {"type": "string", "description": "Relative directory; empty for root"}}, ["domain"]),
    _schema("read_file", "Read a UTF-8 text file from the requirements or output workspace.", {
        "domain": {"type": "string", "enum": ["requirements", "output"]},
        "path": {"type": "string"}, "max_chars": {"type": "integer"}}, ["domain", "path"]),
    _schema("get_requirement", "Read one complete requirement including source scenarios and parent contracts.", {
        "req_id": {"type": "string"}}, ["req_id"]),
    _schema("search_text", "Search source and requirements text files.", {
        "domain": {"type": "string", "enum": ["requirements", "output"]},
        "query": {"type": "string"}}, ["domain", "query"]),
    _schema("inspect_reference_image", "Ask the configured vision model to inspect a reference image from the requirements package. Only use when visual layout matters.", {
        "path": {"type": "string"}, "question": {"type": "string"}}, ["path", "question"]),
    _schema("write_file", "Create or replace one generated application file. Paths are confined to output-dir.", {
        "path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _schema("record_interface", "Record a requirement-linked implemented UI or HTTP interface. The file must exist; do not claim unverified behavior.", {
        "req_id": {"type": "string"}, "kind": {"type": "string", "enum": ["ui", "http"]},
        "content": {"type": "string"}, "file_path": {"type": "string"},
        "line": {"type": "integer"}}, ["req_id", "kind", "content", "file_path"]),
    _schema("validate_application", "Install dependencies and run the frontend build, frontend/backend test suites, or backend health/root/404 startup checks.", {
        "mode": {"type": "string", "enum": ["build", "tests", "startup"]}}, ["mode"]),
    _schema("finish_generation", "Finish only after startup validation succeeded. Returns a validation error otherwise.", {
        "summary": {"type": "string"}}, []),
]


class WorkspaceTools:
    def __init__(self, requirements: Path, output: Path, validator: AppValidator,
                 traceability: Any, requirement_ids: set[str], max_file_bytes: int,
                 requirement_nodes: dict[str, Any] | None = None) -> None:
        self.roots = {"requirements": requirements.resolve(), "output": output.resolve()}
        self.validator, self.traceability = validator, traceability
        self.requirement_ids, self.max_file_bytes = requirement_ids, max_file_bytes
        self.requirement_nodes = requirement_nodes or {}
        self.last_validation: dict[str, Any] | None = None

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            fn = getattr(self, f"tool_{name}", None)
            if not fn:
                return {"ok": False, "error": f"Unknown tool: {name}"}
            return fn(**args)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:1000]}"}

    def _resolve(self, domain: str, rel: str) -> Path:
        if domain not in self.roots:
            raise ValueError("Unsupported workspace domain")
        path = Path(rel)
        if path.is_absolute():
            raise ValueError("Absolute paths are not allowed")
        root = self.roots[domain]
        target = (root / path).resolve()
        target.relative_to(root)
        return target

    @staticmethod
    def _ignored(path: Path) -> bool:
        return any(part in {"node_modules", ".git", "dist", "__pycache__", ".arc-test-db"} for part in path.parts)

    def tool_list_files(self, domain: str, path: str = "") -> dict[str, Any]:
        root = self.roots[domain]
        target = self._resolve(domain, path)
        if not target.is_dir():
            return {"ok": False, "error": "Directory not found"}
        files = [f.relative_to(root).as_posix() for f in sorted(target.rglob("*"))
                 if f.is_file() and not self._ignored(f)]
        return {"ok": True, "files": files[:1000], "truncated": len(files) > 1000}

    def tool_read_file(self, domain: str, path: str, max_chars: int = 40000) -> dict[str, Any]:
        target = self._resolve(domain, path)
        if not target.is_file():
            return {"ok": False, "error": "File not found"}
        if target.stat().st_size > 2_000_000:
            return {"ok": False, "error": "File exceeds 2 MB read limit"}
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": "File is not UTF-8 text"}
        cap = max(1000, min(int(max_chars), 80000))
        return {"ok": True, "path": path, "content": content[:cap],
                "characters": len(content), "truncated": len(content) > cap}

    def tool_get_requirement(self, req_id: str) -> dict[str, Any]:
        node = self.requirement_nodes.get(req_id)
        if node is None:
            return {"ok": False, "error": f"Unknown requirement id: {req_id}"}
        parent_chain = []
        parent_id = node.parent_id
        while parent_id:
            parent = self.requirement_nodes[parent_id]
            parent_chain.append({"id": parent.id, "name": parent.name, "description": parent.description})
            parent_id = parent.parent_id
        return {"ok": True, "id": node.id, "name": node.name, "type": node.kind,
                "description": node.description, "dependencies": node.dependencies,
                "scenarios": node.scenarios, "parent_contracts": list(reversed(parent_chain))}

    def tool_search_text(self, domain: str, query: str) -> dict[str, Any]:
        if not query.strip():
            return {"ok": False, "error": "Query cannot be empty"}
        root = self.roots[domain]
        suffixes = {".yaml", ".yml", ".md", ".json", ".js", ".jsx", ".ts", ".tsx", ".css", ".html"}
        matches = []
        for file in sorted(root.rglob("*")):
            if not file.is_file() or file.suffix.lower() not in suffixes or self._ignored(file):
                continue
            try:
                for n, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query.casefold() in line.casefold():
                        matches.append({"path": file.relative_to(root).as_posix(), "line": n, "text": line[:320]})
                        if len(matches) == 100:
                            return {"ok": True, "matches": matches, "truncated": True}
            except OSError:
                continue
        return {"ok": True, "matches": matches, "truncated": False}

    def tool_write_file(self, path: str, content: str) -> dict[str, Any]:
        if len(content.encode("utf-8")) > self.max_file_bytes:
            return {"ok": False, "error": f"File exceeds {self.max_file_bytes}-byte write limit"}
        target = self._resolve("output", path)
        if target == self.roots["output"]:
            return {"ok": False, "error": "Path must include a filename"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": target.relative_to(self.roots["output"]).as_posix(),
                "bytes": len(content.encode("utf-8"))}

    def tool_record_interface(self, req_id: str, kind: str, content: str, file_path: str,
                              line: int | None = None) -> dict[str, Any]:
        if req_id not in self.requirement_ids:
            return {"ok": False, "error": f"Unknown requirement id: {req_id}"}
        target = self._resolve("output", file_path)
        if not target.is_file():
            return {"ok": False, "error": "Evidence file does not exist"}
        source = target.read_text(encoding="utf-8", errors="replace")
        lines = source.splitlines()
        if line is not None and not 1 <= line <= len(lines):
            return {"ok": False, "error": "Evidence line is outside the file"}
        if not content.strip():
            return {"ok": False, "error": "Interface description cannot be empty"}
        if kind == "ui" and not re.search(r"\b(button|link|textbox|grid|gridcell|tab|checkbox|combobox|dialog|menuitem|generic)\b", content, re.I):
            return {"ok": False, "error": "UI interface content must specify an accessible role"}
        ordinal = len(self.traceability.list_interfaces(req_id=req_id))
        interface_id = f"{req_id}:{'ui' if kind == 'ui' else 'route'}:{ordinal}"
        self.traceability.upsert_interface(interface_id=interface_id, req_ids=[req_id], type=kind if kind == "ui" else "http",
                                           content=content.strip(), file_path=target.relative_to(self.roots["output"]).as_posix(),
                                           first_line=str(line) if line else None, implemented=True)
        return {"ok": True, "interface_id": interface_id, "evidence_file": file_path}

    def tool_validate_application(self, mode: str) -> dict[str, Any]:
        self.last_validation = self.validator.validate(mode)
        return self.last_validation
