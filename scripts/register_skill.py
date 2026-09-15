#!/usr/bin/env python3
"""Explicit local Skill registration only; never changes existing paths or a cluster."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys


def register(source, agent, scope="project", project=None, apply=False, claude_dir=None):
    if agent not in {"codex", "claude-code"} or scope not in {"project", "user"}:
        raise ValueError("Unsupported agent or scope")
    if sys.platform != "linux":
        raise ValueError("This helper is validated for Linux only")
    source = Path(source).expanduser().resolve(strict=True)
    required = ("SKILL.md", "AI-INSTALL.md", "scripts/doctor.py", "scripts/doctorlib", "references", "agents", "requirements-optional.txt")
    if not all((source/name).exists() for name in required):
        raise ValueError("Source must be a complete Milvus Doctor Skill directory")
    with (source/"SKILL.md").open(encoding="utf-8") as stream:
        header = stream.read(4096).split("---", 2)
    if len(header) < 3 or header[0].strip() or not re.search(r"(?m)^name:\s*milvus-doctor\s*$", header[1]):
        raise ValueError("Source does not declare the milvus-doctor Skill")
    if scope == "project":
        if not project:
            raise ValueError("Project scope requires --project")
        base = Path(project).expanduser().resolve(strict=True)
        if not base.is_dir():
            raise ValueError("Project must be an existing directory")
        target = base/(".agents" if agent == "codex" else ".claude")/"skills"/"milvus-doctor"
    else:
        base = (Path(claude_dir or os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser().resolve()
                if agent == "claude-code" else Path.home()/".agents")
        target = base/"skills"/"milvus-doctor"
    command = [sys.executable, str(Path(__file__).resolve()), "--source", str(source), "--agent", agent, "--scope", scope]
    if scope == "project":
        command += ["--project", str(base)]
    elif agent == "claude-code":
        command += ["--claude-dir", str(base)]
    command += ["--apply"]
    result = {"status": "planned", "source": str(source), "target": str(target),
              "user_command": " ".join(shlex.quote(value) for value in command),
              "cluster_access": False, "host_discovery_verified": False}
    if os.path.lexists(target):
        result["status"] = "already_registered" if target.resolve() == source else "conflict"
        result["message"] = "Existing target is unchanged. Verify Skill discovery in a new host turn/session." if result["status"] == "already_registered" else "Existing target is unchanged; inspect it with the user before any update."
        return result
    if os.path.commonpath([str(base), str(target.parent.resolve())]) != str(base):
        result.update(status="conflict", message="A parent link would write outside the selected scope; no changes made.")
        return result
    if not apply:
        result["message"] = "Plan only. --apply creates a new link and necessary parent directories; it never replaces an existing target."
        return result
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
    except FileExistsError:
        result.update(status="conflict", message="Target appeared during registration; it was not replaced.")
    except OSError:
        result.update(status="requires_user_action", message="Registration is blocked by local permissions or path access. Stop retrying; use the host-approved installation flow or ask the user to run user_command in their own terminal. Do not change permissions, disable sandboxing, or switch scope.")
    else:
        result.update(status="registered", message="Link created. Start a new host turn/session and verify Skill discovery; no cluster access was performed.")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, choices=["codex", "claude-code"])
    parser.add_argument("--scope", choices=["project", "user"], default="project")
    parser.add_argument("--project")
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--claude-dir")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = register(args.source, args.agent, args.scope, args.project, args.apply, args.claude_dir)
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "invalid_input", "message": "Check the complete source, existing project, supported Linux platform and requested scope.", "cluster_access": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "requires_user_action" else 2 if result["status"] == "conflict" else 0


if __name__ == "__main__":
    raise SystemExit(main())
