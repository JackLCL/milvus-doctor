from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from . import __version__
from .local_files import read_regular_file
from .reports import build_report, markdown, save_report, support_summary
from .safety import redact, register_secret, run_readonly, validate_endpoint


def parser():
    p = argparse.ArgumentParser(description="Milvus Doctor: bounded, read-only diagnostics. Never executes remediation or uploads reports.")
    p.add_argument("--version", action="version", version=f"milvus-doctor {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    diag = sub.add_parser("diagnose", help="Check explicitly selected targets or offline evidence")
    diag.add_argument("--deployment", choices=["auto", "native", "docker", "compose", "kubernetes", "helm", "operator"], default="auto")
    diag.add_argument("--mode", choices=["auto", "standalone", "cluster"], default="auto")
    diag.add_argument("--container", action="append", default=[])
    diag.add_argument("--compose-project")
    diag.add_argument("--context"); diag.add_argument("--namespace"); diag.add_argument("--selector")
    diag.add_argument("--release"); diag.add_argument("--operator-name")
    diag.add_argument("--endpoint", help="Milvus SDK endpoint, http(s); credentials via --token-env only")
    diag.add_argument("--health-url", help="Explicit HTTP(S) /healthz URL")
    diag.add_argument("--metrics-url", help="Explicit HTTP(S) /metrics URL; optional bounded snapshot")
    diag.add_argument("--token-env", default="MILVUS_DOCTOR_TOKEN", help="Environment variable name; never put credentials on the command line")
    diag.add_argument("--database", default="default")
    diag.add_argument("--collection", action="append", default=[], help="Inspect only these named collections; repeat as needed, avoids collection inventory")
    diag.add_argument("--collection-limit", type=int, default=10, help="1..100, default 10; to skip SDK checks omit --endpoint, not a zero limit")
    diag.add_argument("--timeout", type=float, default=8)
    diag.add_argument("--max-bytes", type=int, default=1048576)
    diag.add_argument("--manifest", action="append", default=[], help="Explicit local Kubernetes/Compose/Helm/Operator YAML or JSON")
    diag.add_argument("--config-file", help="Local Milvus YAML/JSON configuration")
    diag.add_argument("--log-file", action="append", default=[], help="Explicit local log excerpts; no remote log fetching")
    diag.add_argument("--pid", type=int, help="Native process PID to inspect without reading argv/environment")
    diag.add_argument("--data-dir", help="Explicit local filesystem path for capacity counters only (no file reads)")
    diag.add_argument("--snapshot", help="Offline normalized evidence JSON; excludes all live collection")
    diag.add_argument("--previous", help="Previous report JSON for a conservative comparison")
    diag.add_argument("--output-dir", help="Write local reports, support instructions and a copy-ready summary; no overwrite")
    diag.add_argument("--format", choices=["markdown", "json"], default="markdown")
    discover = sub.add_parser("discover", help="Explicit metadata-only inventory; does not run diagnostics")
    discover.add_argument("--deployment", choices=["docker", "kubernetes"], required=True)
    discover.add_argument("--context"); discover.add_argument("--namespace")
    discover.add_argument("--timeout", type=float, default=8)
    support = sub.add_parser("support-summary", help="Format an existing report for the support form; no live checks or submission")
    support.add_argument("--report", required=True, help="Existing Doctor report.json")
    support.add_argument("--context", help="Optional local JSON of already-known symptom, performed actions and requested assistance")
    support.add_argument("--output", help="Save the reviewed summary to a new local file (0600); never overwrites or submits")
    reader = sub.add_parser("read-evidence", help="Show a bounded, redacted local log/config/source excerpt; never executes it")
    reader.add_argument("--file", required=True)
    reader.add_argument("--start-line", type=int, default=1)
    reader.add_argument("--lines", type=int, default=80)
    reader.add_argument("--max-bytes", type=int, default=1048576)
    reader.add_argument("--token-env", default="MILVUS_DOCTOR_TOKEN", help="Known credential to redact, never prints the value")
    reader.add_argument("--format", choices=["markdown", "json"], default="markdown")
    shown = sub.add_parser("show-evidence", help="Display safe evidence saved in a report; no live check")
    shown.add_argument("--report", required=True)
    preflight = sub.add_parser("preflight", help="Check the local installation without connecting to targets or installing anything")
    preflight.add_argument("--format", choices=["markdown", "json"], default="markdown")
    faq = sub.add_parser("faq", help="Search bundled official FAQ references offline; no target access or repairs")
    selection = faq.add_mutually_exclusive_group()
    selection.add_argument("--query", help="Short English/Chinese symptom keywords or a finding code; never paste raw credentials/logs")
    selection.add_argument("--id", dest="faq_id", help="Read one bundled FAQ section by its returned ID")
    faq.add_argument("--milvus-version", help="Observed/user-confirmed server version, e.g. 2.6.17; omitted remains unconfirmed")
    faq.add_argument("--category", choices=["performance", "product", "operational", "limits", "troubleshooting"])
    faq.add_argument("--limit", type=int, default=5, help="1..10 query results")
    faq.add_argument("--format", choices=["json", "markdown"], default="json")
    from .log_export import add_export_parser
    add_export_parser(sub)
    return p


def load_json(path, max_bytes=1048576):
    value = json.loads(read_regular_file(path, max_bytes))
    if not isinstance(value, dict): raise ValueError("Expected a JSON object")
    return value


def validate_args(args):
    if args.command == "faq":
        if any(value is not None and not value.strip() for value in (args.query, args.faq_id, args.milvus_version)):
            raise ValueError("FAQ query, ID and Milvus version must not be empty when provided")
        if not (args.query or args.faq_id) and (args.milvus_version or args.category or args.limit != 5):
            raise ValueError("FAQ filters require --query or --id; omit options to list the bundled catalog")
        if args.faq_id and (args.category or args.limit != 5):
            raise ValueError("--category and --limit apply to FAQ queries, not a selected ID")
        return
    if args.command == "export-logs":
        from .log_export import validate_export
        validate_export(args)
        return
    if args.command in {"support-summary", "preflight", "show-evidence"}: return
    if args.command == "read-evidence":
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.token_env):
            raise ValueError("--token-env must name an environment variable")
        register_secret(os.environ.get(args.token_env, ""))
        return
    if not (0 < args.timeout <= 120): raise ValueError("--timeout must be >0 and <=120 seconds")
    if args.command == "discover":
        if args.deployment == "kubernetes" and not (args.namespace and args.context):
            raise ValueError("Kubernetes inventory requires --context and --namespace")
        return
    if not (1024 <= args.max_bytes <= 16 * 1024 * 1024): raise ValueError("--max-bytes must be 1024..16777216")
    if not (1 <= args.collection_limit <= 100): raise ValueError("--collection-limit must be 1..100")
    if len(args.container) > 100 or len(args.collection) > 100 or len(args.manifest) > 20 or len(args.log_file) > 20:
        raise ValueError("Too many targets: maximum 100 containers/collections or 20 manifests/logs per diagnosis; split the request")
    if args.pid is not None and args.pid <= 0: raise ValueError("--pid must be positive")
    if args.collection and not args.endpoint:
        raise ValueError("--collection requires --endpoint")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", name) for name in args.collection):
        raise ValueError("Invalid collection name")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.token_env): raise ValueError("--token-env must name an environment variable")
    register_secret(os.environ.get(args.token_env, ""))
    for field in ("endpoint", "health_url", "metrics_url"):
        value = getattr(args, field)
        if value: validate_endpoint(value, field)
    for target in args.container + [x for x in (args.compose_project, args.namespace, args.release, args.operator_name) if x]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", target): raise ValueError("Invalid target name")
    if args.context and (args.context.startswith("-") or any(c in args.context for c in "\r\n\x00")):
        raise ValueError("Invalid context name")
    if args.selector and (args.selector.startswith("-") or any(c in args.selector for c in "\r\n\x00")):
        raise ValueError("Invalid label selector")
    live = args.container or args.compose_project or args.namespace or args.context or args.endpoint or args.health_url or args.metrics_url or args.pid or args.data_dir
    if args.snapshot:
        if live or args.manifest or args.config_file or args.log_file or args.collection or args.deployment != "auto" or args.mode != "auto" or args.release or args.operator_name or args.selector or args.database != "default" or args.collection_limit != 10:
            raise ValueError("--snapshot is offline-only and cannot be combined with live targets or other inputs")
        return
    docker_selected = bool(args.container or args.compose_project or args.deployment in {"docker", "compose"})
    kube_selected = bool(args.context or args.namespace or args.release or args.operator_name or args.selector or args.deployment in {"kubernetes", "helm", "operator"})
    if (docker_selected and kube_selected) or (args.pid and (docker_selected or kube_selected)) or (args.deployment == "native" and (docker_selected or kube_selected)):
        raise ValueError("Conflicting deployment target families; run separate scoped diagnoses for Docker, Kubernetes and native processes")
    if args.release and args.operator_name:
        raise ValueError("Choose either --release or --operator-name for a diagnosis")
    if (args.release or args.operator_name or args.selector) and not (args.context and args.namespace):
        raise ValueError("Kubernetes selections require both --context and --namespace")
    if not (live or args.manifest or args.config_file or args.log_file):
        raise ValueError("Select an explicit target or local evidence; run discover first if needed")
    if args.deployment in {"kubernetes", "helm", "operator"} and not (args.context and args.namespace):
        if not (args.manifest and not live): raise ValueError("Live Kubernetes diagnostics require --context and --namespace")
    if args.namespace and not args.context: raise ValueError("--namespace requires an explicit --context")
    if args.context and not args.namespace: raise ValueError("--context requires an explicit --namespace")
    if args.deployment == "docker" and not args.container and not args.manifest:
        raise ValueError("Docker diagnostics require explicit --container target(s)")
    if args.deployment == "compose" and not args.compose_project and not args.container and not args.manifest:
        raise ValueError("Compose diagnostics require --compose-project or --container")


def inventory(args):
    if args.deployment == "docker":
        cp = run_readonly(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=args.timeout)
        if cp.returncode: raise RuntimeError(cp.stderr)
        items = [json.loads(line) for line in cp.stdout.splitlines() if line.strip()]
        return {"containers": [{k: item.get(k) for k in ("Names", "Image", "Status")} for item in items]}
    cp = run_readonly(["kubectl", "--context", args.context, "--namespace", args.namespace, "--request-timeout", f"{args.timeout}s", "get", "pods", "-o", "json"], timeout=args.timeout)
    if cp.returncode: raise RuntimeError(cp.stderr)
    items = json.loads(cp.stdout).get("items", [])
    return {"context": args.context, "namespace": args.namespace, "pods": [{"name": p.get("metadata", {}).get("name"), "phase": p.get("status", {}).get("phase"), "images": [c.get("image") for c in p.get("spec", {}).get("containers", [])]} for p in items]}


def main(argv=None):
    p = parser(); args = p.parse_args(argv)
    try:
        validate_args(args)
        if args.command == "faq":
            from .faq import catalog_overview, get_faq, search_faq
            result = (search_faq(args.query, args.milvus_version, args.category, args.limit) if args.query else
                      get_faq(args.faq_id, args.milvus_version) if args.faq_id else catalog_overview())
            if args.format == "markdown":
                print("# Milvus official FAQ references\n\nLocal reference lookup only; not a diagnosis or permission to execute source examples.\n")
                for warning in result.get("warnings", []): print("- " + warning)
                items = result.get("results", []) if args.query else [result["entry"]] if result.get("entry") else result.get("sources", [])
                for item in items:
                    print("\n## " + item["title"] + "\n")
                    print("ID: " + item["id"] + " · Docs: " + item["docs_version"])
                    print("\n" + item.get("excerpt", ""))
                    if item.get("read_only_guidance"): print("\nRead-only guidance: " + item["read_only_guidance"])
                    for warning in item.get("warnings", []): print("- " + warning)
                    print("\n[Versioned source](" + item["source_url"] + ") · [Official documentation](" + item["docs_url"] + ")")
                if not items: print("\nNo matching bundled reference. Do not infer a diagnosis or silently use another version.")
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            found = bool(result.get("results") or result.get("entry") or result.get("sources"))
            return 0 if result["status"] == "ok" and found else 1
        if args.command == "export-logs":
            from .log_export import run_export
            result = run_export(args)
            if args.format == "markdown" and result.get("report"):
                print(markdown(result["report"]))
                print("\nLocal redacted log bundle: " + result["output_dir"])
                print(result["note"])
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] in {"planned", "no_findings_in_available_evidence", "observations_only"} else 1
        if args.command == "read-evidence":
            from .evidence import read_evidence
            result = read_evidence(args.file, start_line=args.start_line, lines=args.lines, max_bytes=args.max_bytes)
            if args.format == "json":
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("# Selected local evidence (redacted, read-only)")
                print(result.get("note", ""))
                for line in result["lines"]:
                    print(f"{line['line']}: {line['text']}")
                if result.get("truncated"):
                    print("Only the selected bounded line window is shown; request another window if needed.")
            return 0
        if args.command == "preflight":
            from .preflight import run_preflight, preflight_markdown
            report = run_preflight()
            print(json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else preflight_markdown(report))
            return 0 if report["status"] == "ready" else 1
        if args.command == "support-summary":
            report = load_json(args.report, 16 * 1024 * 1024)
            context = load_json(args.context, 16384) if args.context else None
            summary = support_summary(report, context)
            if args.output:
                target = Path(args.output).expanduser()
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(summary)
            print(summary, end="")
            return 0
        if args.command == "show-evidence":
            from .evidence import sanitize_evidence
            report = load_json(args.report, 16 * 1024 * 1024)
            if not isinstance(report.get("evidence"), dict):
                raise ValueError("This report has no saved evidence. Use a new scoped diagnosis; do not look for snapshot.json.")
            print(json.dumps(sanitize_evidence(report["evidence"]), ensure_ascii=False, indent=2))
            return 0
        if args.command == "discover":
            print(json.dumps(redact(inventory(args)), ensure_ascii=False, indent=2)); return 0
        from .collectors import collect
        from .rules import evaluate
        snapshot = load_json(args.snapshot, args.max_bytes) if args.snapshot else collect(args)
        if not isinstance(snapshot.get("sources", []), list): raise ValueError("snapshot.sources must be a list")
        if any(not isinstance(s, dict) or s.get("status") not in {"ok", "skipped", "error"} for s in snapshot.get("sources", [])):
            raise ValueError("Each snapshot source must be an object with ok/skipped/error status")
        if args.snapshot and not any(snapshot.get(k) for k in ("docker", "kubernetes", "milvus", "health", "metrics", "config", "manifests", "logs", "disk", "native")):
            snapshot.setdefault("sources", []).append({"name": "offline_snapshot.content", "status": "skipped", "detail": "No supported diagnostic evidence was provided; parsing JSON does not validate a deployment."})
        if not isinstance(snapshot.get("deployment", {}), dict): raise ValueError("snapshot.deployment must be an object")
        if args.snapshot and snapshot.get("kind") == "diagnostic_evidence":
            raise ValueError("evidence.json is a safe report projection, not a replay snapshot; use show-evidence --report report.json")
        previous = load_json(args.previous, 16 * 1024 * 1024) if args.previous else None
        if previous is not None and not isinstance(previous.get("findings"), list): raise ValueError("Previous input is not a Doctor report")
        report = build_report(snapshot, evaluate(snapshot), previous, include_faq=True)
        if args.output_dir:
            location = save_report(report, args.output_dir)
            print(f"Local report saved: {location}", file=sys.stderr)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else markdown(report))
        return 0 if report["status"] in {"no_findings_in_available_evidence", "observations_only"} else 1
    except (ValueError, OSError, RuntimeError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": redact(str(exc)), "read_only": True}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        # Do not emit an SDK traceback, input body, credential, or raw parser object.
        print(json.dumps({"error": f"Diagnostic processing failed ({type(exc).__name__}); check supported input shape and dependencies.", "read_only": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
