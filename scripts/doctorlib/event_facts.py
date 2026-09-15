"""Finite historical Kubernetes event facts; never export messages or names."""
from datetime import datetime
import re

MAX_EVENTS = 100
MAX_EVENT_INPUTS = 1000
_CATEGORIES = frozenset({"storage_class_not_found", "registry_connection_refused"})
_CATEGORY_CONTEXT = {"storage_class_not_found": ("Warning", "ProvisioningFailed", "PersistentVolumeClaim"),
                     "registry_connection_refused": ("Warning", "Failed", "Pod")}
_REASONS = frozenset({"ProvisioningFailed", "FailedScheduling", "FailedMount", "FailedAttachVolume", "FailedBinding", "Failed", "BackOff", "Unhealthy", "ProbeWarning", "Scheduled", "Pulling", "Pulled", "Created", "Started"})
_KINDS = frozenset({"Pod", "PersistentVolumeClaim", "Deployment", "StatefulSet", "Service", "Node"})


def _dict(value):
    return value if isinstance(value, dict) else {}


def _enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unknown"


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})", value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _typed(raw):
    raw = _dict(raw)
    fact = {"type": _enum(raw.get("type"), {"Normal", "Warning"}),
            "reason": _enum(raw.get("reason"), _REASONS),
            "object_kind": _enum(raw.get("object_kind"), _KINDS), "categories": []}
    count = raw.get("count")
    if type(count) is int and 0 <= count <= 2 ** 63 - 1:
        fact["count"] = count
    for field in ("first_observed_at", "last_observed_at", "event_time"):
        value = _timestamp(raw.get(field))
        if value is not None:
            fact[field] = value
    return fact


def _classify(fact, message):
    # Require an explicit failure statement, not proximity of generic keywords.
    # The cap applies before regex processing; no substring/URL is returned.
    if not isinstance(message, str) or len(message) > 8192 or fact["type"] != "Warning":
        return []
    message = message.strip()
    if fact["object_kind"] == "PersistentVolumeClaim" and fact["reason"] == "ProvisioningFailed":
        if re.fullmatch(r'storageclass(?:es)?(?:\.storage\.k8s\.io)?\s+"[^"\r\n]{1,253}"\s+not found\.?', message, re.I):
            return ["storage_class_not_found"]
    if fact["object_kind"] == "Pod" and fact["reason"] == "Failed":
        if (re.match(r'Failed to pull image\s+"[^"\r\n]{1,1024}"\s*:', message, re.I)
                and re.search(r"\bdial tcp\b", message, re.I)
                and re.search(r"\bconnect:\s+connection refused\s*$", message, re.I)):
            return ["registry_connection_refused"]
    return []


def project_events(events, targets=()):
    """Project up to 100 typed events; targets contain only included Pod/PVCs.

    A current object alias requires matching UID, kind and namespace. Matching a
    name alone cannot distinguish an old event from a recreated object. Current
    collectors omit UID, so those live events intentionally have no object alias.
    """
    events = events if isinstance(events, list) else []
    identities = {}
    for kind, raw, alias in targets:
        metadata = _dict(_dict(raw).get("metadata"))
        uid = metadata.get("uid")
        namespace = metadata.get("namespace")
        if not isinstance(uid, str) or not uid or not isinstance(namespace, str):
            continue
        identities.setdefault((kind, namespace, uid), []).append(alias)
    facts = []
    for raw in events[:MAX_EVENT_INPUTS]:
        raw = _dict(raw)
        involved = _dict(raw.get("involvedObject", raw.get("regarding")))
        kind = _enum(involved.get("kind"), _KINDS)
        fact = _typed({"type": raw.get("type"), "reason": raw.get("reason"), "object_kind": kind,
                       "count": raw.get("count"), "first_observed_at": raw.get("firstTimestamp"),
                       "last_observed_at": raw.get("lastTimestamp"), "event_time": raw.get("eventTime")})
        fact["categories"] = _classify(fact, raw.get("message", raw.get("note")))
        uid = involved.get("uid")
        namespace = involved.get("namespace")
        if kind in {"Pod", "PersistentVolumeClaim"} and isinstance(uid, str) and isinstance(namespace, str):
            aliases = identities.get((kind, namespace, uid), [])
            if len(aliases) == 1:
                fact["object_alias"] = aliases[0]
        facts.append(fact)
    facts.sort(key=lambda fact: not bool(fact["categories"]))
    return [dict(alias="event-{}".format(index), **fact) for index, fact in enumerate(facts[:MAX_EVENTS], 1)], len(events) > MAX_EVENTS


def sanitize_events(events, allowed_objects=()):
    """Revalidate saved typed facts without inventing/reconstructing raw text."""
    events = events if isinstance(events, list) else []
    objects = set(allowed_objects)
    facts = []
    for index, raw in enumerate(events[:MAX_EVENTS], 1):
        raw = _dict(raw)
        fact = _typed(raw)
        categories = raw.get("categories")
        if isinstance(categories, list):
            context = (fact["type"], fact["reason"], fact["object_kind"])
            fact["categories"] = sorted({category for category in categories[:10] if isinstance(category, str) and category in _CATEGORIES and _CATEGORY_CONTEXT[category] == context})
        alias = raw.get("object_alias")
        if isinstance(alias, str) and (fact["object_kind"], alias) in objects:
            fact["object_alias"] = alias
        facts.append(dict(alias="event-{}".format(index), **fact))
    return facts, len(events) > MAX_EVENTS
