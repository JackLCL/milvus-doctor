"""Behavior/negative tests for deterministic diagnosis and cautious routing."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib.rules import evaluate


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def snapshot(**kwargs):
    return {"schema_version": 1, "sources": [{"name": "test", "status": "ok"}], **kwargs}


def codes(value):
    return {finding["code"] for finding in evaluate(value)}


class RulesTests(unittest.TestCase):
    def test_docker_standalone_observations(self):
        result = codes(fixture("rules-docker-standalone.json"))
        self.assertTrue({"DOCKER_NOT_RUNNING", "DOCKER_UNHEALTHY", "DOCKER_OOM_KILLED", "DOCKER_RESTART_HISTORY", "DOCKER_MEMORY_PRESSURE", "COLLECTION_NOT_LOADED", "COLLECTION_NO_VECTOR_INDEX"} <= result)

    def test_kubernetes_cluster_and_operator(self):
        result = codes(fixture("rules-kubernetes-cluster.json"))
        self.assertTrue({"K8S_POD_PENDING", "K8S_IMAGE_PULL_FAILURE", "K8S_CRASH_LOOP", "K8S_OOM_KILLED", "K8S_POD_NOT_READY", "K8S_REPLICAS_NOT_READY", "K8S_PVC_NOT_BOUND", "K8S_SERVICE_SELECTOR_UNCONFIRMED", "K8S_PROBE_FAILURE_EVENT", "HELM_RELEASE_NOT_DEPLOYED", "OPERATOR_STATUS_STALE", "OPERATOR_NOT_READY", "RESOURCE_REQUEST_EXCEEDS_LIMIT"} <= result)

    def test_local_compose_helm_operator_and_workload(self):
        self.assertEqual(codes(fixture("rules-local-manifests.json")), {"COMPOSE_DEPENDENCY_UNDEFINED", "COMPOSE_RESERVATION_EXCEEDS_LIMIT", "HELM_CLUSTER_FLAG_TYPE", "OPERATOR_MODE_INVALID", "RESOURCE_REQUEST_EXCEEDS_LIMIT", "CONFIG_PORT_INVALID"})

    def test_complete_quiet_snapshot_has_no_fabricated_findings(self):
        value = snapshot(docker={"containers": [{"name": "milvus", "state": "running", "health": "healthy", "restart_count": 0, "memory_limit_bytes": 0, "memory_usage_bytes": 999999999999}]},
                         milvus={"collections": [{"name": "search", "loaded": True, "schema": {"fields": [{"type": "FLOAT_VECTOR", "params": {"dim": 128}}]}, "indexes": [{"index_name": "vector", "state": "Finished"}]}]})
        self.assertEqual(evaluate(value), [])

    def test_missing_index_metadata_is_not_missing_index(self):
        self.assertNotIn("COLLECTION_NO_VECTOR_INDEX", codes(snapshot(milvus={"collections": [{"name": "search", "schema": {"fields": [{"type": "FLOAT_VECTOR"}]}}]})))
        self.assertNotIn("COLLECTION_NO_VECTOR_INDEX", codes(snapshot(milvus={"collections": [{"name": "search", "indexes": [], "indexes_complete": False, "schema": {"fields": [{"type": "FLOAT_VECTOR"}]}}]})))

    def test_missing_permissions_produce_coverage_not_health(self):
        value = {"schema_version": 1, "sources": [{"name": "kubernetes", "status": "error", "detail": "permission denied"}, {"name": "milvus", "status": "skipped"}]}
        self.assertTrue({"COVERAGE_INCOMPLETE", "NO_DIAGNOSTIC_EVIDENCE", "AUTHENTICATION_FAILED"} <= codes(value))
        finding = next(item for item in evaluate(value) if item["code"] == "COVERAGE_INCOMPLETE")
        self.assertIn("Only a confirmed permission denial", finding["recommendation"])
        self.assertIn("minimum read-only permission", finding["recommendation"])

    def test_intentionally_skipped_sources_do_not_prompt_permission_expansion(self):
        value = {"schema_version": 1, "sources": [
            {"name": "kubernetes.pods", "status": "ok"},
            {"name": "kubernetes.events", "status": "skipped", "detail": "Events excluded to preserve selected release scope."},
            {"name": "metrics", "status": "skipped", "detail": "No explicit URL selected."},
        ]}
        finding = next(item for item in evaluate(value) if item["code"] == "COVERAGE_INCOMPLETE")
        self.assertEqual(finding["severity"], "info")
        self.assertIn("need no permission change", finding["recommendation"])
        self.assertIn("user explicitly chooses", finding["recommendation"])
        self.assertNotIn("requesting", finding["recommendation"])
        self.assertIn("may remain skipped", finding["verification"])
        self.assertIn("coverage remains unverified", finding["verification"])

    def test_empty_snapshot_and_unsupported_schema(self):
        self.assertIn("COVERAGE_UNDECLARED", codes({}))
        self.assertEqual(codes({"schema_version": 99}), {"SNAPSHOT_UNSUPPORTED"})
        self.assertEqual(codes([]), {"SNAPSHOT_UNSUPPORTED"})

    def test_service_mismatch_is_only_an_unconfirmed_observation(self):
        findings = evaluate(fixture("rules-kubernetes-cluster.json"))
        finding = next(f for f in findings if f["code"] == "K8S_SERVICE_SELECTOR_UNCONFIRMED")
        self.assertEqual(finding["severity"], "info")
        self.assertIn("not proof", " ".join(finding["evidence"]))

    def test_service_matching_and_externalname_do_not_warn(self):
        value = snapshot(kubernetes={"pods": [{"metadata": {"name": "proxy", "namespace": "n", "labels": {"app": "milvus"}}}], "services": [{"metadata": {"name": "milvus", "namespace": "n"}, "spec": {"selector": {"app": "milvus"}}}, {"spec": {"type": "ExternalName"}}]})
        self.assertNotIn("K8S_SERVICE_SELECTOR_UNCONFIRMED", codes(value))

    def test_idle_collection_is_not_classified_as_broken(self):
        result = evaluate(snapshot(milvus={"collections": [{"name": "idle", "loaded": False}]}))
        self.assertEqual(result[0]["severity"], "info")
        self.assertIn("intentional", " ".join(result[0]["evidence"]))

    def test_missing_fields_and_nulls_do_not_crash_or_imply_success(self):
        value = snapshot(docker={"containers": [None, {}, {"memory_limit_bytes": None}]}, kubernetes={"pods": [None, {"status": None}], "pvcs": None, "milvuses": [None]}, milvus={"collections": [{"schema": None, "indexes": None}]}, config=None, logs=None, manifests=[None])
        self.assertEqual(evaluate(value), [])

    def test_lost_volume_requires_private_support_and_preservation(self):
        finding = evaluate(snapshot(kubernetes={"pvcs": [{"metadata": {"name": "data"}, "status": {"phase": "Lost"}}]}))[0]
        self.assertEqual(finding["severity"], "critical")
        self.assertEqual(finding["route"], "private_support")
        self.assertIn("do not delete", finding["recommendation"])

    def test_healthy_operator_and_scaled_down_workload(self):
        value = snapshot(kubernetes={"milvuses": [{"spec": {"mode": "standalone"}, "status": {"status": "Healthy", "conditions": [{"type": "MilvusReady", "status": "True"}]}}], "deployments": [{"spec": {"replicas": 0}, "status": {"readyReplicas": 0}}], "helm_releases": [{"name": "milvus", "status": "deployed"}]})
        self.assertEqual(evaluate(value), [])

    def test_pending_helm_release_is_not_claimed_failed(self):
        finding = evaluate(snapshot(kubernetes={"helm_releases": [{"name": "release", "status": "pending-upgrade"}]}))[0]
        self.assertEqual(finding["severity"], "info")
        self.assertIn("pending-upgrade", finding["summary"])

    def test_helm_labels_alone_do_not_establish_release_status(self):
        result = codes(snapshot(kubernetes={"helm_releases": [{"name": "release", "chart": "milvus-4"}]}))
        self.assertEqual(result, {"HELM_STATUS_UNAVAILABLE"})

    def test_health_access_denied_is_actionable(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertIn("HEALTH_ACCESS_DENIED", codes(snapshot(health={"status": "error", "http_status": status})))

    def test_crash_and_historical_restart_are_distinguished(self):
        value = snapshot(kubernetes={"pods": [{"status": {"containerStatuses": [{"name": "proxy", "restartCount": 10, "state": {"running": {}}}]}}]})
        findings = evaluate(value)
        self.assertEqual({f["code"] for f in findings}, {"K8S_RESTART_HISTORY"})
        self.assertEqual(findings[0]["severity"], "info")

    def test_quantities_compare_units_not_strings(self):
        def resources(requests, limits):
            return snapshot(manifests=[{"kind": "Pod", "spec": {"containers": [{"resources": {"requests": requests, "limits": limits}}]}}])
        self.assertNotIn("RESOURCE_REQUEST_EXCEEDS_LIMIT", codes(resources({"cpu": "1000m", "memory": "1024Mi"}, {"cpu": "1", "memory": "1Gi"})))
        self.assertIn("RESOURCE_REQUEST_EXCEEDS_LIMIT", codes(resources({"cpu": "1.1"}, {"cpu": "1000m"})))
        self.assertNotIn("RESOURCE_REQUEST_EXCEEDS_LIMIT", codes(resources({"memory": "invalid"}, {"memory": "1Gi"})))

    def test_selected_disk_pressure_and_exhaustion(self):
        for used, free, expected in [(90, 10, "warning"), (100, 0, "critical"), (89, 11, None)]:
            with self.subTest(used=used, free=free):
                findings = evaluate(snapshot(disk={"total_bytes": 100, "used_bytes": used, "free_bytes": free}))
                self.assertEqual(findings[0]["severity"] if findings else None, expected)
                if findings:
                    self.assertEqual(findings[0]["code"], "DISK_SPACE_PRESSURE")
                    self.assertIn("not remote object storage", " ".join(findings[0]["evidence"]))

    def test_disk_without_valid_total_does_not_fabricate_ratio(self):
        for total in (None, 0, "NaN", -1):
            with self.subTest(total=total):
                self.assertNotIn("DISK_SPACE_PRESSURE", codes(snapshot(disk={"total_bytes": total, "used_bytes": 99, "free_bytes": 0})))

    def test_compose_optional_dependency_is_not_required(self):
        value = snapshot(manifests=[{"services": {"milvus": {"image": "milvus", "depends_on": {"optional": {"required": False}}}}}])
        self.assertNotIn("COMPOSE_DEPENDENCY_UNDEFINED", codes(value))

    def test_compose_memory_aliases(self):
        value = snapshot(manifests=[{"services": {"milvus": {"image": "milvus", "mem_reservation": "4g", "mem_limit": "2g"}}}])
        self.assertIn("COMPOSE_RESERVATION_EXCEEDS_LIMIT", codes(value))

    def test_helm_and_operator_component_resources(self):
        settings = {"resources": {"requests": {"memory": "8Gi"}, "limits": {"memory": "4Gi"}}}
        for document in ({"queryNode": settings}, {"kind": "Milvus", "spec": {"components": {"queryNode": settings}}}):
            with self.subTest(document=document):
                self.assertIn("RESOURCE_REQUEST_EXCEEDS_LIMIT", codes(snapshot(manifests=[document])))

    def test_sanitized_configmap_and_helm_embedded_config(self):
        for document in ({"kind": "ConfigMap", "config": {"proxy": {"port": 70000}}}, {"extraConfigFiles": {"user.yaml": {"proxy": {"port": 70000}}}}):
            with self.subTest(document=document):
                self.assertIn("CONFIG_PORT_INVALID", codes(snapshot(manifests=[document])))

    def test_manifest_wrappers_and_lists(self):
        value = snapshot(manifests=[{"path": "example.yaml", "documents": [{"kind": "List", "items": [{"kind": "Milvus", "spec": {"mode": "wrong"}}]}]}])
        self.assertIn("OPERATOR_MODE_INVALID", codes(value))

    def test_bad_dense_and_binary_dimensions(self):
        for dtype, dim in [("FLOAT_VECTOR", 0), (100, 127), ("BINARY_VECTOR", 1), ("FLOAT_VECTOR", "1.5")]:
            with self.subTest(dtype=dtype, dim=dim):
                value = snapshot(milvus={"collections": [{"schema": {"fields": [{"type": dtype, "params": {"dim": dim}}]}}]})
                self.assertIn("SCHEMA_VECTOR_DIMENSION_INVALID", codes(value))

    def test_sparse_without_dimension_is_valid(self):
        value = snapshot(milvus={"collections": [{"schema": {"fields": [{"type": "SPARSE_FLOAT_VECTOR", "params": {}}]}}]})
        self.assertNotIn("SCHEMA_VECTOR_DIMENSION_INVALID", codes(value))

    def test_failed_index_not_confused_with_building(self):
        value = snapshot(milvus={"collections": [{"name": "c", "indexes": [{"state": "InProgress"}]}]})
        self.assertNotIn("COLLECTION_INDEX_FAILED", codes(value))
        value["milvus"]["collections"][0]["indexes"][0]["state"] = "Failed"
        self.assertIn("COLLECTION_INDEX_FAILED", codes(value))

    def test_protobuf_index_enum_four_is_failed_five_is_retry(self):
        for state, expected in [(4, True), (5, False), (3, False)]:
            with self.subTest(state=state):
                value = snapshot(milvus={"collections": [{"name": "c", "indexes": [{"state": state}]}]})
                self.assertEqual("COLLECTION_INDEX_FAILED" in codes(value), expected)

    def test_common_error_markers(self):
        cases = {
            "CONNECTION_REFUSED": "rpc failed: connection refused",
            "CONNECTION_TIMEOUT": "rpc: context deadline exceeded",
            "AUTHENTICATION_FAILED": "authentication failed: invalid token",
            "TLS_VALIDATION_FAILED": "x509: certificate signed by unknown authority",
            "VECTOR_DIMENSION_MISMATCH": "the dim 768 does not match collection dim 1024",
            "FIELD_TYPE_MISMATCH": "field type mismatch: expected INT64",
            "REQUEST_SIZE_EXCEEDED": "grpc: received message larger than max (5000 vs 1000)",
            "LOG_COLLECTION_NOT_LOADED": "collection not loaded",
            "LOG_INDEX_MISSING": "index not found",
            "MEMORY_QUOTA_EXCEEDED": "memory quota exceeded, please allocate more resources",
            "DISK_CAPACITY_ERROR": "no space left on device",
            "DEPENDENCY_UNAVAILABLE": "failed to connect to etcd",
            "DATA_INTEGRITY_ERROR": "WAL corrupted: checksum mismatch",
            "INTERNAL_PANIC": "panic: runtime error: invalid memory address",
        }
        for code, log in cases.items():
            with self.subTest(code=code):
                self.assertIn(code, codes(snapshot(logs=log)))

    def test_log_and_source_errors_never_echo_sensitive_input(self):
        secret = "secret-do-not-echo-123"
        value = snapshot(logs=[{"path": "/secret/file", "text": f"connection refused: https://admin:{secret}@private-host:19530"}])
        value["sources"].append({"name": "sdk", "status": "error", "detail": f"invalid token {secret}"})
        serialized = json.dumps(evaluate(value))
        self.assertNotIn(secret, serialized)
        self.assertNotIn("private-host", serialized)
        self.assertNotIn("/secret/file", serialized)

    def test_neutral_logs_do_not_trigger_guesswork(self):
        self.assertEqual(evaluate(snapshot(logs="server started\ncollection loaded\nindex build Finished\nquery latency 1000ms\nsegment count 100000\n")), [])

    def test_error_evidence_is_marked_historical(self):
        finding = evaluate(snapshot(logs="memory quota exceeded"))[0]
        self.assertIn("historical", " ".join(finding["evidence"]))

    def test_data_risk_routes_private(self):
        finding = evaluate(snapshot(logs="metadata corruption"))[0]
        self.assertEqual(finding["route"], "private_support")
        self.assertIn("do not", finding["recommendation"])

    def test_every_finding_has_actionable_contract_and_input_is_unchanged(self):
        value = fixture("rules-kubernetes-cluster.json")
        value["logs"] = "metadata corruption\nconnection refused\n"
        original = copy.deepcopy(value)
        findings = evaluate(value)
        self.assertEqual(value, original)
        self.assertEqual(findings[0]["severity"], "critical")
        for finding in findings:
            self.assertEqual(set(finding), {"code", "severity", "summary", "evidence", "recommendation", "verification", "route"})
            self.assertTrue(all(finding.values()))
            self.assertIn(finding["severity"], {"critical", "warning", "info"})
            self.assertIn(finding["route"], {"self_service", "community_review", "private_support", "security_escalation"})


if __name__ == "__main__":
    unittest.main()
