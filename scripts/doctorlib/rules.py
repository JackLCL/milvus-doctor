"""Evidence-based, side-effect-free diagnostics for Milvus Doctor snapshots.

Rules never invoke a subprocess, connect to a cluster or execute a repair.
An empty result means no implemented rule matched; it is not a health verdict.
Historical observations and incomplete coverage are explicitly qualified.
"""

from decimal import Decimal, InvalidOperation
import re
from .coverage import is_intentional_skip


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    return []


def _objects(value):
    return [item for item in _list(value) if isinstance(item, dict)]


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _quantity(value):
    """Parse Kubernetes decimal/binary quantities, retaining exact comparison."""
    if isinstance(value, bool):
        return None
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([numkKMGTPE]|[KMGTPE]i)?", str(value))
    if not match:
        return None
    number = _number(match[1])
    if number is None:
        return None
    suffix = match[2] or ""
    factors = {"": 1, "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3"), "k": 1000, "K": 1000}
    for index, prefix in enumerate("MGTPE", start=2):
        factors[prefix] = 1000 ** index
    for index, prefix in enumerate("KMGTPE", start=1):
        factors[prefix + "i"] = 1024 ** index
    return number * factors[suffix]


def _name(resource):
    metadata = _dict(resource.get("metadata"))
    name = metadata.get("name", resource.get("name", "unnamed"))
    namespace = metadata.get("namespace")
    return f"{namespace}/{name}" if namespace else str(name)


def _state(value):
    return str(value).strip().lower()


def evaluate(snapshot):
    """Return finding dictionaries, most severe first, without modifying input."""
    findings = []

    def add(code, severity, summary, evidence, recommendation, verification,
            route="self_service"):
        findings.append({
            "code": code, "severity": severity, "summary": summary,
            "evidence": [str(item) for item in evidence],
            "recommendation": recommendation, "verification": verification,
            "route": route,
        })

    if not isinstance(snapshot, dict) or snapshot.get("schema_version", 1) != 1:
        add("SNAPSHOT_UNSUPPORTED", "warning", "Snapshot schema is unsupported",
            ["Expected an object with schema_version=1; rules were not run."],
            "Use a compatible Doctor version or collect a new version-1 snapshot.",
            "Rerun the offline analysis with a supported snapshot.", "community_review")
        return findings

    sources = _objects(snapshot.get("sources"))
    incomplete = [source for source in sources if source.get("status") != "ok" and not is_intentional_skip(source)]
    if incomplete:
        recommendation = (
            "Sources intentionally excluded by the selected scope, and optional sources not requested, need no permission change. "
            "Keep these sources skipped unless the user explicitly chooses additional checks or a broader scope."
        )
        if any(source.get("status") == "error" for source in incomplete):
            recommendation += (
                " For requested sources that failed, first identify whether the cause is input, connectivity, a missing tool/dependency or access. "
                "Only a confirmed permission denial warrants requesting the documented minimum read-only permission for that source; never administrator access."
            )
        if any(source.get("reason") == "target_not_found" for source in incomplete):
            recommendation += " No workload matched a requested target. Verify the intended context, namespace, release and selector before rechecking; do not silently broaden the scan or treat the empty result as health."
        if any(source.get("reason") in ("bounded_limit", "size_limit") for source in incomplete):
            recommendation += " Requested evidence reached a collection limit. Split the selected inputs/targets or explicitly choose a documented bounded limit; this gap is not an intentional exclusion and limits are never increased automatically."
        if any(source.get("reason") in ("dependency_missing", "missing_dependency", "missing_tool") for source in incomplete):
            recommendation += " A requested check lacks a local capability. Prepare only that capability with user authorization in an isolated local environment, or retain the coverage gap; do not modify cluster components."
        add("COVERAGE_INCOMPLETE", "info", "Some diagnostic sources were unavailable or skipped",
            [f"{source.get('name', 'unnamed source')}: {source.get('status', 'unknown')}" for source in incomplete],
            recommendation,
            "Confirm the requested, in-scope checks succeed after any user-chosen correction. Intentionally unrequested or out-of-scope checks may remain skipped; their coverage remains unverified.")
    if not sources:
        add("COVERAGE_UNDECLARED", "info", "Snapshot does not declare collection coverage",
            ["No source status records are available; missing fields cannot be interpreted as passing checks."],
            "Use a Doctor-collected snapshot with source status records, or explicitly document offline evidence limitations.",
            "Confirm the report lists collected, skipped and failed evidence sources.")
    elif not any(source.get("status") == "ok" for source in sources):
        add("NO_DIAGNOSTIC_EVIDENCE", "warning", "No diagnostic source completed successfully",
            ["All declared sources are skipped or error; there is no basis for a health assessment."],
            "Select a reachable target or provide a local manifest, configuration or bounded log file.",
            "Rerun and confirm at least one relevant source completed successfully.")

    health = _dict(snapshot.get("health"))
    if health.get("status") == "error":
        add("HEALTH_CHECK_FAILED", "warning", "The selected health check did not succeed",
            [f"Health status=error; HTTP status={health.get('http_status', 'unavailable')}.",
             "This can be a target, network, authorization or component-health problem; it does not establish a root cause."],
            "Check the selected health endpoint, its reachability from the Doctor environment and the target component's startup/readiness state.",
            "Repeat the same read-only health check and confirm a successful health response.")
        if health.get("http_status") in {401, 403}:
            add("HEALTH_ACCESS_DENIED", "warning", "The selected health endpoint rejected the request's identity or access",
                [f"Health request returned HTTP {health['http_status']}.", "This response may come from a gateway rather than Milvus itself."],
                "Check the intended endpoint/gateway policy and minimal health-read access. The user should configure credentials outside the report; Doctor never requests administrator privileges.",
                "Repeat the same health request with the intended minimal-permission identity and confirm a successful response.")

    _docker(snapshot, add)
    _disk(snapshot, add)
    _kubernetes(snapshot, add)
    _milvus(snapshot, add)
    _manifests(snapshot, add)
    _config(_dict(snapshot.get("config")), "Selected configuration", add)
    _text_rules(snapshot, add)
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda finding: (severity_order[finding["severity"]], finding["code"], finding["summary"]))
    return findings


def _disk(snapshot, add):
    disk = _dict(snapshot.get("disk"))
    total, used, free = (_number(disk.get(key)) for key in ("total_bytes", "used_bytes", "free_bytes"))
    if total is None or total <= 0:
        return
    exhausted = free is not None and free == 0
    pressure = used is not None and used >= 0 and used / total >= Decimal("0.90")
    if exhausted or pressure:
        add("DISK_SPACE_PRESSURE", "critical" if exhausted else "warning", "The explicitly selected data filesystem has little available capacity",
            [f"Filesystem total={total} bytes; used={used if used is not None else 'unknown'} bytes; available={free if free is not None else 'unknown'} bytes.",
             "This measures only the filesystem containing the selected local directory, not remote object storage or all Milvus storage quotas."],
            "Have the storage owner verify the selected mount and plan capacity or documented retention changes. Preserve Milvus data, WAL, binlogs and metadata; Doctor never deletes files or expands storage.",
            "Repeat the selected directory's read-only filesystem check after the owner's change and confirm adequate headroom and no new disk-capacity errors.", "community_review")


def _docker(snapshot, add):
    for container in _objects(_dict(snapshot.get("docker")).get("containers")):
        name = str(container.get("name", "unnamed container"))
        state = _state(container.get("state", "unknown"))
        if state not in {"running", "unknown", ""}:
            add("DOCKER_NOT_RUNNING", "warning", f"Container {name} is {state}",
                [f"Docker state={state}; exit_code={container.get('exit_code', 'unknown')}."],
                "Confirm this service is intended to run. Review the container's last failure and dependency readiness before the user decides whether to start or replace it.",
                "Repeat container inspection and confirm running state and a successful configured health check.")
        if _state(container.get("health")) == "unhealthy":
            add("DOCKER_UNHEALTHY", "warning", f"Container {name} reports unhealthy",
                ["The configured Docker health check reports unhealthy; no health-check output was executed by Doctor."],
                "Review the configured health check, startup time and dependency reachability. Verify the probe matches this Milvus version.",
                "Repeat inspection after the user's adjustment and confirm healthy status.")
        if container.get("oom_killed") is True:
            add("DOCKER_OOM_KILLED", "warning", f"Container {name} has an OOM-killed exit record",
                ["Docker OOMKilled=true records the last process exit; this is not proof of a current memory leak."],
                "Review memory limits, host pressure and the workload at the exit time. The user should assess capacity or workload changes with an appropriate change window.",
                "Compare a subsequent snapshot and memory history under representative load; check for new OOM events.", "community_review")
        restarts = _number(container.get("restart_count"))
        if restarts is not None and restarts > 0:
            add("DOCKER_RESTART_HISTORY", "info", f"Container {name} has restarted",
                [f"Lifetime restart_count={restarts}; one snapshot cannot establish a restart rate."],
                "Compare this counter across snapshots and correlate new restarts with the incident time.",
                "Confirm the restart counter does not increase during the observation period.")
        limit = _number(container.get("memory_limit_bytes"))
        usage = _number(container.get("memory_usage_bytes"))
        if limit is not None and usage is not None and limit > 0 and usage >= 0 and usage / limit >= Decimal("0.90"):
            add("DOCKER_MEMORY_PRESSURE", "warning", f"Container {name} has little memory-limit headroom",
                [f"Observed usage={usage} bytes; explicit container limit={limit} bytes.",
                 "This single sample includes the collector's memory-accounting semantics; cache and transient peaks may contribute."],
                "Review sustained memory and cache behavior, loaded data and workload. Plan any resource change based on sustained measurements, not this sample alone.",
                "Compare memory usage over a representative workload window and check for new OOM events.", "community_review")


def _pod_resources(spec, subject, add):
    for container in _objects(spec.get("containers")) + _objects(spec.get("initContainers")):
        resources = _dict(container.get("resources"))
        requests, limits = _dict(resources.get("requests")), _dict(resources.get("limits"))
        for resource in ("cpu", "memory", "ephemeral-storage"):
            requested, limited = _quantity(requests.get(resource)), _quantity(limits.get(resource))
            if requested is not None and limited is not None and requested > limited:
                add("RESOURCE_REQUEST_EXCEEDS_LIMIT", "warning", f"{subject}: resource request exceeds limit",
                    [f"Container={container.get('name', 'unnamed')}; resource={resource}; request={requests[resource]}; limit={limits[resource]}."],
                    "Have the user correct the resource request/limit relationship in the owning deployment configuration after sizing the workload.",
                    "Recheck the selected manifest and confirm the request is no greater than the limit; for a live rollout also verify scheduling succeeds.")


def _kubernetes(snapshot, add):
    kubernetes = _dict(snapshot.get("kubernetes"))
    pods = _objects(kubernetes.get("pods"))
    for pod in pods:
        name = _name(pod)
        status, spec = _dict(pod.get("status")), _dict(pod.get("spec"))
        phase = status.get("phase")
        conditions = _objects(status.get("conditions"))
        unscheduled = any(c.get("type") == "PodScheduled" and _state(c.get("status")) == "false" and c.get("reason") == "Unschedulable" for c in conditions)
        if phase == "Pending" or unscheduled:
            add("K8S_POD_PENDING", "warning" if unscheduled else "info", f"Pod {name} is not scheduled/started yet",
                [f"Pod phase={phase or 'unknown'}; explicit Unschedulable condition={unscheduled}.",
                 "Pending can be normal during startup; duration is not inferred from a single snapshot."],
                "Review the selected Pod's scheduling events, node capacity, resource requests, affinity, tolerations and volume binding.",
                "Repeat inspection after startup or the user's adjustment and confirm the intended Pod reaches Running and Ready.")
        if phase == "Failed":
            add("K8S_POD_FAILED", "warning", f"Pod {name} has failed",
                [f"Pod phase=Failed; reason={status.get('reason', 'not provided')}."],
                "Determine whether this is a completed historical Pod or an active service failure; inspect the owning controller and the scoped events.",
                "Confirm the intended service has a Ready replacement and no new matching failures.")
        for container in _objects(status.get("containerStatuses")) + _objects(status.get("initContainerStatuses")):
            subject = f"{name}/{container.get('name', 'unnamed')}"
            current = _dict(container.get("state"))
            waiting = _dict(current.get("waiting"))
            reason = waiting.get("reason")
            if reason in {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"}:
                add("K8S_IMAGE_PULL_FAILURE", "warning", f"Container {subject} cannot obtain its image",
                    [f"Waiting reason={reason}."],
                    "Check the configured image/tag, registry reachability and existing image-pull authorization without exposing secret contents.",
                    "Repeat Pod inspection and confirm the waiting image-pull error is gone and the container is running.")
            elif reason == "CrashLoopBackOff":
                add("K8S_CRASH_LOOP", "warning", f"Container {subject} is in CrashLoopBackOff",
                    ["Current waiting reason=CrashLoopBackOff.", f"Restart count={container.get('restartCount', 'unknown')}."],
                    "Review the last termination reason, selected local logs, startup configuration and dependency readiness before the user changes anything.",
                    "Confirm the container becomes Ready and its restart count stops increasing during observation.", "community_review")
            elif reason in {"CreateContainerConfigError", "CreateContainerError", "RunContainerError"}:
                add("K8S_CONTAINER_START_FAILURE", "warning", f"Container {subject} cannot start",
                    [f"Current waiting reason={reason}."],
                    "Review the scoped event reason and the owning manifest's volume/configuration references. Do not print credentials or Secret values.",
                    "Repeat inspection after the user's correction and confirm successful startup.")
            last = _dict(_dict(container.get("lastState")).get("terminated"))
            terminated = _dict(current.get("terminated"))
            if terminated.get("reason") == "OOMKilled" or last.get("reason") == "OOMKilled":
                add("K8S_OOM_KILLED", "warning", f"Container {subject} has an OOM-killed termination",
                    ["OOMKilled appears in current or last termination state; it may be historical."],
                    "Correlate termination time with memory limits, node pressure and loaded workload. Plan changes with the cluster owner.",
                    "Confirm no new OOM termination occurs under representative workload and the service remains Ready.", "community_review")
            restarts = _number(container.get("restartCount"))
            if restarts is not None and restarts > 0 and reason != "CrashLoopBackOff":
                add("K8S_RESTART_HISTORY", "info", f"Container {subject} has restart history",
                    [f"Lifetime restartCount={restarts}; no restart frequency can be inferred from this single snapshot."],
                    "Compare subsequent snapshots and correlate new restarts with recent changes or the user's symptom.",
                    "Verify the count remains stable over the chosen observation window.")
        if phase == "Running" and any(c.get("type") == "Ready" and _state(c.get("status")) == "false" for c in conditions):
            add("K8S_POD_NOT_READY", "warning", f"Pod {name} is Running but not Ready",
                ["Pod Ready condition is explicitly False."],
                "Review readiness conditions, dependencies and the selected probe events; allow for legitimate startup time.",
                "Repeat inspection and confirm Ready=True and the application's selected health check succeeds.")
        _pod_resources(spec, f"Pod {name}", add)

    for kind in ("deployments", "statefulsets"):
        for workload in _objects(kubernetes.get(kind)):
            spec, status = _dict(workload.get("spec")), _dict(workload.get("status"))
            desired, ready = _number(spec.get("replicas")), _number(status.get("readyReplicas", 0))
            if status and desired is not None and ready is not None and desired > ready:
                add("K8S_REPLICAS_NOT_READY", "warning", f"{kind.rstrip('s')} {_name(workload)} has fewer Ready replicas than requested",
                    [f"Desired replicas={desired}; Ready replicas={ready}.", "A rollout or startup may temporarily explain this observation."],
                    "Review the owning rollout status and scoped Pod findings before changing replicas or restarting anything.",
                    "Repeat inspection and confirm the intended number of replicas is Ready.")

    for pvc in _objects(kubernetes.get("pvcs")):
        phase = _dict(pvc.get("status")).get("phase")
        if phase in {"Pending", "Lost"}:
            lost = phase == "Lost"
            add("K8S_PVC_NOT_BOUND", "critical" if lost else "warning", f"PVC {_name(pvc)} is {phase}",
                [f"PVC phase={phase}."],
                "Preserve the claim and volume. Ask the storage/engineering team to investigate binding and data availability; do not delete or recreate the claim." if lost else "Review StorageClass, provisioner, requested size/access mode and binding events. Do not delete a claim containing data.",
                "Confirm the intended claim is Bound and the consuming service is healthy; use the data owner's separate integrity validation when data availability was affected.",
                "private_support" if lost else "self_service")

    for event in _objects(kubernetes.get("events")):
        message = str(event.get("message", event.get("note", ""))).lower()
        if event.get("reason") in {"Unhealthy", "ProbeWarning"} and "probe" in message:
            involved = _dict(event.get("involvedObject", event.get("regarding")))
            add("K8S_PROBE_FAILURE_EVENT", "info", f"Probe failure was recorded for {involved.get('name', 'a selected resource')}",
                [f"Scoped event reason={event.get('reason')}; count={event.get('count', 'unknown')}.",
                 "Events are historical and do not prove the current probe is failing."],
                "Correlate the event timestamp with the incident and inspect the current Ready/health state before the user adjusts a probe.",
                "Confirm Ready/health succeeds and no new failure events appear during observation.")

    for service in _objects(kubernetes.get("services")):
        selector = _dict(_dict(service.get("spec")).get("selector"))
        if not selector or not pods:
            continue
        namespace = _dict(service.get("metadata")).get("namespace")
        matching = [pod for pod in pods if _dict(pod.get("metadata")).get("namespace") == namespace
                    and all(_dict(_dict(pod.get("metadata")).get("labels")).get(k) == v for k, v in selector.items())]
        if not matching:
            add("K8S_SERVICE_SELECTOR_UNCONFIRMED", "info", f"Service {_name(service)} has no matching Pod in this snapshot",
                ["No collected Pod matches every selected Service label in the same namespace.",
                 "Collection selectors can exclude matching Pods; this is not proof the live Service has no endpoints."],
                "Check the Service selector against the intended workload labels and ask for a correctly scoped read-only snapshot if Pods were excluded.",
                "Verify intended Pods match the Service selector and application connectivity succeeds.")

    for release in _objects(kubernetes.get("helm_releases")):
        state = _state(release.get("status", _dict(release.get("info")).get("status", "unknown")))
        if "status" not in release and "status" not in _dict(release.get("info")):
            add("HELM_STATUS_UNAVAILABLE", "info", f"Helm release {_name(release)} was identified but its release status was not collected",
                ["Workload labels can identify a Helm installation but cannot establish the release state."],
                "Use the workload findings for application health. Ask the release owner for an explicitly selected status summary if Helm reconciliation/history is relevant; do not read Helm release Secrets.",
                "Confirm the owner's selected release status and scoped workload readiness agree.")
            continue
        if state == "failed" or state.startswith("pending-") or state in {"uninstalling", "unknown"}:
            add("HELM_RELEASE_NOT_DEPLOYED", "warning" if state == "failed" else "info", f"Helm release {_name(release)} reports {state}",
                [f"Helm release status={state}; this does not by itself establish application availability."],
                "Review release history, chart/version compatibility and scoped workload events with the release owner. Doctor will not upgrade, roll back or uninstall the release.",
                "After the owner completes any intended change, confirm a deployed release and healthy workloads.", "community_review" if state == "failed" else "self_service")

    for milvus in _objects(kubernetes.get("milvuses")):
        name, status = _name(milvus), _dict(milvus.get("status"))
        _operator_spec(milvus, f"Milvus CR {name}", add)
        observed = _number(status.get("observedGeneration"))
        generation = _number(_dict(milvus.get("metadata")).get("generation"))
        if observed is not None and generation is not None and observed < generation:
            add("OPERATOR_STATUS_STALE", "info", f"Milvus CR {name} status has not observed the current generation",
                [f"generation={generation}; observedGeneration={observed}; conditions may describe an older spec."],
                "Wait for the expected reconciliation interval, then review operator availability and reconciliation events if status remains stale.",
                "Confirm observedGeneration catches up and the current CR status becomes Healthy.")
        unhealthy = _state(status.get("status")) in {"unhealthy", "stopped"}
        bad_conditions = [c for c in _objects(status.get("conditions"))
                          if (str(c.get("type", "")).lower().endswith("ready") and _state(c.get("status")) == "false")
                          or (c.get("type") in {"ReconcileError", "Error", "Degraded"} and _state(c.get("status")) == "true")]
        if unhealthy or bad_conditions:
            add("OPERATOR_NOT_READY", "warning", f"Milvus CR {name} reports unavailable/degraded state",
                [f"Overall status={status.get('status', 'unknown')}."] +
                [f"Condition {c.get('type')}={c.get('status')}; reason={c.get('reason', 'not provided')}." for c in bad_conditions],
                "Review the failed dependency/component condition and selected workload events. If reconciliation keeps failing, provide a sanitized CR/status summary to the community; do not edit operator-managed resources directly.",
                "Confirm the current-generation CR is Healthy and relevant Ready conditions are True.", "community_review")
        elif not status or _state(status.get("status")) == "pending":
            add("OPERATOR_READINESS_UNCONFIRMED", "info", f"Milvus CR {name} readiness is not yet confirmed",
                [f"Overall status={status.get('status', 'absent')}; this can be an ordinary initial reconciliation."],
                "Recheck after the expected startup interval and inspect scoped operator/workload events if status does not progress.",
                "Confirm the CR reports Healthy for the current spec.")


def _milvus(snapshot, add):
    for collection in _objects(_dict(snapshot.get("milvus")).get("collections")):
        name = str(collection.get("name", "unnamed collection"))
        loaded = collection.get("loaded")
        if loaded is False or _state(loaded) in {"notload", "notloaded", "not_loaded", "unloaded", "loadstate.notload"}:
            add("COLLECTION_NOT_LOADED", "info", f"Collection {name} is not loaded",
                ["Metadata reports an unloaded collection; unloaded/idle collections can be intentional."],
                "If the user expects to query this collection, have the owner review index readiness, capacity and whether to load it. Doctor never loads a collection.",
                "Repeat metadata inspection after the user's action and confirm the intended load state; the user separately validates their application query.")
        indexes = collection.get("indexes")
        schema = _dict(collection.get("schema"))
        vector_fields = [field for field in _objects(schema.get("fields")) if _is_vector(_field_type(field))]
        if indexes == [] and collection.get("indexes_complete") is not False and vector_fields:
            add("COLLECTION_NO_VECTOR_INDEX", "info", f"Collection {name} has vector fields but no listed index",
                [f"Vector fields in schema={len(vector_fields)}; complete index list is empty.",
                 "This is a readiness observation, not evidence of a failed query or a recommendation for a particular index type."],
                "If serving search is intended, have the owner choose a supported index using workload, metric and recall requirements. Doctor does not create an index.",
                "Repeat index metadata inspection and confirm the user's intended index is present and built.")
        for index in _objects(indexes):
            state = _state(index.get("state", index.get("index_state", index.get("status", ""))))
            if state in {"failed", "indexstate.failed", "4"}:
                add("COLLECTION_INDEX_FAILED", "warning", f"An index on collection {name} reports Failed",
                    [f"Index state={state}; index name={index.get('index_name', index.get('name', 'not provided'))}."],
                    "Review the sanitized build failure reason, storage reachability and resource availability. If the build repeatedly fails, ask the community to review before any rebuild is planned.",
                    "Confirm the intended index reaches Finished and no new build failure is recorded.", "community_review")
        for field in vector_fields:
            params = _dict(field.get("params", field.get("type_params")))
            if isinstance(field.get("type_params"), list):
                params = {p.get("key"): p.get("value") for p in _objects(field["type_params"])}
            dimension = _number(params.get("dim"))
            dtype = _state(_field_type(field))
            invalid = dimension is not None and (dimension <= 0 or dimension != dimension.to_integral_value())
            invalid_binary = dimension is not None and ("binary_vector" in dtype or dtype == "100") and dimension % 8 != 0
            if invalid or invalid_binary:
                add("SCHEMA_VECTOR_DIMENSION_INVALID", "warning", f"Collection {name} declares an invalid vector dimension",
                    [f"Declared dimension={dimension}; vector type={dtype}.", "Dense dimensions must be positive integers; binary dimensions must also be divisible by 8."],
                    "Review the supplied schema and embedding output with the application owner. Never change or recreate a production schema solely from this report.",
                    "Validate the intended schema and embedding dimensions in the user's test environment.")


def _is_vector(dtype):
    text = _state(dtype)
    return "vector" in text or text in {"100", "101", "102", "103", "104", "105"}


def _field_type(field):
    return field.get("type", field.get("dtype", field.get("data_type", field.get("datatype"))))


def _operator_spec(resource, subject, add):
    mode = _dict(resource.get("spec")).get("mode")
    if mode is not None and (not isinstance(mode, str) or mode not in {"standalone", "cluster"}):
        add("OPERATOR_MODE_INVALID", "warning", f"{subject} has an unsupported mode value",
            [f"spec.mode={mode}; supported values for this rule are standalone and cluster."],
            "Check the installed operator CRD and correct the desired mode in the user-managed manifest. Changing a live deployment mode needs a separate migration plan.",
            "Validate against the installed CRD and recheck the corrected manifest.")


def _documents(value):
    if isinstance(value, list):
        for item in value:
            yield from _documents(item)
    elif isinstance(value, dict):
        if value.get("kind") == "List":
            yield from _documents(value.get("items", []))
        elif "documents" in value:
            yield from _documents(value["documents"])
        elif "document" in value and isinstance(value["document"], (dict, list)):
            yield from _documents(value["document"])
        elif "content" in value and isinstance(value["content"], (dict, list)):
            yield from _documents(value["content"])
        else:
            yield value


def _manifests(snapshot, add):
    for index, document in enumerate(_documents(snapshot.get("manifests", [])), start=1):
        subject = f"Local manifest #{index} ({_name(document)})"
        kind = document.get("kind")
        spec = _dict(document.get("spec"))
        if kind == "Milvus":
            _operator_spec(document, subject, add)
            _config(_dict(spec.get("config")), subject, add)
            for component, settings in _dict(spec.get("components")).items():
                _pod_resources({"containers": [{"name": component, "resources": _dict(settings).get("resources")}]}, subject, add)
        elif kind == "ConfigMap":
            _config(_dict(document.get("config")), subject, add)
        elif kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "Pod"}:
            pod_spec = spec if kind == "Pod" else _dict(_dict(spec.get("template")).get("spec"))
            _pod_resources(pod_spec, subject, add)
        services = _dict(document.get("services"))
        if not kind and services and any("image" in _dict(service) or "build" in _dict(service) for service in services.values()):
            for name, service in services.items():
                service = _dict(service)
                dependencies = service.get("depends_on", [])
                names = list(dependencies) if isinstance(dependencies, (dict, list)) else []
                for dependency in names:
                    settings = _dict(dependencies.get(dependency)) if isinstance(dependencies, dict) else {}
                    if dependency not in services and settings.get("required") is not False:
                        add("COMPOSE_DEPENDENCY_UNDEFINED", "warning", f"Compose service {name} references an undefined dependency",
                            [f"Required depends_on target={dependency}; it is not defined in this local document.", "An additional Compose override file may define it; this check does not merge files."],
                            "Review the complete Compose file set and service names. If an override defines the dependency, analyze the merged configuration explicitly.",
                            "Confirm the complete selected Compose model defines every required dependency.")
                limit = _dict(_dict(_dict(service.get("deploy")).get("resources")).get("limits"))
                reservation = _dict(_dict(_dict(service.get("deploy")).get("resources")).get("reservations"))
                for resource in ("memory", "cpus"):
                    lower, upper = _compose_quantity(reservation.get(resource)), _compose_quantity(limit.get(resource))
                    if lower is not None and upper is not None and lower > upper:
                        add("COMPOSE_RESERVATION_EXCEEDS_LIMIT", "warning", f"Compose service {name} reserves more {resource} than its limit",
                            [f"Reservation={reservation[resource]}; limit={limit[resource]}."],
                            "Have the user correct the reservation/limit relationship in the complete Compose model after sizing the service.",
                            "Recheck the merged Compose configuration and confirm reservation does not exceed limit.")
                lower, upper = _compose_quantity(service.get("mem_reservation")), _compose_quantity(service.get("mem_limit"))
                if lower is not None and upper is not None and lower > upper:
                    add("COMPOSE_RESERVATION_EXCEEDS_LIMIT", "warning", f"Compose service {name} has mem_reservation above mem_limit",
                        [f"mem_reservation={service['mem_reservation']}; mem_limit={service['mem_limit']}."],
                        "Have the user correct the memory reservation/limit relationship after reviewing the service's capacity needs.",
                        "Recheck the complete Compose model and confirm the reservation does not exceed the limit.")
        if not kind and "cluster" in document and isinstance(document.get("cluster"), dict):
            enabled = document["cluster"].get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                add("HELM_CLUSTER_FLAG_TYPE", "warning", "Helm-style cluster.enabled is not a YAML boolean",
                    [f"cluster.enabled has type {type(enabled).__name__}; quoted false is a string, not boolean false."],
                    "Check the exact chart's values schema and have the user use a YAML boolean for cluster.enabled if this is a Milvus Helm values file.",
                    "Recheck the selected values file and validate with the exact intended chart version.")
        if not kind and not services:
            _config(document, subject, add)
            for component in ("standalone", "proxy", "rootCoordinator", "queryCoordinator", "queryNode", "dataCoordinator", "dataNode", "indexCoordinator", "indexNode", "mixCoordinator", "streamingNode"):
                settings = _dict(document.get(component))
                if "resources" in settings:
                    _pod_resources({"containers": [{"name": component, "resources": settings["resources"]}]}, subject, add)
            for config in _dict(document.get("extraConfigFiles")).values():
                _config(_dict(config), subject + " embedded configuration", add)


def _compose_quantity(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([bkmg])?b?", str(value).lower())
    if match:
        return Decimal(match[1]) * {None: 1, "b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[match[2]]
    return _quantity(value)


def _config(config, subject, add):
    for component in ("proxy", "etcd", "minio"):
        section = _dict(config.get(component))
        for key in ("port", "internalPort"):
            if key not in section:
                continue
            value = _number(section[key])
            if value is None or value != value.to_integral_value() or not 1 <= value <= 65535:
                add("CONFIG_PORT_INVALID", "warning", f"{subject}: {component}.{key} is not a valid TCP port",
                    [f"{component}.{key} is outside the integer range 1..65535 or has an invalid type."],
                    "Verify the configuration key against the target Milvus version and have the user correct the intended port. Doctor does not edit configuration.",
                    "Recheck the configuration and, after the user's planned change, verify the selected endpoint is reachable.")


# Patterns only recognize explicit error markers. No raw log lines, hostnames,
# credentials, query text or embedding data are copied into findings.
_TEXT_RULES = (
    ("CONNECTION_REFUSED", r"connection refused|actively refused|failed to connect to all addresses|no route to host|network is unreachable|name or service not known|no such host|temporary failure in name resolution",
     "warning", "A connection or name-resolution failure was recorded", "Check the selected endpoint/port, listener, DNS and network path from the caller; identify which dependency the error concerns.", "Repeat the affected read-only connection/health check and confirm the same failure no longer occurs.", "self_service"),
    ("CONNECTION_TIMEOUT", r"(?:i/o|connection|connect|dial|handshake) (?:timed? ?out|timeout)|context deadline exceeded|deadlineexceeded|statuscode\.deadline_exceeded",
     "warning", "A timeout was recorded", "Check reachability and dependency latency and correlate the timeout with workload and incident time. A timeout alone does not identify the slow component.", "Repeat the relevant read-only check and have the user observe application latency under the same workload.", "community_review"),
    ("AUTHENTICATION_FAILED", r"authentication (?:failed|failure)|unauthenticated|invalid (?:token|password|credential)|(?:authorization|permission) denied|statuscode\.permission_denied",
     "warning", "An authentication or authorization error was recorded", "Verify the intended identity, token source and minimal read-only grants. The user should update credentials outside the chat; never paste a token into a report.", "Repeat the same permitted read-only operation using the intended minimal-permission identity.", "self_service"),
    ("TLS_VALIDATION_FAILED", r"certificate verify failed|certificate signed by unknown authority|certificate (?:has )?expired|tls handshake (?:error|failure)|ssl: certificate_verify_failed|x509:.*(?:certificate|authority)",
     "warning", "A TLS validation or handshake error was recorded", "Check CA trust, certificate validity, hostname and the intended TLS mode. Do not disable certificate verification as the fix.", "Repeat the connection with certificate verification enabled and confirm the TLS handshake succeeds.", "self_service"),
    ("VECTOR_DIMENSION_MISMATCH", r"(?:vector )?dim(?:ension)?(?:s)?[^\n]{0,120}(?:mismatch|not match|doesn't match|not equal)|(?:mismatch|not match)[^\n]{0,80}(?:vector )?dim(?:ension)?|length\(\d+\) of float data should divide the dim",
     "warning", "A vector-dimension mismatch was recorded", "Compare the embedding model's output dimension with the collection schema and the submitted batch. The application owner should correct the input or plan a separate schema migration.", "Have the user validate a representative batch in their test environment and check that the same dimension error disappears.", "self_service"),
    ("FIELD_TYPE_MISMATCH", r"(?:field|data) type[^\n]{0,100}(?:mismatch|not match)|(?:data type is not match|unexpected data type|datatypenotmatch|datamatcherror)|the data in the same column must be of the same type|input data type is inconsistent with (?:defined )?schema|field should be [^\n]{0,80}but got",
     "warning", "A schema or field-type mismatch was recorded", "Compare submitted field names/types and required fields with the collection schema. Have the application owner correct serialization or preprocessing; do not recreate the collection automatically.", "The user validates a representative input against the schema in a test environment and confirms the type error is gone.", "self_service"),
    ("REQUEST_SIZE_EXCEEDED", r"(?:received|sending) message larger than max|grpc[^\n]{0,100}message[^\n]{0,80}(?:too large|size exceed)|batch size[^\n]{0,60}(?:exceed|too large)",
     "warning", "An oversized request or batch was recorded", "Have the application owner inspect serialized request size and use smaller batches within the target version's configured limits.", "The user verifies a representative smaller request and confirms the size error is gone.", "self_service"),
    ("LOG_COLLECTION_NOT_LOADED", r"collection[^\n]{0,100}(?:not loaded|not load|was not loaded)",
     "warning", "An operation reported that its collection was not loaded", "Confirm the operation targets the intended database/collection and inspect load/index state and capacity. Only the owner decides whether to load it.", "Recheck load metadata and have the user verify the affected application operation.", "self_service"),
    ("LOG_INDEX_MISSING", r"index not found|index does not exist|there is no index on",
     "warning", "An operation reported a missing index", "Verify the intended collection/vector field and index metadata before the owner plans index creation. A log entry may describe a historical or expected lookup.", "Repeat metadata inspection and have the user confirm the intended operation works with their chosen index.", "self_service"),
    ("MEMORY_QUOTA_EXCEEDED", r"memory quota (?:exceeded|exhausted)|memoryquotaexhausted|insufficient memory to load|load would exceed[^\n]{0,80}memory",
     "warning", "A memory-protection or loading-capacity error was recorded", "Review loaded data, replica count, configured memory protection and sustained resource pressure. Plan capacity/workload changes with the owner; do not disable protection to force a load.", "After the user's change, confirm no new memory-quota errors under representative workload and sufficient sustained headroom.", "community_review"),
    ("DISK_CAPACITY_ERROR", r"no space left on device|disk quota (?:exceeded|exhausted)|diskquotaexhausted|not enough disk space",
     "warning", "A disk-capacity or quota error was recorded", "Identify the affected component and filesystem/storage quota. Preserve Milvus data, WAL, binlogs and metadata; ask the storage owner to plan capacity or documented retention changes.", "Confirm the intended filesystem/quota has sufficient capacity and no new matching errors occur.", "community_review"),
    ("DEPENDENCY_UNAVAILABLE", r"(?:etcd|pulsar|kafka|minio|object storage|woodpecker)[^\n]{0,100}(?:unavailable|unreachable|connection refused|not healthy)|(?:failed|unable) to connect[^\n]{0,80}(?:etcd|pulsar|kafka|minio)",
     "warning", "A named dependency connectivity failure was recorded", "Identify the named dependency, verify its selected service health, endpoint, network path and existing authorization without reading secrets.", "Repeat the relevant read-only health checks and confirm dependency errors stop recurring.", "self_service"),
    ("DATA_INTEGRITY_ERROR", r"(?:checksum|crc) mismatch|corrupt(?:ed|ion)? (?:wal|binlog|segment|metadata)|(?:wal|binlog|segment|metadata)[^\n]{0,50}(?:corrupted|corruption)|data loss detected",
     "critical", "An explicit data-integrity error marker was recorded", "Preserve evidence and existing data. Ask the engineering/private support channel to assess the affected data; do not delete files, repair metadata or recreate a collection.", "Engineering and the data owner define and execute an integrity/recovery validation plan; Doctor only repeats permitted read-only checks.", "private_support"),
    ("INTERNAL_PANIC", r"panic:|fatal error: concurrent map|segmentation fault|sigsegv",
     "warning", "An internal crash marker was recorded", "Record version, incident timing, last change and a sanitized minimal reproduction. Ask the community/engineering team to review; a crash marker alone does not identify its cause.", "Confirm no recurrence under the agreed reproduction/observation window after an owner-approved remedy.", "community_review"),
)


def _log_texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _log_texts(item)
    elif isinstance(value, dict):
        # Accept collector {path,text} entries or a path -> text mapping.
        if isinstance(value.get("text"), str):
            yield value["text"]
        elif isinstance(value.get("content"), str):
            yield value["content"]
        else:
            for key, item in value.items():
                if key not in {"path", "name", "source", "status"}:
                    yield from _log_texts(item)


def _text_rules(snapshot, add):
    texts = list(_log_texts(snapshot.get("logs")))
    # Source errors can explain missing evidence without echoing raw exception
    # text (which may include a connection string or user data).
    texts.extend(str(source.get("detail", "")) for source in _objects(snapshot.get("sources")) if source.get("status") == "error")
    health = _dict(snapshot.get("health"))
    if health.get("status") == "error":
        texts.append(str(health.get("detail", "")))
    for code, pattern, severity, summary, recommendation, verification, route in _TEXT_RULES:
        regex = re.compile(pattern, re.IGNORECASE)
        matches = sum(1 for text in texts for line in text.splitlines() if regex.search(line))
        if matches:
            add(code, severity, summary,
                [f"Recognized explicit error marker in {matches} supplied log/source-error line(s); raw text is intentionally excluded.",
                 "The evidence may be historical. Correlate timestamps and the affected operation before concluding the issue is current."],
                recommendation, verification, route)
