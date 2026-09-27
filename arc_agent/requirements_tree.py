from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


REQ_ID = re.compile(r"^REQ-\d+(?:[.-]\d+)*$")


@dataclass
class Requirement:
    id: str
    name: str
    description: str = ""
    kind: str = ""
    parent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    children: list["Requirement"] = field(default_factory=list)

    @property
    def atomic(self) -> bool:
        return self.kind == "ATOMIC" or (not self.children and self.kind != "FOLDER")


@dataclass
class RequirementTree:
    root: Requirement
    nodes: dict[str, Requirement]
    ordered_atomic: list[Requirement]

    def serializable(self) -> dict[str, Any]:
        def pack(node: Requirement) -> dict[str, Any]:
            return {"id": node.id, "name": node.name, "type": node.kind,
                    "description": node.description, "dependencies": node.dependencies,
                    "scenarios": node.scenarios, "children": [pack(x) for x in node.children]}
        return pack(self.root)

    def prompt_document(self, max_chars: int = 220_000) -> str:
        # Include every node's source description once; scenario titles keep coverage visible
        # without repeating large, often boilerplate-heavy Gherkin blocks.
        out = [f"Application: {self.root.name}\n{self.root.description}"]
        for node in self.nodes.values():
            if node is self.root:
                continue
            out.append(f"\n[{node.id}] {node.name} ({node.kind})")
            if node.description:
                out.append(node.description)
            if node.dependencies:
                out.append("Dependencies: " + ", ".join(node.dependencies))
            if node.scenarios:
                out.append("Scenarios: " + "; ".join(
                    str(s.get("name") or s.get("id") or "scenario") for s in node.scenarios))
        text = "\n".join(out)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[TRUNCATED: inspect requirements.yaml for complete wording.]"
        return text


def load_tree(requirement_dir: Path) -> RequirementTree:
    candidates = [requirement_dir / "requirements.yaml", requirement_dir / "requirements.yml"]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise FileNotFoundError(f"requirements.yaml not found in {requirement_dir}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and not raw.get("id"):
        raw = next((raw[key] for key in ("root", "requirement") if isinstance(raw.get(key), dict)), raw)
    if not isinstance(raw, dict) or not raw.get("id"):
        raise ValueError("Requirement document must be a tree mapping with an id")
    nodes: dict[str, Requirement] = {}

    def parse(item: dict[str, Any], parent: str | None = None) -> Requirement:
        req_id = str(item.get("id") or item.get("req_id") or "").strip()
        if not req_id or (req_id != "ROOT" and not (req_id.startswith("REQ-") and REQ_ID.fullmatch(req_id))):
            raise ValueError(f"Invalid requirement id {req_id!r} under {parent or 'root'}")
        if req_id in nodes:
            raise ValueError(f"Duplicate requirement id: {req_id}")
        node = Requirement(req_id, str(item.get("name") or req_id).strip(),
                           str(item.get("description") or "").strip(),
                           str(item.get("type") or "").upper(), parent,
                           [str(x).strip() for x in item.get("dependencies", []) if str(x).strip()],
                           [dict(x) for x in item.get("scenarios", []) if isinstance(x, dict)])
        nodes[req_id] = node
        node.children = [parse(child, req_id) for child in item.get("children", []) if isinstance(child, dict)]
        for index, scenario in enumerate(node.scenarios, 1):
            scenario.setdefault("id", f"{req_id}::scenario-{index}")
        return node

    root = parse(raw)
    atomic = [n for n in nodes.values() if n.atomic]
    atomic_ids = {n.id for n in atomic}
    pending: dict[str, set[str]] = {}
    for node in atomic:
        deps: set[str] = set()
        for dep in node.dependencies:
            if dep not in nodes:
                continue
            target = nodes[dep]
            if target.atomic:
                deps.add(target.id)
            else:
                deps.update(d.id for d in descendants(target) if d.atomic)
        pending[node.id] = deps & atomic_ids - {node.id}
    ordered: list[Requirement] = []
    done: set[str] = set()
    while pending:
        ready = next((rid for rid in pending if pending[rid] <= done), None)
        if ready is None:
            raise ValueError("Requirement dependencies contain a cycle")
        ordered.append(nodes[ready])
        done.add(ready)
        del pending[ready]
    return RequirementTree(root, nodes, ordered)


def descendants(node: Requirement) -> list[Requirement]:
    return [child for item in node.children for child in ([item] + descendants(item))]
