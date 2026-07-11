"""Cross-conversation persistent memory.

Memory lives as plain markdown files on disk under `.redhawk/memories/`,
exposed to the agent as a virtual `/memories/` filesystem via the backend.
Files persist across process restarts, so the agent accumulates knowledge
across sessions. They are also human-readable — you can `cat` or edit
`.redhawk/memories/AGENTS.md` directly.

The memory file is loaded into the system prompt at startup (unlike skills,
which load on demand), so it shapes every conversation from the start.
"""

from redhawk.workspace import MEMORIES_DIR

# Virtual path the agent sees (routed to MEMORIES_DIR by the CompositeBackend).
MEMORY_FILE = "/memories/AGENTS.md"

_INITIAL_MEMORY = """\
# redhawk memory

Persistent notes accumulated across engagements. The agent updates this via
edit_file; the operator may also edit it by hand. Record recurring target
patterns, reliable payloads, tool quirks, operator preferences, lessons.

## Operator preferences
- (none yet)

## Recurring findings / patterns
- (none yet)

## Tooling notes
- (none yet)
"""


def seed_memory_if_absent() -> None:
    """Create the initial memory file on first run (idempotent)."""
    MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
    target = MEMORIES_DIR / "AGENTS.md"
    if not target.exists():
        target.write_text(_INITIAL_MEMORY)
