# Deployment integration lab

Use these fixtures to develop and verify Doctor against disposable Milvus
deployments. The files in `tests/lab/` are **write-capable developer tools**, not
part of the Doctor skill runtime. They create resources, issue permission probes
and inject intentional faults. Doctor itself only collects read-only evidence.
Do not ask the skill to run lab setup, fault injection or teardown.

## Before you start

- Use an isolated development machine or Kubernetes cluster. Never use production
  endpoints, data, credentials or kubeconfigs.
- Run commands from the repository root. Install Docker with Compose for the
  Docker fixtures, and `kubectl` and Helm for the Kubernetes fixtures. The Python
  Milvus permission check also requires a compatible `pymilvus` installation.
- Check available disk, memory and ports. Reserve the `doctor-test-*` names and
  the `io.milvus.doctor.lab=true` label for these fixtures. Do not reuse an existing
  container, namespace, release or collection just because its name matches.
- Setup and teardown scripts require `--allow-lab-writes`. Direct Compose commands
  are manual write operations without that guard; inspect the project before
  running them. Do not use a shared Docker context.
- Docker publishes only loopback ports. Kubernetes fixtures use ClusterIP services;
  keep any manually authorized port forwarding bound to `127.0.0.1`.
- Storage is disposable. Do not add production mounts or backups. The small
  resource limits are for functional checks, not capacity planning or availability
  testing.

## Docker embedded standalone

This fixture uses `milvusdb/milvus:v2.6.17` with embedded etcd and local storage.
State lives in the disposable container layer. Setup refuses to replace an
existing `doctor-test-embedded` container.

```bash
bash tests/lab/docker-standalone.sh up --allow-lab-writes
bash tests/lab/docker-standalone.sh status
curl --fail --max-time 5 http://127.0.0.1:29091/healthz
```

The Milvus endpoint is `http://127.0.0.1:29530`. Wait for the health check before
running endpoint diagnostics; retain a timeout or unhealthy state as evidence
instead of treating setup completion as proof of readiness.

## Docker Compose standalone

The Compose fixture runs Milvus, etcd and MinIO on a dedicated project network.
Only Milvus ports are published; dependency data stays in disposable containers.
The project name is `doctor-test-compose`.

Provide fresh, lab-only MinIO credentials in your shell. For example, generate
them without printing either value or putting a secret in shell history:

```bash
export DOCTOR_LAB_MINIO_USER="$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
export DOCTOR_LAB_MINIO_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
docker compose -f tests/lab/compose.yaml config --quiet
```

Configuration fails if either variable is unset or empty. Do not run with shell
tracing (`set -x`), print the resolved Compose configuration, or commit an `.env`
file. Docker administrators can inspect container environment variables; these
credentials are appropriate only for an isolated disposable lab.

Before setup, inspect containers and networks for the reserved project label:

```bash
docker ps -a --filter label=com.docker.compose.project=doctor-test-compose
docker network ls --filter label=com.docker.compose.project=doctor-test-compose
```

If resources already exist, stop and establish their ownership; do not let
Compose adopt or recreate them. With an unused project name and available ports:

```bash
docker compose -f tests/lab/compose.yaml up -d
docker compose -f tests/lab/compose.yaml ps
curl --fail --max-time 5 http://127.0.0.1:39091/healthz
```

The Milvus endpoint is `http://127.0.0.1:39530`. Keep the same credential variables
available until teardown: Compose validates required variables for `ps` and
`down` as well as `up`.

## Kubernetes with Helm

Supply an explicit disposable-cluster kubeconfig and a local official Milvus
chart directory. The supplied values target chart `5.0.19` and Milvus `v2.6.15`;
ensure its chart dependencies and container images are available. Treat version
changes as fixture changes that need validation.

```bash
export DOCTOR_LAB_KUBECONFIG=/path/to/disposable-cluster-kubeconfig
export DOCTOR_LAB_CHART=/path/to/milvus-5.0.19/charts/milvus
bash tests/lab/kubernetes.sh helm-up --allow-lab-writes
kubectl --kubeconfig "$DOCTOR_LAB_KUBECONFIG" -n doctor-test-helm get pods,services
```

The script refuses to adopt an existing `doctor-test-helm` namespace. The fixture
uses distributed components with small limits; it does not establish multi-node
availability, production throughput or support for every message-stream backend.

## Kubernetes with Operator

Use a disposable cluster with a compatible Milvus Operator and CRD already
installed. This fixture does not install, upgrade or modify either cluster-wide
component. It creates only the dedicated namespace and standalone Milvus CR:

```bash
bash tests/lab/kubernetes.sh operator-up --allow-lab-writes
kubectl --kubeconfig "$DOCTOR_LAB_KUBECONFIG" -n doctor-test-operator get pods,milvuses.milvus.io
```

Creation fails if the namespace already exists. Inspect readiness and the CR's
status before selecting it as a Doctor target.

## Intentional fault fixtures

These commands deliberately create unhealthy resources in the isolated lab:

```bash
bash tests/lab/kubernetes.sh faults-up --allow-lab-writes
kubectl --kubeconfig "$DOCTOR_LAB_KUBECONFIG" -n doctor-test-faults get pods,pvc
bash tests/lab/docker-faults.sh up --allow-lab-writes
bash tests/lab/docker-faults.sh status
```

The Kubernetes fixtures model an unschedulable pod, an exiting process, an invalid
image tag and an unbound PVC. Docker fixtures model unhealthy health checks, an
exited process and a memory-limited OOM kill. The Docker fault containers do not
run a Milvus workload; they exercise container-state evidence. Retry timing can
change the exact state observed. Never modify a real node, disk or application
to reproduce these faults.

## Verify namespace-only Kubernetes permissions

The Helm setup creates the ServiceAccount and Role in `rbac.yaml`. The Role grants
only namespaced `get/list` for pods, services, PVCs, events, deployments,
statefulsets and Milvus CRs. It does not grant Secrets, ConfigMaps, logs, nodes,
`exec`, port forwarding or resource mutation.

Use a new private temporary directory for local credential artifacts:

```bash
DOCTOR_LAB_ARTIFACT_DIR="$(mktemp -d)"
python3 tests/lab/create-reader-kubeconfig.py \
  --source "$DOCTOR_LAB_KUBECONFIG" \
  --output "$DOCTOR_LAB_ARTIFACT_DIR/reader-kubeconfig" \
  --allow-lab-writes
export DOCTOR_LAB_READER_KUBECONFIG="$DOCTOR_LAB_ARTIFACT_DIR/reader-kubeconfig"
bash tests/lab/verify-readonly-role.sh
```

The generated mode-0600 kubeconfig contains a two-hour ServiceAccount token and
public cluster connection/CA details, not the source administrator identity. The
helper refuses to overwrite a file and does not print the token.

Run Doctor with the reader kubeconfig and an explicit namespace/selector. Missing
permission for an ungranted source is a coverage gap, not a reason to grant admin
access. If you need local endpoint access, a human lab operator can separately
authorize loopback-only port forwarding with the lab administrator context;
Doctor does not require `pods/portforward` permission.

## Verify Milvus metadata-only permissions

Start the dedicated authentication-enabled fixture. Enter its administrator token
through a hidden shell prompt; use only the token for this disposable instance,
never a production token. On a freshly created fixture, use that instance's
initial administrator credential.

```bash
DOCTOR_LAB_AUTH_ARTIFACT_DIR="$(mktemp -d)"
bash tests/lab/docker-auth.sh up --allow-lab-writes
bash tests/lab/docker-auth.sh status
# Wait for healthy status before continuing.
read -r -s -p 'Disposable lab admin token: ' DOCTOR_LAB_ADMIN_TOKEN
export DOCTOR_LAB_ADMIN_TOKEN
python3 tests/lab/verify-milvus-rbac.py \
  --output-dir "$DOCTOR_LAB_AUTH_ARTIFACT_DIR/milvus-rbac" \
  --allow-lab-writes
unset DOCTOR_LAB_ADMIN_TOKEN
```

Do not enable shell tracing. Missing or empty admin credentials fail
before Docker inspection or any SDK call. The helper's fixed target is
`http://127.0.0.1:30530`; before writes, it checks the `doctor-test-auth` ownership
label, pinned image and exclusive loopback binding.

The helper creates a disposable collection, reader user and role. It attempts
`ShowCollections`, `DescribeCollection`, `GetStatistics`, `GetLoadState` and
`IndexDetail` grants, recording unsupported privileges without broadening access.
Server-provided public metadata permissions may still apply. The generated
reader token is saved in a mode-0600 file and is never printed.

It checks seven metadata operations, then deliberately attempts collection
creation, insert, deletion and business-data query with the reader. These probes
must be denied; an unavailable operation is not proof of denial. All probes target
only `doctor_test_metadata` and `doctor_test_write_probe` in the owned fixture.
Inspect `verification.json` and the exit status; do not assume a passing result.
These write attempts are lab verification, never Doctor runtime behavior.

## Cleanup

Teardown destroys disposable lab data. Stop only the port-forward processes you
started. Verify the exact resource names, project/release ownership and
`io.milvus.doctor.lab=true` labels before deleting anything.

The Docker scripts check ownership before removing their exact containers:

```bash
bash tests/lab/docker-standalone.sh down --allow-lab-writes
bash tests/lab/docker-auth.sh down --allow-lab-writes
bash tests/lab/docker-faults.sh down --allow-lab-writes
```

Run only the teardown commands for fixtures you created. For Compose, first
confirm all project containers and the dedicated network belong to this lab.
Keep both MinIO environment variables set for parsing, then run:

```bash
docker compose -f tests/lab/compose.yaml down
unset DOCTOR_LAB_MINIO_USER DOCTOR_LAB_MINIO_PASSWORD
```

For Kubernetes, use the explicit lab kubeconfig. Uninstall only release
`doctor-test-helm` from namespace `doctor-test-helm`. Delete only the lab Milvus CR
`doctor-test-operator` and wait for normal cleanup of its owned dependency
releases. Then remove the verified lab namespaces `doctor-test-helm`,
`doctor-test-operator` and `doctor-test-faults`. If setup failed before an ownership
label was applied, inspect its creation records and resources instead of deleting
by name alone. Do not force-remove finalizers or mutate an existing operator, CRD,
cluster or unrelated namespace.

Remove or revoke generated test credentials separately after verifying their
exact paths, including the reader kubeconfig and `reader-token` file. Do not
publish them with diagnostic reports. Leave cached images in place; do not use
global Docker or Kubernetes prune commands.
