import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib.cli import parser, validate_args, main
from doctorlib.reports import build_report, save_report, support_draft, support_summary, markdown, SUPPORT_FORM_URL


class ReportTests(unittest.TestCase):
    def test_no_repairs_does_not_infer_user_confirmed_recheck_status(self):
        summary = support_summary(build_report({}, []), {"actions_already_performed": "No repairs were made."})
        self.assertIn("Actions already performed by the user:\nNo repairs were made.", summary)
        self.assertIn("Recheck results:\nNot provided", summary)
        self.assertNotIn("Not performed (user-confirmed)", summary)

    def test_no_evidence_is_never_healthy(self):
        self.assertEqual(build_report({}, [])["status"], "incomplete")
        self.assertEqual(build_report({"sources": [{"name": "sdk", "status": "error"}]}, [])["status"], "incomplete")

    def test_missing_finding_is_not_claimed_resolved(self):
        old = {"findings": [{"code": "OOM", "summary": "memory issue"}]}
        report = build_report({"sources": [{"name": "pods", "status": "skipped"}]}, [], old)
        self.assertEqual(len(report["comparison"]["not_observed_now"]), 1)
        self.assertIn("NOT verified resolution", report["comparison"]["interpretation"])
        self.assertEqual(report["status"], "incomplete")

    def test_informational_notes_do_not_demand_repairs(self):
        finding = {"code": "HELM_STATUS_UNAVAILABLE", "severity": "info", "summary": "optional status not collected"}
        report = build_report({"sources": [{"name": "pods", "status": "ok"}]}, [finding])
        self.assertEqual(report["status"], "observations_only")
        self.assertEqual(report["severity_counts"], {"critical": 0, "warning": 0, "info": 1})

    def test_positive_state_summary(self):
        snapshot = {"kubernetes": {"pods": [{"status": {"conditions": [{"type": "Ready", "status": "True"}]}}, {"status": {"conditions": [{"type": "Ready", "status": "False"}]}}], "pvcs": [{"status": {"phase": "Bound"}}, {"status": {"phase": "Pending"}}]}}
        observed = build_report(snapshot, [])["observations"]
        self.assertEqual(observed["pods_ready"], 1)
        self.assertEqual(observed["pvcs_bound"], 1)

    def test_save_no_overwrite_and_private_modes(self):
        with tempfile.TemporaryDirectory() as d:
            report = build_report({}, [])
            save_report(report, d)
            p = Path(d) / "report.json"
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            previous = p.read_bytes()
            with self.assertRaises(ValueError): save_report(report, d)
            self.assertEqual(previous, p.read_bytes())

    def test_handoff_is_unsubmitted_and_omits_evidence(self):
        f = {"code": "X", "severity": "warning", "summary": "private-collection", "evidence": ["business-data-value"], "route": "community_review"}
        report = build_report({}, [f])
        draft = support_draft(report)
        self.assertIn("NOT SUBMITTED", draft)
        self.assertNotIn("private-collection", draft)
        self.assertNotIn("business-data-value", draft)
        self.assertIn(SUPPORT_FORM_URL, draft)
        self.assertIn("question 5", draft)
        self.assertNotIn("Work email (fill in voluntarily)", draft)
        self.assertNotIn("I would like a Zilliz Cloud discussion", draft)

    def test_support_form_is_linked_without_snapshot_override(self):
        report = build_report({"support": {"form_url": "https://untrusted.invalid"}}, [])
        self.assertEqual(report["support"]["form_url"], SUPPORT_FORM_URL)
        self.assertEqual(report["support"]["submission_status"], "not_submitted")
        self.assertIn(SUPPORT_FORM_URL, markdown(report))
        self.assertNotIn("untrusted.invalid", markdown(report))

    def test_saved_handoff_keeps_summary_exact_without_form_audit_history(self):
        report = build_report({}, [])
        with tempfile.TemporaryDirectory() as directory, \
                patch("urllib.request.urlopen", side_effect=AssertionError("No form request")), \
                patch("subprocess.run", side_effect=AssertionError("No external command")):
            save_report(report, directory)
            draft = (Path(directory) / "support-request.md").read_text()
            summary = (Path(directory) / "support-summary.md").read_text()
        self.assertEqual(draft.split("\n---\n\n", 1)[1], summary)
        self.assertEqual(draft.count(SUPPORT_FORM_URL), 1)
        self.assertIn("NOT SUBMITTED", draft)
        self.assertIn("current required-field markers", draft)
        self.assertIn("optional preferred contact details", draft)
        # The only dates belong to the diagnosis, not a historic form audit.
        instructions = draft.split("\n---\n\n", 1)[0]
        self.assertNotRegex(instructions, r"\b\d{4}-\d{2}-\d{2}\b")

    def test_support_link_is_available_for_every_report_outcome_without_contact_fields(self):
        cases = [
            ({"sources": [{"name": "health", "status": "ok"}]}, [], "no_findings_in_available_evidence"),
            ({}, [], "incomplete"),
            ({"sources": [{"name": "health", "status": "ok"}]},
             [{"code": "HELM_STATUS_UNAVAILABLE", "severity": "info", "summary": "Optional state unverified"}], "observations_only"),
            ({"sources": [{"name": "health", "status": "ok"}]},
             [{"code": "HEALTH_CHECK_FAILED", "severity": "warning", "summary": "Health failed"}], "attention_required"),
        ]
        with patch("urllib.request.urlopen", side_effect=AssertionError("No support network action")), \
                patch("subprocess.run", side_effect=AssertionError("No support command action")):
            for snapshot, findings, expected in cases:
                with self.subTest(status=expected):
                    report = build_report(snapshot, findings)
                    self.assertEqual(report["status"], expected)
                    self.assertEqual(report["support"]["form_url"], SUPPORT_FORM_URL)
                    self.assertEqual(report["support"]["submission_status"], "not_submitted")
                    self.assertEqual(markdown(report).count(SUPPORT_FORM_URL), 1)
                    self.assertEqual(support_draft(report).count(SUPPORT_FORM_URL), 1)
                    # The chat invitation stays outside the text users paste into
                    # the form. No contact input or new live check is required.
                    self.assertNotIn(SUPPORT_FORM_URL, support_summary(report))

    def test_standard_summary_order_missing_facts_and_safe_projection(self):
        f = {"code": "K8S_RESTART_HISTORY", "severity": "info", "summary": "private-resource", "evidence": ["private-data"], "recommendation": "Restart the cluster", "route": "self_service"}
        snapshot = {"sources": [{"name": "kubernetes.pods", "status": "error", "detail": "private-endpoint"}]}
        summary = support_summary(build_report(snapshot, [f]))
        headings = ["Doctor version:", "Milvus version:", "Deployment method:", "Deployment topology:", "Symptom and impact:", "Diagnostic findings:", "Evidence coverage and gaps:", "Actions already performed by the user:", "Recheck results:", "Assistance requested:"]
        positions = [summary.index(h) for h in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Symptom and impact:\nNot provided", summary)
        self.assertIn("Actions already performed by the user:\nNot provided", summary)
        self.assertIn("Recheck results:\nNot provided", summary)
        for excluded in ("private-resource", "private-data", "private-endpoint", "Restart the cluster", SUPPORT_FORM_URL):
            self.assertNotIn(excluded, summary)

    def test_support_context_redacted_and_not_inferred(self):
        report = build_report({}, [])
        summary = support_summary(report, {"symptom_and_impact": "Timeout while token=synthetic-sensitive", "assistance_requested": "Please review"})
        self.assertNotIn("synthetic-sensitive", summary)
        self.assertIn("Please review", summary)
        self.assertIn("Actions already performed by the user:\nNot provided", summary)
        for invalid in ({"email": "a@example.com"}, {"actions_already_performed": ["restart"]}, {"symptom_and_impact": "x" * 2001}):
            with self.assertRaises(ValueError): support_summary(report, invalid)

    def test_summary_deduplicates_rule_occurrences(self):
        f = {"code": "K8S_RESTART_HISTORY", "severity": "info", "summary": "resource"}
        summary = support_summary(build_report({}, [f, f, f]))
        self.assertEqual(summary.count("K8S_RESTART_HISTORY"), 1)
        self.assertIn("occurrences=3", summary)

    def test_summary_metadata_cannot_smuggle_internal_identifiers(self):
        report = build_report({"deployment": {"method": "private-host", "mode": "private-namespace"}, "milvus": {"version": "private-customer-build"}}, [])
        report["status"] = "private-deployment"
        report["tool_version"] = "private-installation"
        summary = support_summary(report)
        self.assertNotIn("private-", summary)
        self.assertIn("Deployment method: Not provided", summary)
        report["tool_version"] = "0.1.1"
        report["observations"]["milvus_version"] = "v3.0.0-rc.1"
        self.assertIn("Milvus version: v3.0.0-rc.1", support_summary(report))

    def test_summary_comparison_avoids_resolution_claim_and_resource_names(self):
        old = {"findings": [{"code": "DOCKER_UNHEALTHY", "summary": "private-resource"}]}
        summary = support_summary(build_report({}, [], old))
        self.assertIn("not_observed_now: DOCKER_UNHEALTHY", summary)
        self.assertIn("not verified resolution", summary)
        self.assertNotIn("private-resource", summary)

    def test_saved_copy_ready_summary_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            report = build_report({}, [])
            save_report(report, d)
            summary = Path(d) / "support-summary.md"
            self.assertEqual(summary.read_text(), support_summary(report))
            self.assertEqual(summary.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): save_report(report, d)

    def test_report_redacts_evidence_and_sources(self):
        f = {"code": "X", "severity": "warning", "summary": "error", "evidence": ["password=hidden"], "route": "self_service"}
        report = build_report({"sources": [{"name": "sdk", "status": "error", "detail": "Bearer abc-secret"}]}, [f])
        text = json.dumps(report)
        self.assertNotIn("abc-secret", text); self.assertNotIn("hidden", text)


class ArgumentsTests(unittest.TestCase):
    def test_absolute_entrypoint_resolves_evidence_and_reports_in_project(self):
        with tempfile.TemporaryDirectory(prefix="doctor-project-paths-") as directory:
            project = Path(directory)
            (project / "compose.json").write_text(json.dumps({"services": {
                "milvus": {"image": "milvusdb/milvus:v2.6.17", "depends_on": ["missing-etcd"]}
            }}))
            script = Path(__file__).resolve().parents[1] / "scripts/doctor.py"
            result = subprocess.run([sys.executable, "-B", str(script), "diagnose", "--manifest", "compose.json",
                                     "--output-dir", "cases/first", "--format", "json"], cwd=project,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertIn("COMPOSE_DEPENDENCY_UNDEFINED", {finding["code"] for finding in report["findings"]})
            self.assertEqual(report, json.loads((project / "cases/first/report.json").read_text()))
            self.assertTrue((project / "cases/first/support-summary.md").is_file())

    def test_summary_command_uses_only_saved_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / "report.json"
            context = Path(d) / "context.json"
            report.write_text(json.dumps(build_report({}, [])))
            context.write_text(json.dumps({"symptom_and_impact": "Queries time out", "actions_already_performed": "No changes made"}))
            with patch("doctorlib.collectors.collect", side_effect=AssertionError("live collector called")), patch("urllib.request.urlopen", side_effect=AssertionError("network called")), contextlib.redirect_stdout(io.StringIO()) as out:
                rc = main(["support-summary", "--report", str(report), "--context", str(context)])
            self.assertEqual(rc, 0)
            self.assertIn("Queries time out", out.getvalue())
            self.assertIn("No changes made", out.getvalue())
            self.assertIn("NOT SUBMITTED", out.getvalue())

    def test_targets_cannot_escape_into_cli_flags(self):
        for argv in (["diagnose"], ["diagnose", "--container=--privileged"], ["diagnose", "--namespace", "production"], ["diagnose", "--snapshot", "x", "--endpoint", "http://localhost:19530"], ["diagnose", "--health-url", "http://a/delete"], ["diagnose", "--deployment", "helm", "--namespace", "x"]):
            with self.subTest(argv=argv), self.assertRaises(ValueError): validate_args(parser().parse_args(argv))

    def test_no_execution_subcommand(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in (["fix"], ["diagnose", "--execute"], ["diagnose", "--upload"]):
                with self.subTest(argv=argv), self.assertRaises(SystemExit): parser().parse_args(argv)

    def test_offline_snapshot_does_not_collect_live(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "input.json"
            p.write_text(json.dumps({"schema_version": 1, "sources": [{"name": "fixture", "status": "ok"}]}))
            with patch("doctorlib.collectors.collect", side_effect=AssertionError("network collection called")), contextlib.redirect_stdout(io.StringIO()) as out:
                rc = main(["diagnose", "--snapshot", str(p), "--format", "json"])
            self.assertIn(rc, (0, 1)); self.assertTrue(json.loads(out.getvalue())["read_only"])

    def test_empty_snapshot_never_returns_success(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "input.json"
            for snapshot in ({}, {"sources": [{"name": "json", "status": "ok"}]}):
                p.write_text(json.dumps(snapshot))
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = main(["diagnose", "--snapshot", str(p), "--format", "json"])
                self.assertEqual(rc, 1)
                self.assertEqual(json.loads(out.getvalue())["status"], "incomplete")

    def test_credential_redaction_through_cli_report(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "input.json"
            detail = "Authorization: Basic dXNlcjpwYXNzd29yZA== secret_key=do-not-disclose accessKeyID=also-sensitive"
            p.write_text(json.dumps({"sources": [{"name": "input", "status": "error", "detail": detail}]}))
            with contextlib.redirect_stdout(io.StringIO()) as out:
                main(["diagnose", "--snapshot", str(p), "--format", "json"])
            for secret in ("dXNlcjpwYXNzd29yZA==", "do-not-disclose", "also-sensitive"):
                self.assertNotIn(secret, out.getvalue())

    def test_static_manifest_is_not_claimed_native_deployment(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "compose.json"
            p.write_text(json.dumps({"services": {"milvus": {"image": "milvusdb/milvus:v2.6.17"}}}))
            with patch("doctorlib.collectors.run_readonly", side_effect=AssertionError("subprocess on static input")), contextlib.redirect_stdout(io.StringIO()) as out:
                rc = main(["diagnose", "--manifest", str(p), "--format", "json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.getvalue())["deployment"]["method"], "snapshot")
            self.assertTrue(json.loads(out.getvalue())["coverage"]["complete_for_selected_scope"])


if __name__ == "__main__": unittest.main()
