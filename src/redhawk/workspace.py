"""Workspace and backend stack setup.

A `.redhawk/` directory in the current working directory is the agent's home.
The backend is a CompositeBackend:

- default -> LocalShellBackend rooted at `.redhawk/` (the working area:
  file tools + shell `execute`, real disk, paths used as-is).
- /memories/ -> FilesystemBackend rooted at `.redhawk/memories/` (persistent
  cross-conversation memory, virtual root).

Both persist to disk, so state survives restarts.
"""

from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend

# Workspace lives in the directory the agent is launched from.
WORKSPACE_DIR = Path.cwd() / ".redhawk"
MEMORIES_DIR = WORKSPACE_DIR / "memories"


def ensure_workspace() -> Path:
    """Create the workspace directory if it does not exist; return its path."""
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    return WORKSPACE_DIR


def build_workspace_backend() -> LocalShellBackend:
    """The default backend: real-disk workspace + shell execution.

    - virtual_mode=False: paths are used AS-IS, keeping file tools and the
      shell `execute` tool on the same real-path basis (avoids the absolute-
      path nesting that virtual_mode=True would cause).
    - root_dir sets the cwd for both file tools and shell commands, so
      RELATIVE paths default into the workspace.
    - inherit_env=True so shell `execute` finds nmap/curl/etc. on PATH.

    Not a hard sandbox — `execute` is unrestricted by design.
    """
    return LocalShellBackend(
        root_dir=WORKSPACE_DIR,
        virtual_mode=False,
        inherit_env=True,
    )


def build_backend() -> CompositeBackend:
    """Full backend stack: workspace (default) + persistent memory route."""
    ensure_workspace()
    MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
    return CompositeBackend(
        default=build_workspace_backend(),
        routes={
            "/memories/": FilesystemBackend(
                root_dir=MEMORIES_DIR,
                virtual_mode=True,
            ),
        },
    )
