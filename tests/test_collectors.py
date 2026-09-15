"""Behavioral tests for bounds, source coverage, target selection and sanitization."""
import json
import io
import logging
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib import collectors as c
from doctorlib.safety import validate_command


def args(**kwargs):
    return Namespace(**kwargs)


def snapshot(method="docker"):
    return {"schema_version": 1, "deployment": {"method": method, "mode": "unknown"}, "sources": []}


def completed(argv, payload=None, code=0, stderr=""):
    return subprocess.CompletedProcess(argv, code, json.dumps(payload) if payload is not None else "", stderr)


@contextmanager
def local_http_server():
    request_done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if self.path == "/header-drip":
                    for value in b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK":
                        self.connection.sendall(bytes([value]))
                        time.sleep(0.1)
                else:
                    payload = b"x" * 15 if self.path == "/body-drip" else b"x" * 2048
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    if self.path == "/body-drip":
                        for value in payload:
                            self.wfile.write(bytes([value]))
                            self.wfile.flush()
                            time.sleep(0.2)
                    else:
                        self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                request_done.set()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", request_done
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class CollectorsTest(unittest.TestCase):
    def test_no_target_does_not_scan_machine(self):
        with patch.object(c, "run_readonly") as runner:
            result = c.collect(args())
        runner.assert_not_called()
        self.assertTrue(result["sources"])
        self.assertTrue(all(source["status"] == "skipped" for source in result["sources"]))

    def test_explicit_docker_is_sanitized_and_stats_are_parsed(self):
        raw = [{"Name": "/milvus", "RestartCount": 4,
                "Config": {"Image": "milvusdb/milvus:v2.6.0", "Cmd": ["milvus", "run", "standalone", "password=do-not-print"], "Env": ["TOKEN=do-not-print"], "Labels": {"com.docker.compose.project": "demo", "customer-token": "do-not-print"}},
                "State": {"Status": "running", "OOMKilled": True, "Health": {"Status": "unhealthy", "Log": [{"Output": "do-not-print"}]}},
                "HostConfig": {"Memory": 2147483648, "Binds": ["/do-not-print:/secret"]},
                "NetworkSettings": {"Ports": {"19530/tcp": [{"HostIp": "0.0.0.0", "HostPort": "19530"}]}}}]
        calls = []
        def runner(argv, **kwargs):
            validate_command(argv)
            calls.append(argv)
            if argv[1] == "inspect":
                return completed(argv, raw)
            return subprocess.CompletedProcess(argv, 0, '{"Name":"milvus","MemUsage":"1.5GiB / 2GiB"}\n', "")
        with patch.object(c, "run_readonly", side_effect=runner):
            result = c.collect(args(deployment="docker", container=["milvus"]))
        container = result["docker"]["containers"][0]
        self.assertEqual(container["memory_usage_bytes"], 1610612736)
        self.assertEqual(result["deployment"]["mode"], "standalone")
        self.assertNotIn("do-not-print", json.dumps(result))
        self.assertIn("--type", calls[0])
        self.assertEqual(calls[0][-1], "milvus")

    def test_compose_discovery_is_project_scoped(self):
        calls = []
        def runner(argv, **kwargs):
            validate_command(argv)
            calls.append(argv)
            if argv[1] == "ps":
                return subprocess.CompletedProcess(argv, 0, "abc123456789\n", "")
            return completed(argv, []) if argv[1] == "inspect" else subprocess.CompletedProcess(argv, 0, "", "")
        with patch.object(c, "run_readonly", side_effect=runner):
            c.collect(args(deployment="compose", compose_project="my-milvus"))
        self.assertIn("label=com.docker.compose.project=my-milvus", calls[0])
        self.assertEqual(calls[1][-1], "abc123456789")

    def test_failure_and_timeout_remain_missing_evidence(self):
        for error in (TimeoutError("password=secret-value"), ValueError("limit")):
            with self.subTest(error=type(error).__name__), patch.object(c, "run_readonly", side_effect=error):
                result = c.collect(args(deployment="docker", container=["milvus"]))
            self.assertEqual(result["docker"]["containers"], [])
            self.assertTrue(any(s["status"] == "error" for s in result["sources"]))
            self.assertNotIn("secret-value", json.dumps(result))

    def test_kubernetes_requires_both_context_and_namespace(self):
        with patch.object(c, "run_readonly") as runner:
            result = c.collect(args(deployment="kubernetes", namespace="default"))
        runner.assert_not_called()
        self.assertEqual(result["sources"][0]["status"], "skipped")

    def test_kubernetes_namespace_scope_partial_rbac_and_helm_labels(self):
        calls = []
        def runner(argv, **kwargs):
            validate_command(argv)
            calls.append(argv)
            self.assertEqual(argv[1:5], ["--context", "dev-cluster", "--namespace", "milvus-test"])
            kind = argv[6]
            if kind == "pods":
                return completed(argv, {"items": [{"metadata": {"name": "querynode", "labels": {"app.kubernetes.io/managed-by": "Helm", "app.kubernetes.io/instance": "my-release", "app.kubernetes.io/component": "querynode"}}, "spec": {"containers": [{"name": "querynode", "image": "milvus:v2", "env": [{"name": "SECRET", "value": "never-show"}]}]}, "status": {"phase": "Running"}}]})
            if kind == "events":
                return completed(argv, code=1, stderr="Forbidden: missing read permission")
            return completed(argv, {"items": []})
        with patch.object(c, "run_readonly", side_effect=runner):
            result = c.collect(args(deployment="kubernetes", context="dev-cluster", namespace="milvus-test"))
        self.assertEqual(result["deployment"]["method"], "helm")
        self.assertEqual(result["deployment"]["mode"], "cluster")
        self.assertEqual(result["deployment"]["provenance"], "workload_labels")
        self.assertEqual(result["kubernetes"]["helm_releases"][0]["name"], "my-release")
        self.assertNotIn("never-show", json.dumps(result))
        self.assertTrue(any(s["name"] == "kubernetes.events" and s["status"] == "error" for s in result["sources"]))
        self.assertTrue(all(call[0] == "kubectl" for call in calls))
        self.assertTrue(all("secrets" not in call and "exec" not in call and "logs" not in call for call in calls))

    def test_release_filter_does_not_claim_event_coverage(self):
        calls = []
        def runner(argv, **kwargs):
            validate_command(argv)
            calls.append(argv)
            return completed(argv, {"items": []})
        with patch.object(c, "run_readonly", side_effect=runner):
            result = c.collect(args(deployment="helm", context="dev", namespace="ns", release="selected"))
        selectors = {call[call.index("--selector") + 1] for call in calls}
        self.assertEqual(selectors, {"app.kubernetes.io/instance=selected", "release=selected"})
        self.assertTrue(any(s["name"] == "kubernetes.events" and s["status"] == "skipped" for s in result["sources"]))

    def test_operator_named_cr_keeps_diagnostic_state_only(self):
        calls = []
        def runner(argv, **kwargs):
            validate_command(argv)
            calls.append(argv)
            if "milvuses.milvus.io" in argv:
                self.assertIn("selected-cr", argv)
                return completed(argv, {"kind": "Milvus", "metadata": {"name": "selected-cr"}, "spec": {"mode": "standalone", "config": {"minio": {"accessKeyID": "never-show", "bucketName": "milvus"}}, "dependencies": {"storage": {"type": "MinIO", "secretRef": "never-show"}}}, "status": {"status": "Unhealthy", "conditions": [{"type": "Ready", "status": "False", "reason": "DependencyNotReady"}]}})
            return completed(argv, {"items": []})
        with patch.object(c, "run_readonly", side_effect=runner):
            result = c.collect(args(deployment="operator", context="dev", namespace="ns", operator_name="selected-cr"))
        self.assertEqual(result["deployment"]["mode"], "standalone")
        self.assertEqual(result["kubernetes"]["milvuses"][0]["status"]["status"], "Unhealthy")
        self.assertNotIn("never-show", json.dumps(result))

    def test_pod_fields_retain_actionable_reasons_without_env(self):
        result = c.sanitize_kubernetes_resource({"kind": "Pod", "metadata": {"name": "querynode", "annotations": {"secret": "never-show"}}, "spec": {"containers": [{"name": "node", "args": ["never-show"], "envFrom": [{"secretRef": {"name": "never-show"}}], "resources": {"limits": {"memory": "2Gi"}}, "readinessProbe": {"httpGet": {"path": "/healthz", "port": 9091, "httpHeaders": [{"name": "Authorization", "value": "never-show"}]}}}]}, "status": {"containerStatuses": [{"name": "node", "restartCount": 8, "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}]}})
        self.assertEqual(result["status"]["containerStatuses"][0]["lastState"]["terminated"]["reason"], "OOMKilled")
        self.assertNotIn("never-show", json.dumps(result))
        self.assertEqual(c.sanitize_kubernetes_resource({"kind": "Secret", "data": {"token": "never-show"}}), {})

    def test_manifest_compose_helm_and_config_allowlists(self):
        compose = c.sanitize_manifest({"services": {"standalone": {"image": "milvus:v2", "command": ["milvus", "run", "standalone", "never-show"], "environment": {"TOKEN": "never-show"}, "deploy": {"resources": {"limits": {"memory": "2G"}}}, "healthcheck": {"test": ["CMD", "curl", "never-show"], "retries": 3}, "depends_on": {"etcd": {"condition": "service_healthy"}}}}})
        self.assertEqual(compose["services"]["standalone"]["role"], "standalone")
        self.assertEqual(compose["services"]["standalone"]["depends_on"], {"etcd": {"condition": "service_healthy"}})
        self.assertNotIn("never-show", json.dumps(compose))
        config = c.sanitize_config({"minio": {"address": "minio:9000", "accessKeyID": "never-show", "secretAccessKey": "never-show"}, "etcd": {"endpoints": ["etcd:2379"]}, "unrelated": "never-show"})
        self.assertEqual(config["etcd"]["endpoints"], ["etcd:2379"])
        self.assertNotIn("never-show", json.dumps(config))
        helm = c.sanitize_manifest({"cluster": {"enabled": True}, "queryNode": {"replicas": 2, "resources": {"limits": {"memory": "4Gi"}}, "env": {"TOKEN": "never-show"}}})
        self.assertTrue(helm["cluster"]["enabled"])
        self.assertEqual(helm["queryNode"]["replicas"], 2)
        self.assertNotIn("never-show", json.dumps(helm))

    def test_local_files_are_bounded_and_logs_explicitly_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compose.json").write_text('{"services":{"standalone":{"image":"milvus:v2"}}}')
            (root / "selected.log").write_text("OOMKilled password=never-show\n")
            (root / "large.log").write_text("x" * 2049)
            result = c.collect(args(manifest=[str(root / "compose.json")], log_file=[str(root / "selected.log"), str(root / "large.log")], max_bytes=2048))
        self.assertEqual(len(result["manifests"]), 1)
        self.assertEqual(len(result["logs"]), 1)
        self.assertIn("OOMKilled", result["logs"][0])
        self.assertNotIn("never-show", json.dumps(result))
        self.assertTrue(any(s["name"] == "logs.1" and s["status"] == "error" for s in result["sources"]))

    def test_metrics_strip_labels_and_non_finite_samples(self):
        result = c.parse_metrics('# HELP ignored\nmilvus_query_latency_sum{collection="private"} 12.5\nmilvus_query_latency_count{collection="private"} 2\ngo_threads 9\ngo_bad NaN\nuser_private_secret 12\n')
        self.assertEqual(result["milvus_query_latency_sum"], [12.5])
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("go_bad", result)

    def test_unknown_http_body_is_not_healthy(self):
        for body, expected in (("OK", "ok"), ('{"isHealthy":true}', "ok"), ('{"isHealthy":false}', "error"), ("<html>login</html>", "error")):
            with self.subTest(body=body), patch.object(c, "_http_get", return_value=(200, body)):
                result = c.collect(args(health_url="http://localhost:9091/healthz"))
            self.assertEqual(result["health"]["status"], expected)

    def test_http_rejects_credentials_query_and_non_http(self):
        for url in ("http://user:secret@example.com/healthz", "http://example.com/healthz?token=secret", "file:///etc/passwd"):
            with self.subTest(url=url), patch.object(c.urllib.request, "build_opener") as opener:
                with self.assertRaises(ValueError):
                    c._http_get(args(), url)
                opener.assert_not_called()

    def test_sdk_calls_metadata_only_and_limits_collections(self):
        client = MagicMock()
        client.get_server_version.return_value = "2.6.0"
        client.list_collections.return_value = ["first", "second", "third"]
        client.describe_collection.return_value = {"fields": [{"name": "vector", "type": 101, "params": {"dim": 128, "secret": "never-show"}, "default_value": "never-show"}]}
        client.get_collection_stats.return_value = {"row_count": "42"}
        client.get_load_state.return_value = {"state": 3}
        client.list_indexes.return_value = ["vector_index"]
        client.describe_index.return_value = {"index_type": "HNSW", "metric_type": "COSINE", "state": "Finished", "params": {"M": 16, "secret": "never-show"}}
        module = types.SimpleNamespace(MilvusClient=MagicMock(return_value=client))
        with patch.dict(sys.modules, {"pymilvus": module}):
            result = c.collect(args(endpoint="http://localhost:19530", collection_limit=1))
        self.assertEqual(len(result["milvus"]["collections"]), 1)
        self.assertEqual(result["milvus"]["collections"][0]["row_count"], 42)
        self.assertTrue(result["milvus"]["collections"][0]["loaded"])
        self.assertTrue(any(s["name"] == "milvus.collections.limit" and s["status"] == "skipped" for s in result["sources"]))
        self.assertNotIn("never-show", json.dumps(result))
        allowed = {"get_server_version", "list_collections", "describe_collection", "get_collection_stats", "get_load_state", "list_indexes", "describe_index", "close"}
        self.assertTrue(all(call[0] in allowed for call in client.mock_calls))
        client.close.assert_called_once()

    def test_sdk_permission_failure_preserves_other_evidence(self):
        client = MagicMock()
        client.get_server_version.return_value = "2.6.0"
        client.list_collections.return_value = ["test"]
        client.describe_collection.side_effect = RuntimeError("authorization=never-show")
        client.get_collection_stats.return_value = {"row_count": "3"}
        client.get_load_state.side_effect = RuntimeError("forbidden")
        client.list_indexes.side_effect = RuntimeError("forbidden")
        with patch.dict(sys.modules, {"pymilvus": types.SimpleNamespace(MilvusClient=MagicMock(return_value=client))}):
            result = c.collect(args(endpoint="http://localhost:19530"))
        self.assertEqual(result["milvus"]["collections"][0]["row_count"], 3)
        self.assertNotIn("loaded", result["milvus"]["collections"][0])
        self.assertNotIn("never-show", json.dumps(result))

    def test_native_pid_scans_only_counters(self):
        def runner(argv, **kwargs):
            validate_command(argv)
            self.assertEqual(argv, ["ps", "-p", "123", "-o", "pid=,comm=,pcpu=,pmem=,rss=,vsz="])
            return subprocess.CompletedProcess(argv, 0, "123 milvus 2.0 3.0 4096 8192\n", "")
        with patch.object(c, "run_readonly", side_effect=runner):
            result = c.collect(args(pid=123))
        self.assertEqual(result["native"]["rss_bytes"], 4194304)

    def test_index_description_failure_and_loading_do_not_imply_absence(self):
        client = MagicMock()
        client.get_server_version.return_value = "2.6.0"
        client.list_collections.return_value = ["test"]
        client.describe_collection.return_value = {"fields": []}
        client.get_collection_stats.return_value = {"row_count": 3}
        client.get_load_state.return_value = {"state": 2}
        client.list_indexes.return_value = ["existing_index"]
        client.describe_index.side_effect = RuntimeError("permission denied")
        with patch.dict(sys.modules, {"pymilvus": types.SimpleNamespace(MilvusClient=MagicMock(return_value=client))}):
            result = c.collect(args(endpoint="http://localhost:19530"))
        collection = result["milvus"]["collections"][0]
        self.assertNotIn("indexes", collection)
        self.assertFalse(collection["indexes_complete"])
        self.assertIsNone(collection["loaded"])
        self.assertIn("permission denied", " ".join(s["detail"] for s in result["sources"]))

    def test_default_missing_token_allows_anonymous_metadata(self):
        client = MagicMock()
        client.get_server_version.return_value = "2.6.0"
        client.list_collections.return_value = []
        constructor = MagicMock(return_value=client)
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"pymilvus": types.SimpleNamespace(MilvusClient=constructor)}):
            result = c.collect(args(endpoint="http://localhost:19530", token_env="MILVUS_DOCTOR_TOKEN"))
        constructor.assert_called_once()
        self.assertNotIn("token", constructor.call_args.kwargs)
        self.assertEqual(result["milvus"]["version"], "2.6.0")

    def test_error_classification_never_echoes_exception_payload(self):
        for message, expected in (("connection refused password=never-show", "Connection refused"), ("statuscode.permission_denied never-show", "permission denied"), ("certificate verify failed never-show", "TLS handshake failure"), ("deadline exceeded never-show", "Connection timeout")):
            detail = c._error_detail(RuntimeError(message), "failed")
            self.assertIn(expected, detail)
            self.assertNotIn("never-show", detail)

    def test_selected_disk_reports_capacity_without_listing_files(self):
        usage = types.SimpleNamespace(total=100, used=95, free=5)
        with patch.object(Path, "is_dir", return_value=True), patch.object(c.shutil, "disk_usage", return_value=usage) as disk, patch.object(Path, "iterdir", side_effect=AssertionError("must not list files")):
            result = c.collect(args(data_dir="/selected-milvus-volume"))
        disk.assert_called_once_with(Path("/selected-milvus-volume"))
        self.assertEqual(result["disk"], {"total_bytes": 100, "used_bytes": 95, "free_bytes": 5})

    def test_redirects_are_not_followed(self):
        handler = c._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "http://other-host/"))

    def test_sdk_credentials_never_reach_http_targets(self):
        response = MagicMock()
        response.status = 200
        response.read1.side_effect = [b"OK", b""]
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        with patch.dict(os.environ, {"MILVUS_DOCTOR_TOKEN": "sdk-only-secret"}), patch.object(c.urllib.request, "build_opener", return_value=opener):
            status, body = c._http_get(args(endpoint="http://database:19530", token_env="MILVUS_DOCTOR_TOKEN"), "http://other-host:9091/healthz")
        request = opener.open.call_args.args[0]
        self.assertEqual(status, 200)
        self.assertEqual(body, "OK")
        self.assertFalse(request.has_header("Authorization"))
        self.assertNotIn("sdk-only-secret", str(request.headers))

    def test_malformed_manifest_is_coverage_error_not_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "malformed.json"
            selected.write_text('{"kind":"Pod","spec":{"containers":null}}')
            result = c.collect(args(manifest=[str(selected)]))
        self.assertEqual(result["manifests"], [])
        self.assertTrue(any(source["name"] == "manifest.0" and source["status"] == "error" for source in result["sources"]))

    def test_scalar_allowlist_cannot_smuggle_nested_payloads(self):
        pod = c.sanitize_kubernetes_resource({"kind": "Pod", "metadata": {"name": {"unexpected": "never-show"}}, "spec": {"containers": [{"image": {"unexpected": "never-show"}, "resources": {"limits": {"memory": {"unexpected": "never-show"}}}}]}})
        self.assertNotIn("never-show", json.dumps(pod))

    def test_sdk_error_logging_is_suppressed_then_restored(self):
        output = io.StringIO()
        logger = logging.getLogger("pymilvus.milvus_client")
        handler = logging.StreamHandler(output)
        original = logger.disabled, logger.handlers, logger.propagate, logger.level
        logger.disabled, logger.handlers, logger.propagate, logger.level = False, [handler], False, logging.DEBUG
        def constructor(**kwargs):
            logger.error("RPC failed with token=never-show")
            raise RuntimeError("permission denied token=never-show")
        try:
            with patch.dict(sys.modules, {"pymilvus": types.SimpleNamespace(MilvusClient=constructor)}):
                result = c.collect(args(endpoint="http://localhost:19530"))
            self.assertEqual(output.getvalue(), "")
            self.assertNotIn("never-show", json.dumps(result))
            self.assertFalse(logger.disabled)
            self.assertEqual(logger.handlers, [handler])
            logger.info("restored")
            self.assertIn("restored", output.getvalue())
        finally:
            logger.disabled, logger.handlers, logger.propagate, logger.level = original

    def test_real_http_body_drip_obeys_total_deadline(self):
        with local_http_server() as (url, request_done):
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                c._http_get(args(timeout=1), url + "/body-drip")
            self.assertLess(time.monotonic() - started, 1.75)
            self.assertTrue(request_done.wait(0.8), "Timed out request socket was not interrupted")

    def test_real_http_header_drip_obeys_total_deadline(self):
        with local_http_server() as (url, request_done):
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                c._http_get(args(timeout=1), url + "/header-drip")
            self.assertLess(time.monotonic() - started, 1.75)
            self.assertTrue(request_done.wait(0.8), "Timed out header socket was not interrupted")

    def test_real_http_enforces_response_size_limit(self):
        with local_http_server() as (url, _):
            with self.assertRaisesRegex(ValueError, "size limit"):
                c._http_get(args(timeout=1, max_bytes=1024), url + "/large")
            status, body = c._http_get(args(timeout=1, max_bytes=2048), url + "/exact-limit")
            self.assertEqual(status, 200)
            self.assertEqual(len(body), 2048)


if __name__ == "__main__":
    unittest.main()
