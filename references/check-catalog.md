# V1 diagnostic check catalog

All checks are read-only observations and recommendations. Doctor never fixes,
loads, restarts, upgrades, deletes, repairs metadata, executes an application
query, or applies configuration. `self_service` means the **user** can follow a
documented recommendation; it does not grant an agent execution permission.

Each finding contains evidence, a recommended user action, a separate
verification step and a routing hint. A rule not matching is not a passing test.
Unavailable/skipped sources remain visible as coverage gaps. Explicitly unrequested
or scope-excluded sources are not failed requested checks and do not independently
cause `COVERAGE_INCOMPLETE`; unlabelled legacy skips remain conservative. A single snapshot
does not establish incident duration, a restart rate, a memory leak or a P99
latency cause. Collection and target identifiers in the report must pass the
report layer's redaction before sharing; the rules never copy raw log excerpts.

## Evidence and input checks

| Finding code | Evidence | Meaning / limitation |
| --- | --- | --- |
| `SNAPSHOT_UNSUPPORTED` | Unsupported schema version or non-object input | Stop rule evaluation; use a compatible snapshot. |
| `COVERAGE_UNDECLARED` | No source records | Coverage is unknown. |
| `COVERAGE_INCOMPLETE` | A requested source failed or is unavailable, or selection intent is unknown; excludes explicit `not_requested` / `scope_excluded` skips | Intentionally out-of-scope or unrequested optional sources need no permission change and may remain skipped. Expand checks only when the user chooses. Diagnose actual failures first; request minimum read access only for confirmed permission denial. Skipped coverage remains unverified. |
| `NO_DIAGNOSTIC_EVIDENCE` | No declared source succeeded | No health assessment is possible. |
| `HEALTH_CHECK_FAILED` | Explicit selected health-check error | Does not distinguish network, auth, endpoint and unhealthy component by itself. |
| `HEALTH_ACCESS_DENIED` | HTTP 401/403 from selected health request | Check minimal read access; response may be from a gateway. |

## Docker and Docker Compose runtime

| Finding code | Evidence | Meaning / limitation |
| --- | --- | --- |
| `DOCKER_NOT_RUNNING` | Selected container state is not running | May be intentionally stopped; confirm intended state. |
| `DOCKER_UNHEALTHY` | Docker health reports unhealthy | Check probe and dependency configuration. |
| `DOCKER_OOM_KILLED` | Docker OOMKilled flag | Last termination may be historical; review with owner/community. |
| `DOCKER_RESTART_HISTORY` | Positive lifetime restart counter | Informational; compare snapshots before claiming a loop. |
| `DOCKER_MEMORY_PRESSURE` | Supplied usage is at least 90% of an explicit nonzero limit | A headroom heuristic only; validate over time and consider cache accounting. No limit means no ratio diagnosis. |
| `DISK_SPACE_PRESSURE` | The explicitly selected local filesystem is at least 90% used, or has zero available bytes | 90% is a headroom heuristic; zero availability is critical. Does not measure remote object storage or global quotas. Never recommends deleting Milvus data. |

Docker/Compose checks apply to explicitly selected Milvus and dependency
containers. Deployment mode (standalone/cluster) does not change the meaning of
container states. Native processes can use health, SDK metadata and selected
local logs; they do not receive Docker/Kubernetes findings without that evidence.

## Kubernetes, Helm and Milvus Operator

| Finding code | Evidence | Meaning / limitation |
| --- | --- | --- |
| `K8S_POD_PENDING` | Pending phase or explicit Unschedulable condition | Startup can be normal; inspect scope-specific scheduling events. |
| `K8S_POD_FAILED` | Failed phase | Distinguish historical Pods from active service failure. |
| `K8S_IMAGE_PULL_FAILURE` | ErrImagePull, ImagePullBackOff or InvalidImageName | Check image and registry access without reading secrets. |
| `K8S_CRASH_LOOP` | Current CrashLoopBackOff state | Community review; preserve failure evidence. |
| `K8S_CONTAINER_START_FAILURE` | Explicit container configuration/create/run error | Inspect safe configuration references and events. |
| `K8S_OOM_KILLED` | Current/last OOMKilled termination | May be historical; do not infer a memory leak. |
| `K8S_RESTART_HISTORY` | Positive lifetime restart count | Compare counters over time. |
| `K8S_POD_NOT_READY` | Running and explicit Ready=False | Probe/dependency investigation; not a root cause. |
| `K8S_REPLICAS_NOT_READY` | Observed Ready replicas below requested replicas | May be a transient rollout. Zero desired replicas is not an error. |
| `K8S_PVC_NOT_BOUND` | Pending/Lost phase | Lost is critical/private support; preserve storage and metadata. |
| `K8S_PROBE_FAILURE_EVENT` | Scoped Unhealthy/ProbeWarning probe event | Historical observation; check current state. |
| `K8S_SERVICE_SELECTOR_UNCONFIRMED` | No collected same-namespace Pod matches selector | Informational only: scoped collection may exclude matching Pods; no endpoint-outage assertion. |
| `RESOURCE_REQUEST_EXCEEDS_LIMIT` | CPU/memory/ephemeral-storage request above explicit limit | Exact Kubernetes quantity comparison, including `m`, `Mi`, `Gi`. |
| `HELM_RELEASE_NOT_DEPLOYED` | Failed/pending/uninstalling/unknown release state | Failed warrants review; pending is informational, not a failed upgrade claim. |
| `HELM_STATUS_UNAVAILABLE` | Release identified from labels without status evidence | Never infer release status from labels; no Helm Secret read. |
| `OPERATOR_MODE_INVALID` | Explicit mode outside standalone/cluster | Check installed CRD before any deployment-mode change. |
| `OPERATOR_STATUS_STALE` | observedGeneration below metadata.generation | Status may describe an older spec. |
| `OPERATOR_NOT_READY` | Unhealthy/Stopped or false Ready/true error condition | Review dependency and reconciliation status with community. |
| `OPERATOR_READINESS_UNCONFIRMED` | No observed status or Pending | Startup may be normal. |

## Collection/schema metadata and local configuration

| Finding code | Evidence | Meaning / limitation |
| --- | --- | --- |
| `COLLECTION_NOT_LOADED` | Explicit unloaded state | Informational: idle collections are valid. Loading is an owner action. |
| `COLLECTION_NO_VECTOR_INDEX` | Vector schema field plus explicitly empty index list | Informational readiness check; absent metadata is not an absent index. No automatic index selection. |
| `COLLECTION_INDEX_FAILED` | Explicit Failed index state | Review failure reason and resources; no automatic rebuild. |
| `SCHEMA_VECTOR_DIMENSION_INVALID` | Nonpositive/noninteger dense dimension or non-multiple-of-eight binary dimension | Missing sparse dimensions are valid; no arbitrary upper dimension bound is assumed. |
| `CONFIG_PORT_INVALID` | Known selected port value outside integer 1..65535 | Verify the key against the target version before an owner edits configuration. |
| `COMPOSE_DEPENDENCY_UNDEFINED` | Required depends_on target absent from selected document | Compose override files may supply it; validate complete merged model. Optional dependencies are excluded. |
| `COMPOSE_RESERVATION_EXCEEDS_LIMIT` | Explicit resource reservation above limit | Validate the complete Compose model. |
| `HELM_CLUSTER_FLAG_TYPE` | Helm-style cluster.enabled is not a boolean | Conditional on input being Milvus Helm values; check exact chart schema. |

Offline manifests accept direct documents, Kubernetes Lists and collector
`{path, documents:[...]}` wrappers. Kubernetes Pod/workload resource limits,
Milvus CR modes/component resources and embedded config, sanitized ConfigMap
config, Compose dependencies/resources (including `mem_reservation`/`mem_limit`)
and Helm-style boolean values/component resources/parsed `extraConfigFiles` are
inspected without deploying anything.

## Explicit markers in user-selected local logs / collection errors

Recognized marker families: connection refused/DNS/unreachable
(`CONNECTION_REFUSED`), timeout (`CONNECTION_TIMEOUT`), auth/authorization
(`AUTHENTICATION_FAILED`), certificate/TLS (`TLS_VALIDATION_FAILED`), dimensions
(`VECTOR_DIMENSION_MISMATCH`), field types (`FIELD_TYPE_MISMATCH`), request/batch
size (`REQUEST_SIZE_EXCEEDED`), unloaded collection
(`LOG_COLLECTION_NOT_LOADED`), missing index (`LOG_INDEX_MISSING`), memory quota
(`MEMORY_QUOTA_EXCEEDED`), disk capacity/quota (`DISK_CAPACITY_ERROR`), named
dependency connectivity (`DEPENDENCY_UNAVAILABLE`), explicit corruption/checksum
(`DATA_INTEGRITY_ERROR`) and panic/segfault (`INTERNAL_PANIC`).

These findings give marker counts, never raw lines, query content or credentials.
They explicitly warn that supplied logs may be historical. Patterns are evidence
of a reported failure, not proof of root cause, current impact or recurrence.
No remote logs are fetched implicitly. Corruption/checksum markers route to
private engineering support; no repair command is proposed.

Upstream references for error wording and operator status fields/modes are
[Milvus error definitions](https://github.com/milvus-io/milvus/blob/a876f471053edb2f68a06a9894afee9810ea7906/pkg/util/merr/errors.go),
[rate-limit utilities](https://github.com/milvus-io/milvus/blob/a876f471053edb2f68a06a9894afee9810ea7906/pkg/util/ratelimitutil/utils.go),
and [Milvus Operator API types](https://github.com/zilliztech/milvus-operator/blob/a1ab8816971b63e4a00037bc4883c7d26ef092d9/apis/milvus.io/v1beta1/milvus_types.go).
These links identify specific source revisions, not a compatibility guarantee.
Log wording differs by version, so unmatched errors must be reviewed rather
than treated as healthy.

## Deliberate gaps

- No automatic repair, collection load/index creation, user-data query, replay,
  restart, manifest apply, Helm upgrade/rollback or Birdwatcher metadata mutation.
- No universal latency, QPS, segment-count, disk-size or memory-sizing verdict.
  Such judgments need a workload, time series and explicit operator context.
- No claim of SDK/server compatibility from version strings alone; a maintained
  compatibility matrix is required before adding that rule.
- No inference that all dependency Pods were collected from a filtered namespace
  snapshot, nor that a missing object in partial evidence is deleted.
- No recall or index suitability judgment without representative user-owned
  evaluation data; Doctor does not run that workload.
