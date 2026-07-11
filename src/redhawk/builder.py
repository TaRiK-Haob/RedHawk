"""Assemble the coordinator agent and its sub-agents.

This is the single extension point for new security domains: append a
dict to SUBAGENTS (prompt lives in prompts.py). Tools are inherited from
the backend: built-in file tools + shell `execute` + the `task` delegator.
"""

from deepagents import create_deep_agent

from redhawk.config import build_model, build_recon_model
from redhawk.memory import MEMORY_FILE, seed_memory_if_absent
from redhawk.prompts import (
    BINARY_VULN_PROMPT,
    COORDINATOR_PROMPT,
    EXPLOIT_DEV_PROMPT,
    GENERAL_PURPOSE_PROMPT,
    HOST_RECON_PROMPT,
    POST_EXPLOIT_PROMPT,
    SUBAGENT_SHARED_RULES,
    WEB_PENTEST_PROMPT,
    WEB_RECON_PROMPT,
)
from redhawk.mcp_tools import get_mcp_tools
from redhawk.tracer import TraceMiddleware
from redhawk.workspace import WORKSPACE_DIR, build_backend

# Built-in CTF skills shipped under the workspace. Loaded automatically
# when present (progressive disclosure: names+descriptions enter the system
# prompt; full instructions are read on demand). Absent -> disabled.
SKILLS_DIR = WORKSPACE_DIR / "skills"


def _skills(*names: str):
    """Return existing skill-subdir paths for the given skill names.

    Sub-agents do NOT inherit the coordinator's skills, so each working-tier
    agent declares its own. Missing dirs are skipped so a partial skill
    install doesn't break the build; returns None when none exist.
    """
    paths = [str(SKILLS_DIR / n) for n in names if (SKILLS_DIR / n).is_dir()]
    return paths or None


def _sub(prompt: str) -> str:
    """Append the shared sub-agent rules (e.g. ping health check) to a prompt."""
    return prompt + SUBAGENT_SHARED_RULES

# Each specialist gets an isolated context window. The coordinator calls
# them via deepagents' built-in `task` tool using the `name` below.
# Tools/skills are omitted where inheritance is desired.
# Recon agents get a cheaper/faster model (see config.build_recon_model);
# working-tier agents inherit the coordinator's (main) model.
SUBAGENTS = [
    {
        "name": "web-recon",
        "description": (
            "Reconnaissance and vulnerability discovery against web "
            "applications and HTTP services: fingerprinting, content "
            "discovery, parameter probing, injection testing."
        ),
        "system_prompt": _sub(WEB_RECON_PROMPT),
        "model": build_recon_model(),
    },
    {
        "name": "host-recon",
        "description": (
            "Network and host reconnaissance: live-host discovery, port "
            "scanning, service/OS and version fingerprinting."
        ),
        "system_prompt": _sub(HOST_RECON_PROMPT),
        "model": build_recon_model(),
    },
    # --- Phase 1: closes recon -> discover/exploit loop ----------------------
    {
        "name": "web-pentest",
        "description": (
            "Discover and exploit vulnerabilities in web applications and "
            "HTTP services: injection (SQLi/XSS/SSTI/SSRF/XXE), auth/authz "
            "flaws, deserialization, request smuggling. Read recon context "
            "files first, then probe and build proofs."
        ),
        "system_prompt": _sub(WEB_PENTEST_PROMPT),
        "skills": _skills("ctf-web", "ctf-writeup"),
    },
    {
        "name": "exploit-dev",
        "description": (
            "Turn confirmed vulnerabilities into stable, reliable exploits "
            "and gain access: PoC stabilization, payload engineering, "
            "privilege escalation, reliable shells."
        ),
        "system_prompt": _sub(EXPLOIT_DEV_PROMPT),
        "skills": _skills("ctf-pwn", "ctf-misc"),
    },
    # --- Phase 2: enables binary vuln mining + full kill-chain -----------------
    {
        "name": "binary-vuln",
        "description": (
            "Reverse-engineer binaries and find memory-corruption bugs and "
            "CVE-level vulnerabilities in compiled code: buffer overflows, "
            "use-after-free, format strings, integer issues, logic flaws."
        ),
        "system_prompt": _sub(BINARY_VULN_PROMPT),
        "skills": _skills("ctf-reverse", "ctf-pwn", "ctf-malware"),
    },
    {
        "name": "post-exploit",
        "description": (
            "Once access is gained: internal reconnaissance, privilege "
            "escalation, lateral movement, persistence on the target."
        ),
        "system_prompt": _sub(POST_EXPLOIT_PROMPT),
        "skills": _skills("ctf-misc", "ctf-malware"),
    },
    # --- General-purpose: explicit entry so it gets middleware injection ---
    {
        "name": "general-purpose",
        "description": (
            "General-purpose assistant for miscellaneous tasks that don't "
            "fit a specific security domain: research, file operations, "
            "scripting, cross-domain work."
        ),
        "system_prompt": _sub(GENERAL_PURPOSE_PROMPT),
    },
    # --- Phase 3 (add when evidence of need) ----------------------------------
    # {
    #     "name": "cloud-attack",
    #     "description": "...",
    #     "system_prompt": CLOUD_ATTACK_PROMPT,
    # },
]


def build_agent(
    tui_mode: bool = False,
    extra_middleware: list | None = None,
    subagent_middleware_factory=None,
):
    """Build the compiled coordinator agent ready to invoke.

    Args:
        tui_mode: When True, omit TraceMiddleware (the TUI handles rendering via
            astream rather than middleware hooks).
        extra_middleware: Middleware for the coordinator only.
        subagent_middleware_factory: Optional callable ``(name: str) ->
            AgentMiddleware``. If provided, each sub-agent gets a middleware
            instance from this factory. Used by the TUI to inject
            ToolTrackerMiddleware so sub-agent tool calls are visible.
    """
    seed_memory_if_absent()
    skills = [str(SKILLS_DIR)] if SKILLS_DIR.is_dir() else None
    middleware = [] if tui_mode else [TraceMiddleware()]
    if extra_middleware:
        middleware.extend(extra_middleware)

    # Inject per-subagent middleware (TUI tool tracking) without mutating the
    # module-level SUBAGENTS list.
    subagents = SUBAGENTS
    if subagent_middleware_factory:
        subagents = [
            {
                **sa,
                "middleware": list(sa.get("middleware", []))
                + [subagent_middleware_factory(sa["name"])],
            }
            for sa in SUBAGENTS
        ]

    return create_deep_agent(
        model=build_model(),
        backend=build_backend(),
        tools=get_mcp_tools() or None,
        system_prompt=COORDINATOR_PROMPT.replace("__WORKSPACE__", str(WORKSPACE_DIR)),
        memory=[MEMORY_FILE],
        skills=skills,
        subagents=subagents,
        middleware=middleware or None,
        name="redhawk",
    )
