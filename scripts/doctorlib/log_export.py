"""Opt-in, bounded stdout/stderr export using Milvus' component conventions.

This is not a wrapper around an unbounded upstream shell script. No raw log text
is written or returned to the agent. The normal metadata collector is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time

from . import __version__
from .evidence import redact_evidence_text
from .reports import build_report, save_report
from .rules import evaluate
from .safety import redact, register_secret, run_log_readonly, run_readonly, validate_log_command

UPSTREAM = "https://github.com/milvus-io/milvus/tree/321719486912d4b5b6e3f823b227fc6daffc316c/deployments/export-log"
COMPONENTS = ("milvus", "etcd", "minio", "pulsar", "kafka")
KUBE_METHODS = ("kubernetes", "helm", "operator")
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})) ")


def add_export_parser(subparsers):
    p = subparsers.add_parser("export-logs", help="Plan or explicitly collect bounded, redacted logs and analyze them")
    p.add_argument("--deployment", required=True, choices=["docker", "compose", "kubernetes", "helm", "operator"])
    p.add_argument("--context"); p.add_argument("--namespace")
    p.add_argument("--release", help="Helm/Kubernetes Milvus instance label")
    p.add_argument("--operator-name", help="Operator Milvus instance and standard dependency naming")
    p.add_argument("--selector", help="Explicit Pod selector; ANDed with instance selectors when provided")
    p.add_argument("--pod", action="append", default=[])
    p.add_argument("--pod-container", action="append", default=[], help="Only these containers; default declared/recognized main container, not unrelated sidecars")
    p.add_argument("--container", action="append", default=[], help="Exact Docker containers; no inventory")
    p.add_argument("--compose-project")
    p.add_argument("--component", action="append", choices=COMPONENTS, default=[], help="Also include selected dependencies; instance/project discovery includes Milvus by default")
    p.add_argument("--previous", action="store_true", help="Kubernetes only: also read the prior instance of each restarted container")
    p.add_argument("--include-init", action="store_true", help="Kubernetes only: also include init containers")
    p.add_argument("--since", default="30m", help="Positive s/m/h duration, at most 7 days (default 30m)")
    p.add_argument("--tail", type=int, default=2000, help="1..10000 lines per stream (default 2000)")
    p.add_argument("--max-bytes", type=int, default=1048576, help="1024..4194304 raw bytes per stream; discard on overflow")
    p.add_argument("--max-streams", type=int, default=20, help="1..100 current/previous streams (default 20)")
    p.add_argument("--max-total-bytes", type=int, default=8388608, help="Total log-read/saved-text budget, 1024..33554432")
    p.add_argument("--timeout", type=float, default=8, help="Per-command seconds, maximum 120")
    p.add_argument("--total-timeout", type=float, default=60, help="Batch collection deadline, maximum 300 seconds")
    p.add_argument("--token-env", default="MILVUS_DOCTOR_TOKEN", help="Optional known secret to redact; never printed or passed to log commands")
    p.add_argument("--output-dir", help="New local directory; required with --collect, never overwritten")
    p.add_argument("--collect", action="store_true", help="Perform the selected read-only export; without this flag only an offline plan is returned")
    p.add_argument("--format", choices=["json", "markdown"], default="json")
    return p


def _name(value, kind="target"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) or ".." in value:
        raise ValueError("Invalid " + kind + " name")


def validate_export(args):
    if args.deployment not in ("docker", "compose") + KUBE_METHODS:
        raise ValueError("Unsupported log deployment")
    for value, low, high, field in ((args.tail, 1, 10000, "tail"), (args.max_bytes, 1024, 4194304, "max-bytes"),
            (args.max_streams, 1, 100, "max-streams"), (args.max_total_bytes, 1024, 33554432, "max-total-bytes")):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid " + field + " bound")
    if not 0 < args.timeout <= 120 or not 0 < args.total_timeout <= 300:
        raise ValueError("timeout must be >0..120; total-timeout must be >0..300 seconds")
    if not isinstance(args.token_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.token_env):
        raise ValueError("token-env must name an environment variable")
    for values in (args.pod, args.pod_container, args.container):
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError("Select at most 100 explicit targets per request")
        for value in values: _name(value)
    if not isinstance(args.component, list) or len(args.component) > len(COMPONENTS) or any(c not in COMPONENTS for c in args.component):
        raise ValueError("Invalid component selection")
    for value in (args.release, args.operator_name, args.compose_project):
        if value: _name(value)
    if args.deployment in KUBE_METHODS:
        if not args.context or not args.namespace:
            raise ValueError("Log reads require explicit --context and --namespace")
        if args.container or args.compose_project or (args.release and args.operator_name):
            raise ValueError("Conflicting Docker/Kubernetes or instance selections")
        if args.operator_name and args.deployment != "operator":
            raise ValueError("--operator-name requires --deployment operator")
        if args.release and args.deployment == "operator":
            raise ValueError("Use --operator-name for Operator dependency naming")
        instance = args.release or args.operator_name
        if args.pod and (instance or args.selector or args.component):
            raise ValueError("Explicit Pods cannot be combined with instance/selector/component discovery")
        if not (args.pod or instance or args.selector):
            raise ValueError("Select an instance, Pod or selector; namespace-wide log export is not implicit")
        if args.component and not instance:
            raise ValueError("Dependency components require an instance; otherwise name the exact Pod")
        if args.selector and (len(args.selector) > 1024 or args.selector.startswith('-') or any(ord(c) < 32 for c in args.selector)):
            raise ValueError("Invalid Pod selector")
        for pod in args.pod or ["selected-pod"]:
            for container in args.pod_container or ["selected-container"]:
                validate_log_command(kube_log_command(args, pod, container, args.timeout, args.max_bytes))
    else:
        if any((args.context, args.namespace, args.release, args.operator_name, args.selector, args.pod, args.pod_container, args.include_init, args.previous)):
            raise ValueError("Kubernetes selections/previous/init are not supported for Docker logs")
        if bool(args.container) == bool(args.compose_project):
            raise ValueError("Select either exact --container names or one --compose-project")
        if args.deployment == "docker" and args.compose_project:
            raise ValueError("--compose-project requires --deployment compose")
        if args.container and args.component:
            raise ValueError("Exact Docker containers cannot be combined with component discovery")
        for container in args.container or ["selected-container"]:
            validate_log_command(docker_log_command(args, container))
    if args.collect and not args.output_dir:
        raise ValueError("--collect requires a new --output-dir")


def kube_log_command(args, pod, container, timeout, byte_limit, previous=False):
    command = ["kubectl", "--context", args.context, "--namespace", args.namespace, "--request-timeout", f"{timeout:.6f}s",
               "logs", pod, "--container", container, "--since", args.since, "--tail", str(args.tail), "--timestamps=true",
               "--limit-bytes", str(byte_limit + 1)]
    return command + (["--previous=true"] if previous else [])


def docker_log_command(args, container):
    return ["docker", "logs", "--since", args.since, "--tail", str(args.tail), "--timestamps", container]


def component_selectors(args):
    """Follow official export-log conventions; no unfiltered fallback."""
    instance = args.release or args.operator_name
    if not instance: return [("selected", args.selector)]
    result = []
    for component in dict.fromkeys(["milvus"] + args.component):
        dep = instance + "-" + component if args.deployment == "operator" and component != "milvus" else instance
        if component in ("milvus", "etcd"):
            selectors = [f"app.kubernetes.io/instance={dep},app.kubernetes.io/name={component}"]
        elif component == "minio":
            selectors = [f"app.kubernetes.io/instance={dep},app.kubernetes.io/name=minio", f"release={dep},app=minio"]
        elif component == "pulsar":
            dep = instance if args.deployment != "operator" and "pulsar" in instance else instance + "-pulsar"
            selectors = [f"cluster={dep},app=pulsar"]
        else:
            selectors = [f"app.kubernetes.io/instance={dep},app.kubernetes.io/component=kafka"]
        result.extend((component, f"{args.selector},{s}" if args.selector else s) for s in selectors)
    return result


def redact_log_stream(text):
    """Remove transport timestamps before multiline redaction, then restore them."""
    lines = text.splitlines()
    prefixes, bodies = [], []
    for line in lines:
        match = _STAMP.match(line)
        prefixes.append(match[0] if match else "")
        bodies.append(line[len(match[0]):] if match else line)
    redacted = redact_evidence_text("\n".join(bodies)).splitlines()
    if len(redacted) == len(prefixes):
        redacted = [prefix + body for prefix, body in zip(prefixes, redacted)]
    return "\n".join(redacted) + ("\n" if text.endswith(("\n", "\r")) else "")


def _now(): return datetime.now(timezone.utc).isoformat()


def _dict(value): return value if isinstance(value, dict) else {}


def _objects(value): return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _failure(value, previous=False):
    text = str(value).lower()
    if re.search(r"\bunauthorized\b|\bunauthenticated\b|must be logged in|asked for the client to provide credentials", text):
        return "authentication_failed", "The selected identity was not authenticated. Verify the intended credential locally; do not paste it into the conversation or request broader permissions."
    if re.search(r"(?:^forbidden(?:\s|:|$)|\(forbidden\)|forbidden:|permission denied)", text):
        return "permission_denied", "Requested read was denied. Ask only for the selected metadata/log-read permission; never administrator access."
    if isinstance(value, ValueError) and str(value) == "Diagnostic command exceeded its output limit":
        return "size_limit", "The byte limit was reached; partial raw output was discarded. Select fewer lines/a shorter window or an explicit bounded budget."
    if isinstance(value, TimeoutError) or "timed out" in text or "timeout" in text or "deadline" in text:
        return "timeout", "The bounded read timed out; no automatic retry or limit expansion was performed."
    if isinstance(value, FileNotFoundError):
        return "missing_tool", "The required local CLI is unavailable; target components were not changed."
    if previous:
        return "previous_unavailable", "Previous-instance logs could not be retrieved; restart metadata does not guarantee retention."
    return "unavailable", "The selected metadata/log source was unavailable; raw command errors are not exported."


class _Export:
    def __init__(self, args):
        self.args = args
        self.deadline = time.monotonic() + args.total_timeout
        self.raw_bytes = 0; self.byte_budget_used = 0; self.saved_bytes = 0; self.streams_attempted = 0
        self.files = {}; self.entries = []
        self.snapshot = {"schema_version": 1, "captured_at": _now(), "sources": [], "logs": [],
                         "deployment": {"method": args.deployment, "mode": "unknown", "collection_method": args.deployment, "provenance": "explicit_selection"},
                         "collector_runtime": {"python_version": ".".join(map(str, sys.version_info[:3]))}}

    def source(self, name, status, detail="", reason=None):
        source = {"name": name, "status": status, "detail": detail, "observed_at": _now()}
        if reason: source["reason"] = reason
        self.snapshot["sources"].append(source)

    def remaining(self):
        seconds = min(self.args.timeout, self.deadline - time.monotonic())
        if seconds < 0.001: raise TimeoutError("Log export deadline reached")
        return seconds

    def metadata(self, command, source):
        try:
            result = run_readonly(command, timeout=self.remaining(), max_bytes=1048576)
            if result.returncode:
                reason, detail = _failure(result.stderr)
                self.source(source, "error", detail, reason); return None
            value = json.loads(result.stdout)
            self.source(source, "ok", "Selected metadata read completed.")
            return value
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            reason, detail = _failure(exc)
            self.source(source, "error", detail, reason)
            return None

    def kubernetes_targets(self):
        args = self.args
        base = ["kubectl", "--context", args.context, "--namespace", args.namespace, "--request-timeout", f"{args.timeout:g}s", "get", "pods"]
        found = {}; requested = {}; successful = {}
        queries = [("selected", pod, None) for pod in dict.fromkeys(args.pod)] if args.pod else [(comp, None, selector) for comp, selector in component_selectors(args)]
        for number, (component, pod, selector) in enumerate(queries, 1):
            requested[component] = requested.get(component, 0) + 1
            cmd = base + ([pod] if pod else []) + ["-o", "json"] + (["--selector", selector] if selector else [])
            value = self.metadata(cmd, f"logs.discovery.{number}")
            if value is None: continue
            if not isinstance(value, dict) or (not pod and not isinstance(value.get("items"), list)):
                self.source(f"logs.discovery.{number}.shape", "error", "Unexpected Pod metadata shape.", "invalid_input"); continue
            successful[component] = successful.get(component, 0) + 1
            items = [value] if pod else _objects(value["items"])
            if len(items) > 1000:
                self.source("logs.targets.limit", "skipped", "At most 1000 discovered Pods are considered.", "bounded_limit")
            for item in items[:1000]:
                meta = _dict(item.get("metadata")); name = meta.get("name")
                if not isinstance(name, str) or meta.get("namespace", args.namespace) != args.namespace or (pod and name != pod):
                    self.source("logs.target.invalid", "error", "Pod identity did not match the explicit scope.", "invalid_input"); continue
                if name not in found:
                    if len(found) >= 1000:
                        self.source("logs.targets.limit", "skipped", "Selected Pod union exceeds 1000.", "bounded_limit"); break
                    found[name] = (item, component)
        present = {component for _, component in found.values()}
        for component in requested:
            if component not in present and successful.get(component) == requested[component]:
                self.source("logs.target." + component, "skipped", "No Pod matched this requested component/selection. Verify the instance labels or name an exact Pod; no wider scan was attempted.", "target_not_found")
        targets = []
        for pod, (item, component) in found.items():
            spec = _dict(item.get("spec")); status = _dict(item.get("status")); matched = set()
            selection_missing = False
            for group, field, states in [("regular", "containers", "containerStatuses")] + ([("init", "initContainers", "initContainerStatuses")] if args.include_init else []):
                restarts = {c.get("name"): c.get("restartCount") for c in _objects(status.get(states)) if isinstance(c.get("name"), str)}
                containers = _objects(spec.get(field))
                if len(containers) > 100:
                    self.source("logs.containers.limit", "skipped", "Only the first 100 container definitions per group can be considered.", "bounded_limit")
                if group == "regular" and not args.pod_container and len(containers) > 1:
                    declared = _dict(_dict(item.get("metadata")).get("annotations")).get("kubectl.kubernetes.io/default-container")
                    selected = [c for c in containers if c.get("name") == declared] if isinstance(declared, str) else []
                    if not selected:
                        roles = {component} if component in COMPONENTS and component != "milvus" else {"milvus", "standalone", "proxy", "querynode", "datanode", "mixcoord", "streamingnode", "rootcoord", "querycoord", "datacoord", "indexnode", "indexcoord"}
                        selected = [c for c in containers if isinstance(c.get("name"), str) and c["name"] in roles]
                    if not selected:
                        choices = [c['name'] for c in containers[:10] if isinstance(c.get('name'), str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', c['name'])]
                        self.source("logs.containers.selection", "skipped", "Multiple regular containers require explicit --pod-container selection. Available names: " + redact(", ".join(choices)), "selection_required")
                        selection_missing = True
                    containers = selected
                for c in containers[:100]:
                    name = c.get("name")
                    if not isinstance(name, str) or (args.pod_container and name not in args.pod_container): continue
                    matched.add(name)
                    targets.append({"pod": pod, "container": name, "component": component, "group": group,
                                    "restart_count": restarts.get(name), "previous": False})
                    if args.previous:
                        count = restarts.get(name)
                        if type(count) is int and count > 0:
                            targets.append(dict(targets[-1], previous=True))
                        elif type(count) is int and count == 0:
                            self.source("logs.previous", "skipped", "Previous-instance log not requested for a container with no recorded restart.", "scope_excluded")
                        else:
                            self.source("logs.previous", "skipped", "Restart metadata unavailable; previous-instance log coverage is unknown.", "unavailable")
                    if len(targets) > args.max_streams: return targets
            if not matched and not selection_missing:
                self.source("logs.containers", "skipped", "No selected readable container was found in a matched Pod.", "target_not_found")
            elif args.pod_container and set(args.pod_container) - matched:
                self.source("logs.containers", "skipped", "Some explicitly requested containers were absent from a selected Pod.", "target_not_found")
        return targets

    def docker_targets(self):
        args = self.args
        if args.container:
            return [{"container": name, "component": "selected", "previous": False} for name in dict.fromkeys(args.container)]
        try:
            result = run_readonly(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + args.compose_project, "--format", "{{.ID}}"], timeout=self.remaining(), max_bytes=1048576)
            if result.returncode: raise RuntimeError(result.stderr)
            names = list(dict.fromkeys(result.stdout.split()))
            self.source("logs.discovery.project", "ok", "Selected Compose project container inventory read.")
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            reason, detail = _failure(exc); self.source("logs.discovery.project", "error", detail, reason); return []
        if len(names) > 100:
            self.source("logs.targets.limit", "skipped", "Only the first 100 project containers can be considered.", "bounded_limit")
        targets = []; wanted = set(["milvus"] + args.component); found = set()
        for index, name in enumerate(names[:100], 1):
            _name(name)
            value = self.metadata(["docker", "inspect", "--type", "container", name], f"logs.discovery.container.{index}")
            if not isinstance(value, list) or len(value) != 1: continue
            config = _dict(_dict(value[0]).get("Config")); labels = _dict(config.get("Labels"))
            if labels.get("com.docker.compose.project") != args.compose_project:
                self.source("logs.target.project", "error", "Container no longer belongs to the selected Compose project.", "invalid_input"); continue
            service = str(labels.get("com.docker.compose.service", "")).lower()
            image = str(config.get("Image", "")).lower()
            # Registry/organization names are not component evidence.
            leaf = image.rsplit('/', 1)[-1].split('@', 1)[0].split(':', 1)[0]
            component = service if service in COMPONENTS else leaf if leaf in COMPONENTS else None
            if component is None and service in {"standalone", "proxy", "querynode", "datanode", "mixcoord", "streamingnode", "rootcoord", "querycoord", "datacoord", "indexnode", "indexcoord"}: component = "milvus"
            if component in wanted:
                found.add(component); targets.append({"container": name, "component": component, "previous": False})
        for component in sorted(wanted - found):
            self.source("logs.target." + component, "skipped", "No matching component was established in the selected project; custom naming may require an exact --container selection.", "target_not_found")
        return targets

    def read(self, target, index):
        args = self.args
        instance = ("previous" if target["previous"] else "current") if args.deployment in KUBE_METHODS else "container_history"
        source = f"logs.export.{index}." + instance
        entry = {"alias": f"log-{index:04d}", "component": target["component"], "instance": instance, "observed_at": _now(),
                 "target": redact({key: target[key] for key in ("pod", "container", "group") if key in target})}
        self.entries.append(entry)
        try:
            timeout = self.remaining()
            limit = min(args.max_bytes, args.max_total_bytes - self.byte_budget_used)
            if self.streams_attempted >= args.max_streams or limit < 1024:
                entry.update(status="skipped", reason="bounded_limit")
                self.source(source, "skipped", "Log stream-count or total-byte budget exhausted; remaining logs were not read.", "bounded_limit"); return
            command = kube_log_command(args, target["pod"], target["container"], timeout, limit, target["previous"]) if args.deployment in KUBE_METHODS else docker_log_command(args, target["container"])
            self.streams_attempted += 1
            # Reserve the whole allowance. A timeout/overflow provides no safe
            # partial byte count, so it does not get an unlimited budget refund.
            self.byte_budget_used += limit
            result = run_log_readonly(command, timeout=timeout, max_bytes=limit)
            returned_bytes = sum(len(part.encode("utf-8", "replace")) for part in (result.stdout, result.stderr))
            self.byte_budget_used -= max(0, limit - returned_bytes)
            self.raw_bytes += returned_bytes
            if result.returncode:
                reason, detail = _failure(result.stderr, target["previous"])
                entry.update(status="error", reason=reason); self.source(source, "error", detail, reason); return
            # Docker's stderr stream can contain ordinary application logs on rc=0.
            parts = [part for part in (result.stdout, result.stderr) if part]
            text = "\n".join(redact_log_stream(part) for part in parts)
            size = len(text.encode("utf-8"))
            if self.raw_bytes > args.max_total_bytes or self.saved_bytes + size > args.max_total_bytes:
                entry.update(status="skipped", reason="bounded_limit")
                self.source(source, "skipped", "Total log budget reached; this stream was not saved.", "bounded_limit"); return
            if not text.strip():
                entry.update(status="skipped", reason="logs_empty")
                self.source(source, "skipped", "No stdout/stderr log lines in the selected window; file-persisted logs or retention may require a user-supplied excerpt. Empty output is not health evidence.", "logs_empty"); return
            filename = f"logs/log-{index:04d}.log"
            self.files[filename] = text; self.saved_bytes += size
            entry.update(status="ok", file=filename, bytes=size, lines=len(text.splitlines()))
            entry["finding_codes"] = [f["code"] for f in evaluate({"schema_version": 1, "sources": [{"name": "logs", "status": "ok"}], "logs": [text]})]
            self.snapshot["logs"].append({"text": text})
            self.source(source, "ok", "Bounded selected stdout/stderr was redacted and retained locally; it may contain historical events.")
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            reason, detail = _failure(exc, target["previous"])
            entry.update(status="error", reason=reason); self.source(source, "error", detail, reason)


def run_export(args):
    validate_export(args)
    plan = {"schema_version": 1, "tool_version": __version__, "read_only": True, "status": "planned", "upstream_reference": UPSTREAM,
            "selection": {key: getattr(args, key) for key in ("deployment", "context", "namespace", "release", "operator_name", "selector", "pod", "pod_container", "container", "compose_project", "component", "previous", "include_init")},
            "bounds": {key: getattr(args, key) for key in ("since", "tail", "max_bytes", "max_streams", "max_total_bytes", "timeout", "total_timeout")},
            "note": "Plan only: no cluster access, log read or file write. --collect performs the selected export after user authorization. Instance/project discovery includes Milvus; dependencies require --component. Prefer declared/recognized main Pod containers; use --pod-container for additional or ambiguous containers. Init containers require --include-init.",
            "privacy": "Logs may contain sensitive data. Only redacted text is saved, but redaction is not guaranteed anonymization. Respect AI/data policy; do not automatically upload or submit. This export reads stdout/stderr only, not persistent log files."}
    if not args.collect: return plan
    destination = Path(args.output_dir).expanduser()
    if os.path.lexists(destination): raise ValueError("Log export output already exists; select a new directory")
    if not destination.parent.is_dir(): raise ValueError("Log export parent directory must already exist")
    destination.mkdir(mode=0o700, exist_ok=False)
    register_secret(os.environ.get(args.token_env, ""))
    export = _Export(args)
    targets = export.kubernetes_targets() if args.deployment in KUBE_METHODS else export.docker_targets()
    # Bound retained metadata as well as subprocess calls; a single gap covers extras.
    if len(targets) > args.max_streams:
        export.source("logs.streams.limit", "skipped", "Additional selected log streams were omitted by --max-streams.", "bounded_limit")
    for index, target in enumerate(targets[:args.max_streams], 1): export.read(target, index)
    if not export.files:
        export.source("logs.content", "skipped", "No selected log text was available for analysis; metadata discovery alone is not a log diagnosis.", "unavailable")
    export.source("logs.window", "skipped", "Older/rotated/deleted logs, persistent files and data outside the explicit since/tail window were not collected. Docker stdout/stderr cross-stream order is not guaranteed.", "scope_excluded")
    export.snapshot["completed_at"] = _now()
    duration = re.fullmatch(r"(\d+)([smh])", args.since)
    export.snapshot["log_export"] = {"since_seconds": int(duration[1]) * {"s": 1, "m": 60, "h": 3600}[duration[2]],
                                   "tail_lines": args.tail, "streams_attempted": export.streams_attempted,
                                   "saved_streams": len(export.files), "previous_streams": sum(e["status"] == "ok" and e["instance"] == "previous" for e in export.entries),
                                   "saved_bytes": export.saved_bytes}
    report = build_report(export.snapshot, evaluate(export.snapshot), include_faq=True)
    manifest = {"schema_version": 1, "tool_version": __version__, "read_only": True, "upstream_reference": UPSTREAM,
                "bounds": plan["bounds"], "streams": export.entries, "streams_attempted": export.streams_attempted,
                "returned_command_bytes": export.raw_bytes, "log_byte_budget_charged": export.byte_budget_used, "saved_log_bytes": export.saved_bytes,
                "captured_at": export.snapshot["captured_at"], "completed_at": export.snapshot["completed_at"],
                "privacy": plan["privacy"], "submission_status": "not_submitted"}
    save_report(report, destination)
    (destination / "logs").mkdir(mode=0o700)
    files = dict(export.files)
    files["log-manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    for filename, content in files.items():
        fd = os.open(destination / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream: stream.write(content)
    return {"schema_version": 1, "tool_version": __version__, "read_only": True, "status": report["status"],
            "output_dir": str(destination.resolve()), "report_file": str(destination.resolve() / "report.json"),
            "log_manifest": manifest, "report": report,
            "note": "Collected logs are local redacted files, not included in this response. Read selected files with read-evidence, use the generated report for support-summary, and have the user review before sharing. No upstream script, describe, exec, copy, archive or upload was performed."}
