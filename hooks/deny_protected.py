#!/usr/bin/env python3
"""octos before_tool_call hook: refuse file writes into protected directories.

argv: protected directory paths. stdin: the octos HookPayload JSON. Exit 1 =
deny (stdout becomes the reason shown to the model), exit 0 = allow. Shell
commands cannot be inspected here (octos redacts their arguments), so the
harness additionally restores the protected tree after every turn.
"""
import json
import os
import sys


def main() -> int:
    protected = [os.path.realpath(p) for p in sys.argv[1:] if p]
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 - unreadable payload: allow, never block work
        return 0
    args = payload.get("arguments") or {}
    if not isinstance(args, dict):
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    for key in ("path", "file_path", "filename", "file"):
        value = args.get(key)
        if not isinstance(value, str) or not value:
            continue
        target = os.path.realpath(value if os.path.isabs(value) else os.path.join(cwd, value))
        for root in protected:
            if target == root or target.startswith(root + os.sep):
                print(f"Denied: {value} is inside the protected directory {root} (official tests / "
                      f"requirements are read-only). Change frontend/ or backend/ instead.")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
