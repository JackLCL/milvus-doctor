# Milvus Doctor

[![Version: 0.0.1](https://img.shields.io/badge/version-0.0.1-blue)](https://github.com/JackLCL/milvus-doctor/releases/tag/v0.0.1)

Milvus Doctor helps your AI agent investigate Milvus issues using **read-only**
checks. It explains what it finds and suggests next steps without changing your
deployment.

Use it with standalone or cluster deployments through native endpoints, Docker,
Docker Compose, Kubernetes, Helm or Milvus Operator. You get diagnostic reports,
before-and-after comparisons, [optional log analysis](references/log-export.md),
[official FAQ guidance](references/faq-guide.md), and a Support Summary when you
want community help. Doctor has no telemetry and does not upload your data.
You use your existing AI agent without setting up a Doctor server, registering
an account or providing a separate model API key for Doctor.

## Installation

### Recommended: one message to your AI agent

**Copy this single message into Claude Code or Codex:**

```text
Install Milvus Doctor v0.0.1 by following https://raw.githubusercontent.com/JackLCL/milvus-doctor/v0.0.1/AI-INSTALL.md. Record the selected commit and verify the local installation, then help me diagnose my Milvus issue using read-only checks.
```

Your agent checks the environment, installs the complete Skill, and verifies
that it is ready to use. It asks which deployment or files to inspect when that
information is missing. You do not need to choose shell commands yourself.
The [installation guide](AI-INSTALL.md) is for your agent to read, not a script
to run in a shell. Installation does not grant access to your Milvus deployment.

Prefer a terminal? Use [terminal installation](#terminal-installation) or the
[manual method without Node.js](#manual-installation-and-local-dependencies).
Already installed? See [updates](AI-INSTALL.md#existing-installations-and-updates)
or [uninstall](#uninstall).

**Requirements:** Linux with **Python 3.8+** (3.10+ recommended). macOS and native
Windows are not yet validated. You can use an existing Linux/WSL environment;
the installer does not set one up for you.

**Current release: [0.0.1](https://github.com/JackLCL/milvus-doctor/releases/tag/v0.0.1).**
Installation is pinned to this release. See the [release notes](CHANGELOG.md).
Updates are manual: back up local reports and customizations outside the Skill
directory, follow the [update guide](AI-INSTALL.md#existing-installations-and-updates),
and [verify the installed version](#version-and-downloads).

### Terminal installation

For a **first installation**, run this in a Bash-compatible terminal and select
Claude Code or Codex:

```bash
DISABLE_TELEMETRY=1 npx skills@1.5.26 add https://github.com/JackLCL/milvus-doctor/tree/v0.0.1 -g
```

This installs the complete Skill for your user, across projects. You need Git,
network access and Node.js 22.20.0+ for this installer. Python is installed
separately. The command does not grant cluster access. This installs release
`v0.0.1`; the Agent guide also resolves and records its full source commit.

`DISABLE_TELEMETRY=1` disables the third-party installer's telemetry and remote
audit requests. Doctor itself has no telemetry. See the
[installer documentation](https://github.com/vercel-labs/skills).

Already installed? Follow the [update guide](AI-INSTALL.md#existing-installations-and-updates)
to preserve your reports and customizations: reinstalling can replace the whole
Skill directory, including its virtual environment. Without Node.js, use the
[manual installation](#manual-installation-and-local-dependencies).

If your agent cannot write to its Skill directory, follow the
[protected-directory guide](AI-INSTALL.md#protected-project-skill-directories).
It provides a registration command you can review and run yourself. A downloaded
copy may work from the terminal before it appears in your agent. Keep sandbox
and directory protections in place, and confirm any change in installation scope.

After installation, tell your agent:

> Use Milvus Doctor to check my Milvus issue. First confirm the deployment and read-only scope.

## Use it in your agent

Describe the problem and which deployment or files you want to inspect:

```text
Use Milvus Doctor. My Docker Compose project is my-project and queries sometimes time out. Please run read-only checks and explain what I should do next.
```

You can also invoke `/milvus-doctor` in Claude Code or `$milvus-doctor` in Codex.
Your agent asks for missing target details or permissions only when needed.
If the Skill has not appeared yet, start a new session or reload your agent
before considering another installation.

Look for the optional community support link at the end of a troubleshooting
response, including when no issue was found. You can ask your agent to skip these
reminders, and you never need to provide contact details to continue self-service.
Replies and support invitations follow your preferred language, or the language
of your conversation. The Support Summary for submission is in English.

When you want community help, say:

> Generate my Milvus Doctor Support Summary using this diagnosis and what I've already told you.

Review the text, then submit the [support form](https://zilliverse.feishu.cn/share/base/form/shrcnlGM0veXFaAKlBE0AaNsTBf)
yourself. Doctor never submits it automatically.

## Troubleshooting examples

### Example 1: Diagnose a vector dimension mismatch

**You ask**

> My application is failing with a vector dimension mismatch. Use Milvus Doctor
> to check `./logs/sdk-error.log`. Only inspect this local file.

**Doctor responds**

> The log explicitly reports a **vector dimension mismatch: expected 128,
> received 384**. First check:
>
> 1. The database, collection and vector field actually selected by the call.
> 2. That field's schema `dim` against the embedding model's output dimension.
> 3. Each vector's length, field mapping and serialization in the failed request.
> 4. Whether the model, processing pipeline or target collection recently changed.
>
> I cannot yet determine which part is misconfigured. Only local files were
> analyzed; no server connection, write replay or repair was performed.
>
> If you would like more help, [contact the Milvus community technical team](https://zilliverse.feishu.cn/share/base/form/shrcnlGM0veXFaAKlBE0AaNsTBf).
> I can help prepare a summary of the investigation.

**Outcome:** You get the dimensions reported in the error and concrete checks
to narrow down the cause before making any changes.

### Example 2: Investigate a startup failure and get community support

**You ask**

> Milvus 2.6.17 won't start in Docker standalone. Use Milvus Doctor to check
> `./logs/startup-error.log` only. I haven't made any changes yet. Can you also
> prepare a summary so I can ask the community for help?

**Doctor responds**

> The log reports **a refused connection to etcd**. That identifies a failed
> dependency connection, but it does not establish why the connection failed
> or whether it caused the startup failure.
>
> Next, check the configured etcd address and port, whether etcd was running
> at the time, and whether the Milvus container could reach it. I inspected only
> the supplied log; I have not checked the live services or changed your deployment.
>
> I've prepared a Support Summary with the findings and the evidence still needed.
>
> Review the summary, open the [community support form](https://zilliverse.feishu.cn/share/base/form/shrcnlGM0veXFaAKlBE0AaNsTBf)
> and paste the full text into question 5, **Paste your Milvus Doctor support
> summary**. Complete and submit the form yourself. Status: **NOT SUBMITTED**.

**Outcome:** You get a diagnostic report and a copy-ready Support Summary to
request community help. No support ticket has been submitted. The reply above
is not the full summary; expand the example below to see what goes into the form.

<details>
<summary>View a Support Summary example</summary>

```text
Milvus Doctor Support Summary
Submission status: NOT SUBMITTED

Doctor version: 0.0.1
Milvus version: 2.6.17 (user-confirmed; not independently verified)
Deployment method: docker (user-confirmed; not independently verified)
Deployment topology: standalone (user-confirmed; not independently verified)
Collection method: snapshot
Application SDK: Not provided
Application SDK version: Not provided
Application Python version: Not provided
Doctor collector SDK version (not application SDK): Not provided
Doctor collector Python version (not application Python): Not provided
Problem type: troubleshooting
Incident time / window (user-reported): Not provided
Evidence collection started at: Not provided
Evidence collection completed at: Not provided
Report generated at: Not provided

Symptom and impact:
Milvus fails to start in a Docker standalone deployment. Business impact was not provided.

Expected behavior:
Milvus starts successfully.

Reproduction / diagnostic sequence (identify who performed each step):
1. The user reported the startup failure and supplied a local startup log.
2. Doctor analyzed only that file. No live endpoint or container checks were performed.

Confirmed facts from the conversation:
The user reports Milvus 2.6.17 with Docker standalone and confirms that no changes have been made. The supplied log reports a refused connection to etcd. The live deployment configuration and dependency state remain unverified.

Hypotheses (not established causes):
The etcd service may have been unavailable, or Milvus may have been using an incorrect address or port. The log alone does not establish the cause of the refused connection or its relationship to the startup failure.

Diagnostic findings:
- CONNECTION_REFUSED | warning | self_service | occurrences=1: Supplied evidence records a connection or name-resolution failure.
- DEPENDENCY_UNAVAILABLE | warning | self_service | occurrences=1: Supplied evidence records a dependency connectivity failure.

Selected technical facts (allowlisted metadata):
Not provided; use reviewed excerpts below if available.

Reviewed key evidence (user/Agent-selected, not raw automatic logs):
The selected startup log contains an etcd connection-refused error. The incident time and first relevant startup error have not been confirmed.

Evidence coverage and gaps:
Overall status: attention_required
Complete for selected scope: true
Sources: successful=1, errors=0, unavailable=0, not_requested=3, scope_excluded=0
- health: skipped; reason=not_requested; records=1; intentionally not checked, no permission expansion required
- logs: ok; reason=unknown; records=1
- metrics: skipped; reason=not_requested; records=1; intentionally not checked, no permission expansion required
- milvus: skipped; reason=not_requested; records=1; intentionally not checked, no permission expansion required
Unselected areas remain unverified; complete selected coverage is not a system health guarantee.

Actions already performed by the user:
The user reports that no changes have been made. Other troubleshooting actions were not provided.

Recheck results:
Not provided

Remaining questions / evidence needed:
What was the incident time and first relevant startup error? Was etcd running and reachable from the Milvus container at that time? What etcd address and port are configured? Recent changes before the failure and any recheck results were not provided.

Assistance requested:
Please help determine why Milvus fails to start, which read-only checks can narrow down the etcd connection failure, and what evidence is needed before considering any changes.

Doctor performed read-only checks only. Any changes listed above were performed by the user, not Doctor.
```

</details>

For your own incident, ask Doctor to generate a Support Summary, review it for
accuracy and sensitive information, and paste it into question 5 of the form.
If you have important context that the summary misses, add it under a separate
**Additional information (optional)** heading without changing the generated summary.

## Manual installation and local dependencies

This option does not need Node.js. Clone the repository into a **new** location
you control. Install the entire directory, not just the `SKILL.md` file:

```bash
git clone --depth 1 --branch v0.0.1 https://github.com/JackLCL/milvus-doctor.git
git -C milvus-doctor rev-parse HEAD
```

- **Codex:** place or symlink the directory at `.agents/skills/milvus-doctor`
  in your project, or `~/.agents/skills/milvus-doctor` for your user.
- **Claude Code:** use `.claude/skills/milvus-doctor` in your project or home.
- **Without an AI agent:** run the Python commands directly from the repository.

From the installed Skill directory, check that Doctor is ready locally:

```bash
python3 scripts/doctor.py preflight
```

This checks Python and available dependencies without connecting to Milvus or
changing your deployment. Missing optional dependencies do not block basic
checks. Credentials, connectivity and cluster health are not checked here.
Exit `0` means the local core is ready on a supported platform; `1` means a failed
check or unsupported platform; `2` means an input/runtime error. For JSON output:

```bash
python3 scripts/doctor.py preflight --format json
```

YAML parsing and SDK metadata checks need optional Python packages. If you need
them, install them in a dedicated virtual environment rather than system Python:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-optional.txt
.venv/bin/python scripts/doctor.py preflight
```

Reuse an existing working `.venv` instead of recreating it. Basic JSON/log
analysis, HTTP health checks and Docker/Kubernetes metadata do not need these
packages. Docker/Kubernetes checks need the relevant CLI and read-only access.
Your report shows any checks that could not run because a dependency is missing.

Doctor runs in your agent's environment. To inspect a private cluster, that
environment must be able to reach it and have the read-only access you approve.

## Uninstall

### Ask your agent

```text
Uninstall Milvus Doctor from this agent. First confirm the installation scope and exact paths, preserve my diagnostic reports and local changes outside the installation, and keep any shared copy needed by other agents. Do not change my Milvus deployment.
```

See the [agent uninstall guide](AI-INSTALL.md#uninstalling). Uninstalling Doctor
removes the local Skill, not your Milvus deployment or its data.

### Installed with the Skills CLI

**Back up reports, customizations and any needed `.venv` outside the installation
before removal.** The installer can delete the entire Skill directory; files
inside it are not automatically preserved.

Check the installed path and any symlink targets, then list the agents using
the same copy. A `.agents/skills/milvus-doctor` directory may be shared by Codex,
Claude Code, Cursor, OpenCode or other agents, even if you selected only some of
them during installation. Decide which agents you want to remove Doctor from.
Complete these checks and your backups first: removal may not ask for confirmation.

```bash
# Personal/global installation (across projects)
DISABLE_TELEMETRY=1 npx skills@1.5.26 list --global --json

# Project installation: run from the original project directory, not the Skill directory
DISABLE_TELEMETRY=1 npx skills@1.5.26 list --json
```

Replace `CONFIRMED_AGENT_IDS` below with the IDs of those agents, separated by
spaces. It is a placeholder, not a shell variable. Run from the original project,
adding `--global` only for a personal installation:

```bash
DISABLE_TELEMETRY=1 npx skills@1.5.26 remove milvus-doctor --agent CONFIRMED_AGENT_IDS
```

Specify agent names rather than `--all`, wildcards or an unqualified global
removal. This installer can otherwise also affect project paths, and its list
of remaining agents may not include every agent using a shared directory.
See the [uninstall guide](AI-INSTALL.md#uninstalling) for shared-copy checks.

If you remove Doctor from only one agent, keep the copy that other agents still
need. For example, `--agent codex` may leave a shared copy that Codex can still
discover. Use the guide's manual unregistration steps where appropriate; do not
force-delete that directory or install Node.js solely to uninstall.

**Verify removal:** repeat the list command for the same scope, inspect the
registration and shared-directory paths, and confirm Doctor no longer appears
in the selected agent. An exit code of `0` or "Successfully removed" does not
guarantee complete removal. If a copy remains, check who still uses it before
expanding the removal scope. An empty installer lock file can be left in place.

### Manual clone, archive or symlink installation

Locate the exact registration you used: personal/project
`.agents/skills/milvus-doctor` for Codex or `.claude/skills/milvus-doctor` for
Claude Code, or your agent's custom location.

- **Symlink:** remove only the registration link, not the directory it points
  to. Your separately cloned repository and its reports stay intact.
- **Real directory:** first check whether other agents use it. Once you have
  confirmed the scope, move that directory to a new backup location **outside
  every Skill discovery directory**. This preserves reports, customizations and
  `.venv` while removing the Skill from discovery.

Remove only the specific Skill registration, never an entire `.agents`,
`.claude`, `skills` or project directory. Keep shared runtime dependencies.
Separately stored reports, Milvus services, data, credentials and system Python
are unaffected by uninstalling the Skill.

Start a new agent session and verify that Doctor is no longer available there.
An existing conversation may still contain instructions loaded before removal.

## Advanced: run the checker directly

In the commands below, `scripts/doctor.py` stands for your installed script.
Use its absolute path while working in your project, or resolve all input/output
paths before changing directories. Relative paths refer to your project, not
the Skill directory. Store logs and reports outside the installation so updates
or uninstalling cannot remove them.

### Official FAQ lookup

Ask your agent:

> Milvus 2.6.17 has low search recall. Please use the official FAQ to help troubleshoot.

You can search the bundled documentation without cluster credentials or extra
Python packages. From the terminal:

```bash
python3 scripts/doctor.py faq
python3 scripts/doctor.py faq --query 'low recall' --milvus-version 2.6.17 --limit 3 --format markdown
python3 scripts/doctor.py faq --query 'etcd bad member ID' --milvus-version 3.0.0
```

The offline bundle covers the English Performance, Product, Operational, Limits
and Troubleshooting FAQ pages for **v3.0.x and v2.6.x**, including CPU support.
Linked manuals, images and other versions or languages are not bundled. It is
a bundled copy, not a live view of the website; see the
[FAQ guide](references/faq-guide.md) for source versions and coverage.

Doctor flags known outdated or conflicting answers and risky recovery steps.
It provides recommendations, not executable repairs. If your version is unknown
or unsupported, the result says so rather than silently using another release.
Relevant FAQ references can appear in your report and Support Summary, but they
are guidance, not evidence of what happened in your deployment.

FAQ exit codes: `0` means a usable result or overview; `1` means no match or a
version mismatch; `2` means invalid input or bundle. These are search outcomes,
not cluster health results.

### Optional log export

First preview which logs would be collected, without accessing the cluster:

```bash
python3 scripts/doctor.py export-logs --deployment helm \
  --context my-reader --namespace milvus --release my-milvus --since 30m --previous
```

Once you approve the scope, add `--collect --output-dir ./new-case-directory`
to collect, redact and analyze the selected logs. You can select Docker/Compose
logs, Kubernetes current or previous container logs, and optional dependency
or init-container logs. See the [log guide](references/log-export.md) for access
requirements, collection limits and restrictions on logs stored in files.
Ordinary diagnosis does not fetch remote logs automatically. Exported files
stay local; they are not uploaded.

### Deployment checks

Replace the example names and addresses with the exact targets you want to inspect:

```bash
# Explicit Docker inventory (names/images/status only)
python3 scripts/doctor.py discover --deployment docker

# Docker standalone
python3 scripts/doctor.py diagnose --deployment docker --container my-milvus \
  --health-url http://127.0.0.1:9091/healthz --output-dir ./reports/first-check

# Compose project; optional SDK metadata
python3 scripts/doctor.py diagnose --deployment compose --compose-project my-milvus \
  --endpoint http://127.0.0.1:19530

# Helm-managed cluster, using a namespace-scoped read-only credential
python3 scripts/doctor.py diagnose --deployment helm \
  --context my-reader --namespace milvus --release my-milvus

# Operator-managed instance
python3 scripts/doctor.py diagnose --deployment operator \
  --context my-reader --namespace milvus --operator-name my-milvus

# Offline local files, without connecting to a cluster
python3 scripts/doctor.py diagnose --manifest ./values.yaml --log-file ./incident.log
python3 scripts/doctor.py diagnose --snapshot ./snapshot.json --format json

# Local filesystem capacity only; does not scan directory contents
python3 scripts/doctor.py diagnose --data-dir /path/to/selected/milvus/storage
```

See [deployment examples](references/deployments.md), [minimum access](references/access.md)
and [available checks](references/check-catalog.md).

## Reports and rechecks

Use `--output-dir` to save local reports with permissions restricted to their owner:

- `report.md` and `report.json`: findings and recommendations.
- `support-summary.md`: the standard summary you can review and share.
- `support-request.md`: instructions for contacting the community.
- `evidence.json`: selected technical facts used by the report.

Existing report files are not overwritten. Use `--previous` to compare a new
report with an earlier one. A finding disappearing is not enough to confirm a
fix: recheck the original symptom against the same target, scope and workload.

`evidence.json` can include schema dimensions, index/load state, container
resources, Pod/PVC state and source timestamps. Resource names use aliases
within each report. You can read those facts without reconnecting:

```bash
python3 scripts/doctor.py show-evidence --report ./reports/first-check/report.json
python3 scripts/doctor.py read-evidence --file ./selected-error.log --start-line 1 --lines 80
```

Use `read-evidence` to redact selected log, configuration or application excerpts
before sharing them with your AI agent, instead of reading raw files with
`cat`, `head` or `rg`. Review the result for sensitive information that automatic
redaction may miss. `evidence.json` contains selected facts, not a replayable
snapshot of your environment.

Use `--collection NAME` to inspect only a named collection. An endpoint alone
does not identify how Milvus is deployed. Reports distinguish details you
provided from details checked directly, and your application's SDK version
from Doctor's own SDK version.

Diagnosis exit codes:

- **0:** no actionable findings in the available evidence; informational notes may remain.
- **1:** actionable findings or incomplete coverage; read the report for details.
- **2:** invalid input or an execution failure.

A successful check covers only the evidence inspected, not overall production
readiness. Areas you did not select remain unverified. Failed requested checks
or missing required capabilities are reported as incomplete coverage; deliberately
unselected areas are not.

## Read-only boundary

- Doctor inspects only the resources you select, with no all-namespace scans.
- It does not run commands inside containers, read Kubernetes Secrets, query
  vectors or rows, load/flush data, restart services, repair, upgrade, compact or
  migrate your deployment. Remote logs require your explicit request.
- Helm checks use labels rather than Helm's Secret-backed release store.
- For authenticated metadata checks, use a `MILVUS_DOCTOR_TOKEN` environment
  variable you configure, not credentials in command-line arguments.
- You choose any live HTTP(S) health or metrics URL. Requests are validated and
  limited in time and size.
- Reports are redacted, but resource names and technical details may remain.
  Review them before sharing. The Support Summary uses selected technical facts
  rather than raw logs. Check any added context or excerpts for resource names,
  contact details and other sensitive information that redaction may miss.
- **Local collection does not mean offline AI processing.** If your agent uses
  a cloud model, that provider may process the tool output. Doctor itself does
  not send evidence to Milvus/Zilliz or identify you.

Community support is optional. You can continue troubleshooting without sharing
contact details. Requesting technical support is not consent to sales outreach.

## Request technical support

Ask your agent to prepare a Support Summary from the report and what you have
already shared. It separates confirmed facts from possible causes and records
actions and rechecks without assuming they happened. Missing details stay
`Not provided`; you do not need to rewrite your issue from scratch.

Your agent may ask for one or two important missing details. You can still
request support if you do not have them. Review the summary for accuracy and
sensitive information, and optionally add **Additional information (optional)**
with anything important that is missing.

Open the [support form](https://zilliverse.feishu.cn/share/base/form/shrcnlGM0veXFaAKlBE0AaNsTBf),
complete the required contact and project fields, and paste your full reviewed
summary into question 5, **Paste your Milvus Doctor support summary**. You can
also provide a preferred contact method and details. Submit the form yourself:
generating a summary or opening the link does not submit a support request.
Doctor does not automatically open, fill or submit the form.

To prepare a summary from an existing report without reconnecting:

```bash
python3 scripts/doctor.py support-summary --report ./reports/first-check/report.json
# Optional JSON of already-known symptom, user-performed actions and requested help:
python3 scripts/doctor.py support-summary --report ./reports/first-check/report.json \
  --context ./support-context.json --output ./reports/first-check/support-summary-reviewed.md
```

`--output` saves a new local file; it never submits it. See the
[summary and context guide](references/handoff.md) for the supported fields.

## Version and downloads

Check your installed version:

```bash
python3 scripts/doctor.py --version
```

The current release is **[0.0.1](https://github.com/JackLCL/milvus-doctor/releases/tag/v0.0.1)**.
Routine improvements land on `main`; a new versioned release is published after
a milestone is complete. Normal installations stay pinned to a release. Ask for
`main` explicitly if you want development changes, and retain its selected commit.

For a Git installation, run `git rev-parse HEAD` from the installed repository
and keep the full commit with your diagnostic notes. For an installer-managed
copy without Git metadata, retain the full commit selected during installation;
do not infer it from today's `main` or from the runtime marker. See the
[Agent installation guide](AI-INSTALL.md) for commit-pinned installation.

For a download without Git, use the release's
[complete Skill ZIP](https://github.com/JackLCL/milvus-doctor/releases/download/v0.0.1/milvus-doctor-0.0.1.zip),
[manifest](https://github.com/JackLCL/milvus-doctor/releases/download/v0.0.1/release-manifest.json)
and [checksums](https://github.com/JackLCL/milvus-doctor/releases/download/v0.0.1/SHA256SUMS).
Verify the downloaded files with `sha256sum -c SHA256SUMS`, extract into a new
directory, and follow the [manual installation](#manual-installation-and-local-dependencies)
registration and preflight steps. Compare `RELEASE.json.git_commit` with the
published manifest. Checksums detect changed bytes but are not a digital signature;
download all three files from the intended release. Follow the
[update guide](AI-INSTALL.md#existing-installations-and-updates) before replacing
any existing installation.
