# ARC-Bench Full Rewrite Agent

This project starts from the supplied ARC-Bench blank starter and replaces its generation logic. It targets web-app tasks using the bundled React/Vite + Express/SQLite template; it does not depend on Octos or reuse the v5 orchestration loop.

## Entry contract

```bash
python3 main.py /path/to/requirements --output-dir /path/to/output --type web
```

The runner must inject `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `MODEL`. Optionally inject `VISION_MODEL` or `VISUAL_MODEL` to enable reference-image analysis. Runtime limits can be tuned through `ARC_AGENT_TIME_BUDGET`, `ARC_AGENT_MAX_TOOL_ROUNDS`, `ARC_AGENT_REQUEST_TIMEOUT`, `ARC_AGENT_MAX_OUTPUT_TOKENS`, and `ARC_AGENT_MAX_FILE_BYTES`.

The agent copies missing starter files without overwriting existing workspace files, parses the requirement tree (including dotted and hyphenated IDs), uses model tool calls to inspect and implement the app, then builds and starts it locally. Completion requires the generated frontend build, a successful `GET /api/health`, a working root response, and a 404 for an unknown URL. The optional test validation runs both frontend and backend test scripts. The agent records only interfaces linked to existing source files and never replaces the platform-seeded requirement/scenario tables.

## Modules

- `arc_agent/requirements_tree.py`: YAML parsing, dependency ordering, prompt serialization.
- `arc_agent/model_client.py`: OpenAI-compatible chat/tool-call transport with bounded retries and usage accounting.
- `arc_agent/workspace_tools.py`: confined source tools and SDK-backed interface records.
- `arc_agent/validator.py`: build and startup contract checks.
- `arc_agent/orchestrator.py`: model/tool loop, starter initialization, deadlines, final validation, and checkpoint.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
