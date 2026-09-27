from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "arcbench-agent-runtime" / "src"))

from arc_agent.config import Config
from arc_agent.model_client import ChatClient
from arc_agent.orchestrator import Agent
from arc_agent.requirements_tree import load_tree
from arc_agent.validator import AppValidator
from arc_agent.workspace_tools import WorkspaceTools
from arcbench_agent_runtime import AgentRuntime


class RequirementTreeTests(unittest.TestCase):
    def _load(self, text: str):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        (directory / "requirements.yaml").write_text(text, encoding="utf-8")
        return load_tree(directory)

    def test_supports_both_id_formats_and_sorts_dependencies(self):
        tree = self._load("id: ROOT\nname: App\ntype: FOLDER\nchildren:\n  - id: REQ-1.2\n    type: ATOMIC\n  - id: REQ-1-1-1\n    type: ATOMIC\n    dependencies: [REQ-1.2]\n")
        self.assertEqual([n.id for n in tree.ordered_atomic], ["REQ-1.2", "REQ-1-1-1"])

    def test_rejects_dependency_cycles(self):
        text = "id: ROOT\ntype: FOLDER\nchildren:\n  - {id: REQ-1, type: ATOMIC, dependencies: [REQ-2]}\n  - {id: REQ-2, type: ATOMIC, dependencies: [REQ-1]}\n"
        with self.assertRaisesRegex(ValueError, "cycle"):
            self._load(text)

    def test_complete_tree_descriptions_are_not_truncated(self):
        tree = self._load("id: ROOT\ntype: FOLDER\nchildren:\n  - id: REQ-1-1-1\n    name: Feature\n    type: ATOMIC\n    description: Full contract\n    scenarios:\n      - name: Persistent refresh\n        steps:\n          - {keyword: THEN, content: Values remain after refresh}\n")
        self.assertIn("[REQ-1-1-1] Feature", tree.prompt_document())
        self.assertIn("Persistent refresh", tree.prompt_document())
        self.assertLess(len(tree.prompt_document()), 220000)


class ToolBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.req, self.out = root / "req", root / "out"
        self.req.mkdir()
        self.out.mkdir()
        self.runtime = AgentRuntime.from_env(project_dir=str(self.out))
        self.tools = WorkspaceTools(self.req, self.out, AppValidator(self.out), self.runtime.traceability,
                                    {"REQ-1-1-1"}, 1024 * 1024)

    def test_paths_are_confined(self):
        self.assertTrue(self.tools.tool_write_file("frontend/src/App.tsx", "<button>Save</button>")["ok"])
        self.assertTrue(self.tools.tool_read_file("output", "frontend/src/App.tsx")["ok"])
        for path in ("../outside", "/tmp/outside"):
            self.assertFalse(self.tools.execute("write_file", {"path": path, "content": "no"})["ok"])

    def test_interface_requires_valid_id_role_and_file(self):
        self.tools.tool_write_file("frontend/src/App.tsx", '<button aria-label="Save">Save</button>')
        self.assertFalse(self.tools.tool_record_interface("REQ-9", "ui", 'button "Save"', "frontend/src/App.tsx")["ok"])
        self.assertFalse(self.tools.tool_record_interface("REQ-1-1-1", "ui", "Save control", "frontend/src/App.tsx")["ok"])
        result = self.tools.tool_record_interface("REQ-1-1-1", "ui", 'button "Save"', "frontend/src/App.tsx", 1)
        self.assertTrue(result["ok"])
        self.assertEqual(self.runtime.traceability.list_interfaces()[0]["type"], "ui")

    def test_requirement_lookup_includes_full_scenario_steps(self):
        self.tools.requirement_nodes = {
            "ROOT": type("Node", (), {"id": "ROOT", "parent_id": None, "name": "root", "description": ""})(),
            "REQ-1-1-1": type("Node", (), {"id": "REQ-1-1-1", "parent_id": "ROOT", "name": "Feature",
                                               "description": "Contract", "kind": "ATOMIC", "dependencies": [],
                                               "scenarios": [{"name": "flow", "steps": [{"keyword": "THEN", "content": "exact state"}]}]})(),
        }
        result = self.tools.tool_get_requirement("REQ-1-1-1")
        self.assertEqual(result["scenarios"][0]["steps"][0]["content"], "exact state")

    def test_store_init_preserves_platform_seeded_requirements(self):
        file = self.out / ".arc/traceability/requirements.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        seeded = {"REQ-1-1-1": {"req_id": "REQ-1-1-1", "name": "seeded"}}
        file.write_text(json.dumps(seeded), encoding="utf-8")
        self.runtime.traceability.init_store()
        self.assertEqual(json.loads(file.read_text(encoding="utf-8")), seeded)

    def test_validator_checks_required_package_contract(self):
        result = self.tools.validator.validate("build")
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "structure")


class ModelClientTests(unittest.TestCase):
    def test_preserves_reasoning_and_tool_calls(self):
        client = ChatClient("https://example.test/chat/completions", "key", "deepseek-v4-flash", 5, 2048)
        message = {"role": "assistant", "content": None, "reasoning_content": "plan",
                   "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}]}
        payload = {"choices": [{"message": message}], "usage": {"prompt_tokens": 3, "completion_tokens": 5}}

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(payload).encode()

        with patch("arc_agent.model_client.request.urlopen", side_effect=[FakeResponse(), FakeResponse()]) as mock:
            result = client.complete([{"role": "user", "content": "go"}], [{"type": "function"}])
            next_messages = [{"role": "user", "content": "go"}, result.message,
                             {"role": "tool", "tool_call_id": "call-1", "name": "list_files", "content": "{}"}]
            client.complete(next_messages, [{"type": "function"}])
        self.assertEqual(result.message["reasoning_content"], "plan")
        self.assertEqual(result.message["tool_calls"][0]["id"], "call-1")
        request_body = json.loads(mock.call_args_list[0].args[0].data)
        self.assertEqual(request_body["thinking"]["type"], "enabled")
        self.assertIn("tools", request_body)
        self.assertNotIn("tool_choice", request_body)
        self.assertEqual(client.prompt_tokens, 6)
        followup_body = json.loads(mock.call_args_list[1].args[0].data)
        self.assertEqual(followup_body["messages"][1]["reasoning_content"], "plan")


class ConfigTests(unittest.TestCase):
    def test_uses_the_simulators_separate_visual_credentials(self):
        env = {
            "OPENAI_API_KEY": "primary-key", "OPENAI_BASE_URL": "https://main.example/v1", "MODEL": "main-model",
            "VISUAL_API_KEY": "visual-key", "VISUAL_BASE_URL": "https://vision.example/v1",
            "VISUAL_MODEL": "vision-model",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env(Path("requirements"), Path("output"))
        self.assertEqual(config.endpoint, "https://main.example/v1/chat/completions")
        self.assertEqual(config.vision_endpoint, "https://vision.example/v1/chat/completions")
        self.assertEqual(config.api_key, "primary-key")
        self.assertEqual(config.vision_api_key, "visual-key")
        self.assertEqual(config.vision_model, "vision-model")

    def test_visual_credentials_fall_back_to_primary_when_not_separated(self):
        env = {"OPENAI_API_KEY": "primary-key", "OPENAI_BASE_URL": "https://main.example/v1",
               "MODEL": "main-model", "VISION_MODEL": "vision-model"}
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env(Path("requirements"), Path("output"))
        self.assertEqual(config.vision_endpoint, config.endpoint)
        self.assertEqual(config.vision_api_key, config.api_key)


class VisionToolTests(unittest.TestCase):
    def test_vision_failure_is_reported_without_exposing_the_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reference").mkdir()
            (root / "reference/ui.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            agent = Agent.__new__(Agent)
            agent.config = SimpleNamespace(requirement_dir=root, vision_model="vision", vision_api_key="visual-secret")
            agent.vision_client = Mock()
            agent.vision_client.complete.side_effect = RuntimeError("service rejected visual-secret")
            result = agent._execute("inspect_reference_image", {"path": "reference/ui.png", "question": "Read the form"})
        self.assertFalse(result["ok"])
        self.assertNotIn("visual-secret", result["error"])
        self.assertIn("Continue from written requirements", result["error"])


class ContextCompactionTests(unittest.TestCase):
    def test_tool_arguments_must_be_a_json_object(self):
        self.assertEqual(Agent._parse_tool_arguments(None), {})
        self.assertEqual(Agent._parse_tool_arguments('{"path":"App.tsx"}'), {"path": "App.tsx"})
        with self.assertRaisesRegex(ValueError, "must be an object"):
            Agent._parse_tool_arguments('["not", "an", "object"]')

    def test_checkpoint_keeps_initial_contract_recent_notes_and_workspace_files(self):
        agent = Agent.__new__(Agent)
        agent.tools = SimpleNamespace(
            tool_list_files=Mock(return_value={"files": ["frontend/src/App.tsx", "backend/src/server.js"]}),
            last_validation={"phase": "startup", "ok": False, "error": "unknown route returned 200"},
        )
        agent.run_notes = ["write_file: ok - frontend/src/App.tsx (123 bytes)"]
        original = [
            {"role": "system", "content": "system contract"},
            {"role": "user", "content": "full requirement contract"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "old"}]},
            {"role": "tool", "tool_call_id": "old", "content": "large previous output"},
        ]

        compacted = agent._compact_messages(original)

        self.assertEqual(len(compacted), 3)
        self.assertEqual(compacted[0], original[0])
        self.assertEqual(compacted[1], original[1])
        self.assertIn("write_file: ok", compacted[2]["content"])
        self.assertIn("unknown route returned 200", compacted[2]["content"])
        self.assertIn("frontend/src/App.tsx", compacted[2]["content"])
        self.assertNotIn("large previous output", compacted[2]["content"])


if __name__ == "__main__":
    unittest.main()
