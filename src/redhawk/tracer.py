"""Middleware that traces sub-agent dispatches and coordinator tool calls to
stdout in real time. Hooks fire synchronously during invoke — no streaming,
no async, no namespace mapping needed."""

from __future__ import annotations

from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware
from langchain.tools.tool_node import ToolCallRequest

# Shared toolset — populated by main.py at startup.
TOOLS: list[str] = []


def set_tools(tool_names: list[str]) -> None:
    TOOLS.extend(tool_names)


def _fmt_args(args: dict, maxlen: int = 50) -> str:
    """Short single-line preview of a tool-call argument dict."""
    for key in ("command", "file_path", "query", "url", "pattern", "path", "data"):
        v = args.get(key)
        if v is not None:
            s = str(v).replace("\n", " ").strip()
            return s[:maxlen] + ("..." if len(s) > maxlen else "")
    for v in args.values():
        s = str(v).replace("\n", " ").strip()
        return s[:maxlen] + ("..." if len(s) > maxlen else "")
    return ""


def _skill_names(paths):
    if not paths:
        return []
    return [Path(p).name for p in paths]


_CYAN = "\033[36m"
_DIM = "\033[2m"
_RESET = "\033[0m"


class TraceMiddleware(AgentMiddleware):
    """Intercepts coordinator tool calls for live CLI progress display.

    - task()   → prints the dispatch line + [skills]/[tools] once per agent.
    - others   → prints tool(args) trace (execute, read_file, …).
    - write_todos → silently skipped (internal planning tool, noisy).
    """

    def __init__(self):
        self.info_shown: set[str] = set()

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        tc = request.tool_call
        name = str(tc.get("name", ""))

        if name == "task":
            args = tc.get("args", {})
            agent = str(args.get("subagent_type", "?"))
            desc = str(args.get("description", "")).strip().replace("\n", " ")
            preview = desc[:60] + ("..." if len(desc) > 60 else "")
            print(f"{_CYAN}[{agent}]{_RESET} {preview}")
            if agent not in self.info_shown:
                self.info_shown.add(agent)
                from redhawk.builder import SUBAGENTS as _SAs
                skills_by_name = {s["name"]: s.get("skills") for s in _SAs}
                skills = _skill_names(skills_by_name.get(agent))
                print(f"  {_DIM}[skills]{_RESET} {', '.join(skills) if skills else '(none)'}")
                print(f"  {_DIM}[tools]{_RESET}  {', '.join(TOOLS)}")
        elif name == "write_todos":
            return handler(request)
        else:
            args = tc.get("args", {})
            print(f"  {name}({_fmt_args(args)})")

        return handler(request)
