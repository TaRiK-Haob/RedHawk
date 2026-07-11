# tinyAgent

A minimal security red-team agent built on [deepagents](https://github.com/langchain-ai/deepagents).
This is the foundational agent loop — extensible toward full offensive-security
coverage (web/binary vuln mining, exploitation, multi-stage pentest, cloud, evasion).

## Quick start

```bash
# deps already added via: uv add deepagents langchain-deepseek langchain-mcp-adapters python-dotenv textual
uv run python main.py        # Textual TUI (default)
uv run python main.py --cli  # inline REPL fallback
```

Configure the model in `.env` (OpenAI-compatible endpoint):

```
OPENAI_API_KEY=...
OPENAI_API_BASE_URL=https://api.deepseek.com
OPENAI_API_MODEL=deepseek-v4-flash
```

Then type objectives, e.g. `recon 10.10.14.5 and report the open services`.

## Architecture

```
main.py              # convenience launcher (delegates to package entry point)
src/
└── tinyagent/
    ├── __init__.py      # exports build_agent()
    ├── __main__.py      # package entry point (python -m tinyagent)
    ├── config.py        # loads .env, builds the LLM client (ChatDeepSeek)
    ├── prompts.py       # system prompts (coordinator + 7 sub-agents)
    ├── builder.py       # assembles the agent + sub-agents  <-- EXTENSION POINT
    ├── textual_app.py   # Textual TUI (chat panel, sidebar, middleware)
    ├── tui_cli.py       # CLI fallback REPL
    ├── tracer.py        # CLI tool-call tracing middleware
    ├── workspace.py     # `.redhawk/` workspace setup + CompositeBackend
    ├── memory.py        # cross-session persistent memory
    └── mcp_tools.py     # Tavily + Exa web search tool loader
```

The coordinator agent plans and delegates to specialists via deepagents'
built-in `task` tool. Each specialist runs in an isolated context window.
The backend provides the built-in toolset: file tools (ls/read_file/
write_file/edit_file/glob/grep), the shell `execute` tool, and `task`.

## Workspace (`.redhawk/`)

At startup the agent creates a `.redhawk/` directory in the current working
directory as its workspace. **It is the working directory for all tools**:

- Both file tools (ls/read_file/write_file/edit_file/glob/grep) and the shell
  `execute` tool run with `.redhawk/` as cwd, so relative paths land there.
  Paths are used as-is (`virtual_mode=False`) — this keeps file tools and
  shell tools on the same real-path basis, avoiding path nesting.
- The workspace absolute path is injected into the coordinator prompt so the
  LLM writes to correct locations.
- Note: this is a working-directory convention, not a hard sandbox — the
  shell `execute` tool is unrestricted by design (a pentest agent must reach
  external targets).

## Memory (cross-session)

The agent has persistent long-term memory at `.redhawk/memories/AGENTS.md`,
exposed as a virtual `/memories/` filesystem via the backend. It is loaded
into the system prompt at startup, so the agent recalls prior knowledge
across **separate runs** (engagement findings, payload notes, operator
preferences). The agent updates it itself via `edit_file`, and you can edit
the markdown file by hand at any time.

Implementation: a `CompositeBackend` routes `/memories/` to a
`FilesystemBackend` (real-disk markdown, zero extra deps) — chosen over a
`StoreBackend` because persistence + human-readability matter more than
multi-user namespacing for this single-user CLI.

## Adding a new security domain

Two changes only:

1. Add a prompt in `src/tinyagent/prompts.py`: 

   ```python
   BINARY_EXPLOIT_PROMPT = "You are a binary-exploitation specialist..."
   ```

2. Append a dict in `src/tinyagent/builder.py`: 

   ```python
   SUBAGENTS = [
       ...,
       {
           "name": "binary-exploit",
           "description": "Reverse-engineer binaries and find memory-corruption bugs.",
           "system_prompt": BINARY_EXPLOIT_PROMPT,
       },
   ]
   ```

That's it — the coordinator will now delegate to it automatically.

## Scope & safety

Intended only for targets you are explicitly authorized to test. The
coordinator prompt enforces an authorization check before acting.
