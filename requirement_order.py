"""Requirement-tree helpers: ATOMIC flattening, dependency-ordered traversal,
ancestor lookup and content fingerprints (used by evolution mode).

Pure functions, no I/O — see arc/tests/test_requirement_order.py.
"""

from __future__ import annotations

import hashlib
import json


def flatten_atomic(node: dict, out: list | None = None) -> list[dict]:
    """Leaves in document order. Nodes typed ATOMIC, or untyped leaves, count."""
    if out is None:
        out = []
    node_type = str(node.get("type") or "").upper()
    if node_type == "ATOMIC" or (not node.get("children") and node_type != "FOLDER"):
        out.append(node)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            flatten_atomic(child, out)
    return out


def _descendant_atomic_ids(tree: dict) -> dict[str, list[str]]:
    """Map every node id (folders included) to the ids of its ATOMIC descendants
    (a leaf maps to itself)."""
    mapping: dict[str, list[str]] = {}

    def walk(node: dict) -> list[str]:
        node_id = str(node.get("id") or "")
        children = [c for c in (node.get("children") or []) if isinstance(c, dict)]
        node_type = str(node.get("type") or "").upper()
        if node_type == "ATOMIC" or (not children and node_type != "FOLDER"):
            ids = [node_id]
        else:
            ids = []
            for child in children:
                ids.extend(walk(child))
        if node_id:
            mapping[node_id] = ids
        return ids

    walk(tree)
    return mapping


def topo_order(tree: dict) -> list[dict]:
    """ATOMIC nodes, dependencies first; among ready nodes keep document order.

    Folder dependencies expand to the folder's atomic descendants. Unknown ids
    and self references are ignored. A cycle is broken by taking the earliest
    remaining node in document order, so the function always terminates and
    returns every node exactly once.
    """
    nodes = flatten_atomic(tree)
    descendants = _descendant_atomic_ids(tree)
    ids = [str(n.get("id")) for n in nodes]
    known = set(ids)
    deps: dict[str, list[str]] = {}
    for node, node_id in zip(nodes, ids):
        expanded: list[str] = []
        for dep in node.get("dependencies") or []:
            for atom in descendants.get(str(dep), [str(dep)]):
                if atom in known and atom != node_id and atom not in expanded:
                    expanded.append(atom)
        deps[node_id] = expanded

    done: set[str] = set()
    remaining = list(zip(ids, nodes))
    ordered: list[dict] = []
    while remaining:
        pick = next(((i, n) for i, n in remaining if all(d in done for d in deps[i])), None)
        if pick is None:  # cycle: fall back to document order
            pick = remaining[0]
        remaining.remove(pick)
        done.add(pick[0])
        ordered.append(pick[1])
    return ordered


def ancestors_of(node_id: str, ordered_nodes: list[dict]) -> list[str]:
    """Transitive dependencies of `node_id`, in the given (topological) order."""
    by_id = {str(n.get("id")): n for n in ordered_nodes}
    seen: set[str] = set()
    stack = [str(d) for d in (by_id.get(node_id, {}).get("dependencies") or [])]
    while stack:
        dep = stack.pop()
        if dep in seen or dep not in by_id or dep == node_id:
            continue
        seen.add(dep)
        stack.extend(str(d) for d in (by_id[dep].get("dependencies") or []))
    return [str(n.get("id")) for n in ordered_nodes if str(n.get("id")) in seen]


def node_fingerprint(node: dict) -> str:
    """Stable hash of the requirement content (not of child nodes)."""
    payload = {
        "id": str(node.get("id") or ""),
        "name": str(node.get("name") or ""),
        "description": str(node.get("description") or ""),
        "scenarios": node.get("scenarios") or [],
        "dependencies": [str(d) for d in (node.get("dependencies") or [])],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
