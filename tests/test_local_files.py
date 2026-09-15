"""All local-input paths share the same filesystem and byte-budget boundary."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from doctorlib.cli import main
from doctorlib.local_files import read_regular_file
from doctorlib.reports import build_report


class RegularFileTests(unittest.TestCase):
    def test_regular_bytes_limits_and_normal_ancestor_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "source"
            selected.mkdir()
            payload = selected / "input.json"
            payload.write_bytes(b'{"sample":123}')
            link = root / "linked-directory"
            link.symlink_to(selected, target_is_directory=True)
            self.assertEqual(read_regular_file(link / payload.name, 14), payload.read_bytes())
            with self.assertRaisesRegex(ValueError, "exceeds max_bytes"):
                read_regular_file(payload, 3)
            with patch("doctorlib.local_files.os.read", return_value=b"x" * 15):
                with self.assertRaisesRegex(ValueError, "exceeds max_bytes"):
                    read_regular_file(payload, 14)

    def test_invalid_byte_budget_is_rejected_before_open(self):
        for budget in (0, -1, True, 2.5, 16777217):
            with self.subTest(budget=budget), patch("doctorlib.local_files.os.open") as opened:
                with self.assertRaises(ValueError):
                    read_regular_file("not-opened", budget)
                opened.assert_not_called()

    def test_special_filesystem_is_rejected_before_open_or_read(self):
        # These are ordinary public process/kernel counters, never credentials.
        for path in ("/proc/self/status", "/sys/kernel/uevent_seqnum", "/dev/null"):
            with self.subTest(path=path), patch("doctorlib.local_files.os.open") as opened, patch("doctorlib.local_files.os.read") as reader:
                with self.assertRaises(ValueError):
                    read_regular_file(path)
                opened.assert_not_called()
                reader.assert_not_called()

    def test_leaf_link_is_rejected_even_if_substituted_after_initial_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "selected.log"
            selected.write_text("ordinary error")
            other = root / "other.log"
            other.write_text("DEMO_ONLY_PRIVATE_VALUE")
            original_open = os.open
            def replace_then_open(path, flags):
                selected.rename(root / "original.log")
                selected.symlink_to(other)
                return original_open(path, flags)
            with patch("doctorlib.local_files.os.open", side_effect=replace_then_open), patch("doctorlib.local_files.os.read") as reader:
                with self.assertRaises(ValueError):
                    read_regular_file(selected)
                reader.assert_not_called()
            self.assertEqual(other.read_text(), "DEMO_ONLY_PRIVATE_VALUE")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor validation")
    def test_ancestor_race_checks_actual_descriptor_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "selected"
            parent.mkdir()
            selected = parent / "status"
            selected.write_text("ordinary local input")
            original_open = os.open
            opened_descriptors = []
            def replace_then_open(path, flags):
                parent.rename(root / "original-directory")
                parent.symlink_to("/proc/self", target_is_directory=True)
                descriptor = original_open(path, flags)
                opened_descriptors.append(descriptor)
                return descriptor
            with patch("doctorlib.local_files.os.open", side_effect=replace_then_open), patch("doctorlib.local_files.os.read") as reader:
                with self.assertRaisesRegex(ValueError, "Special filesystem"):
                    read_regular_file(selected)
                reader.assert_not_called()
            self.assertEqual(len(opened_descriptors), 1)
            with self.assertRaises(OSError):
                os.fstat(opened_descriptors[0])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor validation")
    def test_ancestor_race_to_another_regular_file_is_also_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "selected"
            parent.mkdir()
            selected = parent / "input.log"
            selected.write_text("ordinary local input")
            replacement = root / "different-scope"
            replacement.mkdir()
            (replacement / "input.log").write_text("DEMO_ONLY_PRIVATE_VALUE")
            original_open = os.open
            def replace_then_open(path, flags):
                parent.rename(root / "original-directory")
                parent.symlink_to(replacement, target_is_directory=True)
                return original_open(path, flags)
            with patch("doctorlib.local_files.os.open", side_effect=replace_then_open), patch("doctorlib.local_files.os.read") as reader:
                with self.assertRaisesRegex(ValueError, "changed during opening"):
                    read_regular_file(selected)
                reader.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor validation")
    def test_unverifiable_descriptor_fails_closed_without_path_or_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "DEMO_PRIVATE_FILENAME.log"
            selected.write_text("DEMO_ONLY_PRIVATE_VALUE")
            original_readlink = os.readlink
            def unavailable_descriptor(path, *args, **kwargs):
                if str(path).startswith("/proc/self/fd/"):
                    raise OSError("DEMO_PRIVATE_ERROR")
                return original_readlink(path, *args, **kwargs)
            with patch("doctorlib.local_files.os.readlink", side_effect=unavailable_descriptor), patch("doctorlib.local_files.os.read") as reader:
                with self.assertRaises(ValueError) as caught:
                    read_regular_file(selected)
                reader.assert_not_called()
            self.assertNotIn("DEMO_PRIVATE", str(caught.exception))


class LocalInputCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.snapshot = self.root / "snapshot.json"
        self.snapshot.write_text(json.dumps({"schema_version": 1, "sources": [{"name": "logs", "status": "ok"}], "logs": ["ordinary error"]}))
        self.report = self.root / "report.json"
        self.report.write_text(json.dumps(build_report({"sources": [{"name": "logs", "status": "ok"}]}, [])))

    def commands(self, path):
        path = str(path)
        return (
            (["read-evidence", "--file", path], 2),
            (["diagnose", "--log-file", path, "--format", "json"], 1),
            (["diagnose", "--manifest", path, "--format", "json"], 1),
            (["diagnose", "--config-file", path, "--format", "json"], 1),
            (["diagnose", "--snapshot", path], 2),
            (["diagnose", "--snapshot", str(self.snapshot), "--previous", path], 2),
            (["show-evidence", "--report", path], 2),
            (["support-summary", "--report", path], 2),
            (["support-summary", "--report", str(self.report), "--context", path], 2),
        )

    def run_cli(self, command):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch("doctorlib.collectors.run_readonly", side_effect=AssertionError("Unexpected live collection")), patch("doctorlib.collectors._http_get", side_effect=AssertionError("Unexpected network")):
            result = main(command)
        return result, out.getvalue(), err.getvalue()

    def test_symlink_cannot_bypass_any_local_input_cli(self):
        selected = self.root / "DEMO_PRIVATE_LINK"
        selected.symlink_to(self.snapshot)
        for command, expected in self.commands(selected):
            with self.subTest(command=command):
                result, out, err = self.run_cli(command)
                self.assertEqual(result, expected)
                self.assertNotIn("DEMO_PRIVATE_LINK", out + err)
                if expected == 1:
                    report = json.loads(out)
                    self.assertEqual(report["status"], "incomplete")
                    self.assertTrue(any(s["status"] == "error" for s in report["sources"]))

    def test_special_path_cannot_bypass_any_local_input_cli(self):
        for command, expected in self.commands("/proc/self/status"):
            with self.subTest(command=command):
                result, out, err = self.run_cli(command)
                self.assertEqual(result, expected)
                self.assertNotIn("/proc/self/status", out + err)

    def test_fifo_input_is_rejected_promptly_by_each_cli(self):
        fifo = self.root / "DEMO_PRIVATE_PIPE"
        os.mkfifo(fifo)
        for command, expected in self.commands(fifo):
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, str(ROOT / "scripts/doctor.py")] + command,
                                        capture_output=True, timeout=3)
                self.assertEqual(result.returncode, expected, result.stderr.decode("utf-8", "replace"))
                self.assertNotIn(b"DEMO_PRIVATE_PIPE", result.stdout + result.stderr)

    def test_regular_snapshot_report_context_and_log_still_work(self):
        for command, expected in ((["diagnose", "--snapshot", str(self.snapshot), "--format", "json"], 0),
                                  (["show-evidence", "--report", str(self.report)], 0),
                                  (["support-summary", "--report", str(self.report)], 0),
                                  (["diagnose", "--log-file", str(self.snapshot), "--format", "json"], 0)):
            with self.subTest(command=command):
                self.assertEqual(self.run_cli(command)[0], expected)


if __name__ == "__main__":
    unittest.main()
