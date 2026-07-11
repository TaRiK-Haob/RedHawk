# AGENTS.md

## Quick Start
```bash
uv run python main.py        # Textual TUI (default)
uv run python main.py --cli  # inline REPL fallback
uv run tinyagent             # via entry point (same as default)
```

## Architecture
- **Framework**: `deepagents >= 0.6.12` with sub-agents. Python 3.12, uv deps.
- **Assembly**: `src/tinyagent/builder.py` → `build_agent()` — coordinator + 6 domain sub-agents + auto general-purpose.
- **Config**: Two model tiers in `src/tinyagent/config.py` — `build_model()` (main, temp=0.2) and `build_recon_model()` (cheaper, temp=0.1). OpenAI-compat via `.env`.
- **Extension**: Add sub-agent prompt to `src/tinyagent/prompts.py`, then append dict to `SUBAGENTS` in `builder.py`.

## Workspace (.redhawk/)
- Created at runtime. All file + shell tools operate inside this directory.
- **Memory**: `.redhawk/memories/AGENTS.md` — cross-session persistent. Mounted as virtual `/memories/AGENTS.md` via `FilesystemBackend`.
- **Skills**: `.redhawk/skills/` — 11 CTF skill dirs (separate git repo). Sub-agents load skills explicitly; coordinator does not share them.
- **Backend**: `CompositeBackend` — `LocalShellBackend(virtual_mode=False)` for default route, `FilesystemBackend(virtual_mode=True)` for `/memories/`.

## Sub-Agent Discipline (BLACKBOARD)
The coordinator **cannot see** sub-agent internal context — `task()` returns only the final message. Working-tier sub-agents (`web-pentest`, `exploit-dev`, `binary-vuln`, `post-exploit`) must persist all evidence and intermediate results to workspace files. The coordinator reads those files. Recon sub-agents return summaries directly (lighter output).

## MCP Web Search
- Exa + Tavily via `langchain-mcp-adapters`. Uses `npx -y mcp-remote <url>` as SSE→stdio bridge.
- Async init: `src/tinyagent/mcp_tools.py → init_mcp_tools()`. Uses native HTTP transport for both servers; no npx needed.
- Logs go to `.redhawk/mcp.log` (can grow large).

## TUI Quirks
- Chat input: **Enter** submits, **Ctrl+J** inserts newline.
- Quit: **Ctrl+Q** or **Ctrl+D**.
- Focus left sidebar: **Ctrl+L**. Click any agent row or press **Enter** to open its detail popup.
- **Escape** closes the agent detail popup (or refocuses the input when no popup is open).
- Tool interception uses `ToolTrackerMiddleware` (Textual messages), NOT the CLI's `TraceMiddleware`. Each sub-agent gets its own `ToolTrackerMiddleware` instance via `builder.py`'s `subagent_middleware_factory`.
- **Must define both `wrap_tool_call` (sync) and `awrap_tool_call` (async)** — coordinator runs via `astream()` but sub-agents run via `invoke()` inside the `task` tool.
- **Do not** name App-level attributes `_flush` — it collides with Textual's internal `App._flush()` and silently breaks the message pump.

## No Tests / No CI
- No test suite, no CI pipeline, no linter config. Changes are validated manually.

## Environment
- `.env` (gitignored) supplies: `OPENAI_API_KEY`, `OPENAI_API_BASE_URL` (default `https://api.deepseek.com`), `OPENAI_API_MODEL` (default `deepseek-v4-flash`), `TAVILY_API_KEY`, `EXA_API_KEY`.
