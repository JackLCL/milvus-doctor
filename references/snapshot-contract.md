# Implementation contract

The collector returns a JSON-compatible dict. Missing sources must be marked
`skipped` or `error`, never interpreted as passing checks.

Top level:
- `schema_version`: `1`
- `deployment`: `{mode: standalone|cluster|unknown, method: native|docker|compose|kubernetes|helm|operator|snapshot|unknown, collection_method, provenance}`. A bare endpoint yields unknown installation method; collection_method describes how evidence was read. Provenance distinguishes explicit_selection, target_selection, workload_labels, offline_snapshot and unknown.
- `captured_at` / `completed_at`: ISO UTC collection window, distinct from report generation and user-reported incident time.
- `collector_runtime`: optional `{pymilvus_version, python_version}` for Doctor itself, never the user's application runtime.
- `sources`: list of `{name, status: ok|skipped|error, detail, observed_at?, reason?}`. Only explicit `skipped` with reason `not_requested` or `scope_excluded` is intentionally out of scope. Missing needed dependency / failed requested reads / old skips with unknown intent remain incomplete.
- `docker`: `{containers: [...]}`. Each container: `name`, `image`, `role`,
  `state` (running/exited/restarting), `health`, `restart_count`, `oom_killed`,
  `exit_code`, `memory_limit_bytes`, optional `memory_usage_bytes`, `labels`
  (only compose/Helm/Milvus identification labels), `ports`.
- `kubernetes`: `{pods, deployments, statefulsets, services, pvcs, events,
  milvuses, helm_releases}`. Resources use Kubernetes JSON structure but only
  allowlisted relevant fields; exclude environment values, secret references
  containing credentials, Secret objects and managedFields. No `exec` or writes.
- `milvus`: `{version, collections: [{name, row_count, loaded, schema, indexes}]}`.
  SDK calls only list/describe/statistics, bounded by collection limit. No data
  query/search, load, flush, insert, compaction or other mutations.
- `health`: `{url, status: ok|error, http_status, detail}`.
- `metrics`: numeric Prometheus metric name to list of numeric values (no labels).
- `config`: normalized Milvus configuration mapping (nested, optional).
- `manifests`: parsed local YAML/JSON objects (compose, helm values, operator CR,
  Kubernetes resources). Rules should handle common static configuration errors.
- `logs`: optional user-selected bounded local log text; never fetch remote logs
  implicitly. Must be redacted before any output.
- `log_export`: optional typed export metadata: `since_seconds`, `tail_lines`,
  `streams_attempted`, `saved_streams`, `previous_streams`, `saved_bytes`.
  The explicit `export-logs --collect` workflow supplies already-redacted text
  to the same rules and writes separate local log files; normal collectors do
  not gain implicit log access. The evidence projection retains only these
  counters/window bounds, not raw log bodies or target identifiers.

`doctorlib.collectors.collect(args)` returns this snapshot. It uses
`doctorlib.safety.run_readonly(argv, timeout=..., max_bytes=...)` for subprocesses,
and `doctorlib.safety.redact(value)` for text/JSON before output. `args` is the
`argparse.Namespace` produced by the `diagnose` parser in
[`scripts/doctorlib/cli.py`](../scripts/doctorlib/cli.py). Run
`python3 scripts/doctor.py diagnose --help` for available options; use `getattr`
when consuming optional inputs.

`doctorlib.rules.evaluate(snapshot)` returns findings. Every finding contains:
`code`, `severity` (critical/warning/info), `summary`, `evidence` (list[str]),
`recommendation`, `verification`, `route`
(self_service/community_review/private_support/security_escalation).
Recommendations describe USER actions; Doctor never executes them. Rules do not
turn explicitly unrequested sources into an error or a permission request.

`report.json` contains a separate `evidence` projection, also saved as
`evidence.json`. It is deliberately **not a replay snapshot**: raw logs,
configuration bodies, row/vector values and resource names are excluded. Use
`show-evidence --report ...` for this saved view, and `read-evidence --file ...`
for explicitly selected, bounded and redacted file excerpts. Never fabricate a
snapshot.json path or use the safe projection as `--snapshot` input.

CLI args: `deployment` (auto/native/docker/compose/kubernetes/helm/operator),
`mode` (auto/standalone/cluster), `container` (repeatable explicit names),
`compose_project`, `context`, `namespace`, `selector`, `release`, `operator_name`,
`endpoint`, `health_url`, `metrics_url`, `token_env`, `database`,
`collection_limit` (default 10), `timeout` (default 8), `max_bytes` (default 1048576),
`manifest` (repeatable local file), `config_file`, `log_file` (repeatable local file),
`pid`, `snapshot` (offline JSON input), `output_dir`, `format` (markdown/json),
`data_dir` (explicit local filesystem capacity only), `previous` (report JSON for comparison). Targets must be explicit: do not scan all
Docker containers or Kubernetes namespaces. `discover` is a separate explicit
inventory action listing only names/images/namespace/context, no diagnostics.
