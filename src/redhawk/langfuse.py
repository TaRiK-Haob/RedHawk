"""Langfuse tracing integration — optional.

Enabled automatically when ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY``
are present in the environment. The LangChain callback handler is injected into
agent invocations (TUI ``astream`` + CLI ``invoke``) so every LLM call, tool
run, and sub-agent delegation is traced.

If the keys are absent or initialization fails, tracing is silently disabled
and ``get_callback_handler()`` returns ``None`` — callers must pass ``None``
through (LangChain treats a missing ``config`` normally).
"""

import os

from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig

load_dotenv()

_client = None
_handler = None
_tried = False


def _init():
    """Lazily initialize the Langfuse client + LangChain callback handler.

    Returns ``(client, handler)``; ``(None, None)`` when disabled or on failure.
    """
    global _client, _handler, _tried
    if _tried:
        return _client, _handler
    _tried = True
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get(
        "LANGFUSE_SECRET_KEY"
    ):
        return None, None
    try:
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        _client = get_client()
        _handler = CallbackHandler()
    except Exception:  # noqa: BLE001 — tracing must never break the app
        _client, _handler = None, None
    return _client, _handler


def get_callback_handler():
    """Return the Langfuse LangChain callback handler, or ``None`` if disabled."""
    return _init()[1]


def callback_config() -> RunnableConfig | None:
    """Return a ``{"callbacks": [handler]}`` config, or ``None`` when disabled.

    Convenient to splat into ``agent.invoke``/``agent.astream`` as ``config=``.
    """
    h = get_callback_handler()
    return {"callbacks": [h]} if h else None


def flush():
    """Flush pending trace events. Call at app shutdown.

    Langfuse exports asynchronously; without a flush, short-lived runs may lose
    the tail of the trace.
    """
    if _client is not None:
        try:
            _client.flush()
        except Exception:  # noqa: BLE001
            pass
