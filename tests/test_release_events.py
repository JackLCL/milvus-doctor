"""Known event observations survive handoff without names, messages or URLs."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib.cli import main
from doctorlib.evidence import build_evidence, sanitize_evidence
from doctorlib.event_facts import MAX_EVENTS, project_events, sanitize_events
from doctorlib.reports import build_report


def storage_event():
    return {"type": "Warning", "reason": "ProvisioningFailed", "count": 3,
            "message": 'storageclass.storage.k8s.io "DEMO_PRIVATE_STORAGE" not found',
            "firstTimestamp": "2026-09-13T13:50:00Z", "lastTimestamp": "2026-09-13T13:51:00Z",
            "eventTime": "2026-09-13T13:51:00.123456Z",
            "involvedObject": {"kind": "PersistentVolumeClaim", "namespace": "DEMO_PRIVATE_NAMESPACE", "name": "DEMO_PRIVATE_PVC"}}


def registry_event():
    return {"type": "Warning", "reason": "Failed", "count": 4,
            "message": 'Failed to pull image "DEMO_PRIVATE_REGISTRY/DEMO_PRIVATE_IMAGE:tag": failed to do request: Head "http://DEMO_PRIVATE_HOST/v2/image/manifests/tag": dial tcp 127.0.0.1:9: connect: connection refused',
            "involvedObject": {"kind": "Pod", "namespace": "DEMO_PRIVATE_NAMESPACE", "name": "DEMO_PRIVATE_POD"}}


class EventFactsTests(unittest.TestCase):
    def test_observed_event_patterns_produce_only_fixed_facts(self):
        events = [storage_event(), registry_event()]
        facts, truncated = project_events(events)
        self.assertFalse(truncated)
        self.assertEqual(facts[0]["categories"], ["storage_class_not_found"])
        self.assertEqual(facts[1]["categories"], ["registry_connection_refused"])
        self.assertEqual(facts[0]["alias"], "event-1")
        self.assertEqual(facts[0]["count"], 3)
        self.assertEqual(facts[0]["first_observed_at"], "2026-09-13T13:50:00Z")
        self.assertEqual(facts[0]["last_observed_at"], "2026-09-13T13:51:00Z")
        self.assertEqual(facts[0]["event_time"], "2026-09-13T13:51:00.123456Z")
        encoded = json.dumps(facts)
        for secret in ("DEMO_PRIVATE", "127.0.0.1", "http://", "message", "involvedObject", "namespace"):
            self.assertNotIn(secret, encoded)

    def test_no_alias_from_name_only_or_a_recreated_resource(self):
        event = storage_event()
        metadata = {"namespace": "DEMO_PRIVATE_NAMESPACE", "name": "DEMO_PRIVATE_PVC"}
        targets = [("PersistentVolumeClaim", {"metadata": metadata}, "pvc-1")]
        self.assertNotIn("object_alias", project_events([event], targets)[0][0])
        metadata["uid"] = "DEMO_NEW_UID"
        event["involvedObject"]["uid"] = "DEMO_OLD_UID"
        self.assertNotIn("object_alias", project_events([event], targets)[0][0])
        event["involvedObject"]["uid"] = "DEMO_NEW_UID"
        fact = project_events([event], targets)[0][0]
        self.assertEqual(fact["object_alias"], "pvc-1")
        self.assertNotIn("DEMO_NEW_UID", json.dumps(fact))
        self.assertNotIn("object_alias", project_events([event], targets + targets)[0][0])
        event["involvedObject"]["namespace"] = "different-namespace"
        self.assertNotIn("object_alias", project_events([event], targets)[0][0])

    def test_unrelated_negated_and_incomplete_failures_are_not_root_cause_guesses(self):
        cases = []
        for message in ('storageclass.storage.k8s.io "example" was not found earlier but is now available',
                        'storageclass "example" exists; PVC not found',
                        'No storageclass not found error occurred', 'connection refused',
                        'Error: ErrImagePull', 'ImagePullBackOff'):
            value = storage_event()
            value["message"] = message
            cases.append(value)
        for change in ({"type": "Normal"}, {"reason": "BackOff"}, {"involvedObject": {"kind": "Node"}},
                       {"message": registry_event()["message"] + "; recovered successfully"},
                       {"message": registry_event()["message"].replace("Failed to pull image", "Successfully pulled image")}):
            value = registry_event()
            value.update(change)
            cases.append(value)
        self.assertTrue(all(not fact["categories"] for fact in project_events(cases)[0]))

    def test_malformed_fields_and_long_messages_do_not_smuggle_information(self):
        for count in (True, -1, 2 ** 63, "3", {}, None):
            value = storage_event()
            value.update(count=count, firstTimestamp="DEMO_PRIVATE_TIME", lastTimestamp="2026-99-99T13:00:00Z", eventTime={"token": "DEMO_PRIVATE"})
            fact = project_events([value])[0][0]
            self.assertNotIn("count", fact)
            self.assertNotIn("first_observed_at", fact)
            self.assertNotIn("last_observed_at", fact)
            self.assertNotIn("event_time", fact)
        value = registry_event()
        value["message"] = 'Failed to pull image "example": ' + "x" * 10000 + ' dial tcp example: connect: connection refused'
        self.assertEqual(project_events([value])[0][0]["categories"], [])
        for raw in (None, [], "DEMO_PRIVATE", {"reason": {"password": "DEMO_PRIVATE"}, "message": ["DEMO_PRIVATE"], "involvedObject": []}):
            self.assertNotIn("DEMO_PRIVATE", json.dumps(project_events([raw])[0]))
        self.assertEqual(project_events({"message": "DEMO_PRIVATE"}), ([], False))

    def test_raw_precomputed_categories_are_not_trusted(self):
        event = {"type": "Warning", "reason": "Failed", "involvedObject": {"kind": "Pod"},
                 "categories": ["registry_connection_refused"], "message": "ordinary event"}
        self.assertEqual(project_events([event])[0][0]["categories"], [])

    def test_limit_is_explicit_and_classified_events_are_prioritized(self):
        ordinary = {"type": "Normal", "reason": "Scheduled", "involvedObject": {"kind": "Pod"}}
        facts, truncated = project_events([ordinary] * MAX_EVENTS + [storage_event()])
        self.assertTrue(truncated)
        self.assertEqual(len(facts), MAX_EVENTS)
        self.assertEqual(facts[0]["categories"], ["storage_class_not_found"])
        self.assertEqual(facts[-1]["alias"], "event-100")
        self.assertFalse(project_events([ordinary] * MAX_EVENTS)[1])
        self.assertEqual(len(project_events([ordinary] * 1001)[0]), MAX_EVENTS)

    def test_saved_event_fields_are_revalidated_and_unknown_aliases_removed(self):
        events = project_events([storage_event()])[0]
        events[0].update(alias="DEMO_PRIVATE_EVENT", object_alias="pvc-999", message="DEMO_PRIVATE_MESSAGE",
                         note="DEMO_PRIVATE_NOTE", url="http://DEMO_PRIVATE_URL", uid="DEMO_PRIVATE_UID")
        events[0]["categories"] += [{"token": "DEMO_PRIVATE"}, "http://DEMO_PRIVATE_URL"]
        facts, truncated = sanitize_events(events, [("PersistentVolumeClaim", "pvc-1")])
        self.assertFalse(truncated)
        self.assertNotIn("DEMO_PRIVATE", json.dumps(facts))
        self.assertNotIn("object_alias", facts[0])
        self.assertEqual(facts[0]["categories"], ["storage_class_not_found"])
        events[0]["object_alias"] = "pvc-1"
        self.assertEqual(sanitize_events(events, [("PersistentVolumeClaim", "pvc-1")])[0][0]["object_alias"], "pvc-1")
        events[0]["reason"] = "FailedScheduling"
        self.assertEqual(sanitize_events(events)[0][0]["categories"], [])

    def test_saved_limits_and_malformed_inputs_are_bounded(self):
        events = project_events([registry_event()])[0] * (MAX_EVENTS + 1)
        facts, truncated = sanitize_events(events)
        self.assertTrue(truncated)
        self.assertEqual(len(facts), MAX_EVENTS)
        self.assertEqual(sanitize_events(None), ([], False))
        self.assertNotIn("DEMO_PRIVATE", json.dumps(sanitize_events([{"object_kind": ["DEMO_PRIVATE"], "reason": "DEMO_PRIVATE", "count": True, "categories": "DEMO_PRIVATE"}])[0]))

    def test_build_sanitize_roundtrip_preserves_typed_events_and_omits_unproven_alias(self):
        snapshot = {"sources": [{"name": "kubernetes.events", "status": "ok"}], "kubernetes": {
            "pods": [{"metadata": {"name": "DEMO_PRIVATE_POD", "namespace": "DEMO_PRIVATE_NAMESPACE"}, "status": {"phase": "Pending"}}],
            "pvcs": [{"metadata": {"name": "DEMO_PRIVATE_PVC", "namespace": "DEMO_PRIVATE_NAMESPACE"}, "status": {"phase": "Pending"}}],
            "events": [storage_event(), registry_event()]}}
        before = copy.deepcopy(snapshot)
        evidence = build_evidence(snapshot)
        self.assertEqual(sanitize_evidence(evidence), evidence)
        self.assertEqual(snapshot, before)
        self.assertNotIn("DEMO_PRIVATE", json.dumps(evidence))
        self.assertTrue(all("object_alias" not in event for event in evidence["kubernetes"]["events"]))
        evidence["kubernetes"]["events"] *= 101
        safe = sanitize_evidence(evidence)
        self.assertEqual(len(safe["kubernetes"]["events"]), MAX_EVENTS)
        self.assertTrue(safe["limits"]["events_truncated"])
        self.assertTrue(safe["limits"]["truncated"])
        self.assertEqual(sanitize_evidence(safe), safe)

    def test_show_evidence_uses_saved_event_facts_without_live_collection(self):
        snapshot = {"sources": [{"name": "kubernetes.events", "status": "ok"}],
                    "kubernetes": {"events": [storage_event(), registry_event()]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(build_report(snapshot, [])), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), patch("doctorlib.collectors.collect", side_effect=AssertionError("live collection")):
                self.assertEqual(main(["show-evidence", "--report", str(path)]), 0)
            result = json.loads(out.getvalue())
        self.assertEqual([fact["categories"] for fact in result["kubernetes"]["events"]],
                         [["storage_class_not_found"], ["registry_connection_refused"]])
        self.assertNotIn("DEMO_PRIVATE", out.getvalue())


if __name__ == "__main__":
    unittest.main()
