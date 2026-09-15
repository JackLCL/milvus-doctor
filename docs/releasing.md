# Build and verify a versioned Skill release

This is a maintainer workflow, not part of Doctor's diagnostic execution. Building
does not create a tag, publish a GitHub release, install a Skill, or contact Milvus.
Publishing and choosing a license require separate maintainer decisions.

## Release cadence

Merge routine improvements into `main` and record user-facing changes under
`Unreleased` in `CHANGELOG.md`. Individual edits do not require a version bump,
tag or GitHub Release. Normal installation instructions stay pinned to the
current release, `v0.0.1`; `main` remains available for explicitly requested
development use. The Agent guide resolves and records the selected full source
commit, dereferencing annotated tags rather than using their tag-object IDs.

Publish when a milestone is complete. Choose its version, reconcile the runtime
`__version__`, README and AI-INSTALL installation pins, version-specific examples
and release notes, then verify one committed revision. Publish that same revision
as a release tag with its ZIP, manifest and checksums. Replacing an existing tag
or download asset is a separate explicit release decision, not part of routine
merges or release preparation.

## Release identity

Choose the intended version and committed revision first. Runtime `__version__`,
the release/tag name, current installation instructions and release notes must
agree. Validate the selected revision; results from a different version do not
establish that this release passed.

Before building, review and commit every intended runtime module, including new
files under `scripts/doctorlib/`. A dirty worktree is not included. The builder
resolves `--ref` once to a commit and reads its regular Git blobs only; even the
version comes from that commit's `scripts/doctorlib/__init__.py`, never from the
running worktree or a supplied version argument.

Use an immutable commit for pre-publication verification. An existing reviewed
tag may also be passed. Do not create or move a tag solely to make the build run.

## Build into a new directory

From the source repository, with Python 3.8+ and Git already available:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py --ref <reviewed-commit-or-existing-tag> \
  --output-dir /approved/existing/parent/new-release-directory
```

Replace placeholders with the selected revision and a **nonexistent** output
directory whose parent already exists. Existing directories, files and dangling
symlinks are refused; there is no overwrite or cleanup switch. If a write fails,
inspect the new partial output and choose a fresh directory rather than deleting
an unresolved path.

The output contains:

- `milvus-doctor-<version>.zip`, with one top-level `milvus-doctor/` directory.
- `release-manifest.json`, recording version, full commit, source file hashes,
  archive SHA256 and the selected source ref.
- `SHA256SUMS`, covering the ZIP and external manifest.

The archive also contains generated `milvus-doctor/RELEASE.json` with its version
and source commit. All tracked regular files under `agents/`, `scripts/` and
`references/` are included unless explicitly excluded, together with `SKILL.md`,
`AI-INSTALL.md`, `README.md`, `CHANGELOG.md` and `requirements-optional.txt`. This automatically
includes newly committed runtime modules such as `local_files.py`.

The curated package excludes this build script, tests, docs, diagnostic reports,
evidence, virtual environments, caches, Git data and credential/token/secret
paths. Symlinks or submodules in allowed paths cause a build failure. Developer
documentation linked from the README uses versioned source-hosted URLs, not
nonexistent local ZIP paths. GitHub's automatically generated source archives
are separate artifacts and may include developer tests/docs.

Only an existing tracked `LICENSE`, `LICENSE.md` or `LICENSE.txt` is included.
When absent, the builder and manifest explicitly warn that **no open-source
license or reuse permission is asserted**. Do not invent a license or assume the
upstream Milvus license automatically applies to this repository.

Separately attributed FAQ documentation under `references/faq/` carries its own
included upstream Apache-2.0 license files. Those do not license Doctor's code.

Archive order, file modes and timestamps are fixed from the commit, without a
build-time timestamp; the same commit and builder/compression environment produce
identical archive bytes. File-path filtering is not a complete secret-content
audit: review the committed source and actual archive before publishing.

## Verify the actual artifact

From the newly generated/downloaded artifact directory:

```bash
sha256sum -c SHA256SUMS
unzip -l milvus-doctor-<version>.zip
```

Check the manifest's full commit against the intended reviewed release commit.
An intact SHA256 file detects changed bytes; it is not a digital signature or
proof of the publisher's identity. Use the intended repository/release source.

Extract to a fresh directory and run the **extracted** tool, not the source tree:

```bash
release_verify_dir=$(mktemp -d)
unzip -q milvus-doctor-<version>.zip -d "$release_verify_dir"
python3 "$release_verify_dir/milvus-doctor/scripts/doctor.py" --version
python3 "$release_verify_dir/milvus-doctor/scripts/doctor.py" preflight --format json
```

The version must equal the manifest and intended release. Preflight must report
core readiness on the supported platform. It checks local runtime only: it does
not prove optional dependency compatibility, host Skill discovery, credentials or
Milvus health. Record the actual results; do not relabel failures as success.
The temporary extracted directory can be retained for review; this workflow does
not automatically delete it.

## Fixed-version installation instructions

After the intended tag exists, verify its final commit and test both the actual
tag install and the actual downloadable ZIP. Replace `<published-tag>` below
with the tag that actually exists; this placeholder is not a published version:

```bash
DISABLE_TELEMETRY=1 npx skills@1.5.26 add 'https://github.com/JackLCL/milvus-doctor/tree/<published-tag>' -g
```

For Agent-controlled installation, use the explicitly selected `--agent codex`
or `--agent claude-code`, `--skill milvus-doctor` and ordinary host-approved
noninteractive options, after checking the destination is unused. Omit the global
flag for a requested project installation. `repo@version` is not a Git-ref pin in
Skills CLI 1.5.26; its `@` suffix selects a Skill name. `/tree/<ref>` is supported.

Pin the Agent's raw installation-guide URL to the same tag/commit, and pin the
commands **inside** that guide as well. Do not silently fetch `main` from a pinned
guide. A no-Node Git install may use `git clone --depth 1 --branch <tag>` into a
confirmed new destination, then verify its commit and extracted/local readiness.

Keep file installation, local preflight and actual AI-host discovery/conversation
as distinct checks. Copy mode may install directly into `.claude/skills` or
`.agents/skills`; use the installer's actual final path rather than guessing a
shared canonical location. Preserve existing user files and follow AI-INSTALL's
update/registration handoff. Respect the host's permissions and report unavailable
verification steps accurately.

Publish only the reviewed runtime ZIP, manifest and checksums. Diagnostic data,
raw logs, credentials and evidence bundles are **not public Release assets**.
Publication requires an explicit maintainer action. Distinguish local test results
from checks that actually ran in hosted CI.
