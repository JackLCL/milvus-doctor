# Install Milvus Doctor for an AI agent

This guide is for Claude Code, Codex, or another agent installing the **complete
Milvus Doctor Skill** for a user. Installation ends with local verification, not
with an implicit connection to a Milvus cluster. No Doctor server, account,
separate model API key, or support-form submission is required.

Canonical source: https://github.com/JackLCL/milvus-doctor, development branch `main`.
No versioned release is currently published. The runtime marker `0.0.1-dev` is
not a release or a unique commit identity; `main` changes as updates are merged.
Read this complete guide before installing. It is Markdown instructions, **not**
a shell script; never pipe it to `bash` or `sh`. If fetching through a reader
truncates it, retrieve the complete raw Markdown instead:

```bash
curl -fsSL https://raw.githubusercontent.com/JackLCL/milvus-doctor/main/AI-INSTALL.md
```

## 1. Establish the local installation target

Use the current agent identity and user-selected workspace/scope. Default to a
personal installation for the requesting agent only, unless the user asks for a
project/team installation. Do not install into every detected agent.

- **Codex:** personal `~/.agents/skills/milvus-doctor`, project
  `.agents/skills/milvus-doctor`. Respect an existing supported host-configured
  installation (for example, one managed by its built-in Skill Installer).
- **Claude Code:** personal `~/.claude/skills/milvus-doctor`, project
  `.claude/skills/milvus-doctor`. Respect a custom Claude configuration directory.
- Inspect the chosen destination **and any symlink target/canonical copy** first.
  The Skills CLI normally shares a canonical `.agents/skills/milvus-doctor` copy;
  installing for Claude can therefore affect an existing Codex installation.
  Check relevant personal/project paths for duplicates without searching the
  user's whole home directory. See the existing-install procedure below if found.
- Check the platform, Python version, and availability of Git; check Node/npm
  only if using the Skills CLI. Do not dump environment variables or credentials.

Diagnosis is currently supported on **Linux, Python 3.8+** (3.10+ recommended).
Do not claim macOS or native Windows support based on a successful file copy.
On an unsupported platform, explain the limit before installing. An existing,
user-approved Linux/WSL environment may be used; do not provision a VM, WSL,
container, remote host, or system Python automatically. If Python is absent,
explain the requirement and stop before claiming the checker is ready.

## 2. Install into an unused destination

### Select one source revision

Resolve `main` to its full commit once, before installing:

```bash
git ls-remote --exit-code https://github.com/JackLCL/milvus-doctor.git refs/heads/main
```

Require one successful result with a full 40-character hexadecimal commit and
`refs/heads/main`. Set `doctor_commit` to that exact commit for the commands
below, and retain it in the installation handoff. If resolution fails, stop;
do not guess a commit or silently use another source. Fetch and read this guide
at the selected commit so the instructions and installed source agree:

```bash
curl -fsSL "https://raw.githubusercontent.com/JackLCL/milvus-doctor/$doctor_commit/AI-INSTALL.md"
```

If Git is unavailable, a host-supported repository lookup may resolve `main`
to its full commit for an archive installer instead. Preserve the same source
identity checks; do not install Git or guess a revision merely to continue.

If the user explicitly requests another revision, verify its full commit and
use that revision's guide and files throughout. Future milestone releases will
pin the installation guide and commands to the same published tag/commit.

### With the Skills CLI

Use this route if Git and a suitable Node.js/npm are already available. The
installer version is pinned; `skills@1.5.26` needs Node.js **22.20.0+**.
The commands below are for Bash-compatible shells, after confirming destinations
are unused. Do not use `--yes` to bypass the host's permission controls.

For Claude Code:

```bash
DISABLE_TELEMETRY=1 npx -y skills@1.5.26 add "https://github.com/JackLCL/milvus-doctor/tree/$doctor_commit" --skill milvus-doctor --agent claude-code --global --yes
```

For Codex:

```bash
DISABLE_TELEMETRY=1 npx -y skills@1.5.26 add "https://github.com/JackLCL/milvus-doctor/tree/$doctor_commit" --skill milvus-doctor --agent codex --global --yes
```

For a project installation, run from that user-selected project and omit
`--global`. Do not add both agent names unless the user wants both. Preserve
`DISABLE_TELEMETRY=1`: it disables the third-party installer's telemetry and
remote audit requests. Normal package/repository downloads still use the network.
Do not accept an optional offer to install unrelated skills.

### Without Node.js

Do not install Node just for this Skill. If the host offers its built-in Skill
Installer, use it with repository `JackLCL/milvus-doctor`, repository-relative
path `.`, ref set to the selected full commit, and explicit installed name
`milvus-doctor`. The explicit name matters because the Skill lives at the
repository root. Use the helper actually available
in that host; do not invent a local path to it.

Otherwise, clone the repository into the confirmed, unused Skill destination,
creating only the necessary parent directories. For example, a fresh personal
Claude Code installation with the default configuration location:

```bash
git clone --no-checkout --single-branch --branch main https://github.com/JackLCL/milvus-doctor.git ~/.claude/skills/milvus-doctor
git -C ~/.claude/skills/milvus-doctor checkout --detach "$doctor_commit"
git -C ~/.claude/skills/milvus-doctor rev-parse HEAD
```

For Codex, use the confirmed `.agents/skills/milvus-doctor` destination instead.
Use a quoted absolute destination when its path contains spaces. Never force
overwrite, delete an existing directory, or replace a symlink to make this work.
If Git is unavailable, use the host's supported archive installer or explain the
missing prerequisite; don't install system packages without separate authorization.

Verify that checkout succeeded and `HEAD` equals the selected full commit before
running the downloaded code. If that commit is unavailable, stop and explain;
do not use a different `main` revision implicitly. For an explicitly selected
non-main revision, use the host's commit/archive installer or an exact verified
checkout. Do not use `repo@version`: that installer's `@` suffix selects a Skill
name, not a Git revision. Version and local preflight must also be verified.

### Protected project Skill directories

Some agent sandboxes deliberately protect `.agents` or `.codex`, even while the
rest of the project is writable. If the requested destination is protected,
do not disable sandboxing, change permissions, repeatedly retry, or silently move
a project installation into the user's global directory.

Prefer the host's supported installation/approval flow. If unavailable, explain
the limit. Within the user's authorized project, a complete new copy may be
prepared in a writable tools directory, but **that is not automatic registration**.
Use the bundled helper to plan registration at the real requested location:

```bash
python3 scripts/register_skill.py --agent codex --scope project --project /actual/project
```

Run this from the downloaded Skill, or supply its exact `--source`. Use
`--agent claude-code` for Claude Code; `--scope user` is only for a requested
personal installation. A custom Claude location can use `--claude-dir`.
The default prints a plan without writing. `--apply` creates only a missing link
and its parent directories, never replaces an existing path, changes permissions,
or accesses a cluster. If blocked, it returns `requires_user_action` and a safely
quoted `user_command`. Ask the user to run that one command in their own terminal
on the same host, then verify registration on the next turn/session. Do not claim
the Skill is discovered until the host actually lists or loads it.

If the user prefers to continue without registration, explicitly read the project
copy's `SKILL.md` for this session and label the state **tool ready, registration
pending**. Never hide this fallback or interpret it as permission to inspect Milvus.

## 3. Verify the core; add only needed capabilities

Use the installed directory reported by the installer, resolving its symlink as
needed. Confirm it includes `SKILL.md`, `AI-INSTALL.md`, `scripts/doctor.py`,
`scripts/doctorlib/`, `references/`, `agents/`, and `requirements-optional.txt`.
Do not install just the text of `SKILL.md`.

Run from that directory. Prefer its working `.venv/bin/python` when one already
exists; otherwise use the available Python 3 interpreter:

```bash
python3 scripts/doctor.py --version
python3 scripts/doctor.py preflight --format json
```

Preflight inspects local Python/platform, discovers optional `yaml`/`pymilvus`
modules and `docker`/`kubectl` commands, and runs the rule/report/summary pipeline
against synthetic evidence **in memory**. It does not run those CLIs, read
credentials, access a cluster, install dependencies, or write a report.

- Exit `0`: local core ready on a supported platform. Missing optional capabilities
  are informational; do not call them a broken installation.
- Exit `1`: unsupported platform or a failed core check. Explain the actual result,
  do not proceed as if ready. Exit `2` is an input/runtime error.
- Module discoverability and a command on PATH do not prove that its dependencies,
  credentials, server connection, or permissions work. Live checks retain their
  own coverage gaps and require an explicitly authorized target.

The core needs no pip packages. If the user's next check requires YAML or SDK
metadata, prepare the needed packages in this Skill's dedicated `.venv`, within
the installation request and the host's normal approval policy. Reuse an existing
healthy venv; do not recreate or replace one containing user changes. From the
Skill directory, a fresh venv with both optional capabilities can be prepared as:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-optional.txt
.venv/bin/python scripts/doctor.py preflight --format json
```

For just YAML use `"PyYAML>=6.0,<7"`; for just SDK metadata use
`"pymilvus>=2.6,<3.1"` with the same venv pip. No sudo, system-Python changes,
credential creation, Docker group membership changes, or cluster modifications.
If an optional install fails, report that capability as unavailable and offer
supported core/local-evidence checks. Do not hide the failed install or loop on it.

## 4. Hand off to the first diagnosis

Report the actual installed path, Doctor version, selected full source commit,
local preflight result, and any unavailable capabilities. Distinguish
**files installed**, **local core ready**,
and **skill discovered by the host**; do not claim a UI/session check you did not do.
New skills may require a new turn/session or host reload/restart. Check the host's
skill list first; do not reinstall repeatedly if discovery has not refreshed.

For Git installs, verify the installed `HEAD`. For copy/archive installers without
Git metadata, report the commit supplied to the successful installation and
which checks were actually performed; do not claim an independent byte-for-byte
source verification unless you did one. If provenance is missing, label it
unknown rather than attributing the copy to today's `main`.

Then follow the installed `SKILL.md` using the symptom and target the user already
gave. Ask only for missing scope: a container/Compose project, Kubernetes context
AND namespace (plus release/selector as needed), explicit endpoint, or selected
local evidence. Installing the Skill is **not** permission to scan deployments.

The user can explicitly invoke `/milvus-doctor` in Claude Code or `$milvus-doctor`
in Codex. If the agent environment cannot reach the target, offer analysis of
selected local logs/configuration/snapshots. Do not open ports or deploy an agent.
Explain evidence and recommendations; all repairs remain the user's actions.
If requested, prepare the standard Support Summary; the user reviews and submits
the form manually. Installation does not collect contact details or upload reports.

## Existing installations and updates

If the Skill already exists, verify and reuse it for an installation request.
An update requires user intent to update; don't silently change versions.

Development updates can share the same runtime marker, so compare actual source
commits, not just `--version`. Resolve and record the intended revision using the
source-selection procedure above. Preserve an existing installation even if its
original source revision is no longer available; an unreachable revision is not
permission to erase local files or replace it. If a future published ZIP is used,
compare its `RELEASE.json.git_commit` with that release's manifest as well.

Skills CLI reinstall/update can **replace the whole canonical directory**, losing
reports, local code, and `.venv`. It may also affect other agents sharing that
directory. Before an authorized update, inspect the actual target and local
changes, preserve user files in a separate confirmed location, and use a staged
new version or reviewed fast-forward update. Do not delete/reclone or force a
Git reset. No existing installation needs to be removed just to run preflight.
If an older version has no preflight subcommand, report that and offer an update;
it does not prove the older diagnostic core is broken.

After any update, verify local readiness again and confirm the host loads the
intended copy. Never change agent-wide permissions or unrelated configuration.

## Uninstalling

Perform removal only when the user explicitly requests it. This removes local
Skill registration/files, not Milvus services or data. Do not run a diagnostic,
read credentials or connect to a cluster as part of uninstalling.

1. Confirm the requested agent and project/personal scope. Locate the exact
   registration, its type (real directory or symlink) and any canonical/source
   directory it references. Respect custom host locations. Inspect only the
   relevant known Skill paths, not the user's entire home or workspace.
2. Identify other registrations sharing that directory. In particular, Codex's
   `.agents/skills/milvus-doctor` may itself be the canonical copy linked from
   Claude Code. Removing a single agent name is not proof that a shared directory
   is safe to delete. Explain the affected agents; stop for clarification if the
   requested scope and the shared ownership cannot be reconciled safely.
3. Preserve reports, local code changes and any needed isolated `.venv` in a
   confirmed location outside the installation before deleting a real directory.
   Never assume the installer preserves them. Do not delete separate clones,
   externally stored reports, user credentials or shared Python/Node packages.
4. For a **manual symlink registration**, unlink only that exact verified link;
   do not recursively delete it, dereference it for deletion, or add a trailing
   slash. Leave its source directory intact. For a **manual real directory** with
   no remaining dependents, prefer moving it to a new, non-overwriting backup
   outside all Skill discovery paths rather than permanently deleting it.
5. For a Skills CLI installation, use the pinned `skills@1.5.26` removal only
   after checking the exact affected paths and preserving files. Run the selected
   scope's `list --json` first (`--global` for personal scope), then confirm all
   actual Doctor consumers and inspect shared paths. Codex/Claude registrations
   can share the canonical copy with Cursor, OpenCode or other detected hosts.
   Use `remove milvus-doctor --agent CONFIRMED_AGENT_IDS`, replacing the literal
   placeholder with the user-confirmed IDs; add `--global` only for personal
   scope, otherwise run from the original project. Keep explicit names: an unqualified
   global removal in 1.5.26 can also touch project paths for other agent types.
   Agent detection can make removal non-interactive even without `--yes`; obtain
   the required scope/backup decisions before invocation, not from an assumed
   confirmation prompt. Keep `DISABLE_TELEMETRY=1`, and never add `--all`,
   wildcards or broad path deletion. If the
   CLI is unavailable, do not install system dependencies merely to remove files.
   For a one-agent request, do not remove additional agents without consent. The installer may
   retain a canonical copy for detected consumers, so `--agent codex` alone can
   leave Doctor discoverable in Codex. Undetected/custom consumers may not be
   protected. Use only verified safe registration removal, or explain why the
   shared copy must remain; never force deletion to make a success claim.
6. If local permission controls prevent removal, use the host-approved/user
   terminal workflow. Do not disable protections, use sudo, or widen scope.
   Do not trust exit 0 or the installer's success message alone: repeat the scoped
   list, inspect original registrations/canonical paths, and verify Doctor's lock
   entry is gone when a full removal was requested. A remaining shared copy means
   partial removal, not success. Empty lock files can remain; do not edit them.
   Report exactly what was unregistered, deleted, backed up or deliberately kept,
   and whether it is recoverable. Reload/start a new host session and verify the
   selected Skill is no longer discovered; do not claim that an existing
   conversation has forgotten instructions already loaded into its context.

The default objective is to stop the selected agent using Doctor, not erase every
copy of its source or remove other agents' installations. If safe separation is
not possible, ask the user before touching the shared directory. Do not edit
installer lock files by hand or claim manual file removal updated their records.

## Installation references

- [Skills CLI options, paths and telemetry](https://github.com/vercel-labs/skills)
- [Pinned Skills CLI removal behavior](https://github.com/vercel-labs/skills/blob/v1.5.26/src/remove.ts)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [Codex skills](https://learn.chatgpt.com/docs/build-skills)
