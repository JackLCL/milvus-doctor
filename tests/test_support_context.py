"""Meaningful handoff regressions from the installation/SDK/startup journeys."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib.reports import build_report, markdown, save_report, support_draft, support_summary, validate_support_context


def complete_snapshot(**extra):
    return {"sources": [{"name": "docker.inspect", "status": "ok"}], **extra}


class SupportContextTests(unittest.TestCase):
    def test_user_partial_and_prerelease_versions_are_preserved(self):
        for value in ("3.11", "2.6.0rc1", "3.12.0b2", "2.6.0.dev1", "v3.0.0-rc.1"):
            context = validate_support_context({"app_sdk_version": value})
            self.assertEqual(context["app_sdk_version"], value)
            self.assertIn("Application SDK version: " + value, support_summary(build_report(complete_snapshot(), []), context))

    def test_optional_evidence_does_not_make_scope_incomplete(self):
        snapshot = complete_snapshot()
        snapshot["sources"] += [
            {"name": "milvus", "status": "skipped", "reason": "not_requested"},
            {"name": "kubernetes.events", "status": "skipped", "reason": "scope_excluded"},
        ]
        report = build_report(snapshot, [])
        self.assertEqual(report["status"], "no_findings_in_available_evidence")
        self.assertTrue(report["coverage"]["complete_for_selected_scope"])
        self.assertEqual(report["coverage"]["not_requested"], 1)
        self.assertEqual(report["coverage"]["scope_excluded"], 1)
        summary = support_summary(report)
        self.assertIn("docker.inspect: ok", summary)
        self.assertIn("reason=scope_excluded", summary)
        self.assertIn("no permission expansion required", summary)
        self.assertIn("not a system health guarantee", summary)

    def test_requested_and_legacy_unavailable_sources_remain_incomplete(self):
        for source in (
            {"name": "milvus", "status": "skipped", "reason": "dependency_missing"},
            {"name": "milvus", "status": "skipped"},
            {"name": "milvus", "status": "error", "reason": "not_requested"},
        ):
            snapshot = complete_snapshot()
            snapshot["sources"].append(source)
            report = build_report(snapshot, [])
            self.assertEqual(report["status"], "incomplete")
            self.assertFalse(report["coverage"]["complete_for_selected_scope"])

    def test_malformed_saved_sources_do_not_disappear_from_coverage(self):
        for invalid in (None, "not-a-source", {"status": []}, {"status": "mystery"}):
            report = build_report({"sources": [{"name": "docker", "status": "ok"}, invalid]}, [])
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(report["coverage"]["unavailable"], 1)
            self.assertIn("Complete for selected scope: false", support_summary(report))

    def test_new_summary_preserves_three_field_context(self):
        summary = support_summary(build_report({}, []), {
            "symptom_and_impact": "Queries time out",
            "actions_already_performed": "No changes have been made",
            "assistance_requested": "Please help choose the next read-only check",
        })
        self.assertIn("Queries time out", summary)
        self.assertIn("No changes have been made", summary)
        self.assertIn("next read-only check", summary)
        self.assertIn("Application SDK version: Not provided", summary)
        self.assertIn("Hypotheses (not established causes):\nNot provided", summary)

    def test_collector_sdk_is_never_the_application_sdk(self):
        report = build_report(complete_snapshot(
            collector_runtime={"pymilvus_version": "2.6.17", "python_version": "3.10.12"},
            milvus={"version": "2.6.15"},
        ), [])
        summary = support_summary(report)
        self.assertIn("Application SDK version: Not provided", summary)
        self.assertIn("Application Python version: Not provided", summary)
        self.assertIn("Doctor collector SDK version (not application SDK): 2.6.17", summary)
        summary = support_summary(report, {"app_sdk_name": "pymilvus", "app_sdk_version": "2.5.16", "app_python_version": "3.11.9"})
        self.assertIn("Application SDK version: 2.5.16", summary)
        self.assertIn("Application Python version: 3.11.9", summary)
        self.assertIn("Doctor collector SDK version (not application SDK): 2.6.17", summary)

    def test_legacy_native_label_is_not_bare_metal_evidence(self):
        report = build_report(complete_snapshot(
            deployment={"method": "native", "mode": "standalone"},
            native={"pid": 54321},
        ), [])
        summary = support_summary(report)
        self.assertIn("Deployment method: Not provided (legacy native label is unverified)", summary)
        summary = support_summary(report, {"deployment_method": "docker"})
        self.assertIn("Deployment method: docker (user-confirmed; not independently verified)", summary)
        self.assertNotIn("Deployment method: native", summary)

    def test_explicit_native_keeps_source_not_automatic_detection_claim(self):
        report = build_report(complete_snapshot(
            deployment={"method": "native", "mode": "unknown", "provenance": "explicit_selection", "collection_method": "endpoint"},
        ), [])
        summary = support_summary(report)
        self.assertIn("Deployment method: native (source: explicit_selection)", summary)
        self.assertIn("Collection method: endpoint", summary)

    def test_conflicting_user_metadata_is_visible_and_does_not_rewrite_report(self):
        report = build_report(complete_snapshot(
            deployment={"method": "docker", "mode": "standalone", "provenance": "target_selection"},
            milvus={"version": "2.6.17"},
        ), [])
        original = json.dumps(report, sort_keys=True)
        summary = support_summary(report, {"deployment_method": "helm", "deployment_topology": "cluster", "milvus_version": "2.6.15"})
        self.assertIn("docker (report); helm (user-confirmed; conflicts with report", summary)
        self.assertIn("standalone (report); cluster (user-confirmed; conflicts with report", summary)
        self.assertIn("2.6.17 (report); 2.6.15 (user-confirmed; conflicts with report", summary)
        self.assertEqual(json.dumps(report, sort_keys=True), original)

    def test_semver_prefix_is_not_a_false_conflict(self):
        report = build_report(complete_snapshot(milvus={"version": "v2.6.17"}), [])
        summary = support_summary(report, {"milvus_version": "2.6.17"})
        self.assertIn("v2.6.17 (report and user-confirmed)", summary)
        self.assertNotIn("conflicts with report", summary)

    def test_evidence_contains_useful_dimensions_but_no_names_or_vectors(self):
        report = build_report(complete_snapshot(
            docker={"containers": [{"name": "customer-private-container", "state": "running", "memory_usage_bytes": 950, "memory_limit_bytes": 1000, "restart_count": 3}]},
            milvus={"collections": [{"name": "customer-private-collection", "schema": {"fields": [{"name": "customer-private-field", "type": "FLOAT_VECTOR", "params": {"dim": 8}}]}, "row_data": "PRIVATE-ROW-DATA", "vectors": [[1, 2, 3, 4]]}]},
            logs="PRIVATE-RAW-LOG",
        ), [{"code": "DOCKER_MEMORY_PRESSURE", "severity": "warning", "summary": "PRIVATE-TITLE", "evidence": ["PRIVATE-EVIDENCE"]}])
        summary = support_summary(report)
        self.assertIn("memory_usage_bytes=950", summary)
        self.assertIn("memory_limit_bytes=1000", summary)
        self.assertIn("field-1 dimension=8", summary)
        self.assertIn("single sample / historical counters", summary)
        for secret in ("customer-private", "PRIVATE-ROW-DATA", "PRIVATE-RAW-LOG", "PRIVATE-TITLE", "PRIVATE-EVIDENCE"):
            self.assertNotIn(secret, summary)
            self.assertNotIn(secret, json.dumps(report["evidence"]))

    def test_saved_evidence_is_reprojected_before_markdown_export(self):
        report = build_report({}, [])
        report["evidence"]["injected"] = "PRIVATE-RAW-LOG"
        self.assertNotIn("PRIVATE-RAW-LOG", markdown(report))
        with tempfile.TemporaryDirectory() as directory:
            save_report(report, directory)
            evidence_file = Path(directory) / "evidence.json"
            self.assertNotIn("PRIVATE-RAW-LOG", evidence_file.read_text())
            self.assertEqual(evidence_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(evidence_file.read_text())["kind"], "diagnostic_evidence")

    def test_summary_does_not_trust_tampered_saved_evidence(self):
        report = build_report({}, [])
        report["evidence"] = {"docker": {"containers": [{"alias": "PRIVATE-ALIAS", "memory_usage_bytes": "PRIVATE-USAGE", "memory_limit_bytes": True, "state": "PRIVATE-STATE"}]}, "logs": "PRIVATE-RAW-LOG"}
        summary = support_summary(report)
        self.assertNotIn("PRIVATE-", summary)
        self.assertNotIn("memory_limit_bytes=True", summary)

    def test_observed_and_incident_times_are_distinct(self):
        report = build_report(complete_snapshot(captured_at="2026-09-13T12:00:00+00:00", completed_at="2026-09-13T12:00:02+00:00"), [])
        summary = support_summary(report, {"incident_time": "2026-09-13 around 10:00 UTC, reported by the user"})
        self.assertIn("Evidence collection started at: 2026-09-13T12:00:00+00:00", summary)
        self.assertIn("Incident time / window (user-reported): 2026-09-13 around 10:00 UTC", summary)
        report["captured_at"] = "private-customer-window"
        self.assertNotIn("private-customer-window", support_summary(report))

    def test_fact_hypothesis_and_performed_action_remain_separate(self):
        report = build_report({}, [{"code": "INTERNAL_PANIC", "severity": "warning", "recommendation": "Restart", "summary": "Crash"}])
        summary = support_summary(report, {
            "problem_type": "troubleshooting", "confirmed_facts": "The supplied excerpt records a panic after a YAML parse failure.",
            "hypotheses": "The invalid configuration may explain startup failure; not yet verified.",
            "key_evidence": "Reviewed excerpt: yaml: line 5: did not find expected key",
            "remaining_questions": "Has the owner corrected the file and verified startup?",
        })
        self.assertIn("Hypotheses (not established causes):\nThe invalid configuration", summary)
        self.assertIn("Actions already performed by the user:\nNot provided", summary)
        self.assertNotIn("\nRestart", summary)
        self.assertIn("yaml: line 5", summary)

    def test_expanded_evidence_does_not_claim_user_fixed_anything(self):
        old = {"findings": [{"code": "INTERNAL_PANIC", "summary": "private-resource"}]}
        report = build_report({}, [], old)
        summary = support_summary(report, {"recheck_status": "evidence_expanded", "actions_already_performed": "Only a longer existing local log excerpt was supplied. No changes were made."})
        self.assertIn("Evidence expanded only", summary)
        self.assertIn("not_observed_now: INTERNAL_PANIC", summary)
        self.assertIn("not verified resolution", summary)
        self.assertNotIn("private-resource", summary)

    def test_explicit_after_change_recheck_remains_not_a_recovery_claim(self):
        summary = support_summary(build_report({}, []), {"recheck_status": "after_user_change", "actions_already_performed": "The user corrected the application serialization."})
        self.assertIn("After a user-reported change", summary)
        self.assertIn("original symptom recovery still requires explicit verification", summary)

    def test_additional_information_is_optional_redacted_and_after_summary(self):
        report = build_report({}, [])
        self.assertNotIn("Additional information (optional):", support_summary(report))
        context = {"additional_information": "Launch next week; token=synthetic-secret user@example.com"}
        summary = support_summary(report, context)
        self.assertIn("Launch next week", summary)
        self.assertNotIn("synthetic-secret", summary)
        self.assertNotIn("user@example.com", summary)
        self.assertGreater(summary.index("Additional information (optional):"), summary.index("Doctor performed read-only checks only"))
        draft = support_draft(report, context)
        self.assertTrue(draft.endswith(summary))
        self.assertIn("optionally append", draft)

    def test_context_is_strictly_typed_and_bounded(self):
        invalid = (
            {"company": "Should use form"}, {"app_sdk_version": "private-build"},
            {"app_sdk_name": "private-company-library"}, {"deployment_method": "baremetal"},
            {"problem_type": "anything"}, {"recheck_status": "fixed"},
            {"key_evidence": ["not a string"]}, {"incident_time": "x" * 2001},
            {"confirmed_facts": "文" * 2000, "hypotheses": "文" * 2000, "key_evidence": "文" * 2000},
        )
        for context in invalid:
            with self.subTest(context_keys=list(context)), self.assertRaises(ValueError):
                validate_support_context(context)
        self.assertEqual(validate_support_context({"milvus_version": "unknown", "key_evidence": " "}), {"milvus_version": "unknown", "key_evidence": ""})

    def test_reviewed_context_still_redacts_multiline_credentials(self):
        for raw in (
            'Error excerpt\ntoken="first-secret-line\nsecond-secret-line"\nSafe next line',
            "Error excerpt\n-----BEGIN PRIVATE KEY-----\nfirst-secret-line\nsecond-secret-line",
            "Error excerpt\npassword: |\n  first-secret-line\n  second-secret-line\nSafe next line",
        ):
            summary = support_summary(build_report({}, []), {"key_evidence": raw})
            self.assertNotIn("first-secret-line", summary)
            self.assertNotIn("second-secret-line", summary)
            self.assertIn("Error excerpt", summary)
            self.assertIn("Assistance requested:", summary)

    def test_summary_prioritizes_abnormal_container_and_pod_facts(self):
        containers = [{"name": "healthy-" + str(index), "state": "running"} for index in range(4)]
        containers.append({"name": "unhealthy-last", "state": "exited", "exit_code": 134})
        pods = [{"status": {"phase": "Running"}} for _ in range(4)]
        pods.append({"status": {"phase": "Pending"}})
        report = build_report(complete_snapshot(docker={"containers": containers}, kubernetes={"pods": pods}), [])
        summary = support_summary(report)
        self.assertIn("container-5: exit_code=134", summary)
        self.assertIn("pod-5: phase=Pending", summary)

    def test_canonical_sources_omit_identifiers_and_error_text(self):
        report = build_report({"sources": [
            {"name": "milvus.collection.1.schema", "status": "ok"},
            {"name": "milvus.collection.1.statistics", "status": "error", "detail": "PRIVATE-ERROR"},
            {"name": "logs.PRIVATE-FILE", "status": "ok"},
        ]}, [])
        summary = support_summary(report)
        self.assertIn("milvus.collection.schema: ok", summary)
        self.assertIn("milvus.collection.statistics: error", summary)
        self.assertIn("logs: ok", summary)
        self.assertNotIn("PRIVATE-", summary)


if __name__ == "__main__":
    unittest.main()
