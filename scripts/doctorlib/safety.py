"""Narrow command execution and output redaction for diagnostic collectors."""
from __future__ import annotations

import ipaddress
import os
import re
import selectors
import subprocess
import time
from urllib.parse import urlsplit, urlunsplit


_SENSITIVE = re.compile(r"password|passwd|secret|credential|authorization|access.?key|api.?key|private.?key|(^|_)token($|_)", re.I)
_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$")
_KUBE_RESOURCES = {"pods", "pod", "deployments", "deployment", "statefulsets", "statefulset", "services", "service", "svc", "pvc", "persistentvolumeclaims", "events", "milvuses", "milvus", "milvuses.milvus.io"}
_KNOWN_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    if value and len(value) >= 4:
        _KNOWN_SECRETS.add(value)


def _safe_name(value: str) -> bool:
    return bool(_IDENT.fullmatch(value)) and ".." not in value


def validate_command(argv: list[str]) -> None:
    """Fail closed. No shell, exec, logs, credentials, mutations or arbitrary flags."""
    if not argv or not all(isinstance(x, str) and "\x00" not in x for x in argv):
        raise ValueError("Invalid diagnostic command")
    if argv[0] not in {"docker", "kubectl", "helm", "ps"}:
        raise ValueError("Only supported read-only diagnostic programs are allowed")
    program, rest = argv[0], argv[1:]
    if program == "docker":
        if not rest:
            raise ValueError("Missing Docker read operation")
        action, args = rest[0], rest[1:]
        if action not in {"ps", "inspect", "stats", "version"}:
            raise ValueError("Docker operation is not read-only allowlisted")
        allowed = {"--format", "--filter", "--type"}
        flags = {"--no-stream", "-a", "--all", "--no-trunc"}
        names = []
        i = 0
        while i < len(args):
            value = args[i]
            if value == "--":
                names.extend(args[i + 1:]); break
            if value in flags:
                i += 1; continue
            if value in allowed:
                if i + 1 >= len(args):
                    raise ValueError("Missing Docker option value")
                following = args[i + 1]
                if value == "--format" and following not in {"{{json .}}", "{{.ID}}", "{{.Names}}", "{{.Server.Version}}"}:
                    raise ValueError("Docker format is not allowlisted")
                if value == "--filter" and not (following.startswith("label=com.docker.compose.project=") and _safe_name(following.split("=", 2)[-1])):
                    raise ValueError("Only an explicit Compose project filter is allowed")
                if value == "--type" and following != "container":
                    raise ValueError("Only container inspection is supported")
                i += 2; continue
            if value.startswith("-") or not _safe_name(value):
                raise ValueError("Invalid Docker target or option")
            names.append(value); i += 1
        if action in {"inspect", "stats"} and not names:
            raise ValueError("Explicit container targets are required")
        if action == "stats" and "--no-stream" not in args:
            raise ValueError("Streaming Docker statistics are not allowed")
        return
    if program == "ps":
        if len(rest) != 4 or rest[0] != "-p" or not rest[1].isdigit() or rest[2] != "-o" or rest[3] != "pid=,comm=,pcpu=,pmem=,rss=,vsz=":
            raise ValueError("Only explicit PID statistics are allowed")
        return
    if program == "helm":
        if rest != ["version", "--short"]:
            raise ValueError("Helm release queries may read Secrets; use workload labels instead")
        return
    # kubectl: only named context inventory or namespace-scoped resource GETs.
    if rest in [["config", "current-context"], ["config", "get-contexts", "-o", "name"]]:
        return
    positional = []; namespace = None; output = None; i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in {"--context", "--namespace", "-n", "-o", "--output", "-l", "--selector", "--request-timeout", "--field-selector"}:
            if i + 1 >= len(rest): raise ValueError("Missing Kubernetes option value")
            v = rest[i + 1]
            if arg in {"--namespace", "-n"}: namespace = v
            if arg in {"-o", "--output"}: output = v
            if not v or v.startswith("-") or any(c in v for c in "\r\n\x00"):
                raise ValueError("Invalid Kubernetes option value")
            i += 2; continue
        if arg.startswith("--request-timeout="):
            if not re.fullmatch(r"--request-timeout=\d+(?:\.\d+)?s", arg): raise ValueError("Invalid timeout")
            i += 1; continue
        if arg.startswith("-"): raise ValueError("Kubernetes flag is not allowlisted")
        positional.append(arg); i += 1
    if not namespace or not _safe_name(namespace) or output != "json":
        raise ValueError("Explicit namespace and JSON output are required")
    if len(positional) not in {2, 3} or positional[0] != "get":
        raise ValueError("Only Kubernetes GET is allowed")
    if any(x not in _KUBE_RESOURCES for x in positional[1].split(",")):
        raise ValueError("Kubernetes resource is not allowlisted")
    if len(positional) == 3 and not _safe_name(positional[2]):
        raise ValueError("Invalid Kubernetes resource name")


def run_readonly(argv: list[str], timeout: float = 8, max_bytes: int = 1048576) -> subprocess.CompletedProcess:
    validate_command(argv)
    return _run_bounded(argv, timeout, max_bytes)


def validate_log_command(argv):
    """Separate opt-in log boundary; ordinary metadata execution stays unchanged."""
    if not isinstance(argv, list) or not argv or not all(isinstance(value, str) and "\x00" not in value for value in argv):
        raise ValueError("Invalid log command")
    def duration(value):
        match = re.fullmatch(r"([1-9][0-9]{0,5})([smh])", value)
        return bool(match and int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]] <= 604800)
    def integer(value, low, high):
        return bool(re.fullmatch(r"[0-9]{1,9}", value)) and low <= int(value) <= high
    def dns(value, maximum=253):
        return len(value) <= maximum and all(len(part) <= 63 and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", part) for part in value.split('.'))
    if argv[0] == "docker":
        if (len(argv) != 8 or argv[1:3] != ["logs", "--since"] or argv[4] != "--tail"
                or argv[6] != "--timestamps" or not duration(argv[3])
                or not integer(argv[5], 1, 10000)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", argv[7])):
            raise ValueError("Docker logs require one named container, bounded since/tail and timestamps; no extra options")
        return
    if argv[0] == "kubectl":
        flags = {1: "--context", 3: "--namespace", 5: "--request-timeout", 7: "logs", 9: "--container", 11: "--since", 13: "--tail", 15: "--timestamps=true", 16: "--limit-bytes"}
        if len(argv) not in (18, 19) or any(argv[index] != flag for index, flag in flags.items()):
            raise ValueError("Kubernetes logs require explicit context/namespace/pod/container and fixed bounds")
        context = argv[2]
        timeout = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", argv[6])
        if (not context or len(context) > 253 or context.startswith('-') or any(ord(char) < 32 for char in context)
                or not dns(argv[4], 63) or not dns(argv[8]) or not dns(argv[10], 63)
                or not duration(argv[12]) or not integer(argv[14], 1, 10000)
                or not integer(argv[17], 1025, 4194305)
                or not timeout or not 0 < float(timeout[1]) <= 120
                or (len(argv) == 19 and argv[18] != "--previous=true")):
            raise ValueError("Invalid or unbounded Kubernetes log selection")
        return
    raise ValueError("Only bounded Docker or Kubernetes log reads are supported")


def run_log_readonly(argv, timeout=8, max_bytes=1048576):
    """Return raw data only to the exporter, which must redact before saving it."""
    validate_log_command(argv)
    if not 1024 <= max_bytes <= 4194304:
        raise ValueError("Log byte limit must be 1024..4194304")
    return _run_bounded(argv, timeout, max_bytes)


def _run_bounded(argv, timeout, max_bytes):
    if not (0 < timeout <= 120) or not (1024 <= max_bytes <= 16 * 1024 * 1024):
        raise ValueError("Diagnostic limits are outside the supported range")
    # Stream into bounded memory, kill the child on timeout or excessive output.
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, shell=False)
    selector = selectors.DefaultSelector(); buffers = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + timeout
        total = 0
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError("Read-only diagnostic command timed out")
            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = os.read(key.fd, min(65536, max_bytes - total + 1))
                if not chunk: selector.unregister(key.fileobj); continue
                total += len(chunk)
                if total > max_bytes: raise ValueError("Diagnostic command exceeded its output limit")
                buffers[key.data].extend(chunk)
        rc = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        return subprocess.CompletedProcess(argv, rc, buffers["stdout"].decode("utf-8", "replace"), buffers["stderr"].decode("utf-8", "replace"))
    finally:
        selector.close()
        if proc.poll() is None: proc.kill(); proc.wait()
        proc.stdout.close(); proc.stderr.close()


def redact_text(text: str) -> str:
    for secret in sorted(_KNOWN_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"(?i)(\b(?:proxy[-_])?authorization[\"']?\s*[:=]\s*[\"']?)(?:basic|bearer|negotiate)\s+[^\s\"'<>]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)(\b[a-z][a-z0-9+.-]{0,31}://)[^\s/@]+(?::[^\s/@]*)?@", r"\1[REDACTED]@", text)
    credential_key = r"(?:password|passwd|secret(?:[_-]?(?:key|value))?|token|api[_-]?key|access[_-]?key(?:[_-]?id)?|(?:client|session|refresh|id)[_-]?(?:secret|token)|aws[_-]?(?:access[_-]?key[_-]?id|secret[_-]?access[_-]?key)|authorization)"
    text = re.sub(r"(?i)([\"']?" + credential_key + r"[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}]+)", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(--(?:password|passwd|token|api-key|secret-key|access-key)(?:=|\s+))(\"[^\"]*\"|'[^']*'|[^\s]+)", r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b", "[REDACTED]", text)
    text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[EMAIL]", text)
    def ip_replace(match):
        try: ipaddress.ip_address(match.group()); return "[IP]"
        except ValueError: return match.group()
    return re.sub(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", ip_replace, text)


def redact(value):
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if _SENSITIVE.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str): return redact_text(value)
    return value


def validate_endpoint(value: str, purpose: str = "endpoint") -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{purpose} must be http(s) with no embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{purpose} must not contain query parameters or fragments")
    try: parsed.port
    except ValueError as exc: raise ValueError("Invalid endpoint port") from exc
    if purpose == "health_url" and not parsed.path.rstrip("/").endswith("/healthz"):
        raise ValueError("Health probes require an explicit /healthz path")
    if purpose == "metrics_url" and not parsed.path.rstrip("/").endswith("/metrics"):
        raise ValueError("Metrics probes require an explicit /metrics path")
    return urlunsplit(parsed)
