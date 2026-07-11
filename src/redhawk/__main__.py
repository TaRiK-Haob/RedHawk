"""TinyAgent entry point.

Defaults to the Textual TUI. Pass ``--cli`` for the inline-print REPL.
"""

import sys


def run() -> None:
    if "--cli" in sys.argv:
        from redhawk.tui_cli import main as cli_main

        cli_main()
    else:
        from redhawk.textual_app import run as tui_run

        tui_run()


if __name__ == "__main__":
    run()
