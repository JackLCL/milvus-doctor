# Select a deployment

In examples, `scripts/doctor.py` means the installed entrypoint: prefer its absolute
path while keeping the user's project as cwd. Resolve example/user input paths
against that project before changing cwd, and put outputs outside the installed
Skill. All addresses/names below are examples: replace them with the user's explicit target.
Do not open ports, change kubeconfig or install anything on an existing cluster.

## Endpoint-only / explicitly native

```bash
python3 scripts/doctor.py diagnose --mode standalone \
  --endpoint http://127.0.0.1:19530 \
  --health-url http://127.0.0.1:9091/healthz --format json
```

An endpoint does not reveal Docker/Helm/native installation: automatic deployment
method remains `unknown`, with collection method `endpoint`. Only add
`--deployment native` when the user actually confirms that installation method;
it is recorded as a user selection, not independent proof of bare-metal deployment.

For a user-selected host process add `--pid 1234`; only process resource counters
are read, not its command line or environment. For a remote cluster endpoint use
`--mode cluster`. This path does not inspect a service manager, filesystem contents
or magically infer the installation method. For a Lite `.db` file, do not open it
through MilvusClient: that can create or modify local database state. Analyze the
user-supplied configuration/logs instead; live Lite inspection is out of scope.

## Docker / Compose

```bash
python3 scripts/doctor.py discover --deployment docker
python3 scripts/doctor.py diagnose --deployment docker --container my-milvus \
  --health-url http://127.0.0.1:9091/healthz --format json
python3 scripts/doctor.py diagnose --deployment compose --compose-project my-project \
  --endpoint http://127.0.0.1:19530 --format json
```

Repeat `--container` for explicitly selected dependency containers. Compose uses
the project label and only inspects members of that project, without executing
`docker compose config`, starting services, or evaluating interpolation scripts.
Docker socket access itself is generally powerful: do not ask users to join the
Docker group just for Doctor; a supplied snapshot is the fallback.

## Kubernetes, Helm, Operator

```bash
python3 scripts/doctor.py diagnose --deployment kubernetes \
  --context my-readonly-context --namespace milvus --selector app=milvus
python3 scripts/doctor.py diagnose --deployment helm \
  --context my-readonly-context --namespace milvus --release my-milvus
python3 scripts/doctor.py diagnose --deployment operator \
  --context my-readonly-context --namespace milvus --operator-name my-milvus
```

Specify a selector for a multi-tenant namespace. Without one, the user authorizes
reading the listed resource types across the chosen namespace. `--operator-name`
limits CR selection, not all other namespace resources. Namespaces are never
expanded automatically. Label-scoped scans omit Events and mark that gap because
Events usually lack workload labels. Helm is detected from workload labels, not
from the Secret-based Helm release store; Helm release status/history is only
analyzed when supplied in an offline snapshot.

Use user-provided `--endpoint`, `--health-url`, `--metrics-url` if reachable.
Doctor does not execute `kubectl port-forward` or create a public Service. Optional
metrics are a single bounded numeric snapshot; labels are discarded, and no
rate, P99 interval or causal performance diagnosis is fabricated from one sample.

## Local files / offline evidence

For explicitly requested container stdout/stderr (including Kubernetes previous
container instances), use the separate [log-export workflow](log-export.md).
Normal `diagnose` and `read-evidence` do not fetch remote logs. Logs written only
to persistent files still require the owner to provide a selected local excerpt.

```bash
python3 scripts/doctor.py diagnose --manifest ./docker-compose.yml
python3 scripts/doctor.py diagnose --manifest ./values.yaml --config-file ./milvus.yaml
python3 scripts/doctor.py diagnose --manifest ./milvus-cr.yaml --log-file ./selected-error.log
python3 scripts/doctor.py diagnose --snapshot ./snapshot-input.json --format json
```

Static checks do not validate live rollout success. Snapshot mode is exclusive:
it rejects all live-target flags and does not use network/subprocess collectors.
Snapshots follow [snapshot-contract.md](snapshot-contract.md). Request only the
relevant local log time window; never collect raw container logs implicitly.

## Read evidence without bypassing redaction

```bash
python3 scripts/doctor.py read-evidence --file ./selected-error.log --start-line 1 --lines 80
python3 scripts/doctor.py read-evidence --file ./application.py --start-line 20 --lines 40 --format json
python3 scripts/doctor.py show-evidence --report ./doctor-case-1/report.json
```

`read-evidence` accepts one explicitly selected regular file, redacts its bounded
contents before selecting lines, and reports omitted/truncated content. It does
not execute code, parse arbitrary instructions or fetch remote logs. Default
read limit is 1 MiB; default output 80 lines, maximum 400. If the file exceeds the
limit, obtain a smaller reviewed local excerpt or explicitly choose an allowed
bound; never bypass it with cat/head/rg. No redactor can guarantee removal of all
business-sensitive text; review before sharing with an AI or the support team.

Reports include `evidence.json`, a safe allowlisted metadata projection. It
preserves dimensions, load/index state, resource counters and source timestamps
without raw rows/vectors/logs or resource names. It is **not** the normalized
input snapshot above; do not pass it to `diagnose --snapshot` or invent
`snapshot.json`. `show-evidence` reads the projection from an existing report,
without reconnecting or importing private collectors.

For a named collection, use `--endpoint ... --collection my_collection`.
The name can be repeated; `--collection-limit` remains 1..100. To skip SDK checks,
omit `--endpoint`; a collection limit of zero is invalid.

## Output and recheck

```bash
python3 scripts/doctor.py diagnose --snapshot ./before.json --output-dir ./doctor-case-1
python3 scripts/doctor.py diagnose --snapshot ./after.json \
  --previous ./doctor-case-1/report.json --output-dir ./doctor-case-2
```

Exit codes: `0` no actionable findings in selected available evidence (information-only notes may remain); `1` actionable findings or
incomplete coverage (a report was produced); `2` usage/collection input/output
failure. Do not repeatedly rerun a valid report merely because exit code is `1`.
Explicit `not_requested` and `scope_excluded` sources remain visible and unverified,
but do not make the selected scope incomplete. Requested failures, missing needed
dependencies, unknown legacy skip intent and no successful evidence still do.

For engineer help, use `support-summary --report ./doctor-case-2/report.json` to
prepare the standard text without reconnecting to the cluster. Follow
[handoff.md](handoff.md) for the designated form and optional conversation context.
