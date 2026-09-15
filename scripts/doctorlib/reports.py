"""Local reports, conservative comparisons, and an opt-in handoff draft."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid

from . import __version__
from .coverage import summarize_coverage
from .evidence import build_evidence, redact_evidence_text, sanitize_evidence
from .safety import redact

SUPPORT_FORM_URL = "https://zilliverse.feishu.cn/share/base/form/shrcnlGM0veXFaAKlBE0AaNsTBf"
SUPPORT_CONTEXT_FIELDS = (
    "symptom_and_impact", "actions_already_performed", "assistance_requested",
    "app_sdk_name", "app_sdk_version", "app_python_version", "milvus_version",
    "deployment_method", "deployment_topology", "problem_type", "incident_time",
    "expected_behavior", "reproduction_steps", "confirmed_facts", "hypotheses",
    "key_evidence", "remaining_questions", "recheck_status", "additional_information",
)
SUPPORT_CONTEXT_MAX_BYTES = 16 * 1024
_METHODS = {"native", "docker", "compose", "kubernetes", "helm", "operator"}
_TOPOLOGIES = {"standalone", "cluster"}
_CONTEXT_ENUMS = {
    "deployment_method": _METHODS | {"unknown"},
    "deployment_topology": _TOPOLOGIES | {"unknown"},
    "problem_type": {"deployment", "sdk", "troubleshooting", "performance", "unknown"},
    "recheck_status": {"not_performed", "evidence_expanded", "after_user_change"},
    "app_sdk_name": {"pymilvus", "PyMilvus", "Java SDK", "Node.js SDK", "Go SDK", "C# SDK", "REST API", "other", "unknown"},
}
_VERSION_PATTERN = r"v?\d{1,4}\.\d{1,4}\.\d{1,4}(?:-(?:dev|alpha|beta|rc)(?:[.-]?\d+)?)?(?:\+[0-9a-f]{7,40})?"
_CONTEXT_VERSION_PATTERN = r"v?\d{1,4}\.\d{1,4}(?:\.\d{1,4})?(?:(?:-?(?:dev|alpha|beta|rc|a|b)|\.?(?:dev|post))(?:[.-]?\d+)?)?(?:\+[0-9a-f]{7,40})?"
_SOURCE_ROOTS = {"docker", "kubernetes", "milvus", "health", "metrics", "native", "disk", "manifest", "config", "logs", "offline_snapshot"}
_SOURCE_REASONS = {
    "not_requested", "scope_excluded", "unavailable", "missing_dependency", "missing_tool",
    "dependency_missing", "bounded_limit", "size_limit", "target_not_found",
    "authentication_failed", "permission_denied", "connection_failed", "timeout", "invalid_input", "collection_failed",
    "unsupported", "unknown", "not_available", "failed", "error", "error_unknown",
    "logs_empty", "previous_unavailable", "selection_required",
}

# Never copy a finding's free-form title/evidence into a support request. These
# fixed descriptions give engineers useful meaning without naming resources.
_FINDING_MEANINGS = {
    "COVERAGE_INCOMPLETE": "Some evidence was unavailable or its selection intent is unknown; this is not a diagnosis of the service.",
    "COVERAGE_UNDECLARED": "The supplied evidence does not declare collection coverage.",
    "NO_DIAGNOSTIC_EVIDENCE": "No requested diagnostic source completed successfully.",
    "SNAPSHOT_UNSUPPORTED": "The supplied snapshot schema is unsupported.",
    "HEALTH_CHECK_FAILED": "The selected health request failed; the cause is not established.",
    "HEALTH_ACCESS_DENIED": "The selected health endpoint or gateway rejected access.",
    "DOCKER_NOT_RUNNING": "A selected container is not running.",
    "DOCKER_UNHEALTHY": "A selected container reports an unhealthy health check.",
    "DOCKER_OOM_KILLED": "A container has a historical OOM-killed exit record, not proof of a current leak.",
    "DOCKER_RESTART_HISTORY": "A lifetime restart counter is nonzero; restart frequency is unknown.",
    "DOCKER_MEMORY_PRESSURE": "One memory sample is at least 90% of an explicit container limit; sustained pressure is unverified.",
    "DISK_SPACE_PRESSURE": "The selected local filesystem has little available capacity.",
    "RESOURCE_REQUEST_EXCEEDS_LIMIT": "A selected CPU, memory or ephemeral-storage request exceeds its limit.",
    "K8S_POD_PENDING": "A Pod is Pending or explicitly unschedulable; elapsed duration is unverified.",
    "K8S_POD_FAILED": "A selected Pod has a Failed phase, which may be historical.",
    "K8S_IMAGE_PULL_FAILURE": "A container reports an image retrieval error.",
    "K8S_CRASH_LOOP": "A container currently reports CrashLoopBackOff.",
    "K8S_CONTAINER_START_FAILURE": "A container reports a configuration or startup error.",
    "K8S_OOM_KILLED": "A current or previous container termination was OOMKilled.",
    "K8S_RESTART_HISTORY": "A lifetime Kubernetes restart counter is nonzero; frequency is unknown.",
    "K8S_POD_NOT_READY": "A Running Pod has an explicit Ready=False condition.",
    "K8S_REPLICAS_NOT_READY": "A workload has fewer Ready replicas than requested.",
    "K8S_PVC_NOT_BOUND": "A selected PVC is Pending or Lost; preserve existing data.",
    "K8S_PROBE_FAILURE_EVENT": "A historical event records a probe failure.",
    "K8S_SERVICE_SELECTOR_UNCONFIRMED": "No collected Pod matches the Service selector; collection scope may explain this.",
    "HELM_STATUS_UNAVAILABLE": "Workload labels identify Helm, but release state was not read from Secrets.",
    "HELM_RELEASE_NOT_DEPLOYED": "Supplied Helm release state is not deployed; application availability is not established.",
    "OPERATOR_STATUS_STALE": "An Operator status has not observed the current resource generation.",
    "OPERATOR_NOT_READY": "A Milvus custom resource reports unavailable or degraded state.",
    "OPERATOR_READINESS_UNCONFIRMED": "A Milvus custom resource has no confirmed Healthy status yet.",
    "OPERATOR_MODE_INVALID": "A supplied Operator deployment mode is not standalone or cluster.",
    "COLLECTION_NOT_LOADED": "Metadata reports an unloaded collection; this can be intentional.",
    "COLLECTION_NO_VECTOR_INDEX": "Complete index metadata reports no index for a collection with vector fields.",
    "COLLECTION_INDEX_FAILED": "Index metadata reports a failed index build.",
    "SCHEMA_VECTOR_DIMENSION_INVALID": "A supplied vector dimension is invalid for its declared type.",
    "COMPOSE_DEPENDENCY_UNDEFINED": "A selected Compose file references an undefined required dependency; override files may define it.",
    "COMPOSE_RESERVATION_EXCEEDS_LIMIT": "A selected Compose resource reservation exceeds its limit.",
    "HELM_CLUSTER_FLAG_TYPE": "A Helm-style cluster.enabled value is not a YAML boolean.",
    "CONFIG_PORT_INVALID": "A selected configuration value is not a valid integer TCP port.",
    "CONNECTION_REFUSED": "Supplied evidence records a connection or name-resolution failure.",
    "CONNECTION_TIMEOUT": "Supplied evidence records a timeout, without establishing the slow component.",
    "AUTHENTICATION_FAILED": "Supplied evidence records an authentication or authorization error.",
    "TLS_VALIDATION_FAILED": "Supplied evidence records a TLS validation or handshake error.",
    "VECTOR_DIMENSION_MISMATCH": "Supplied evidence records a vector-dimension mismatch; compare application output and schema.",
    "FIELD_TYPE_MISMATCH": "Supplied evidence records a schema or field-type mismatch.",
    "REQUEST_SIZE_EXCEEDED": "Supplied evidence records an oversized request or batch.",
    "LOG_COLLECTION_NOT_LOADED": "A supplied log records an operation on an unloaded collection.",
    "LOG_INDEX_MISSING": "A supplied log records a missing index.",
    "MEMORY_QUOTA_EXCEEDED": "Supplied evidence records a memory-protection or loading-capacity error.",
    "DISK_CAPACITY_ERROR": "Supplied evidence records a storage capacity or quota error.",
    "DEPENDENCY_UNAVAILABLE": "Supplied evidence records a dependency connectivity failure.",
    "DATA_INTEGRITY_ERROR": "Supplied evidence contains a data-integrity error marker; preserve data and use private support.",
    "INTERNAL_PANIC": "Supplied evidence contains a crash marker; its cause is not established.",
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _sources(value):
    """Malformed saved source entries remain coverage gaps, not silent passes."""
    if not isinstance(value, list): return []
    sources = []
    for raw in value:
        source = dict(raw) if isinstance(raw, dict) else {"name": "other"}
        state = source.get("status")
        if not isinstance(state, str) or state not in {"ok", "skipped", "error"}:
            source["status"] = "unknown"
        if not isinstance(source.get("name"), str): source["name"] = "other"
        if not isinstance(source.get("reason", "unknown"), str): source["reason"] = "unknown"
        sources.append(source)
    return sources


def _version(value):
    return value if isinstance(value, str) and re.fullmatch(_VERSION_PATTERN, value) else "Not provided"


def validate_support_context(context):
    """Validate local, user-reviewed context; missing facts never block a handoff."""
    context = {} if context is None else context
    if not isinstance(context, dict) or set(context) - set(SUPPORT_CONTEXT_FIELDS):
        raise ValueError("Support context contains unsupported fields; see references/handoff.md")
    if any(not isinstance(value, str) or len(value) > 2000 for value in context.values()):
        raise ValueError("Each support context field must be a string of at most 2000 characters")
    if len(json.dumps(context, ensure_ascii=False).encode("utf-8")) > SUPPORT_CONTEXT_MAX_BYTES:
        raise ValueError("Support context must be at most 16384 bytes")
    for field, allowed in _CONTEXT_ENUMS.items():
        if context.get(field, "").strip() and context[field].strip() not in allowed:
            raise ValueError("Invalid support context value for " + field)
    for field in ("milvus_version", "app_sdk_version", "app_python_version"):
        item = context.get(field, "").strip()
        if item and item != "unknown" and not re.fullmatch(_CONTEXT_VERSION_PATTERN, item):
            raise ValueError("Support context " + field + " must be a two/three-part numeric version (optional prerelease), or unknown")
    return {key: value.strip() for key, value in context.items()}


def build_report(snapshot, findings, previous=None, include_faq=False):
    sources = _sources(snapshot.get("sources"))
    coverage = summarize_coverage(sources)
    partial = not coverage["complete_for_selected_scope"]
    # No findings is not proof of health, root cause, or production readiness.
    actionable = any(f.get("severity") in {"critical", "warning"} for f in findings)
    status = "incomplete" if partial else "attention_required" if actionable else "observations_only" if findings else "no_findings_in_available_evidence"
    report = {
        "schema_version": 1, "tool_version": __version__, "case_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "captured_at": snapshot.get("captured_at"), "completed_at": snapshot.get("completed_at"),
        "read_only": True, "status": status,
        "coverage": coverage,
        "evidence": build_evidence(snapshot),
        "support": {"form_url": SUPPORT_FORM_URL, "submission_status": "not_submitted", "summary_file": "support-summary.md"},
        "severity_counts": {level: sum(f.get("severity") == level for f in findings) for level in ("critical", "warning", "info")},
        "deployment": snapshot.get("deployment", {"mode": "unknown", "method": "snapshot"}),
        "observations": {
            "milvus_version": snapshot.get("milvus", {}).get("version") if isinstance(snapshot.get("milvus"), dict) else None,
            "doctor_sdk_version": _dict(snapshot.get("collector_runtime")).get("pymilvus_version"),
            "doctor_python_version": _dict(snapshot.get("collector_runtime")).get("python_version"),
            "collections_inspected": len(snapshot.get("milvus", {}).get("collections", [])) if isinstance(snapshot.get("milvus"), dict) else None,
            "containers_inspected": len(snapshot.get("docker", {}).get("containers", [])) if isinstance(snapshot.get("docker"), dict) else None,
            "pods_inspected": len(snapshot.get("kubernetes", {}).get("pods", [])) if isinstance(snapshot.get("kubernetes"), dict) else None,
            "numeric_metric_names": len(snapshot.get("metrics", {})) if isinstance(snapshot.get("metrics"), dict) else None,
            "process_counters": snapshot.get("native"),
            "selected_filesystem": snapshot.get("disk"),
        },
        "sources": sources,
        "findings": sorted(findings, key=lambda f: ({"critical": 0, "warning": 1, "info": 2}.get(f.get("severity"), 3), f.get("code", ""))),
        "limitations": [
            "Checks cover only the selected targets and available evidence; unobserved checks are not passes.",
            "No user data queries or remediation are executed. Only the user may apply recommendations outside Doctor.",
            "Local collection does not imply offline AI inference. Redacted output sent to your AI follows that provider's settings.",
            "No telemetry, account identification, or automatic report upload is performed.",
        ],
    }
    k8s = snapshot.get("kubernetes") or {}
    if isinstance(k8s, dict):
        pods = k8s.get("pods", [])
        if pods:
            ready = lambda pod: any(c.get("type") == "Ready" and str(c.get("status")).lower() == "true" for c in pod.get("status", {}).get("conditions", []))
            report["observations"]["pods_ready"] = sum(ready(p) for p in pods if isinstance(p, dict))
        pvcs = k8s.get("pvcs", [])
        if pvcs:
            report["observations"]["pvcs_bound"] = sum(p.get("status", {}).get("phase") == "Bound" for p in pvcs if isinstance(p, dict))
            report["observations"]["pvcs_inspected"] = len(pvcs)
    if previous is not None:
        key = lambda f: (f.get("code", ""), f.get("summary", ""))
        old = {key(f) for f in previous.get("findings", [])}; new = {key(f) for f in findings}
        report["comparison"] = {
            "new": [{"code": c, "summary": s} for c, s in sorted(new - old)],
            "still_observed": [{"code": c, "summary": s} for c, s in sorted(new & old)],
            "not_observed_now": [{"code": c, "summary": s} for c, s in sorted(old - new)],
            "interpretation": "Not observed now is NOT verified resolution. Confirm same target, coverage, workload, time window and original symptom.",
        }
    if include_faq and findings:
        try:
            from .faq import load_catalog, references_for_findings
            load_catalog()
            version = _version(report["observations"].get("milvus_version"))
            refs = references_for_findings(findings, None if version == "Not provided" else version)
            report["faq_knowledge"] = {"status": "references_available" if refs else "no_matching_reference", "references": refs,
                                       "note": "Related official FAQ references, not diagnostic evidence. Confirm version and applicability; source examples never authorize execution."}
        except (ValueError, OSError, ImportError):
            report["faq_knowledge"] = {"status": "unavailable", "references": [], "note": "Bundled FAQ references could not be validated. Diagnostic evidence and coverage are unchanged; no network fallback was attempted."}
    return redact(report)


def _faq_references(report, fallback_version=None):
    """Never copy free-form titles/URLs from a saved report into the handoff."""
    saved = _dict(report.get("faq_knowledge")).get("references")
    if not isinstance(saved, list) or not saved: return []
    try:
        from .faq import resolve_saved_references
        version = _version(_dict(report.get("observations")).get("milvus_version"))
        return resolve_saved_references(saved, fallback_version if version == "Not provided" else version)
    except (ValueError, OSError, ImportError):
        return []


def markdown(report):
    dep = report.get("deployment", {})
    lines = ["# Milvus Doctor", "", f"Status: **{report['status']}** · Read-only", f"Deployment: {dep.get('method', 'unknown')} / {dep.get('mode', 'unknown')}", "", "## Evidence coverage", ""]
    observed = report.get("observations", {})
    coverage = summarize_coverage(_sources(report.get("sources")))
    lines.append("- Complete for selected scope: " + str(coverage["complete_for_selected_scope"]).lower())
    lines.append("- Coverage: " + ", ".join(f"{key}={coverage[key]}" for key in ("successful", "errors", "unavailable", "not_requested", "scope_excluded")))
    lines.append("- Optional sources not requested and sources excluded by the selected scope are not failed checks or requests for broader permissions.")
    lines.append("- Finding counts: " + ", ".join(f"{k}={v}" for k, v in report.get("severity_counts", {}).items()))
    for field in ("milvus_version", "collections_inspected", "containers_inspected", "pods_inspected", "pods_ready", "pvcs_inspected", "pvcs_bound", "numeric_metric_names"):
        if observed.get(field) is not None:
            lines.append(f"- {field}: {observed[field]}")
    for source in _sources(report.get("sources")):
        detail = str(source.get("detail", "")).replace("\n", " ")
        reason = str(source.get("reason", "unknown")).replace("\n", " ")
        lines.append(f"- {source.get('name', '?')}: {source.get('status', 'unknown')} (reason: {reason}) — {detail}")
    if report.get("evidence"):
        selected_evidence = sanitize_evidence(report["evidence"])
        lines += ["", "## Selected technical evidence", "",
                  "The bounded allowlist below uses local aliases instead of resource names. It is not raw logs or a replayable snapshot. Review it before sharing; aliases are local to this report and are not stable identities across rechecks.",
                  "", "```json", json.dumps(selected_evidence, ensure_ascii=False, indent=2), "```", ""]
    lines += ["", "## Findings", ""]
    if not report["findings"]:
        lines += ["No issue was flagged in the available evidence. This is not a health guarantee.", ""]
    for finding in report["findings"]:
        lines += [f"### [{finding['severity']}] {finding['code']}: {finding['summary']}", ""]
        lines += [f"- Evidence: {e}" for e in finding.get("evidence", [])]
        lines += [f"- User action (not executed): {finding.get('recommendation', '')}", f"- Verify: {finding.get('verification', '')}", f"- Help route: {finding.get('route', 'community_review')}", ""]
    refs = _faq_references(report)
    if refs:
        lines += ["## Related official FAQ references", "", "Reference candidates only, not additional checks or established causes. The documentation minor version and the deployed patch/configuration must be reconciled.", ""]
        for item in refs:
            lines += ["- [" + item["title"].replace("[", "").replace("]", "") + "](" + item["source_url"] + ") — " + item["docs_version"] + "; " + item["version_status"],
                      "  - " + item["read_only_guidance"]]
            lines += ["  - " + warning for warning in item["warnings"]]
    elif _dict(report.get("faq_knowledge")).get("status") == "unavailable":
        lines += ["", "FAQ reference lookup unavailable; no FAQ-based conclusion or network fallback was added.", ""]
    if "comparison" in report:
        lines += ["## Comparison", "", report["comparison"]["interpretation"], ""]
        for field in ("new", "still_observed", "not_observed_now"):
            lines.append(f"- {field}: " + ", ".join(x["code"] for x in report["comparison"][field]))
    lines += ["", "## Boundaries", ""] + ["- " + x for x in report["limitations"]]
    lines += ["", "## Request technical support", "",
              f"[Submit Support Request]({SUPPORT_FORM_URL})", "",
              "Copy the complete, reviewed Support Summary into question 5, 'Paste your Milvus Doctor support summary'. When --output-dir is used, support-summary.md contains the standard summary and support-request.md contains submission instructions.",
              "If important context is missing, the user may append it under 'Additional information (optional)' while preserving the generated summary. Never paste credentials, business data or unreviewed logs.",
              "The user fills in contact details and project stage in the form and submits it. Generating a report or opening the link does not submit a request.", ""]
    return "\n".join(lines)


def _enum(item, allowed, default="Not provided"):
    return item if isinstance(item, str) and item in allowed else default


def _code(item):
    return item if isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", item) else "UNSPECIFIED"


def _timestamp(item):
    if not isinstance(item, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", item):
        return "Not provided"
    try:
        datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError:
        return "Not provided"
    return item


def _source_kind(item):
    """Keep collection stages, not endpoint, resource, or file identifiers."""
    if not isinstance(item, str): return "other"
    root = item.split(".")[0]
    if root not in _SOURCE_ROOTS: return "other"
    log_stream = re.fullmatch(r"logs\.export\.(?:[1-9]\d{0,2}\.)?(current|previous|container_history)", item)
    if log_stream: return "logs.export." + log_stream[1]
    if re.fullmatch(r"logs\.discovery(?:\.(?:[1-9]\d{0,2}|project|container\.[1-9]\d{0,2}))?", item):
        return "logs.discovery"
    known = {
        "docker.inspect", "docker.stats", "docker.compose", "native.process",
        "milvus.version", "milvus.collections", "milvus.collections.limit",
        "kubernetes.pods", "kubernetes.deployments", "kubernetes.statefulsets",
        "kubernetes.services", "kubernetes.pvcs", "kubernetes.events", "kubernetes.milvuses",
    }
    if item in known: return item
    match = re.fullmatch(r"milvus\.collection\.\d{1,4}\.(schema|stats|statistics|load_state|indexes)(\.limit)?", item)
    if match: return "milvus.collection." + match[1] + (match[2] or "")
    return root


def _metadata_value(observed, confirmed, provenance=None):
    if confirmed and confirmed != "unknown":
        if observed != "Not provided" and observed.lstrip("v") != confirmed.lstrip("v"):
            return f"{observed} (report); {confirmed} (user-confirmed; conflicts with report, needs verification)"
        if observed == "Not provided":
            return f"{confirmed} (user-confirmed; not independently verified)"
        return f"{observed} (report and user-confirmed)"
    if observed == "Not provided": return observed
    return f"{observed} (source: {provenance})" if provenance else observed


def _technical_facts(report):
    """Small, strictly re-projected numeric facts; never trust saved free text."""
    try:
        evidence = sanitize_evidence(report.get("evidence"))
    except ValueError:
        return []  # Historical reports did not contain an evidence projection.
    facts = []
    def number(item):
        # Counters/dimensions/capacity are integers in the evidence contract.
        return str(item) if type(item) is int and 0 <= item <= 10 ** 24 else None
    def alias(item, kind):
        return item if isinstance(item, str) and re.fullmatch(kind + r"-[1-9]\d{0,3}", item) else kind
    log_export = _dict(evidence.get("log_export"))
    log_values = [key + "=" + number(log_export[key]) for key in ("since_seconds", "tail_lines", "streams_attempted", "saved_streams", "previous_streams", "saved_bytes") if number(log_export.get(key)) is not None]
    if log_values:
        facts.append("Selected log export: " + ", ".join(log_values) + "; bounded stdout/stderr only, not proof of complete incident history; raw text is not in this summary.")
    containers = _objects(_dict(evidence.get("docker")).get("containers"))
    containers.sort(key=lambda item: (
        not (item.get("oom_killed") is True or item.get("state") in {"exited", "dead", "restarting"} or item.get("health") == "unhealthy"),
        not (type(item.get("memory_usage_bytes")) is int and type(item.get("memory_limit_bytes")) is int and item["memory_limit_bytes"] > 0 and item["memory_usage_bytes"] / item["memory_limit_bytes"] >= 0.90),
        not (type(item.get("restart_count")) is int and item["restart_count"] > 0),
    ))
    for container in containers[:3]:
        values = []
        role = _enum(container.get("role"), {"milvus", "standalone", "proxy", "rootcoord", "querycoord", "querynode", "datacoord", "datanode", "indexcoord", "indexnode", "mixcoord", "streamingnode", "etcd", "minio", "pulsar", "kafka", "woodpecker"}, "")
        if role: values.append("component=" + role)
        for field in ("memory_usage_bytes", "memory_limit_bytes", "restart_count", "exit_code"):
            item = number(container.get(field))
            if item is not None: values.append(f"{field}={item}")
        for field, allowed in (("state", {"created", "running", "paused", "restarting", "removing", "exited", "dead"}), ("health", {"healthy", "unhealthy", "starting", "none"})):
            item = _enum(container.get(field), allowed, "")
            if item: values.append(f"{field}={item}")
        if type(container.get("oom_killed")) is bool: values.append("oom_killed=" + str(container["oom_killed"]).lower())
        if values: facts.append(alias(container.get("alias"), "container") + ": " + ", ".join(values) + " (single sample / historical counters)")
    kubernetes = _dict(evidence.get("kubernetes"))
    pods = _objects(kubernetes.get("pods"))
    pods.sort(key=lambda item: (
        item.get("phase") not in {"Pending", "Failed"},
        not any(condition.get("type") == "Ready" and condition.get("status") == "False" for condition in _objects(item.get("conditions"))),
    ))
    for pod in pods[:3]:
        values = []
        phase = _enum(pod.get("phase"), {"Pending", "Running", "Succeeded", "Failed", "Unknown"}, "")
        if phase: values.append("phase=" + phase)
        for condition in _objects(pod.get("conditions")):
            if condition.get("type") == "Ready" and condition.get("status") in {"True", "False", "Unknown"}:
                values.append("Ready=" + condition["status"])
        for container in _objects(pod.get("containers"))[:3]:
            restarts = number(container.get("restart_count"))
            if restarts is not None: values.append(alias(container.get("alias"), "container") + " restart_count=" + restarts)
            reason = _enum(container.get("reason"), {"Error", "OOMKilled", "ContainerCannotRun", "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError", "InvalidImageName", "RunContainerError", "StartError", "DeadlineExceeded", "Evicted", "Unschedulable"}, "")
            if reason: values.append(alias(container.get("alias"), "container") + " reason=" + reason)
        if values: facts.append(alias(pod.get("alias"), "pod") + ": " + ", ".join(values))
    for pvc in _objects(kubernetes.get("pvcs"))[:2]:
        phase = _enum(pvc.get("phase"), {"Pending", "Bound", "Lost"}, "")
        if phase: facts.append(alias(pvc.get("alias"), "pvc") + ": phase=" + phase)
    event_meanings = {
        "storage_class_not_found": "a provisioning event reported that its referenced StorageClass was not found",
        "registry_connection_refused": "an image-pull event reported a refused connection while retrieving an image",
    }
    classified_events = [event for event in _objects(kubernetes.get("events")) if isinstance(event.get("categories"), list) and any(isinstance(category, str) and category in event_meanings for category in event["categories"])]
    for event in classified_events[:3]:
        values = [event_meanings[category] for category in event["categories"] if isinstance(category, str) and category in event_meanings]
        count = number(event.get("count"))
        if count is not None: values.append("count=" + count)
        timestamp = event.get("last_observed_at") or event.get("event_time") or event.get("first_observed_at")
        # The projection has already validated these timestamp fields.
        if isinstance(timestamp, str): values.append("observed_at=" + timestamp)
        facts.append(alias(event.get("alias"), "event") + ": " + "; ".join(values) + " (historical event, not independent verification of current configuration or connectivity)")
    for collection in _objects(_dict(evidence.get("milvus")).get("collections"))[:3]:
        values = []
        if type(collection.get("loaded")) is bool: values.append("loaded=" + str(collection["loaded"]).lower())
        row_count = number(collection.get("row_count"))
        if row_count is not None: values.append("row_count=" + row_count)
        vector_fields = [field for field in _objects(_dict(collection.get("schema")).get("fields")) if number(field.get("dim")) is not None]
        for field in vector_fields[:6]:
            dim = number(field.get("dim"))
            if dim is not None: values.append(alias(field.get("alias"), "field") + " dimension=" + dim)
        if values: facts.append(alias(collection.get("alias"), "collection") + ": " + ", ".join(values))
    health = _dict(evidence.get("health"))
    health_status = _enum(health.get("status"), {"ok", "error"}, "")
    http_status = health.get("http_status")
    if health_status:
        facts.append("health: status=" + health_status + (f", HTTP={http_status}" if type(http_status) is int and 100 <= http_status <= 599 else ""))
    if facts:
        facts = facts[:12]
        facts.append("These are selected metadata facts only; aliases are local to this report, not stable identities across rechecks. Full allowlisted evidence remains local in evidence.json.")
    return facts


def support_summary(report, context=None):
    """Copy-ready question-5 text with reviewed facts and explicit uncertainty."""
    if not isinstance(report, dict) or not isinstance(report.get("findings"), list):
        raise ValueError("Support summary requires a Doctor report with findings")
    context = validate_support_context(context)
    def context_value(field):
        item = context.get(field, "")
        return redact_evidence_text(item) if item and item != "unknown" else "Not provided"
    deployment = _dict(report.get("deployment"))
    observed = _dict(report.get("observations"))
    provenance = _enum(deployment.get("provenance"), {"explicit_selection", "target_selection", "workload_labels", "unknown", "offline_snapshot"}, "unknown")
    method = _enum(deployment.get("method"), _METHODS)
    # A native label without provenance is not evidence of bare-metal deployment;
    # neither an endpoint nor a host PID distinguishes it from Docker.
    legacy_native = method == "native" and provenance == "unknown"
    if legacy_native: method = "Not provided"
    method_text = _metadata_value(method, context.get("deployment_method"), provenance)
    if legacy_native: method_text += " (legacy native label is unverified)"
    collection_method = _enum(deployment.get("collection_method"), _METHODS | {"endpoint", "snapshot", "local_files"})
    lines = ["Milvus Doctor Support Summary", "Submission status: NOT SUBMITTED", "",
             f"Doctor version: {_version(report.get('tool_version'))}",
             "Milvus version: " + _metadata_value(_version(observed.get("milvus_version")), context.get("milvus_version")),
             "Deployment method: " + method_text,
             "Deployment topology: " + _metadata_value(_enum(deployment.get("mode"), _TOPOLOGIES), context.get("deployment_topology")),
             f"Collection method: {collection_method}",
             "Application SDK: " + context_value("app_sdk_name"),
             "Application SDK version: " + context_value("app_sdk_version"),
             "Application Python version: " + context_value("app_python_version"),
             "Doctor collector SDK version (not application SDK): " + _version(observed.get("doctor_sdk_version")),
             "Doctor collector Python version (not application Python): " + _version(observed.get("doctor_python_version")),
             "Problem type: " + context_value("problem_type"),
             "Incident time / window (user-reported): " + context_value("incident_time"),
             "Evidence collection started at: " + _timestamp(report.get("captured_at")),
             "Evidence collection completed at: " + _timestamp(report.get("completed_at")),
             "Report generated at: " + _timestamp(report.get("created_at")), "",
             "Symptom and impact:", context_value("symptom_and_impact"), "",
             "Expected behavior:", context_value("expected_behavior"), "",
             "Reproduction / diagnostic sequence (identify who performed each step):", context_value("reproduction_steps"), "",
             "Confirmed facts from the conversation:", context_value("confirmed_facts"), "",
             "Hypotheses (not established causes):", context_value("hypotheses"), "",
             "Diagnostic findings:"]
    findings = _objects(report["findings"])
    if not findings: lines.append("No rule findings in the available evidence; this does not establish that the issue is resolved.")
    categories = {}
    for f in findings:
        level = _enum(f.get("severity"), {"critical", "warning", "info"}, "unknown")
        route = _enum(f.get("route"), {"self_service", "community_review", "private_support", "security_escalation"}, "community_review")
        key = (_code(f.get("code")), level, route)
        categories[key] = categories.get(key, 0) + 1
    for (rule, level, route), count in categories.items():
        meaning = _FINDING_MEANINGS.get(rule, "Diagnostic rule matched; consult the local report and version-matched check catalog.")
        lines.append(f"- {rule} | {level} | {route} | occurrences={count}: {meaning}")
    context_version = context.get("milvus_version")
    faq_refs = _faq_references(report, None if context_version in (None, "unknown", "") else context_version)
    if faq_refs:
        lines += ["", "Related official FAQ references (knowledge, not evidence or actions performed):"]
        for ref in faq_refs:
            lines.append(f"- {ref['id']} | {ref['docs_version']} | {ref['version_status']} | {ref['title']}")
            lines.append("  Source: " + ref["source_url"])
            lines.append("  Read-only guidance: " + ref["read_only_guidance"])
            for warning in ref["warnings"]:
                lines.append("  Caution: " + warning)
    facts = _technical_facts(report)
    lines += ["", "Selected technical facts (allowlisted metadata):"]
    lines += ["- " + fact for fact in facts] if facts else ["Not provided; use reviewed excerpts below if available."]
    lines += ["", "Reviewed key evidence (user/Agent-selected, not raw automatic logs):", context_value("key_evidence")]
    status = _enum(report.get("status"), {"incomplete", "attention_required", "observations_only", "no_findings_in_available_evidence"})
    lines += ["", "Evidence coverage and gaps:", f"Overall status: {status}"]
    sources = _sources(report.get("sources"))
    coverage = summarize_coverage(sources)
    lines.append("Complete for selected scope: " + str(coverage["complete_for_selected_scope"]).lower())
    lines.append("Sources: " + ", ".join(f"{key}={coverage[key]}" for key in ("successful", "errors", "unavailable", "not_requested", "scope_excluded")))
    groups = {}
    for source in sources:
        state = _enum(source.get("status"), {"ok", "skipped", "error"}, "unknown")
        reason = _enum(source.get("reason"), _SOURCE_REASONS, "unknown")
        key = (_source_kind(source.get("name")), state, reason)
        groups[key] = groups.get(key, 0) + 1
    for (kind, state, reason), count in sorted(groups.items())[:40]:
        explanation = ""
        if state == "skipped" and reason in {"not_requested", "scope_excluded"}:
            explanation = "; intentionally not checked, no permission expansion required"
        elif state == "skipped":
            explanation = "; requested evidence unavailable or selection intent unknown"
        lines.append(f"- {kind}: {state}; reason={reason}; records={count}{explanation}")
    if len(groups) > 40: lines.append("Additional source categories omitted here; review the local report.")
    lines += ["Unselected areas remain unverified; complete selected coverage is not a system health guarantee.", "",
              "Actions already performed by the user:", context_value("actions_already_performed"), "",
              "Recheck results:"]
    recheck = context.get("recheck_status")
    recheck_text = {
        "not_performed": "Not performed (user-confirmed).",
        "evidence_expanded": "Evidence expanded only; no user change or recovery is established by this comparison.",
        "after_user_change": "After a user-reported change; original symptom recovery still requires explicit verification.",
    }
    lines.append(recheck_text.get(recheck, "Not provided"))
    comparison = report.get("comparison")
    if isinstance(comparison, dict):
        lines.append("Report comparison (does not establish a repair or business recovery):")
        for field in ("new", "still_observed", "not_observed_now"):
            entries = _objects(comparison.get(field))
            codes = sorted({_code(f.get("code")) for f in entries})
            lines.append(f"- {field}: " + (", ".join(codes[:60]) or "None"))
        lines.append("A finding not observed now is not verified resolution; compare target, coverage, workload and the original symptom.")
    lines += ["", "Remaining questions / evidence needed:", context_value("remaining_questions"), "",
              "Assistance requested:", context_value("assistance_requested"), "",
              "Doctor performed read-only checks only. Any changes listed above were performed by the user, not Doctor."]
    if context.get("additional_information"):
        lines += ["", "Additional information (optional):", context_value("additional_information")]
    lines.append("")
    return redact_evidence_text("\n".join(lines))


def support_draft(report, context=None):
    """Local form instructions wrapping the copy-ready support summary."""
    lines = ["# Request Technical Support (NOT SUBMITTED)", "",
             f"[Submit Support Request]({SUPPORT_FORM_URL})", "",
             "Complete the contact, company/organization and project-stage fields according to the form's current required-field markers. You can also provide optional preferred contact details. These fields are not duplicated in the summary.",
             "Review the Support Summary below, then copy it into question 5: 'Paste your Milvus Doctor support summary'.",
             "Unknown information is marked Not provided. Doctor can use information already shared in your conversation; you do not need to rewrite your issue in the form.",
             "If important information is not covered, optionally append it under 'Additional information (optional)' after the summary. For example: business impact, urgency, recent changes, or anything else the technical team should know. Keep the original summary format.",
             "Review selected technical facts and any excerpts before sharing. Never include credentials, personal contacts already requested by the form, business data, or unreviewed raw logs. Missing optional context does not prevent requesting help.",
             "Only the user submits the form. Opening this link or generating these files does not create a ticket, notify an engineer, or establish a response deadline.", "",
             "---", "", support_summary(report, context)]
    return "\n".join(lines)


def save_report(report, directory):
    dest = Path(directory).expanduser()
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    if dest.is_symlink(): raise ValueError("Report directory must not be a symlink")
    evidence = sanitize_evidence(report["evidence"]) if report.get("evidence") else build_evidence({})
    files = {"report.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n", "report.md": markdown(report), "evidence.json": json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", "support-request.md": support_draft(report), "support-summary.md": support_summary(report)}
    if any((dest / filename).exists() or (dest / filename).is_symlink() for filename in files):
        raise ValueError("Report files already exist; choose a new output directory")
    for filename, content in files.items():
        fd = os.open(dest / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream: stream.write(content)
    return str(dest.resolve())
