# Changelog

## Unreleased

- License project-authored code and Skill documentation under Apache-2.0,
  preserving the bundled FAQ's separate attribution and license files.

## 0.0.1 - 2026-09-15

- Diagnose Milvus deployments through bounded, read-only endpoint, metadata and
  local-evidence checks for Docker/Compose, Kubernetes/Helm/Operator and native
  deployments. Report evidence, coverage gaps and recommendations without repairs.
- Offer explicit, scoped log export with redaction, size/time limits and support
  for previous Kubernetes container logs; no automatic uploads or raw log bundles.
- Provide version-aware official FAQ lookup and separate documentation guidance
  from observed diagnostic evidence.
- Generate local reports, before-and-after comparisons and an English Support
  Summary for voluntary community review. Support invitations follow the user's
  language; the user reviews and submits the form manually.
- Support one-message Agent installation, local preflight, scoped registration
  and backup-preserving updates/uninstall. Install the complete Skill and verify
  its source revision without granting access to a Milvus deployment.
- Include user-facing troubleshooting examples and a complete Support Summary
  example. Explain privacy limits, unknown recheck status and shared-installation
  handling.
- Develop on `main` and publish versioned releases after completed milestones.
  Normal installation is pinned to `v0.0.1` and records its full source commit.
