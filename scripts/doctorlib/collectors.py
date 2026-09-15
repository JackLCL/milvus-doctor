"""Bounded, read-only collection for explicitly selected Milvus targets.

The collector deliberately does not execute a shell, inspect environment values,
read Kubernetes Secrets, fetch remote logs, or run commands inside containers.
Every external source has an independent coverage result.
"""

from __future__ import annotations

import json
import http.client
import logging
import math
import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .local_files import read_regular_file
from .safety import redact, run_readonly


LABEL_KEYS = {
    "app", "component", "release", "chart", "heritage", "app.kubernetes.io/name",
    "app.kubernetes.io/instance", "app.kubernetes.io/component", "app.kubernetes.io/version",
    "app.kubernetes.io/managed-by", "app.kubernetes.io/part-of", "helm.sh/chart",
    "milvus.io/instance", "milvus.io/component", "milvus.io/service",
    "com.docker.compose.project", "com.docker.compose.service",
    "com.docker.compose.version", "com.docker.compose.container-number",
}
CONFIG_KEYS = {
    "etcd": {"endpoints", "rootPath", "metaSubPath", "kvSubPath", "requestTimeout", "dialTimeout", "use", "path", "configPath", "embed"},
    "minio": {"address", "port", "useSSL", "bucketName", "rootPath", "useIAM", "cloudProvider", "region", "requestTimeoutMs"},
    "pulsar": {"address", "port", "webport", "maxMessageSize", "tenant", "namespace"},
    "kafka": {"brokerList", "readTimeout", "securityProtocol"},
    "rocksmq": {"path", "rocksmqPageSize", "retentionTimeInMinutes", "retentionSizeInMB"},
    "woodpecker": {"storage", "type", "rootPath", "bucketName", "address", "port", "useSSL"},
    "mq": {"type", "enablePursuitMode", "pursuitLag", "pursuitBufferTime", "pursuitBufferSize"},
    "common": {"storageType", "simdType", "retentionDuration", "gracefulTime", "defaultPartitionName", "defaultIndexName", "security", "authorizationEnabled", "superUsers"},
    "proxy": {"port", "internalPort", "maxTaskNum", "maxNameLength", "maxFieldNum", "maxVectorFieldNum", "maxShardNum", "maxDimension", "http", "enabled", "debug_mode", "port", "timeTickInterval"},
    "queryNode": {"port", "cache", "memoryLimit", "disk", "enabled", "search", "grouping", "readTaskConcurrency", "maxDiskUsagePercentage", "segcore", "knowhereThreadPoolNumRatio", "loadMemoryUsageFactor", "enableDisk", "mmap", "mmapEnabled", "growingMmapEnabled", "lazyload", "waitTimeout", "workerPoolSize"},
    "queryCoord": {"port", "autoHandoff", "autoBalance", "balanceIntervalSeconds", "memoryUsageMaxDifferencePercentage", "overloadedMemoryThresholdPercentage", "taskCheckInterval", "loadTimeoutSeconds"},
    "dataNode": {"port", "flush", "insertBufSize", "deleteBufBytes", "flowGraph", "maxQueueLength", "maxParallelism", "memory", "forceSyncEnable", "forceSyncWatermark", "checkInterval"},
    "dataCoord": {"port", "segment", "maxSize", "sealProportion", "assignmentExpiration", "enableCompaction", "compaction", "enableAutoCompaction", "indexBasedCompaction", "minSegment", "maxSegment", "garbageCollection", "enabled", "interval"},
    "indexNode": {"port", "scheduler", "buildParallel", "enableDisk", "maxDiskUsagePercentage"},
    "indexCoord": {"port", "bindIndexNodeMode"},
    "rootCoord": {"port", "maxPartitionNum", "minSegmentSizeToEnableIndex", "maxDatabaseNum", "dmlChannelNum"},
    "streaming": {"enabled", "wal", "balancer", "interval"},
    "localStorage": {"path"},
    "log": {"level", "format", "file", "rootPath", "maxSize", "maxAge", "maxBackups", "stdout"},
    "quotaAndLimits": {"enabled", "quotaCenterCollectInterval", "limitWriting", "ttProtection", "enabled", "maxTimeTickDelay", "memProtection", "dataNodeMemoryLowWaterLevel", "dataNodeMemoryHighWaterLevel", "queryNodeMemoryLowWaterLevel", "queryNodeMemoryHighWaterLevel", "diskProtection", "diskQuota", "diskQuotaPerCollection", "dml", "insertRate", "deleteRate", "bulkLoadRate", "dql", "searchRate", "queryRate"},
    "grpc": {"serverMaxSendSize", "serverMaxRecvSize", "clientMaxSendSize", "clientMaxRecvSize", "client"},
    "tls": {"tlsMode"},
}
RESOURCE_KINDS = ["pods", "deployments", "statefulsets", "services", "persistentvolumeclaims", "events"]


def _source(snapshot: dict, name: str, status: str, detail: str = "", reason=None) -> None:
    source = {"name": name, "status": status, "detail": redact(str(detail))[:1200],
              "observed_at": datetime.now(timezone.utc).isoformat()}
    if reason:
        source["reason"] = reason
    snapshot["sources"].append(source)


def _bounded_failure_reason(exc: Exception):
    """Recognize our own bounds, without reflecting an exception's payload."""
    if isinstance(exc, ValueError) and str(exc) in {
        "HTTP response exceeds configured collection size limit.",
        "Diagnostic command exceeded its output limit",
    }:
        return "size_limit"
    if isinstance(exc, TimeoutError) or re.search(r"timed? ?out|timeout|deadline.?exceeded|exceeded its total deadline", str(exc).lower()):
        return "timeout"
    return None


def _auth_failure_reason(exc: Exception):
    """Read explicit gRPC status from a bounded exception chain, never payloads.

    PyMilvus may wrap UNAUTHENTICATED in a generic MilvusException(code=2).
    That numeric SDK code alone is not evidence of authentication failure.
    """
    pending = [exc]
    seen = set()
    for _ in range(8):
        if not pending:
            break
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        try:
            code = getattr(current, "code", None)
            code = code() if callable(code) else code
            name = getattr(code, "name", None)
        except Exception:
            name = None
        if name == "UNAUTHENTICATED":
            return "authentication_failed"
        if name == "PERMISSION_DENIED":
            return "permission_denied"
        for field in ("__cause__", "__context__"):
            child = getattr(current, field, None)
            if isinstance(child, BaseException) and id(child) not in seen:
                pending.append(child)
    return None


def _error_detail(exc: Exception, fallback: str) -> str:
    """Classify transport failures without ever returning exception contents."""
    auth_reason = _auth_failure_reason(exc)
    if auth_reason == "authentication_failed":
        return "Authentication failed: the SDK transport returned UNAUTHENTICATED. Verify the selected credential locally; do not paste it into the conversation or request administrator access."
    if auth_reason == "permission_denied":
        return "Permission denied: the SDK transport returned PERMISSION_DENIED. Verify only the documented metadata-read privileges for the selected target; do not request administrator access."
    text = str(exc).lower()
    if re.search(r"certificate|ssl|tls handshake|x509", text):
        return "TLS handshake failure; verify certificate trust, validity and target hostname."
    if re.search(r"unauthenticated|authentication|invalid (?:token|password|credential)|permission.?denied|authorization|forbidden|\b401\b|\b403\b", text):
        return "Authentication failed or permission denied; verify the selected minimal read-only identity."
    bounded_reason = _bounded_failure_reason(exc)
    if bounded_reason == "size_limit":
        return "Response size limit reached; the selected source exceeded --max-bytes. No complete result was used. The user may select a smaller source or explicitly increase --max-bytes, up to 16777216 bytes; the bound is never increased automatically."
    if bounded_reason == "timeout":
        return "Connection timeout; selected source did not finish within --timeout. Check the selected endpoint and network path, or explicitly choose a longer bounded --timeout (maximum 120 seconds); no automatic retry or timeout increase was performed."
    if re.search(r"connection refused|actively refused|failed to connect|no route to host|network is unreachable|name or service not known|no such host|temporary failure in name resolution", text):
        return "Connection refused or name-resolution failure; check the selected endpoint, listener and network path."
    return f"{fallback} ({type(exc).__name__})."


def _limit(args: Any, name: str, default: int | float, maximum: int | float):
    try:
        minimum = 0.001 if name == "timeout" else 1
        return min(maximum, max(minimum, float(getattr(args, name, None) or default)))
    except (TypeError, ValueError):
        return default


def _run(args: Any, argv: list[str]):
    return run_readonly(argv, timeout=_limit(args, "timeout", 8, 120),
                        max_bytes=int(_limit(args, "max_bytes", 1048576, 16777216)))


def _json_command(args: Any, snapshot: dict, name: str, argv: list[str]):
    try:
        result = _run(args, argv)
        if result.returncode:
            _source(snapshot, name, "error", result.stderr or f"Command exited {result.returncode}")
            return None
        data = json.loads(result.stdout)
        _source(snapshot, name, "ok")
        return data
    except Exception as exc:
        # subprocess.TimeoutExpired and optional transport failures are coverage failures.
        _source(snapshot, name, "error", _error_detail(exc, "Collection failed; no result available"))
    return None


def _pick(value: Any, fields: set[str]) -> dict:
    return {k: _scalar(v) for k, v in value.items() if k in fields and not isinstance(v, dict)} if isinstance(value, dict) else {}


def _labels(labels: Any) -> dict:
    return _pick(labels, LABEL_KEYS)


def _scalar(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_scalar(item, depth + 1) for item in value[:100] if not isinstance(item, dict)]
    return None


def _config_tree(value: Any, allowed: set[str], depth: int = 0) -> Any:
    if depth > 8:
        return None
    if not isinstance(value, dict):
        return _scalar(value)
    return {k: _config_tree(v, allowed, depth + 1) for k, v in value.items() if k in allowed}


def sanitize_config(config: Any) -> dict:
    """Known diagnostic configuration only; credential keys are never copied."""
    if not isinstance(config, dict):
        return {}
    return {k: _config_tree(v, CONFIG_KEYS[k]) for k, v in config.items() if k in CONFIG_KEYS}


def _probe(probe: Any) -> dict:
    result = _pick(probe, {"initialDelaySeconds", "periodSeconds", "timeoutSeconds", "failureThreshold", "successThreshold"})
    if isinstance(probe, dict):
        for key, fields in (("httpGet", {"path", "port", "scheme"}), ("tcpSocket", {"port"}), ("grpc", {"port", "service"})):
            if key in probe:
                result[key] = _pick(probe[key], fields)
        if "exec" in probe:
            result["exec_present"] = True
    return result


def _container_spec(container: Any) -> dict:
    result = _pick(container, {"name", "image", "imagePullPolicy"})
    if not isinstance(container, dict):
        return result
    resources = container.get("resources", {})
    result["resources"] = {k: _pick(resources.get(k), {"cpu", "memory", "ephemeral-storage", "nvidia.com/gpu"}) for k in ("requests", "limits") if k in resources}
    result["ports"] = [_pick(p, {"name", "containerPort", "hostPort", "protocol"}) for p in container.get("ports", [])[:100]]
    for key in ("livenessProbe", "readinessProbe", "startupProbe"):
        if key in container:
            result[key] = _probe(container[key])
    # Presence is enough to review privilege, without environment, args or mounts.
    if "securityContext" in container:
        result["securityContext"] = _pick(container["securityContext"], {"privileged", "runAsNonRoot", "readOnlyRootFilesystem", "allowPrivilegeEscalation"})
    return result


def _pod_spec(spec: Any) -> dict:
    result = _pick(spec, {"nodeName", "restartPolicy", "schedulerName", "hostNetwork", "terminationGracePeriodSeconds"})
    if isinstance(spec, dict):
        for key in ("containers", "initContainers"):
            result[key] = [_container_spec(c) for c in spec.get(key, [])[:100]]
        result["nodeSelector"] = _pick(spec.get("nodeSelector"), {"kubernetes.io/arch", "kubernetes.io/os", "node.kubernetes.io/instance-type"})
        result["volumes"] = [{"name": v.get("name"), "type": next((k for k in ("persistentVolumeClaim", "emptyDir", "hostPath", "configMap", "secret") if k in v), "other"), **({"persistentVolumeClaim": _pick(v["persistentVolumeClaim"], {"claimName", "readOnly"})} if "persistentVolumeClaim" in v else {})} for v in spec.get("volumes", [])[:100] if isinstance(v, dict)]
    return result


def _conditions(conditions: Any) -> list:
    return [_pick(c, {"type", "status", "reason", "message", "lastTransitionTime"}) for c in (conditions or [])[:100]]


def _pod_status(status: Any) -> dict:
    result = _pick(status, {"phase", "reason", "message", "startTime"})
    if not isinstance(status, dict):
        return result
    result["conditions"] = _conditions(status.get("conditions"))
    for group in ("containerStatuses", "initContainerStatuses"):
        result[group] = []
        for state in status.get(group, [])[:100]:
            entry = _pick(state, {"name", "ready", "started", "restartCount", "image"})
            for field in ("state", "lastState"):
                entry[field] = {k: _pick(v, {"reason", "message", "exitCode", "signal", "startedAt", "finishedAt"}) for k, v in state.get(field, {}).items() if k in {"running", "terminated", "waiting"}}
            result[group].append(entry)
    return result


def sanitize_kubernetes_resource(resource: Any) -> dict:
    """Retain resource shape needed for rules, dropping opaque/credential data."""
    if not isinstance(resource, dict) or resource.get("kind") == "Secret":
        return {}
    kind = resource.get("kind", "")
    if kind not in {"Pod", "Deployment", "StatefulSet", "Service", "PersistentVolumeClaim", "Event", "Milvus", "ConfigMap"}:
        return {}
    metadata = _pick(resource.get("metadata"), {"name", "namespace", "generation", "creationTimestamp", "deletionTimestamp"})
    metadata["labels"] = _labels(resource.get("metadata", {}).get("labels"))
    result = {"apiVersion": resource.get("apiVersion"), "kind": kind, "metadata": metadata}
    spec, status = resource.get("spec", {}), resource.get("status", {})
    if kind == "Pod":
        result.update(spec=_pod_spec(spec), status=_pod_status(status))
    elif kind in {"Deployment", "StatefulSet"}:
        result["spec"] = _pick(spec, {"replicas", "serviceName", "podManagementPolicy", "minReadySeconds"})
        template = spec.get("template", {})
        result["spec"]["template"] = {"metadata": {"labels": _labels(template.get("metadata", {}).get("labels"))}, "spec": _pod_spec(template.get("spec", {}))}
        result["status"] = _pick(status, {"replicas", "readyReplicas", "availableReplicas", "updatedReplicas", "currentReplicas", "observedGeneration"})
        result["status"]["conditions"] = _conditions(status.get("conditions"))
    elif kind == "Service":
        result["spec"] = _pick(spec, {"type", "clusterIP", "publishNotReadyAddresses", "externalTrafficPolicy"})
        result["spec"]["ports"] = [_pick(p, {"name", "port", "targetPort", "nodePort", "protocol"}) for p in spec.get("ports", [])[:100]]
        result["spec"]["selector"] = _labels(spec.get("selector"))
    elif kind == "PersistentVolumeClaim":
        result["spec"] = _pick(spec, {"accessModes", "storageClassName", "volumeMode"})
        result["spec"]["resources"] = {"requests": _pick(spec.get("resources", {}).get("requests"), {"storage"})}
        result["status"] = {"phase": status.get("phase"), "capacity": _pick(status.get("capacity"), {"storage"}), "conditions": _conditions(status.get("conditions"))}
    elif kind == "Event":
        result.update(_pick(resource, {"type", "reason", "message", "count", "firstTimestamp", "lastTimestamp", "eventTime"}))
        result["involvedObject"] = _pick(resource.get("involvedObject", resource.get("regarding")), {"kind", "name", "namespace"})
    elif kind == "Milvus":
        result["spec"] = _pick(spec, {"mode", "version"})
        result["spec"]["config"] = sanitize_config(spec.get("config", {}))
        result["spec"]["components"] = {k: {**_pick(v, {"replicas", "image", "imageUpdateMode"}), "resources": _container_spec({"resources": v.get("resources", {})}).get("resources", {})} for k, v in spec.get("components", {}).items() if k in {"standalone", "proxy", "rootCoord", "queryCoord", "queryNode", "dataCoord", "dataNode", "indexCoord", "indexNode", "mixCoord", "streamingNode"} and isinstance(v, dict)}
        result["spec"]["dependencies"] = {k: _pick(v, {"external", "inCluster", "type"}) for k, v in spec.get("dependencies", {}).items() if k in {"etcd", "storage", "pulsar", "kafka", "woodpecker"} and isinstance(v, dict)}
        result["status"] = _pick(status, {"status", "observedGeneration"})
        result["status"]["conditions"] = _conditions(status.get("conditions"))
    elif kind == "ConfigMap":
        # Parse only conventional Milvus configuration names; never preserve raw data.
        result["config"] = {}
        for name in ("milvus.yaml", "user.yaml"):
            if name in resource.get("data", {}):
                try:
                    docs = _parse_documents(resource["data"][name])
                    if docs:
                        result["config"].update(sanitize_config(docs[0]))
                except Exception:
                    pass
    return result


def sanitize_manifest(document: Any) -> dict:
    if not isinstance(document, dict):
        return {}
    if document.get("kind"):
        if document["kind"] == "List":
            return {"kind": "List", "items": [item for raw in document.get("items", [])[:1000] if (item := sanitize_kubernetes_resource(raw))]}
        return sanitize_kubernetes_resource(document)
    if isinstance(document.get("services"), dict):
        services = {}
        for name, service in list(document["services"].items())[:100]:
            if not isinstance(service, dict):
                continue
            safe = _pick(service, {"image", "restart", "mem_limit", "mem_reservation", "cpus", "pids_limit"})
            # Commands may carry credentials. Retain only recognized Milvus roles.
            safe["role"] = _role(name, service.get("image", ""), service.get("command", []))
            safe["labels"] = _labels(service.get("labels"))
            safe["ports"] = [_scalar(p) if not isinstance(p, dict) else _pick(p, {"target", "published", "protocol", "mode"}) for p in service.get("ports", [])[:100]]
            dependencies = service.get("depends_on", {})
            if isinstance(dependencies, dict):
                safe["depends_on"] = {dependency: _pick(options, {"condition", "required"}) for dependency, options in list(dependencies.items())[:100]}
            else:
                safe["depends_on"] = dependencies[:100] if isinstance(dependencies, list) else []
            deploy = service.get("deploy", {})
            safe["deploy"] = {"replicas": deploy.get("replicas"), "resources": {group: _pick(deploy.get("resources", {}).get(group), {"cpus", "memory", "pids"}) for group in ("limits", "reservations")}}
            health = service.get("healthcheck", {})
            safe["healthcheck"] = _pick(health, {"interval", "timeout", "retries", "start_period", "disable"})
            if "test" in health:
                safe["healthcheck"]["test_present"] = True
            # Environment contents are omitted entirely, including compose interpolation.
            services[str(name)] = safe
        return {"services": services}
    # Helm values: extract only explicit diagnostic structure.
    helm_keys = {"cluster", "standalone", "proxy", "rootCoordinator", "queryCoordinator", "queryNode", "dataCoordinator", "dataNode", "indexCoordinator", "indexNode", "mixCoordinator", "streamingNode", "etcd", "minio", "pulsar", "pulsarv3", "kafka", "woodpecker", "service", "persistence", "metrics", "image", "extraConfigFiles"}
    result = sanitize_config(document)
    for key in helm_keys.intersection(document):
        value = document[key]
        if not isinstance(value, dict):
            continue
        safe = _pick(value, {"enabled", "replicas", "replicaCount", "type", "port", "repository", "tag", "pullPolicy", "storageClass", "size", "accessModes", "retentionPolicy"})
        if "resources" in value:
            safe["resources"] = _container_spec({"resources": value["resources"]})["resources"]
        if "persistence" in value and isinstance(value["persistence"], dict):
            safe["persistence"] = _pick(value["persistence"], {"enabled", "storageClass", "size", "accessModes"})
        if key == "extraConfigFiles":
            safe = {}
            for filename in ("user.yaml", "milvus.yaml"):
                if isinstance(value.get(filename), str):
                    try:
                        docs = _parse_documents(value[filename])
                        safe[filename] = sanitize_config(docs[0]) if docs else {}
                    except Exception:
                        safe[filename] = {"parse_error": True}
        result[key] = safe
    return result


def _role(name: str, image: str, command: Any = None) -> str:
    # Only classify known tokens. Never return raw command arguments.
    tokens = command if isinstance(command, list) else str(command or "").split()
    combined = " ".join([str(name), str(image), *(str(t) for t in tokens)]).lower()
    for role in ("standalone", "streamingnode", "querynode", "datanode", "indexnode", "mixcoord", "querycoord", "datacoord", "rootcoord", "indexcoord", "proxy", "etcd", "minio", "pulsar", "kafka"):
        if role in combined:
            return role
    return "milvus" if "milvus" in combined else "unknown"


def parse_size(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgtpe]?i?b?)?\s*", str(value), re.I)
    if not match:
        return None
    suffix = (match.group(2) or "").lower()
    exponent = "kmgtpe".find(suffix[0]) + 1 if suffix and suffix[0] in "kmgtpe" else 0
    return int(float(match.group(1)) * (1024 if "i" in suffix else 1000) ** exponent)


def _docker_container(raw: dict) -> dict:
    state, config = raw.get("State", {}), raw.get("Config", {})
    ports = []
    for internal, bindings in raw.get("NetworkSettings", {}).get("Ports", {}).items():
        ports.append({"container_port": internal, "host_ports": [b.get("HostPort") for b in (bindings or [])]})
    return {"name": raw.get("Name", "").lstrip("/"), "image": config.get("Image", ""),
            "role": _role(raw.get("Name", ""), config.get("Image", ""), config.get("Cmd", [])),
            "state": state.get("Status", "unknown"), "health": state.get("Health", {}).get("Status", "not_configured"),
            "restart_count": raw.get("RestartCount", 0), "oom_killed": state.get("OOMKilled", False),
            "exit_code": state.get("ExitCode"), "memory_limit_bytes": raw.get("HostConfig", {}).get("Memory", 0),
            "labels": _labels(config.get("Labels")), "ports": ports}


def collect_docker(args: Any, snapshot: dict) -> None:
    names = list(getattr(args, "container", None) or [])
    project = getattr(args, "compose_project", None)
    if project:
        try:
            result = _run(args, ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.ID}}"])
            if result.returncode:
                _source(snapshot, "docker.compose", "error", result.stderr or "Compose inventory failed")
                return
            names.extend(n for n in result.stdout.splitlines() if re.fullmatch(r"[a-f0-9]{12,64}", n))
            _source(snapshot, "docker.compose", "ok", f"Found {len(names)} selected project containers.")
        except Exception as exc:
            _source(snapshot, "docker.compose", "error", f"Compose inventory failed ({type(exc).__name__}).")
            return
    names = list(dict.fromkeys(names))
    if len(names) > 100:
        _source(snapshot, "docker.targets.limit", "skipped", "Selected container count exceeds 100; split the diagnosis into bounded target groups.", "bounded_limit")
    names = names[:100]
    snapshot["docker"] = {"containers": []}
    if not names:
        _source(snapshot, "docker.inspect", "skipped", "No explicit containers or matching Compose project containers.")
        return
    raw = _json_command(args, snapshot, "docker.inspect", ["docker", "inspect", "--type", "container", *names])
    if not isinstance(raw, list):
        return
    snapshot["docker"]["containers"] = [_docker_container(c) for c in raw if isinstance(c, dict)]
    try:
        result = _run(args, ["docker", "stats", "--no-stream", "--format", "{{json .}}", *names])
        if result.returncode:
            _source(snapshot, "docker.stats", "error", result.stderr or "Container stats unavailable.")
            return
        stats = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        by_name = {str(item.get("Name", "")).lstrip("/"): item for item in stats}
        for container in snapshot["docker"]["containers"]:
            stat = by_name.get(container["name"], {})
            memory = parse_size(str(stat.get("MemUsage", "")).split("/")[0])
            if memory is not None:
                container["memory_usage_bytes"] = memory
        _source(snapshot, "docker.stats", "ok")
    except Exception as exc:
        _source(snapshot, "docker.stats", "error", f"Container stats unavailable ({type(exc).__name__}).")
    _infer_mode(snapshot)


def _infer_mode(snapshot: dict) -> None:
    if snapshot["deployment"]["mode"] != "unknown":
        return
    roles = {c.get("role") for c in snapshot.get("docker", {}).get("containers", [])}
    if "standalone" in roles:
        snapshot["deployment"]["mode"] = "standalone"
    elif roles.intersection({"proxy", "querynode", "datanode", "rootcoord", "mixcoord", "streamingnode"}):
        snapshot["deployment"]["mode"] = "cluster"
    for cr in snapshot.get("kubernetes", {}).get("milvuses", []):
        mode = str(cr.get("spec", {}).get("mode", "")).lower()
        if mode in {"standalone", "cluster"}:
            snapshot["deployment"]["mode"] = mode
    for pod in snapshot.get("kubernetes", {}).get("pods", []):
        labels = pod.get("metadata", {}).get("labels", {})
        component = str(labels.get("app.kubernetes.io/component", labels.get("component", ""))).lower()
        if component == "standalone":
            snapshot["deployment"]["mode"] = "standalone"
        elif component in {"proxy", "querynode", "datanode", "mixcoord", "streamingnode"}:
            snapshot["deployment"]["mode"] = "cluster"


def collect_kubernetes(args: Any, snapshot: dict) -> None:
    context, namespace = getattr(args, "context", None), getattr(args, "namespace", None)
    if not context or not namespace:
        _source(snapshot, "kubernetes", "skipped", "Both explicit context and namespace are required; no cluster-wide collection was performed.")
        return
    snapshot["kubernetes"] = {key: [] for key in ("pods", "deployments", "statefulsets", "services", "pvcs", "events", "milvuses", "helm_releases")}
    snapshot["kubernetes"].update(context=context, namespace=namespace)
    base = ["kubectl", "--context", context, "--namespace", namespace]
    selector = getattr(args, "selector", None)
    release = getattr(args, "release", None)
    # Official Helm dependencies may use either the canonical instance label or
    # legacy release label. Kubernetes selectors cannot OR different keys: make
    # two independently bounded requests, both confined to this explicit release
    # AND any additional user selector. Never fall back to a namespace-wide scan.
    selectors = [selector]
    if release:
        selectors = [f"{selector},{label}={release}" if selector else f"{label}={release}"
                     for label in ("app.kubernetes.io/instance", "release")]
    kinds = list(RESOURCE_KINDS)
    if snapshot["deployment"]["method"] == "operator" or getattr(args, "operator_name", None):
        kinds.append("milvuses.milvus.io")
    workloads = ("pods", "deployments", "statefulsets", "milvuses")
    workload_queries_complete = True
    for kind in kinds:
        # Events generally lack workload labels: exclude rather than invent coverage.
        if kind == "events" and (selector or release):
            _source(snapshot, "kubernetes.events", "skipped", "Events omitted for label-scoped scans; select namespace without a selector to inspect namespace events.", "scope_excluded")
            continue
        key = {"persistentvolumeclaims": "pvcs", "milvuses.milvus.io": "milvuses"}.get(kind, kind)
        named_cr = kind == "milvuses.milvus.io" and getattr(args, "operator_name", None)
        query_selectors = [None] if named_cr else selectors
        merged = []
        seen = set()
        limited = False
        default_kind = {"pods": "Pod", "deployments": "Deployment", "statefulsets": "StatefulSet", "services": "Service", "pvcs": "PersistentVolumeClaim", "events": "Event", "milvuses": "Milvus"}[key]
        for query_index, query_selector in enumerate(query_selectors):
            argv = base + ["get", kind]
            if named_cr:
                argv.append(args.operator_name)
            elif query_selector:
                argv += ["--selector", query_selector]
            argv += ["-o", "json"]
            source_name = f"kubernetes.{key}" + (".legacy_label" if query_index else "")
            data = _json_command(args, snapshot, source_name, argv)
            if not isinstance(data, dict):
                if key in workloads:
                    workload_queries_complete = False
                continue
            items = data.get("items", [data])
            if not isinstance(items, list):
                _source(snapshot, source_name + ".shape", "error", "Kubernetes response did not contain a valid resource list.")
                if key in workloads:
                    workload_queries_complete = False
                continue
            limited = limited or len(items) > 1000
            for index, item in enumerate(items[:1000]):
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata", {})
                metadata = metadata if isinstance(metadata, dict) else {}
                identity = (default_kind, metadata.get("namespace", namespace), metadata.get("name"))
                if not metadata.get("name"):
                    identity = (default_kind, query_index, index)
                if identity in seen:
                    continue
                seen.add(identity)
                if len(merged) >= 1000:
                    limited = True
                    continue
                # Lists may omit per-item kind; infer from the selected resource endpoint.
                safe = sanitize_kubernetes_resource({"kind": default_kind, **item})
                if safe:
                    merged.append(safe)
        snapshot["kubernetes"][key] = merged
        if limited:
            _source(snapshot, f"kubernetes.{key}.limit", "skipped", "Resource selection exceeds 1000 unique items or a bounded response; only the first 1000 unique resources were inspected.", "bounded_limit")
            if key in workloads:
                workload_queries_complete = False
    releases = {}
    for key in ("pods", "deployments", "statefulsets", "services"):
        for obj in snapshot["kubernetes"][key]:
            labels = obj.get("metadata", {}).get("labels", {})
            if labels.get("app.kubernetes.io/managed-by", "").lower() == "helm" or labels.get("helm.sh/chart") or labels.get("heritage", "").lower() == "helm":
                name = labels.get("app.kubernetes.io/instance", labels.get("release", "unknown"))
                releases[name] = {"name": name, "namespace": namespace, "chart": labels.get("helm.sh/chart", labels.get("chart", "unknown"))}
    snapshot["kubernetes"]["helm_releases"] = list(releases.values())
    if workload_queries_complete and not any(snapshot["kubernetes"][key] for key in workloads):
        _source(snapshot, "kubernetes.target", "skipped", "No workload matched the selected namespace/release/selector. Check the explicit target; an empty successful API response does not establish deployment health.", "target_not_found")
    if snapshot["kubernetes"]["milvuses"]:
        snapshot["deployment"]["method"] = "operator"
        snapshot["deployment"]["provenance"] = "workload_labels"
    elif releases and snapshot["deployment"]["method"] == "kubernetes":
        snapshot["deployment"]["method"] = "helm"
        snapshot["deployment"]["provenance"] = "workload_labels"
    _infer_mode(snapshot)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a selected target's bearer token to another location.
        return None


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return urllib.parse.urlunsplit((parsed.scheme, hostname + (f":{parsed.port}" if parsed.port else ""), parsed.path, "", ""))


class _HTTPDeadline:
    """A per-request wall deadline with interruptible sockets, including headers.

    urllib's timeout is an inactivity timeout, so a server can otherwise send
    bytes forever just before each timeout. A daemon worker lets the caller
    enforce the deadline even during DNS/connect/header parsing. On expiration,
    sockets owned by this request are shut down to release any blocked reader.
    A blocked platform DNS resolver may finish later; cancellation then prevents
    the newly connected socket from sending the HTTP request.
    """

    def __init__(self, timeout: float):
        self.deadline = time.monotonic() + timeout
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.connections = []
        self.sockets = []
        self.result = None
        self.error = None

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise TimeoutError("HTTP collection exceeded its total deadline")
        return remaining

    def connection_factory(self, connection_type):
        owner = self

        class Connection(connection_type):
            def connect(self):
                owner.remaining()
                super().connect()
                with owner.lock:
                    owner.sockets.append(self.sock)
                try:
                    self.sock.settimeout(owner.remaining())
                except Exception:
                    self.close()
                    raise

        def create(*args, **kwargs):
            kwargs["timeout"] = owner.remaining()
            connection = Connection(*args, **kwargs)
            with owner.lock:
                owner.connections.append(connection)
            return connection

        return create

    def handlers(self):
        http_factory = self.connection_factory(http.client.HTTPConnection)
        https_factory = self.connection_factory(http.client.HTTPSConnection)

        class HTTPHandler(urllib.request.HTTPHandler):
            def http_open(self, req):
                return self.do_open(http_factory, req)

        class HTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):
                options = {"context": self._context}
                if hasattr(self, "_check_hostname"):
                    options["check_hostname"] = self._check_hostname
                return self.do_open(https_factory, req, **options)

        return HTTPHandler(), HTTPSHandler(), _NoRedirect()

    def abort(self):
        self.cancelled.set()
        with self.lock:
            sockets = self.sockets + [connection.sock for connection in self.connections if connection.sock is not None]
        for connection_socket in sockets:
            try:
                connection_socket.shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass

    def read(self, request, max_bytes: int) -> tuple[int, str]:
        def work():
            try:
                opener = urllib.request.build_opener(*self.handlers())
                with opener.open(request, timeout=self.remaining()) as response:
                    contents = bytearray()
                    while True:
                        remaining = self.remaining()
                        with self.lock:
                            current_sockets = list(self.sockets)
                        for connection_socket in current_sockets:
                            try:
                                connection_socket.settimeout(remaining)
                            except OSError:
                                pass
                        chunk = response.read1(min(65536, max_bytes + 1 - len(contents)))
                        self.remaining()
                        if not chunk:
                            break
                        contents.extend(chunk)
                        if len(contents) > max_bytes:
                            raise ValueError("HTTP response exceeds configured collection size limit.")
                    self.result = response.status, contents.decode("utf-8", errors="replace")
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                self.error = exc
            finally:
                self.finished.set()

        worker = threading.Thread(target=work, name="milvus-doctor-http", daemon=True)
        worker.start()
        try:
            if not self.finished.wait(self.remaining()):
                raise TimeoutError("HTTP collection exceeded its total deadline")
            self.remaining()
            if self.error is not None:
                raise self.error
            return self.result
        except Exception:
            self.abort()
            raise


def _http_get(args: Any, url: str) -> tuple[int, str]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an explicit HTTP(S) URL without credentials, query or fragment.")
    headers = {"Accept": "text/plain, application/json", "User-Agent": "milvus-doctor-readonly/1"}
    # SDK credentials never apply to independent health/metrics destinations.
    # An authenticated HTTP endpoint remains an explicit coverage limitation.
    req = urllib.request.Request(url, headers=headers, method="GET")
    deadline = _HTTPDeadline(_limit(args, "timeout", 8, 120))
    return deadline.read(req, int(_limit(args, "max_bytes", 1048576, 16777216)))


def parse_metrics(text: str) -> dict[str, list[float]]:
    """Parse scalar samples only, with no Prometheus labels in the output."""
    metrics: dict[str, list[float]] = {}
    pattern = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{.*\})?\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)(?:\s+\S+)?$")
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        name, value = match.groups()
        # Restrict service metrics; metric names themselves may otherwise contain arbitrary text.
        if not name.startswith(("milvus_", "process_", "go_", "etcd_", "pulsar_", "minio_", "grpc_")):
            continue
        number = float(value)
        if math.isfinite(number) and len(metrics.setdefault(name, [])) < 10000:
            metrics[name].append(number)
    return metrics


def collect_http(args: Any, snapshot: dict) -> None:
    for arg, name in (("health_url", "health"), ("metrics_url", "metrics")):
        url = getattr(args, arg, None)
        if not url:
            _source(snapshot, name, "skipped", "No explicit URL selected.", "not_requested")
            continue
        try:
            status, body = _http_get(args, url)
            if name == "health":
                stripped = body.strip()
                healthy = False
                # Milvus endpoints can return JSON health data or a simple OK string.
                if stripped.startswith("{"):
                    health_data = json.loads(stripped)
                    if isinstance(health_data, dict):
                        if "isHealthy" in health_data:
                            healthy = health_data["isHealthy"] is True
                        elif "status" in health_data or "state" in health_data:
                            healthy = str(health_data.get("status", health_data.get("state", ""))).lower() in {"ok", "healthy", "ready"}
                else:
                    healthy = stripped.lower() in {"ok", "healthy", "ready"}
                healthy = healthy and 200 <= status < 300
                snapshot[name] = {"url": _safe_url(url), "status": "ok" if healthy else "error", "http_status": status,
                                  "detail": "Endpoint reports healthy." if healthy else "Endpoint did not provide a recognized healthy response."}
                _source(snapshot, name, "ok", "Health response collected.")
            else:
                snapshot[name] = parse_metrics(body)
                _source(snapshot, name, "ok" if snapshot[name] else "error", f"Collected {len(snapshot[name])} numeric metric names; labels omitted.")
        except urllib.error.HTTPError as exc:
            if name == "health":
                snapshot[name] = {"url": _safe_url(url), "status": "error", "http_status": exc.code, "detail": "HTTP health request failed."}
            _source(snapshot, name, "error", f"HTTP request failed with status {exc.code}.")
        except Exception as exc:
            _source(snapshot, name, "error", _error_detail(exc, "Endpoint collection failed; check URL, access and response size"), _bounded_failure_reason(exc))


def _schema(collection: dict) -> dict:
    return {"auto_id": collection.get("auto_id"), "enable_dynamic_field": collection.get("enable_dynamic_field"),
            "fields": [{**_pick(field, {"name", "type", "datatype", "is_primary", "auto_id", "nullable"}),
                        "params": _pick(field.get("params"), {"dim", "max_length", "max_capacity"})}
                       for field in collection.get("fields", [])[:100] if isinstance(field, dict)]}


def _suppress_sdk_logs() -> list:
    """Keep SDK exception payloads out of terminal output; restore after collection."""
    for name in ("pymilvus", "grpc"):
        logging.getLogger(name)
    saved = []
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger) and (name in {"pymilvus", "grpc"} or name.startswith(("pymilvus.", "grpc."))):
            saved.append((logger, logger.disabled, logger.handlers, logger.propagate))
            logger.disabled = True
            logger.handlers = [logging.NullHandler()]
            logger.propagate = False
    return saved


def _restore_sdk_logs(saved: list) -> None:
    for logger, disabled, handlers, propagate in saved:
        logger.disabled = disabled
        logger.handlers = handlers
        logger.propagate = propagate


def collect_milvus(args: Any, snapshot: dict) -> None:
    endpoint = getattr(args, "endpoint", None)
    if not endpoint:
        _source(snapshot, "milvus", "skipped", "No explicit Milvus endpoint selected.", "not_requested")
        return
    parsed = urllib.parse.urlsplit(endpoint if "://" in endpoint else f"http://{endpoint}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        _source(snapshot, "milvus", "error", "Use an HTTP(S) endpoint without credentials or query parameters; pass credentials via token_env.")
        return
    try:
        import pymilvus
        from pymilvus import MilvusClient
    except ImportError:
        _source(snapshot, "milvus", "skipped", "Optional pymilvus is not installed; SDK metadata checks unavailable.", "dependency_missing")
        return
    snapshot["collector_runtime"] = {"pymilvus_version": getattr(pymilvus, "__version__", None),
                                     "python_version": ".".join(str(n) for n in sys.version_info[:3])}
    timeout = _limit(args, "timeout", 8, 120)
    client = None
    sdk_log_states = _suppress_sdk_logs()
    try:
        kwargs = {"uri": endpoint if "://" in endpoint else f"http://{endpoint}", "timeout": timeout}
        token_env = getattr(args, "token_env", None)
        if token_env:
            token = os.environ.get(token_env)
            if not token and token_env != "MILVUS_DOCTOR_TOKEN":
                _source(snapshot, "milvus", "error", "Configured credential environment variable is not set.")
                return
            if token:
                kwargs["token"] = token
        if getattr(args, "database", None):
            kwargs["db_name"] = args.database
        client = MilvusClient(**kwargs)
        snapshot["milvus"] = {"collections": []}
        try:
            snapshot["milvus"]["version"] = client.get_server_version(timeout=timeout)
            _source(snapshot, "milvus.version", "ok")
        except Exception as exc:
            _source(snapshot, "milvus.version", "error", _error_detail(exc, "Version unavailable"), _auth_failure_reason(exc))
        selected = getattr(args, "collection", None) or []
        names = list(dict.fromkeys(selected)) if selected else client.list_collections(timeout=timeout)
        limit = int(_limit(args, "collection_limit", 10, 100))
        _source(snapshot, "milvus.collections", "ok", f"{'User selected' if selected else 'Listed'} {len(names)} collections; inspect at most {limit}.")
        if len(names) > limit:
            _source(snapshot, "milvus.collections.limit", "skipped", f"{len(names) - limit} collections not inspected due to configured limit.", "bounded_limit")
        for name in names[:limit]:
            entry = {"name": name}
            for suffix, method in (("schema", "describe_collection"), ("statistics", "get_collection_stats"), ("load_state", "get_load_state")):
                try:
                    value = getattr(client, method)(collection_name=name, timeout=timeout)
                    if suffix == "schema":
                        entry["schema"] = _schema(value)
                        entry.update(_pick(value, {"num_shards", "consistency_level", "num_partitions"}))
                    elif suffix == "statistics":
                        count = value.get("row_count")
                        if count is not None:
                            entry["row_count"] = int(count)
                    else:
                        state = value.get("state")
                        # pymilvus LoadState: NotExist=0, NotLoad=1, Loading=2, Loaded=3.
                        named_state = str(state).lower().split(".")[-1]
                        if state == 3 or named_state == "loaded":
                            entry["loaded"] = True
                        elif state == 1 or named_state in {"notload", "notloaded"}:
                            entry["loaded"] = False
                        else:
                            entry["loaded"] = None
                        entry["load_state"] = str(state)
                    _source(snapshot, f"milvus.collection.{len(snapshot['milvus']['collections'])}.{suffix}", "ok")
                except Exception as exc:
                    _source(snapshot, f"milvus.collection.{len(snapshot['milvus']['collections'])}.{suffix}", "error", _error_detail(exc, "Collection metadata unavailable"), _auth_failure_reason(exc))
            try:
                indexes = client.list_indexes(collection_name=name, timeout=timeout)
                collected_indexes = []
                for index in indexes[:20]:
                    info = client.describe_index(collection_name=name, index_name=index, timeout=timeout)
                    collected_indexes.append({**_pick(info, {"index_name", "field_name", "index_type", "metric_type", "state", "total_rows", "indexed_rows", "pending_index_rows"}), "params": _pick(info.get("params"), {"M", "efConstruction", "nlist", "m", "nbits"})})
                entry["indexes"] = collected_indexes
                entry["indexes_complete"] = len(indexes) <= 20
                if len(indexes) > 20:
                    _source(snapshot, f"milvus.collection.{len(snapshot['milvus']['collections'])}.indexes.limit", "skipped", f"{len(indexes) - 20} indexes not inspected due to limit.")
                _source(snapshot, f"milvus.collection.{len(snapshot['milvus']['collections'])}.indexes", "ok")
            except Exception as exc:
                entry["indexes_complete"] = False
                _source(snapshot, f"milvus.collection.{len(snapshot['milvus']['collections'])}.indexes", "error", _error_detail(exc, "Index metadata unavailable"), _auth_failure_reason(exc))
            snapshot["milvus"]["collections"].append(entry)
    except Exception as exc:
        _source(snapshot, "milvus", "error", _error_detail(exc, "SDK collection failed; check connection and metadata permissions"), _auth_failure_reason(exc))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        _restore_sdk_logs(sdk_log_states)


def _read_file(path: str, max_bytes: int) -> str:
    return read_regular_file(path, max_bytes).decode("utf-8", errors="replace")


def _parse_documents(text: str) -> list:
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError("PyYAML is required to read YAML; JSON works without it") from exc
        return list(yaml.safe_load_all(text))


def collect_files(args: Any, snapshot: dict) -> None:
    max_bytes = int(_limit(args, "max_bytes", 1048576, 16777216))
    manifests = getattr(args, "manifest", None) or []
    if len(manifests) > 20:
        _source(snapshot, "manifest.limit", "skipped", "More than 20 manifests selected; remaining files were not inspected.", "bounded_limit")
    if manifests:
        snapshot["manifests"] = []
    for i, path in enumerate(manifests[:20]):
        try:
            docs = _parse_documents(_read_file(path, max_bytes))
            if len(docs) > 100:
                _source(snapshot, f"manifest.{i}.documents.limit", "skipped", "More than 100 documents in selected manifest; only first 100 inspected.", "bounded_limit")
            safe = [item for doc in docs[:100] if (item := sanitize_manifest(doc))]
            snapshot["manifests"].extend(safe)
            _source(snapshot, f"manifest.{i}", "ok" if safe else "skipped", f"Read {len(safe)} supported diagnostic documents; unrelated fields omitted.")
        except Exception as exc:
            _source(snapshot, f"manifest.{i}", "error", f"Local manifest could not be read ({type(exc).__name__}); check format, size and YAML dependency.")
    config_file = getattr(args, "config_file", None)
    if config_file:
        try:
            docs = _parse_documents(_read_file(config_file, max_bytes))
            if len(docs) > 1:
                _source(snapshot, "config.documents.limit", "skipped", "Configuration contains multiple documents; only the first is interpreted.", "bounded_limit")
            snapshot["config"] = sanitize_config(docs[0] if docs else {})
            _source(snapshot, "config", "ok" if snapshot["config"] else "skipped", "Read allowlisted diagnostic settings; credential fields omitted.")
        except Exception as exc:
            _source(snapshot, "config", "error", f"Local config could not be read ({type(exc).__name__}); check format, size and YAML dependency.")
    log_files = getattr(args, "log_file", None) or []
    if len(log_files) > 20:
        _source(snapshot, "logs.limit", "skipped", "More than 20 log files selected; remaining files were not inspected.", "bounded_limit")
    if log_files:
        snapshot["logs"] = []
    for i, path in enumerate(log_files[:20]):
        try:
            snapshot["logs"].append(redact(_read_file(path, max_bytes)))
            _source(snapshot, f"logs.{i}", "ok", "Read explicitly selected local log; no remote logs fetched.")
        except Exception as exc:
            _source(snapshot, f"logs.{i}", "error", f"Local log could not be read ({type(exc).__name__}); check file and size.")


def collect_native(args: Any, snapshot: dict) -> None:
    pid = getattr(args, "pid", None)
    if not pid:
        _source(snapshot, "native.process", "skipped", "No explicit PID selected; no process inventory collected.", "not_requested")
        return
    try:
        if not str(pid).isdigit() or int(pid) <= 0:
            raise ValueError("PID must be a positive integer")
        result = _run(args, ["ps", "-p", str(pid), "-o", "pid=,comm=,pcpu=,pmem=,rss=,vsz="])
        if result.returncode or not result.stdout.strip():
            _source(snapshot, "native.process", "error", "Selected process is absent or inaccessible.")
            return
        fields = result.stdout.strip().split()
        if len(fields) != 6:
            raise ValueError("Unexpected process status format")
        snapshot["native"] = {"pid": int(fields[0]), "command": fields[1], "cpu_percent": float(fields[2]), "memory_percent": float(fields[3]), "rss_bytes": int(fields[4]) * 1024, "virtual_memory_bytes": int(fields[5]) * 1024}
        _source(snapshot, "native.process", "ok", "Read process counters only; no arguments or environment collected.")
    except Exception as exc:
        _source(snapshot, "native.process", "error", f"Process counters unavailable ({type(exc).__name__}).")


def collect_disk(args: Any, snapshot: dict) -> None:
    selected = getattr(args, "data_dir", None)
    if not selected:
        return
    try:
        path = Path(selected).expanduser()
        if not path.is_dir():
            raise ValueError("Selected data directory is not a directory")
        usage = shutil.disk_usage(path)
        snapshot["disk"] = {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}
        _source(snapshot, "disk", "ok", "Read filesystem capacity for the selected data directory; did not list or open its contents.")
    except Exception as exc:
        _source(snapshot, "disk", "error", _error_detail(exc, "Selected filesystem capacity unavailable"))


def collect(args: Any) -> dict:
    method = getattr(args, "deployment", "auto") or "auto"
    explicit = method != "auto"
    if method == "auto":
        if getattr(args, "operator_name", None):
            method = "operator"
        elif getattr(args, "release", None):
            method = "helm"
        elif getattr(args, "namespace", None):
            method = "kubernetes"
        elif getattr(args, "compose_project", None):
            method = "compose"
        elif getattr(args, "container", None):
            method = "docker"
        elif (getattr(args, "manifest", None) or getattr(args, "config_file", None) or getattr(args, "log_file", None)) and not any(getattr(args, key, None) for key in ("endpoint", "health_url", "metrics_url", "pid", "data_dir")):
            method = "snapshot"
        else:
            method = "unknown"
    mode = getattr(args, "mode", "auto")
    collection_method = method
    if method == "unknown":
        collection_method = "native" if getattr(args, "pid", None) else "endpoint" if any(
            getattr(args, field, None) for field in ("endpoint", "health_url", "metrics_url")) else "local_files"
    snapshot = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                "collector_runtime": {"python_version": ".".join(str(n) for n in sys.version_info[:3])},
                "deployment": {"method": method, "mode": mode if mode in {"standalone", "cluster"} else "unknown",
                               "collection_method": collection_method,
                               "provenance": "explicit_selection" if explicit else "unknown" if method == "unknown" else "target_selection"},
                "sources": []}
    collect_files(args, snapshot)
    collect_disk(args, snapshot)
    if method in {"docker", "compose"}:
        collect_docker(args, snapshot)
    elif method in {"kubernetes", "helm", "operator"}:
        collect_kubernetes(args, snapshot)
    elif collection_method == "native":
        collect_native(args, snapshot)
    collect_http(args, snapshot)
    collect_milvus(args, snapshot)
    snapshot["completed_at"] = datetime.now(timezone.utc).isoformat()
    return redact(snapshot)
