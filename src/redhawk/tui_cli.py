"""Fallback CLI mode (``--cli`` flag): inline-print REPL with middleware tracing."""

import asyncio

from redhawk import build_agent
from redhawk.mcp_tools import init_mcp_tools
from redhawk.tracer import set_tools
from redhawk.workspace import ensure_workspace


def main() -> None:
    workspace = ensure_workspace()
    asyncio.run(init_mcp_tools())
    agent = build_agent()
    set_tools(sorted(agent.get_graph().nodes["tools"].data.tools_by_name.keys()))

    print("TinyAgent security console — CLI mode. (type 'exit' to quit)")
    print(f"workspace: {workspace}\n")

    messages: list = []
    while True:
        try:
            user_input = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break
        messages.append({"role": "user", "content": user_input})
        result = agent.invoke({"messages": messages})
        messages = result["messages"]
        print(f"\nagent > {messages[-1].content}\n")
