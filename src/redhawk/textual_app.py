"""Textual-based TUI for the redhawk security agent.

Multi-pane layout:
  - Left sidebar: sub-agent status list (name, status indicator, elapsed time)
  - Main pane: chat conversation with collapsible thinking/tool call cards
  - Footer: session status bar

Supports streaming thinking, real-time agent status, execution timing,
and click-to-expand tool/thinking sections.
"""

from __future__ import annotations

import time

from langchain_core.messages import AIMessageChunk
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Collapsible,
    Label,
    Static,
    TextArea,
)


class ChatInput(TextArea):
    """Multi-line input: Enter submits, Ctrl+J inserts a newline."""

    class Submitted(Message):
        """Posted when the user submits the input."""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def on_mount(self) -> None:
        self.soft_wrap = True

    def on_key(self, event) -> None:
        if event.key == "enter":
            event.stop()
            self.post_message(self.Submitted(self.text))
            # Defer clear so it runs AFTER any TextArea-internal newline insertion
            self.call_after_refresh(self.clear)
        elif event.key == "j" and event.control:
            event.stop()
            self.insert("\n")

from redhawk import build_agent
from redhawk.mcp_tools import init_mcp_tools
from redhawk.tracer import set_tools
from redhawk.workspace import ensure_workspace
from langchain.agents.middleware.types import AgentMiddleware

# ---------------------------------------------------------------------------
# Agent singleton (lazy-built with ToolTrackerMiddleware)
# ---------------------------------------------------------------------------
_agent = None
_history: list = []


def _get_agent(app: App | None = None):
    global _agent
    # Always set _app — handles the race where run_agent builds the agent
    # before _init_agent's init_mcp_tools() finishes.
    if app:
        ToolTrackerMiddleware.set_app(app)
    if _agent is None:
        _agent = build_agent(
            tui_mode=True,
            extra_middleware=[ToolTrackerMiddleware()],          # coordinator
            subagent_middleware_factory=lambda name: ToolTrackerMiddleware(name),  # per sub-agent
        )
        set_tools(
            sorted(_agent.get_graph().nodes["tools"].data.tools_by_name.keys()),
        )
        ensure_workspace()
    return _agent


# ---------------------------------------------------------------------------
# ToolTrackerMiddleware — unified tool-call tracking for coordinator + sub-agents
# ---------------------------------------------------------------------------

class ToolTrackerMiddleware(AgentMiddleware):
    """Intercepts tool calls and posts structured Textual messages.

    A single class used for BOTH the coordinator (agent_name="") and each
    sub-agent (agent_name="web-recon" etc). Sub-agent instances are injected
    via the SubAgent dict's ``middleware`` key in builder.py.

    IMPORTANT: Both sync ``wrap_tool_call`` and async ``awrap_tool_call`` are
    defined. The coordinator runs via ``astream()`` (async), but sub-agents
    are invoked synchronously via ``subagent.invoke()`` inside the ``task``
    tool. Without the sync hook, sub-agent tool calls are silently skipped.
    """
    _app: App | None = None
    _timers: dict[str, float] = {}

    @classmethod
    def set_app(cls, app: App) -> None:
        cls._app = app

    def __init__(self, agent_name: str = "") -> None:
        self._agent_name = agent_name

    # -- shared helpers (used by both sync + async hooks) --

    def _parse(self, request) -> tuple[str, str, dict, bool, str] | None:
        """Extract tool call info. Returns None to skip (internal/noise)."""
        tc = request.tool_call
        name = str(tc.get("name", "") or "")
        cid = str(tc.get("id", "") or "")
        args = tc.get("args", {}) or {}

        if name in ("", "write_todos"):
            return None

        is_task = name == "task"
        target_agent = ""
        if is_task:
            target_agent = str(args.get("subagent_type", ""))
            if not target_agent or target_agent == "?":
                return None

        return name, cid, args, is_task, target_agent

    def _post_start(self, app, name, cid, args, is_task, target_agent) -> None:
        ToolTrackerMiddleware._timers[cid] = time.time()
        app.post_message(ToolCallStart(  # type: ignore[arg-type]
            agent_name=self._agent_name, tool_name=name, args=args,
            call_id=cid, is_task=is_task, target_agent=target_agent,
        ))

    def _post_done(self, app, cid, is_task, target_agent,
                   result=None, error="") -> None:
        elapsed = time.time() - ToolTrackerMiddleware._timers.pop(cid, time.time())
        out_text = error
        is_error = bool(error)
        if not out_text and result is not None:
            try:
                update = getattr(result, "update", {}) or {}
                msgs = update.get("messages", []) if isinstance(update, dict) else []
                if msgs:
                    out_text = str(getattr(msgs[0], "content", "") or "")
            except Exception:
                pass
        app.post_message(ToolCallDone(  # type: ignore[arg-type]
            agent_name=self._agent_name, call_id=cid, elapsed=elapsed,
            output=out_text, is_task=is_task, target_agent=target_agent,
            error=is_error,
        ))

    # -- sync hook (sub-agents run via invoke()) --

    def wrap_tool_call(self, request, handler):
        app = ToolTrackerMiddleware._app
        if app is None:
            return handler(request)
        info = self._parse(request)
        if info is None:
            return handler(request)
        name, cid, args, is_task, target = info
        self._post_start(app, name, cid, args, is_task, target)
        try:
            result = handler(request)
        except Exception as exc:
            self._post_done(app, cid, is_task, target, error=f"[ERROR] {exc}")
            raise
        self._post_done(app, cid, is_task, target, result=result)
        return result

    # -- async hook (coordinator runs via astream()) --

    async def awrap_tool_call(self, request, handler):
        app = ToolTrackerMiddleware._app
        if app is None:
            return await handler(request)
        info = self._parse(request)
        if info is None:
            return await handler(request)
        name, cid, args, is_task, target = info
        self._post_start(app, name, cid, args, is_task, target)
        try:
            result = await handler(request)
        except Exception as exc:
            self._post_done(app, cid, is_task, target, error=f"[ERROR] {exc}")
            raise
        self._post_done(app, cid, is_task, target, result=result)
        return result


# ---------------------------------------------------------------------------
# Custom Textual messages (posted by ToolTrackerMiddleware → handled by App)
# ---------------------------------------------------------------------------

class ToolCallStart(Message):
    """Posted when any tool call starts (coordinator or sub-agent)."""
    def __init__(self, agent_name: str, tool_name: str, args: dict,
                 call_id: str, is_task: bool = False,
                 target_agent: str = "") -> None:
        super().__init__()
        self.agent_name = agent_name      # "" = coordinator, "web-recon" etc = sub-agent
        self.tool_name = tool_name
        self.args = args
        self.call_id = call_id
        self.is_task = is_task            # True if this is a `task` dispatch
        self.target_agent = target_agent  # sub-agent name for task calls


class ToolCallDone(Message):
    """Posted when any tool call completes."""
    def __init__(self, agent_name: str, call_id: str, elapsed: float,
                 output: str = "", is_task: bool = False,
                 target_agent: str = "", error: bool = False) -> None:
        super().__init__()
        self.agent_name = agent_name
        self.call_id = call_id
        self.elapsed = elapsed
        self.output = output
        self.is_task = is_task
        self.target_agent = target_agent
        self.error = error


class AgentSelected(Message):
    """Posted when the user selects an agent in the sidebar."""
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


class AgentDetailScreen(ModalScreen):
    """Modal popup showing a sub-agent's last task, thinking, tool calls, and result.

    Press Escape (or click outside) to close.
    """

    CSS = """
    AgentDetailScreen {
        align: center middle;
    }

    #agent-detail-modal {
        width: 76;
        max-width: 92%;
        height: auto;
        max-height: 85%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    .modal-title {
        text-style: bold reverse;
        width: 1fr;
        padding: 0 1;
        margin-bottom: 1;
    }

    #agent-detail-modal > Label {
        width: 1fr;
        margin: 0 0 1 0;
    }

    .modal-section-header {
        text-style: bold;
        color: $accent;
        margin: 1 0 0 0;
    }

    .modal-thinking {
        color: $text-muted;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    .modal-tool {
        padding: 0 1;
        margin: 0 0 0 0;
    }
    """

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, name: str, info: dict) -> None:
        super().__init__()
        self._name = name
        self._info = info

    def compose(self) -> ComposeResult:
        task = self._info.get("task", "(no task recorded)")
        out = self._info.get("output", "(no output)")
        if len(out) > 1500:
            out = out[:1500] + "\n... (truncated)"
        elapsed = self._info.get("elapsed", 0)
        ts = self._info.get("time", 0)
        ts_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "never"
        status = self._info.get("status", "—")
        thinking = self._info.get("thinking") or ""
        if len(thinking) > 3000:
            thinking = thinking[:3000] + "\n... (truncated)"
        tools = self._info.get("tools") or []

        with VerticalScroll(id="agent-detail-modal"):
            yield Label(f" {self._name} ", classes="modal-title")
            yield Label(f"[bold]Status[/]: {status}")
            yield Label(f"[bold]Last task[/]\n{task}")

            if thinking:
                yield Label("Thinking", classes="modal-section-header")
                yield Static(thinking, classes="modal-thinking")

            if tools:
                yield Label(
                    f"Tool calls ({len(tools)})", classes="modal-section-header"
                )
                for t in tools:
                    st = t.get("status", "running")
                    icon = "\u2705" if st == "done" else ("\u274c" if st == "error" else "\u23f3")
                    preview = _fmt_args(t.get("args", {}), 70)
                    line = f"  {icon} {t['name']}"
                    if preview:
                        line += f": {preview}"
                    el = t.get("elapsed", 0)
                    if t.get("status") == "done" and el:
                        line += f" [dim]({el:.1f}s)[/]"
                    yield Static(line, classes="modal-tool")

            yield Label("Result", classes="modal-section-header")
            yield Static(out)
            yield Label(f"[dim]{elapsed:.1f}s \u00b7 {ts_str}[/]")

    def action_close(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# Agent status widget (left sidebar)
# ---------------------------------------------------------------------------

_AGENT_NAMES = [
    "web-recon", "host-recon", "web-pentest",
    "exploit-dev", "binary-vuln", "post-exploit",
    "general-purpose",
]


def _ns_to_name(ns) -> str | None:
    """Extract sub-agent name from a LangGraph stream namespace tuple.
    Returns None for unrecognized (skip), '' for root/coordinator."""
    if not ns:
        return ""
    ns_str = " ".join(str(p) for p in ns) if isinstance(ns, (tuple, list)) else str(ns)
    for name in _AGENT_NAMES:
        if name in ns_str:
            return name
    return None  # unrecognized — skip, don't leak into coordinator thinking


class AgentRow(Static):
    """A clickable agent status row — click to select that agent."""

    can_focus = False

    def __init__(self, name: str, index: int = 0, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.agent_name = name
        self._index = index

    async def on_click(self, event) -> None:
        event.stop()
        panel = self.app.query_one(AgentStatusPanel)
        panel._cursor = self._index
        panel._render_all()
        self.post_message(AgentSelected(self.agent_name))


class AgentStatusPanel(Vertical):
    """Left sidebar — live status list, navigable with j/k, Enter to select, or click."""

    can_focus = True

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("enter", "select", "Select"),
    ]

    _cursor: int = 0
    _states: dict[str, tuple[str, float]] = {}  # name -> (status, elapsed)

    def compose(self) -> ComposeResult:
        yield Label("[bold]Agents[/]", classes="panel-title")
        with VerticalScroll(id="agent-list"):
            for i, name in enumerate(_AGENT_NAMES):
                yield AgentRow(name, i, id=f"agent-{name}", classes="agent-row")

    def on_mount(self) -> None:
        for name in _AGENT_NAMES:
            self._states[name] = ("idle", 0)
        self._render_all()

    def _icon(self, status: str) -> str:
        return {"running": "●", "done": "✅", "error": "❌", "idle": "○"}.get(status, "○")

    def _style(self, status: str) -> str:
        return {"running": "bold yellow", "done": "green", "error": "red", "idle": "dim"}.get(
            status, "dim"
        )

    def set_status(self, name: str, status: str, elapsed: float = 0) -> None:
        if not name or name == "?":
            return
        self._states[name] = (status, elapsed)
        self._render_name(name)

    def _render_name(self, name: str) -> None:
        safe_name = name.replace(" ", "-").replace(".", "-")
        try:
            widget = self.query_one(f"#agent-{safe_name}", Static)
        except NoMatches:
            return
        status, elapsed = self._states.get(name, ("idle", 0))
        elapsed_s = f" {elapsed:.1f}s" if elapsed > 0 else ""
        icon = self._icon(status)
        style = self._style(status)
        cursor_marker = "▸ " if _AGENT_NAMES[self._cursor] == name else "  "
        widget.update(f"[{style}]{cursor_marker}{icon} {name}{elapsed_s}[/]")

    def _render_all(self) -> None:
        for name in _AGENT_NAMES:
            self._render_name(name)

    # -- keyboard actions --

    def action_cursor_down(self) -> None:
        self._cursor = min(self._cursor + 1, len(_AGENT_NAMES) - 1)
        self._render_all()

    def action_cursor_up(self) -> None:
        self._cursor = max(self._cursor - 1, 0)
        self._render_all()

    def action_select(self) -> None:
        name = _AGENT_NAMES[self._cursor]
        self.post_message(AgentSelected(name))


# ---------------------------------------------------------------------------
# Chat panel
# ---------------------------------------------------------------------------

class ChatPanel(Vertical):
    """Main chat area with streaming messages and collapsible cards."""

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-messages")
        yield ChatInput.code_editor(
            "",
            id="chat-input",
            language=None,
        )

    async def add_user_message(self, text: str) -> None:
        msgs = self.query_one("#chat-messages", VerticalScroll)
        widget = Static(f"[bold]you[/] {text}")
        await msgs.mount(widget)
        msgs.scroll_end()

    async def start_thinking(self) -> Static:
        """Mount a new collapsible thinking block, return the content widget.
        Uses CSS classes (not IDs) so multiple blocks can coexist."""
        msgs = self.query_one("#chat-messages", VerticalScroll)
        content = Static("", classes="thinking-content")
        collapsible = Collapsible(content, title="thinking...", classes="thinking-block")
        await msgs.mount(collapsible)
        msgs.scroll_end()
        return content

    def update_thinking(self, content_widget: Static, full_text: str) -> None:
        """Set the full thinking text on the widget."""
        content_widget.update(full_text)

    def finalize_thinking(self, content_widget: Static, title: str) -> None:
        """Mark thinking complete and update the title."""
        collapsible = content_widget.parent
        if isinstance(collapsible, Collapsible):
            collapsible.collapsed = True
            collapsible.title = title

    async def add_tool_call(
        self, call_id: str, name: str, args: dict,
    ) -> Collapsible:
        """Mount a tool-call card (collapsible). Title shows tool + args preview;
        body shows full args without keys or truncation."""
        msgs = self.query_one("#chat-messages", VerticalScroll)
        preview = _fmt_args(args, 60)
        full = _fmt_args_full(args)
        title = f"\u26a1 {name}"
        if preview:
            title += f" {preview}"
        body_text = full if full else "(no args)"
        body = Static(body_text, id=f"tool-body-{call_id}")
        collapsible = Collapsible(body, title=title, id=f"tool-{call_id}")
        await msgs.mount(collapsible)
        msgs.scroll_end()
        return collapsible

    async def update_tool_call(
        self, call_id: str, elapsed: float, status: str = "done",
    ) -> None:
        """Update tool card icon + elapsed time, preserving the args preview."""
        try:
            collapsible = self.query_one(f"#tool-{call_id}", Collapsible)
        except NoMatches:
            return
        icon = "\u2705" if status == "done" else "\u274c"
        # Strip previous icon + elapsed, keep tool name + args preview
        raw = collapsible.title
        for prefix in ("\u26a1 ", "\u2705 ", "\u274c "):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        if " (" in raw:
            raw = raw.rsplit(" (", 1)[0]
        collapsible.title = f"{icon} {raw} ({elapsed:.1f}s)"
        if status == "done":
            collapsible.collapsed = True

    async def add_assistant_message(self, text: str) -> None:
        msgs = self.query_one("#chat-messages", VerticalScroll)
        widget = Static(f"[bold]assistant[/] {text}")
        await msgs.mount(widget)
        msgs.scroll_end()


def _fmt_args_full(args: dict) -> str:
    """Full args value (no keys, no truncation) for the collapsible body."""
    for key in ("command", "file_path", "query", "url", "pattern", "path", "data"):
        v = args.get(key)
        if v is not None:
            return str(v).strip()
    for v in args.values():
        return str(v).strip()
    return ""


def _fmt_args(args: dict, maxlen: int = 60) -> str:
    """Short single-line preview for the tool title."""
    s = _fmt_args_full(args).replace("\n", " ").strip()
    return s[:maxlen] + ("..." if len(s) > maxlen else "")


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

APP_CSS = """
Screen {
    layout: horizontal;
}

#agents-panel {
    width: 28;
    min-width: 20;
    max-width: 40;
    border: solid $primary;
    padding: 0 1;
    background: $surface;
}

.panel-title {
    text-style: bold;
    padding: 1 0;
    border-bottom: solid $primary;
}

#agent-list {
    height: 1fr;
}

.agent-row {
    padding: 0 1;
    height: 1;
}

.agent-row:hover {
    background: $boost;
    text-style: bold;
}

#chat-panel {
    width: 1fr;
    height: 1fr;
}

#chat-messages {
    height: 1fr;
    overflow-y: auto;
    padding: 0 1;
}

#chat-messages > * {
    margin: 0 0 1 0;
}

#chat-input {
    dock: bottom;
    margin: 0 1 1 1;
    height: 10;
}

Collapsible {
    margin: 0 0 1 0;
}

Collapsible > .collapsible--title {
    background: $boost;
    padding: 0 1;
}

#status-bar {
    height: 1;
    dock: bottom;
    background: $panel;
    color: $text-muted;
    padding: 0 1;
}
"""


class RedhawkApp(App):
    """Textual TUI for the redhawk security agent."""

    CSS = APP_CSS

    TITLE = "redhawk"
    SUB_TITLE = "security assessment agent"

    # ctrl+q is the standard quit.
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+d", "quit", "Quit"),
        ("ctrl+l", "focus_left", "Focus sidebar"),
        ("escape", "back", "Back to coordinator"),
    ]

    # Instance state.
    _agent_timers: dict[str, float] = {}
    _agent_history: dict[str, dict] = {}  # name -> {task, elapsed, time}
    _selected_agent: str | None = None
    _thinking_text: str = ""
    _current_thinking: Static | None = None
    _need_new_thinking: bool = False

    # ---- lifecycle ----

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield AgentStatusPanel(id="agents-panel")
            yield ChatPanel(id="chat-panel")
        yield Static("", id="status-bar")

    def on_ready(self) -> None:
        """Build the agent and wire the middleware to this app."""
        self._init_agent()  # @work-decorated, runs as background worker
        panel = self.query_one(AgentStatusPanel)
        for name in _AGENT_NAMES:
            panel.set_status(name, "idle")
        self.query_one("#chat-input", ChatInput).focus()

    @work(exclusive=False, thread=False)
    async def _init_agent(self) -> None:
        await init_mcp_tools()
        _get_agent(self)  # builds agent and calls ToolTrackerMiddleware.set_app(self)

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        prompt = event.text.strip()
        if not prompt:
            return
        if prompt == "/exit":
            self.exit()
            return
        if prompt == "/clear":
            self.run_clear()
            return
        self.run_agent(prompt)
        self.query_one("#chat-input", ChatInput).focus()

    @work(exclusive=False, thread=False)
    async def run_clear(self) -> None:
        """Clear the chat history and reset the conversation."""
        global _history
        _history = []
        chat = self.query_one(ChatPanel)
        msgs = chat.query_one("#chat-messages", VerticalScroll)
        await msgs.remove_children()
        # Reset agent statuses
        panel = self.query_one(AgentStatusPanel)
        for name in _AGENT_NAMES:
            panel.set_status(name, "idle")

    # ---- worker (astream: thinking tokens only) ----

    @work(exclusive=False, thread=False)
    async def run_agent(self, prompt: str) -> None:
        global _history
        agent = _get_agent(self)
        chat = self.query_one(ChatPanel)

        await chat.add_user_message(prompt)
        inp = _history + [{"role": "user", "content": prompt}]

        # Remove old thinking blocks from prior turn
        msgs_container = chat.query_one("#chat-messages", VerticalScroll)
        for old in msgs_container.query(".thinking-block"):
            await old.remove()

        self._current_thinking = await chat.start_thinking()
        self._thinking_text = ""
        self._need_new_thinking = False
        final_msgs = inp

        async for event in agent.astream(
            {"messages": inp},
            stream_mode=["messages", "values"],
            subgraphs=True,
        ):
            ns, mode, data = event
            agent_name = _ns_to_name(ns)
            if agent_name is None:
                continue  # unrecognized subgraph — skip

            if mode == "messages":
                chunk, _meta = data
                if isinstance(chunk, AIMessageChunk):
                    # DeepSeek: reasoning_content = thinking, content = output
                    reasoning = chunk.additional_kwargs.get("reasoning_content")

                    if not reasoning and not chunk.content:
                        continue

                    if not agent_name:
                        # Coordinator — if a tool just completed, start fresh block
                        if self._need_new_thinking:
                            chat.finalize_thinking(self._current_thinking, "\u2705 thinking")
                            chat.update_thinking(self._current_thinking, self._thinking_text)
                            self._current_thinking = await chat.start_thinking()
                            self._thinking_text = ""
                            self._need_new_thinking = False
                        if reasoning:
                            self._thinking_text += reasoning
                            chat.update_thinking(self._current_thinking, self._thinking_text)
                        # content is the output — shown as assistant message, not in thinking
                    else:
                        # Sub-agent — reasoning only, no output in thinking
                        if reasoning:
                            entry = self._agent_history.setdefault(agent_name, {})
                            entry["thinking"] = (entry.get("thinking") or "") + reasoning

            elif mode == "values" and isinstance(data, dict) and not agent_name:
                msgs = data.get("messages", [])
                if msgs:
                    final_msgs = msgs

        # Finalize — keep all thinking blocks, they contain genuine reasoning
        if self._current_thinking and self._thinking_text:
            chat.finalize_thinking(self._current_thinking, "\u2705 thinking")
            chat.update_thinking(self._current_thinking, self._thinking_text)

        _history = final_msgs
        await chat.add_assistant_message(
            getattr(final_msgs[-1], "content", "") if final_msgs else ""
        )

    # ---- tool-call handlers (events posted by ToolTrackerMiddleware) ----

    async def on_tool_call_start(self, event: ToolCallStart) -> None:
        # Flush or remove the current thinking block
        if self._current_thinking:
            if self._thinking_text:
                self._current_thinking.update(self._thinking_text)
            else:
                # No reasoning text before this tool — remove empty block
                collapsible = self._current_thinking.parent
                if collapsible:
                    await collapsible.remove()  # type: ignore[union-attr]
                self._current_thinking = None

        if event.is_task:
            # Coordinator dispatching a sub-agent via `task`
            sub = event.target_agent
            desc = str(event.args.get("description", ""))[:60]
            panel = self.query_one(AgentStatusPanel)
            chat = self.query_one(ChatPanel)
            panel.set_status(sub, "running")
            self._agent_timers[sub] = time.time()
            # Reset per-task tracking so popup shows fresh data
            self._agent_history[sub] = {
                "task": desc, "time": time.time(),
                "thinking": "", "tools": [],
            }
            await chat.add_tool_call(event.call_id, f"\u2192 {sub}", {"task": desc})
        elif event.agent_name:
            # Sub-agent internal tool call → track for popup
            entry = self._agent_history.setdefault(event.agent_name, {})
            entry.setdefault("tools", []).append({
                "name": event.tool_name,
                "args": event.args,
                "id": event.call_id,
                "status": "running",
                "elapsed": 0,
            })
        else:
            # Coordinator's own tool call → chat card
            chat = self.query_one(ChatPanel)
            await chat.add_tool_call(event.call_id, event.tool_name, event.args)

    async def on_tool_call_done(self, event: ToolCallDone) -> None:
        # Only coordinator-visible calls have chat cards (task dispatches + coord tools)
        if not event.agent_name or event.is_task:
            chat = self.query_one(ChatPanel)
            try:
                await chat.update_tool_call(
                    event.call_id, event.elapsed,
                    status="error" if event.error else "done",
                )
            except NoMatches:
                pass

        if event.is_task and event.target_agent:
            # Sub-agent task completed → update sidebar + history
            panel = self.query_one(AgentStatusPanel)
            timer = self._agent_timers.pop(event.target_agent, time.time())
            elapsed = time.time() - timer if timer else 0
            panel.set_status(event.target_agent, "done", elapsed)
            entry = self._agent_history.setdefault(event.target_agent, {})
            entry["elapsed"] = elapsed
            entry["time"] = time.time()
            entry["output"] = event.output
            # Next coordinator thinking should start a fresh block
            self._need_new_thinking = True
        elif event.agent_name:
            # Sub-agent internal tool completed → update tools list
            entry = self._agent_history.setdefault(event.agent_name, {})
            for t in entry.get("tools", []):
                if t["id"] == event.call_id:
                    t["status"] = "error" if event.error else "done"
                    t["output"] = event.output[:300]
                    t["elapsed"] = event.elapsed
                    break
        else:
            # Coordinator's own tool completed → next thinking needs fresh block
            self._need_new_thinking = True

    # -- agent detail popup --

    async def on_agent_selected(self, event: AgentSelected) -> None:
        self._selected_agent = event.name
        info = self._agent_history.get(event.name, {})
        # Merge current live status into the info dict so the popup reflects it.
        panel = self.query_one(AgentStatusPanel)
        status, elapsed = panel._states.get(event.name, ("idle", 0))
        info.setdefault("status", status)
        self.push_screen(AgentDetailScreen(event.name, info))

    async def action_back(self) -> None:
        """Focus the chat input (e.g. after navigating the sidebar)."""
        self._selected_agent = None
        self.query_one("#chat-input", ChatInput).focus()

    def action_focus_left(self) -> None:
        """Focus the agent sidebar."""
        self.query_one(AgentStatusPanel).focus()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    app = RedhawkApp()
    app.run()
