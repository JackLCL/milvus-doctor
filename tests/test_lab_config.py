"""Offline checks for the write-capable developer fixtures; no live services."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tests/lab/verify-milvus-rbac.py"
SPEC = importlib.util.spec_from_file_location("doctor_lab_rbac", HELPER)
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


def owned_container():
    return {
        "Config": {
            "Labels": {"io.milvus.doctor.lab": "true"},
            "Image": "milvusdb/milvus:v2.6.17",
        },
        "NetworkSettings": {
            "Ports": {"19530/tcp": [{"HostIp": "127.0.0.1", "HostPort": "30530"}]},
        },
    }


class LabConfigurationTests(unittest.TestCase):
    def test_admin_token_is_explicit_and_never_defaults(self):
        for value in (None, "", " \t "):
            environment = {} if value is None else {"DOCTOR_LAB_ADMIN_TOKEN": value}
            with self.subTest(value=value), patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(ValueError, "DOCTOR_LAB_ADMIN_TOKEN"):
                    lab.require_admin_token()
        with patch.dict(os.environ, {"DOCTOR_LAB_ADMIN_TOKEN": "example:private"}, clear=True):
            self.assertEqual(lab.require_admin_token(), "example:private")

    def test_missing_token_fails_before_subprocess_or_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created"
            with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", [
                str(HELPER), "--output-dir", str(output), "--allow-lab-writes",
            ]), patch.object(lab.subprocess, "check_output") as command, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    lab.main()
                self.assertEqual(failure.exception.code, 2)
                command.assert_not_called()
                self.assertFalse(output.exists())

    def test_write_opt_in_is_required_before_inspection(self):
        with patch.dict(os.environ, {"DOCTOR_LAB_ADMIN_TOKEN": "example:private"}), patch.object(sys, "argv", [
            str(HELPER), "--output-dir", "/not-used",
        ]), patch.object(lab.subprocess, "check_output") as command, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                lab.main()
            self.assertEqual(failure.exception.code, 2)
            command.assert_not_called()

    def test_only_owned_pinned_loopback_container_is_accepted(self):
        lab.verify_owned_container(owned_container())
        invalid = []
        unowned = owned_container()
        unowned["Config"]["Labels"].clear()
        invalid.append(unowned)
        image = owned_container()
        image["Config"]["Image"] = "unexpected-image"
        invalid.append(image)
        for binding in (
            [],
            [{"HostIp": "0.0.0.0", "HostPort": "30530"}],
            [{"HostIp": "127.0.0.1", "HostPort": "19530"}],
            [{"HostIp": "127.0.0.1", "HostPort": "30530"}, {"HostIp": "::", "HostPort": "30530"}],
        ):
            item = owned_container()
            item["NetworkSettings"]["Ports"]["19530/tcp"] = binding
            invalid.append(item)
        invalid.append({})
        for item in invalid:
            with self.subTest(container=item), self.assertRaises(ValueError):
                lab.verify_owned_container(item)

    def test_guard_failure_makes_no_sdk_call_or_artifact(self):
        module = types.ModuleType("pymilvus")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created"
            invalid = owned_container()
            invalid["Config"]["Labels"].clear()
            with patch.dict(os.environ, {"DOCTOR_LAB_ADMIN_TOKEN": "example:private"}), patch.object(sys, "argv", [
                str(HELPER), "--output-dir", str(output), "--allow-lab-writes",
            ]), patch.object(lab.subprocess, "check_output", return_value=json.dumps([invalid])), patch.dict(sys.modules, {"pymilvus": module}):
                with self.assertRaisesRegex(ValueError, "owned"):
                    lab.main()
                self.assertFalse(output.exists())

    def test_private_artifacts_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reader-token"
            lab.write_private(path, "example-token")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                lab.write_private(path, "replacement")
            self.assertEqual(path.read_text(), "example-token")

    def test_entrypoint_does_not_print_exception_secrets(self):
        error = io.StringIO()
        with patch.dict(os.environ, {"DOCTOR_LAB_ADMIN_TOKEN": "example:private"}), patch.object(sys, "argv", [
            str(HELPER), "--output-dir", "/not-used", "--allow-lab-writes",
        ]), patch("subprocess.check_output", side_effect=RuntimeError("token=never-print-this")), contextlib.redirect_stderr(error):
            with self.assertRaises(SystemExit) as failure:
                runpy.run_path(str(HELPER), run_name="__main__")
            self.assertEqual(failure.exception.code, 1)
        self.assertIn("RuntimeError", error.getvalue())
        self.assertNotIn("never-print-this", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_compose_credentials_have_no_committed_fallback(self):
        source = (ROOT / "tests/lab/compose.yaml").read_text()
        for variable in ("DOCTOR_LAB_MINIO_USER", "DOCTOR_LAB_MINIO_PASSWORD"):
            self.assertEqual(source.count("${" + variable + ":?"), 2)
        for previous_value in ("doctorlab", "doctor-lab-test-only"):
            self.assertNotIn(previous_value, source)
        self.assertIn("127.0.0.1:39530:19530", source)
        self.assertIn("127.0.0.1:39091:9091", source)


if __name__ == "__main__":
    unittest.main()
