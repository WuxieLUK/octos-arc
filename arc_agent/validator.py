from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib import error, request


class AppValidator:
    def __init__(self, output_dir: Path, timeout: int = 240) -> None:
        self.root, self.timeout = output_dir, timeout

    def validate(self, mode: str = "build") -> dict:
        if mode not in {"build", "tests", "startup"}:
            return {"ok": False, "error": f"unsupported validation mode: {mode}"}
        issue = self._structure()
        if issue:
            return {"ok": False, "phase": "structure", "error": issue}
        npm = shutil.which("npm")
        if not npm:
            return {"ok": False, "phase": "runtime", "error": "npm is not installed"}
        logs = []
        for directory in (self.root / "backend", self.root / "frontend"):
            result = self._run([npm, "install", "--no-audit", "--no-fund"], directory)
            logs.append(result)
            if result["returncode"]:
                return {"ok": False, "phase": "install", "commands": logs}
        build = self._run([npm, "run", "build"], self.root / "frontend")
        logs.append(build)
        if build["returncode"]:
            return {"ok": False, "phase": "build", "commands": logs}
        if mode == "build":
            return {"ok": True, "phase": "build", "commands": logs}
        if mode == "tests":
            for directory in (self.root / "frontend", self.root / "backend"):
                result = self._run([npm, "test"], directory)
                logs.append(result)
                if result["returncode"]:
                    return {"ok": False, "phase": "tests", "commands": logs}
            return {"ok": True, "phase": "tests", "commands": logs}
        port = self._free_port()
        env = os.environ.copy()
        env.update(PORT=str(port), ARC_EXTRA_PORTS="0")
        with tempfile.TemporaryFile(mode="w+b") as output:
            try:
                server = subprocess.Popen([npm, "start"], cwd=self.root / "backend", env=env,
                                          stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                          start_new_session=True)
            except OSError as exc:
                return {"ok": False, "phase": "startup", "error": str(exc), "commands": logs}
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and server.poll() is None:
                    if self._get(base + "/api/health")[0] is not None:
                        break
                    time.sleep(0.4)
                health = self._get(base + "/api/health")
                root = self._get(base + "/")
                unknown = self._get(base + "/__agent_validation_404__")
                output.seek(0)
                startup_log = output.read(8000).decode("utf-8", errors="replace")
                checks = {"health_status": health[0], "root_status": root[0], "unknown_status": unknown[0]}
                ok = health[0] is not None and 200 <= health[0] < 300 and root[0] is not None and 200 <= root[0] < 400 and unknown[0] == 404
                return {"ok": ok, "phase": "startup", "checks": checks, "startup_log": startup_log,
                        "commands": logs}
            finally:
                if server.poll() is None:
                    try:
                        os.killpg(server.pid, 15)
                        server.wait(timeout=5)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(server.pid, 9)
                        except ProcessLookupError:
                            pass
                        server.wait(timeout=5)

    def _structure(self) -> str | None:
        for rel, script in (("frontend/package.json", "build"), ("backend/package.json", "start")):
            file = self.root / rel
            if not file.is_file():
                return f"{rel} is required"
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return f"Invalid {rel}: {exc}"
            if not (data.get("scripts") or {}).get(script):
                return f"{rel} must define scripts.{script}"
        return None

    def _run(self, command: list[str], cwd: Path) -> dict:
        try:
            result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                    timeout=self.timeout, check=False)
            return {"command": command[1:], "cwd": cwd.name, "returncode": result.returncode,
                    "stdout": result.stdout[-3500:], "stderr": result.stderr[-3500:]}
        except subprocess.TimeoutExpired as exc:
            return {"command": command[1:], "cwd": cwd.name, "returncode": 124, "timeout": self.timeout,
                    "stdout": _text(exc.stdout)[-1500:], "stderr": _text(exc.stderr)[-1500:]}

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _get(url: str) -> tuple[int | None, str]:
        try:
            with request.urlopen(url, timeout=3) as response:
                return response.status, response.read(1000).decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            return exc.code, exc.read(1000).decode("utf-8", errors="replace")
        except (error.URLError, TimeoutError):
            return None, "no response"


def _text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
