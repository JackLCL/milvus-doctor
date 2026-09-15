"""Independent release checks: selected scope is never silently discarded."""
from argparse import Namespace
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib import collectors
from doctorlib.cli import main, parser, validate_args
from doctorlib.evidence import build_evidence, sanitize_evidence
from doctorlib.reports import build_report
from doctorlib.rules import evaluate


def repeated(option, count):
    return [part for index in range(count) for part in (option, "selected_{}".format(index))]


def snapshot(method="unknown"):
    return {"schema_version": 1, "deployment": {"method": method, "mode": "unknown"}, "sources": []}


class ArgumentScopeTests(unittest.TestCase):
    def rejected_before_io(self, options):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch("doctorlib.collectors.collect", side_effect=AssertionError("collection before validation")), \
                patch("doctorlib.cli.load_json", side_effect=AssertionError("file read before validation")):
            code = main(["diagnose"] + options)
        self.assertEqual(code, 2, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(json.loads(stderr.getvalue())["read_only"])

    def test_101_container_or_collection_selections_rejected(self):
        for option in ("--container", "--collection"):
            with self.subTest(option=option):
                self.rejected_before_io(["--endpoint", "http://example.invalid:19530"] + repeated(option, 101))

    def test_21_manifest_or_log_selections_rejected(self):
        for option in ("--manifest", "--log-file"):
            with self.subTest(option=option):
                self.rejected_before_io(repeated(option, 21))

    def test_exact_selection_limits_remain_valid(self):
        for option, limit in (("--container", 100), ("--collection", 100), ("--manifest", 20), ("--log-file", 20)):
            with self.subTest(option=option):
                options = repeated(option, limit)
                if option == "--collection":
                    options += ["--endpoint", "http://example.invalid:19530", "--collection-limit", "100"]
                validate_args(parser().parse_args(["diagnose"] + options))

    def test_docker_kubernetes_and_pid_conflicts_rejected(self):
        kube = ["--context", "selected-context", "--namespace", "selected-namespace"]
        cases = (
            ["--container", "selected"] + kube,
            ["--compose-project", "selected"] + kube,
            ["--container", "selected", "--pid", "123"],
            kube + ["--pid", "123"],
            ["--deployment", "native", "--container", "selected"],
            ["--deployment", "native", "--compose-project", "selected"],
            ["--deployment", "native"] + kube,
            ["--deployment", "docker"] + kube,
            ["--deployment", "kubernetes", "--container", "selected"] + kube,
            ["--deployment", "helm", "--pid", "123"] + kube,
            kube + ["--release", "selected-release", "--operator-name", "selected-cr"],
        )
        for options in cases:
            with self.subTest(options=options):
                self.rejected_before_io(options)

    def test_kubernetes_selection_flags_need_complete_context_and_namespace(self):
        for option in ("--release", "--operator-name", "--selector"):
            for incomplete in ([], ["--context", "selected-context"], ["--namespace", "selected-namespace"]):
                with self.subTest(option=option, incomplete=incomplete):
                    self.rejected_before_io([option, "selected"] + incomplete)

    def test_snapshot_cannot_silently_ignore_deployment_or_live_selection(self):
        cases = (
            ["--deployment", "docker"], ["--deployment", "native"],
            ["--mode", "cluster"], ["--mode", "standalone"],
            ["--database", "selected_database"], ["--collection-limit", "1"],
            ["--container", "selected"], ["--compose-project", "selected"],
            ["--context", "selected"], ["--namespace", "selected"],
            ["--release", "selected"], ["--operator-name", "selected"],
            ["--selector", "app=selected"], ["--pid", "123"], ["--data-dir", "/selected"],
            ["--endpoint", "http://example.invalid:19530"],
            ["--health-url", "http://example.invalid/healthz"],
            ["--metrics-url", "http://example.invalid/metrics"],
            ["--manifest", "selected.yaml"], ["--config-file", "selected.yaml"],
            ["--log-file", "selected.log"],
            ["--endpoint", "http://example.invalid:19530", "--collection", "selected"],
        )
        for options in cases:
            with self.subTest(options=options):
                self.rejected_before_io(["--snapshot", "not-opened.json"] + options)

    def test_independent_allowed_scope_combinations_and_snapshot_outputs(self):
        cases = (
            ["--snapshot", "offline.json", "--previous", "prior.json", "--max-bytes", "2048", "--output-dir", "fresh", "--format", "json"],
            ["--deployment", "docker", "--container", "selected", "--endpoint", "http://example.invalid:19530"],
            ["--deployment", "native", "--pid", "123", "--endpoint", "http://example.invalid:19530"],
            ["--context", "selected", "--namespace", "selected", "--endpoint", "http://example.invalid:19530", "--log-file", "selected.log"],
            ["--manifest", "selected.yaml", "--config-file", "selected-config.yaml"],
        )
        for options in cases:
            with self.subTest(options=options):
                validate_args(parser().parse_args(["diagnose"] + options))


class CollectorScopeTests(unittest.TestCase):
    def test_actionable_guidance_for_requested_skipped_sources(self):
        from doctorlib.rules import evaluate
        for reason, expected in (("target_not_found", "No workload matched a requested target"), ("bounded_limit", "Requested evidence reached a collection limit"), ("dependency_missing", "A requested check lacks a local capability")):
            result = evaluate({"schema_version": 1, "sources": [{"name": "kubernetes.pods", "status": "ok"}, {"name": "selected", "status": "skipped", "reason": reason}]})
            finding = next(item for item in result if item["code"] == "COVERAGE_INCOMPLETE")
            self.assertIn(expected, finding["recommendation"])
        for malformed in ({}, [], None):
            self.assertTrue(evaluate({"schema_version": 1, "sources": [{"name": "selected", "status": "skipped", "reason": malformed}]}))

    def assert_gap(self, selected, name):
        matches = [source for source in selected["sources"] if source["name"] == name]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["status"], "skipped")
        self.assertEqual(matches[0]["reason"], "bounded_limit")
        report = build_report(selected, evaluate(selected))
        self.assertFalse(report["coverage"]["complete_for_selected_scope"])
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("COVERAGE_INCOMPLETE", [finding["code"] for finding in report["findings"]])

    def docker_runner(self, argv, **kwargs):
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, "\n".join("{:012x}".format(n) for n in range(101)), "")
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 0, json.dumps([
                {"Name": "/" + name, "Config": {}, "State": {"Status": "running"}}
                for name in argv[4:]]), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def test_direct_container_and_compose_inventory_truncation_stays_visible(self):
        for args in (Namespace(container=["selected_{}".format(n) for n in range(101)]),
                     Namespace(compose_project="selected")):
            with self.subTest(args=args):
                selected = snapshot("docker")
                with patch.object(collectors, "run_readonly", side_effect=self.docker_runner) as runner:
                    collectors.collect_docker(args, selected)
                self.assertEqual(len(selected["docker"]["containers"]), 100)
                for call in runner.call_args_list:
                    argv = call.args[0]
                    if argv[1] == "inspect":
                        self.assertEqual(len(argv[4:]), 100)
                self.assert_gap(selected, "docker.targets.limit")

    def test_explicit_named_collection_limit_never_inventories_other_collections(self):
        client = MagicMock()
        client.list_collections.side_effect = AssertionError("unselected collection inventory")
        client.get_server_version.return_value = "2.6.17"
        client.describe_collection.return_value = {"fields": []}
        client.get_collection_stats.return_value = {"row_count": "0"}
        client.get_load_state.return_value = {"state": 3}
        client.list_indexes.return_value = []
        module = types.SimpleNamespace(MilvusClient=MagicMock(return_value=client), __version__="2.6.17")
        args = Namespace(endpoint="http://example.invalid:19530", collection=["selected_{}".format(n) for n in range(101)], collection_limit=100)
        selected = snapshot()
        with patch.dict(sys.modules, {"pymilvus": module}), patch.dict(os.environ, {"MILVUS_DOCTOR_TOKEN": ""}):
            collectors.collect_milvus(args, selected)
        self.assertEqual(len(selected["milvus"]["collections"]), 100)
        self.assertEqual(client.describe_collection.call_count, 100)
        self.assertNotIn("selected_100", [call.kwargs["collection_name"] for call in client.describe_collection.call_args_list])
        client.list_collections.assert_not_called()
        self.assert_gap(selected, "milvus.collections.limit")

    def test_direct_manifest_and_log_file_truncation_is_explicit(self):
        for field, name in (("manifest", "manifest.limit"), ("log_file", "logs.limit")):
            args = Namespace(**{field: ["selected_{}".format(n) for n in range(21)]})
            selected = snapshot()
            with patch.object(collectors, "_read_file", return_value='{"services":{"standalone":{"image":"milvus:v2"}}}') as reader:
                collectors.collect_files(args, selected)
            self.assertEqual(reader.call_count, 20)
            self.assertNotIn("selected_20", [call.args[0] for call in reader.call_args_list])
            self.assert_gap(selected, name)

    def test_multidocument_manifest_and_config_truncation_is_explicit(self):
        cases = (
            (Namespace(manifest=["selected.yaml"]), [{"services": {"standalone": {"image": "milvus:v2"}}}] * 101, "manifest.0.documents.limit", "manifests", 100),
            (Namespace(config_file="selected.yaml"), [{"proxy": {"port": 19530}}, {"proxy": {"port": 70000}}], "config.documents.limit", None, None),
        )
        for args, documents, name, field, expected_count in cases:
            with self.subTest(name=name):
                selected = snapshot()
                with patch.object(collectors, "_read_file", return_value="reviewed data"), patch.object(collectors, "_parse_documents", return_value=documents):
                    collectors.collect_files(args, selected)
                if field:
                    self.assertEqual(len(selected[field]), expected_count)
                else:
                    self.assertEqual(selected["config"]["proxy"]["port"], 19530)
                self.assert_gap(selected, name)

    def test_limit_and_timeout_reasons_survive_safe_evidence_roundtrip(self):
        for reason in ("bounded_limit", "size_limit", "timeout", "target_not_found"):
            source = {"name": "kubernetes.pods", "status": "error", "reason": reason,
                      "observed_at": "2026-09-13T13:00:00Z"}
            result = build_evidence({"sources": [source]})
            self.assertEqual(result["sources"], [source])
            self.assertEqual(sanitize_evidence(result), result)


if __name__ == "__main__":
    unittest.main()
