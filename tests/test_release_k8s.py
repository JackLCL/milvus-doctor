"""Release regressions from real Helm and Operator test environments."""
import json
import subprocess
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib import collectors as c
from doctorlib.safety import validate_command


def args(**kwargs):
    return Namespace(**kwargs)


def response(argv, items, code=0):
    return subprocess.CompletedProcess(argv, code, json.dumps({"items": items}), "Forbidden" if code else "")


def pod(name):
    return {"metadata": {"name": name, "namespace": "selected"}, "status": {"phase": "Running"}}


class ReleaseKubernetesTest(unittest.TestCase):
    def test_helm_both_labels_merged_deduplicated_and_extra_selector_preserved(self):
        calls = []
        def runner(argv, **kwargs):
            validate_command(argv)
            calls.append(argv)
            selector = argv[argv.index("--selector") + 1]
            self.assertEqual(argv[1:5], ["--context", "reader", "--namespace", "selected"])
            self.assertTrue(selector.startswith("component=selected,"))
            self.assertNotIn(argv[6], {"events", "secrets"})
            if argv[6] == "pods":
                return response(argv, [pod("shared"), pod("minio" if selector.endswith(",release=test") else "proxy")])
            return response(argv, [])
        with patch.object(c, "run_readonly", side_effect=runner):
            result = c.collect(args(deployment="helm", context="reader", namespace="selected", release="test", selector="component=selected"))
        self.assertEqual(len(calls), 10)
        self.assertEqual({p["metadata"]["name"] for p in result["kubernetes"]["pods"]}, {"shared", "proxy", "minio"})
        self.assertEqual(len(result["kubernetes"]["pods"]), 3)
        self.assertFalse(any(s.get("reason") == "target_not_found" for s in result["sources"]))

    def test_denied_workload_query_is_not_reported_as_missing_target(self):
        with patch.object(c, "run_readonly", side_effect=lambda argv, **kw: response(argv, [], 1)):
            result = c.collect(args(deployment="helm", context="reader", namespace="denied", release="test"))
        self.assertTrue(any(s["status"] == "error" for s in result["sources"]))
        self.assertFalse(any(s.get("reason") == "target_not_found" for s in result["sources"]))

    def test_one_failed_label_query_does_not_establish_target_absence(self):
        def runner(argv, **kwargs):
            selector = argv[argv.index("--selector") + 1]
            return response(argv, [], int(argv[6] == "pods" and selector == "release=test"))
        with patch.object(c, "run_readonly", side_effect=runner):
            result = c.collect(args(deployment="helm", context="reader", namespace="selected", release="test"))
        self.assertFalse(any(s.get("reason") == "target_not_found" for s in result["sources"]))
        self.assertTrue(any(s["status"] == "error" for s in result["sources"]))

    def test_successful_empty_dual_queries_remain_missing_target(self):
        with patch.object(c, "run_readonly", side_effect=lambda argv, **kw: response(argv, [])):
            result = c.collect(args(deployment="helm", context="reader", namespace="selected", release="test"))
        self.assertTrue(any(s.get("reason") == "target_not_found" for s in result["sources"]))

    def test_combined_label_results_respect_total_unique_cap(self):
        def runner(argv, **kwargs):
            if argv[6] != "pods":
                return response(argv, [])
            legacy = argv[argv.index("--selector") + 1] == "release=test"
            return response(argv, [pod(("legacy" if legacy else "canonical") + str(i)) for i in range(600)])
        with patch.object(c, "run_readonly", side_effect=runner):
            result = c.collect(args(deployment="helm", context="reader", namespace="selected", release="test"))
        self.assertEqual(len(result["kubernetes"]["pods"]), 1000)
        self.assertTrue(any(s["name"] == "kubernetes.pods.limit" and s.get("reason") == "bounded_limit" for s in result["sources"]))

    def test_no_helm_release_does_not_add_second_query(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return response(argv, [pod("selected")] if argv[6] == "pods" else [])
        with patch.object(c, "run_readonly", side_effect=runner):
            c.collect(args(deployment="kubernetes", context="reader", namespace="selected", selector="app=test"))
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(call[call.index("--selector") + 1] == "app=test" for call in calls))

    def test_http_size_limit_is_explicit_and_never_retried(self):
        with patch.object(c, "_http_get", side_effect=ValueError("HTTP response exceeds configured collection size limit.")) as getter:
            result = c.collect(args(metrics_url="http://127.0.0.1:9091/metrics", max_bytes=1048576))
        self.assertEqual(getter.call_count, 1)
        source = next(s for s in result["sources"] if s["name"] == "metrics")
        self.assertEqual(source["status"], "error")
        self.assertEqual(source["reason"], "size_limit")
        self.assertIn("--max-bytes", source["detail"])
        self.assertNotIn("metrics", result)

    def test_http_timeout_is_explicit_without_exception_payload(self):
        with patch.object(c, "_http_get", side_effect=TimeoutError("token=do-not-include")) as getter:
            result = c.collect(args(metrics_url="http://127.0.0.1:9091/metrics", timeout=2))
        source = next(s for s in result["sources"] if s["name"] == "metrics")
        self.assertEqual(source["reason"], "timeout")
        self.assertIn("--timeout", source["detail"])
        self.assertEqual(getter.call_count, 1)
        self.assertNotIn("do-not-include", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
