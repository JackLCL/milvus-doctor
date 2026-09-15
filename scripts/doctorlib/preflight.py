"""Local installation checks; no collection, target access or installation."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys

from . import __version__
from .reports import build_report, markdown, support_summary
from .rules import evaluate


def _discovery(check, name):
    """Presence is not an import, executable run, or access/compatibility check."""
    try:
        return "discovered" if check(name) is not None else "missing"
    except Exception:
        # Import hooks or PATH checks may fail. Do not expose paths or raw errors.
        return "check_failed"


def _smoke_test():
    """Exercise the real offline pipeline using only a tiny in-memory example."""
    try:
        # diagnose imports this module even for --snapshot. Verify the required
        # entrypoint without calling it or importing optional SDK dependencies.
        from .collectors import collect
        if not callable(collect):
            raise RuntimeError("Required collector entrypoint is unavailable")
        snapshot = {
            "schema_version": 1,
            "deployment": {"method": "snapshot", "mode": "unknown"},
            "sources": [{"name": "logs", "status": "ok"}],
            "logs": "the dim 768 does not match collection dim 1024",
        }
        report = build_report(snapshot, evaluate(snapshot))
        encoded = json.dumps(report)
        rendered = markdown(report)
        summary = support_summary(report)
        expected = "VECTOR_DIMENSION_MISMATCH"
        if (report.get("read_only") is not True
                or report.get("status") != "attention_required"
                or expected not in {f.get("code") for f in report.get("findings", [])}
                or expected not in rendered
                or expected not in summary
                or "Submission status: NOT SUBMITTED" not in summary
                or json.loads(encoded) != report):
            raise RuntimeError("Unexpected offline smoke-test result")
    except Exception:
        return {
            "status": "failed",
            "detail": "The required-module or in-memory rule/report/summary check failed. Repair the local installation before diagnosing.",
        }
    return {
        "status": "passed",
        "detail": "The required collector module imported without running collection. Rules, JSON/Markdown reports and Support Summary processed synthetic in-memory evidence; no live target was checked.",
    }


def run_preflight():
    """Return a local readiness report without importing optional dependencies."""
    python_supported = sys.version_info[:2] >= (3, 8)
    platform_supported = sys.platform == "linux"
    smoke = _smoke_test()
    core_ready = python_supported and smoke["status"] == "passed"
    status = ("core_unavailable" if not core_ready else
              "unsupported_platform" if not platform_supported else "ready")
    capabilities = {}
    for name, module, purpose in (
            ("pyyaml", "yaml", "Read selected YAML manifests/configuration; JSON and log input do not require it."),
            ("pymilvus", "pymilvus", "Read selected Milvus SDK metadata; other collectors do not require it.")):
        capabilities[name] = {
            "status": _discovery(importlib.util.find_spec, module),
            "check": "module discovery only; not imported or compatibility-tested",
            "purpose": purpose,
        }
    for command, purpose in (
            ("docker", "Read explicitly selected Docker/Compose metadata."),
            ("kubectl", "Read explicitly scoped Kubernetes/Helm/Operator metadata.")):
        capabilities[command] = {
            "status": _discovery(shutil.which, command),
            "check": "PATH presence only; command not executed",
            "purpose": purpose,
        }
    return {
        "schema_version": 1,
        "tool_version": __version__,
        "read_only": True,
        "status": status,
        "core_ready": core_ready,
        "python": {
            "version": ".".join(str(n) for n in sys.version_info[:3]),
            "minimum": "3.8",
            "supported": python_supported,
        },
        "platform": {
            "name": sys.platform,
            "supported": platform_supported,
            "validation": "Linux only. macOS and Windows have not been validated.",
        },
        "optional_capabilities": capabilities,
        "smoke_test": smoke,
        "limitations": [
            "Missing optional capabilities do not block the core offline workflow; prepare only what the selected diagnosis needs.",
            "Discovery does not verify dependency compatibility, daemon availability, credentials, permissions or connectivity.",
            "No endpoint was contacted, no credentials were read, no target command was executed, no dependencies were installed and no reports were saved.",
            "This verifies the local CLI only, not AI-host Skill discovery or Milvus cluster health.",
        ],
    }


def preflight_markdown(report):
    lines = ["# Milvus Doctor installation preflight", "",
             f"Status: **{report['status']}** · Local, read-only",
             f"Doctor version: {report['tool_version']}",
             f"Core ready: {'yes' if report['core_ready'] else 'no'}", "",
             f"- Python: {report['python']['version']} (requires {report['python']['minimum']}+)",
             f"- Platform: {report['platform']['name']} — {report['platform']['validation']}",
             f"- Offline smoke test: {report['smoke_test']['status']} — {report['smoke_test']['detail']}",
             "", "## Optional capabilities", ""]
    for name, capability in report["optional_capabilities"].items():
        lines.append(f"- {name}: {capability['status']} ({capability['check']}). {capability['purpose']}")
    lines += ["", "## Boundaries", ""] + ["- " + item for item in report["limitations"]]
    return "\n".join(lines)
