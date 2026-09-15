import builtins
import contextlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib.cli import main, parser
from doctorlib.preflight import run_preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        # Isolate platform simulation from stdlib's own platform-dependent imports.
        self.stack.enter_context(patch("doctorlib.preflight.sys", SimpleNamespace(platform="linux", version_info=(3, 8, 10))))
        self.modules = self.stack.enter_context(patch("doctorlib.preflight.importlib.util.find_spec", return_value=None))
        self.commands = self.stack.enter_context(patch("doctorlib.preflight.shutil.which", return_value=None))

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(["preflight"] + list(args))
        return code, out.getvalue()

    def test_missing_optional_capabilities_do_not_block_core(self):
        report = run_preflight()
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["core_ready"])
        self.assertTrue(report["read_only"])
        self.assertEqual(report["smoke_test"]["status"], "passed")
        self.assertEqual({v["status"] for v in report["optional_capabilities"].values()}, {"missing"})
        self.assertEqual([c.args for c in self.modules.call_args_list], [("yaml",), ("pymilvus",)])
        self.assertEqual([c.args for c in self.commands.call_args_list], [("docker",), ("kubectl",)])

    def test_discovery_does_not_publish_paths_or_promise_compatibility(self):
        self.modules.return_value = object()
        self.commands.return_value = "/private-user/bin/docker"
        report = run_preflight()
        self.assertEqual({v["status"] for v in report["optional_capabilities"].values()}, {"discovered"})
        self.assertNotIn("private-user", json.dumps(report))
        self.assertIn("not imported or compatibility-tested", report["optional_capabilities"]["pymilvus"]["check"])

    def test_optional_modules_are_not_imported(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {"yaml", "pymilvus"}:
                self.fail("Preflight must not import optional packages")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            self.assertEqual(run_preflight()["status"], "ready")

    def test_optional_discovery_failures_do_not_leak_or_block(self):
        self.modules.side_effect = ValueError("private-module-secret")
        self.commands.side_effect = OSError("private-path-secret")
        report = run_preflight()
        self.assertEqual(report["status"], "ready")
        self.assertEqual({v["status"] for v in report["optional_capabilities"].values()}, {"check_failed"})
        self.assertNotIn("secret", json.dumps(report))

    def test_unsupported_platform_is_explicit_nonzero(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform), patch("doctorlib.preflight.sys.platform", platform):
                code, output = self.cli("--format", "json")
                report = json.loads(output)
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "unsupported_platform")
                self.assertTrue(report["core_ready"])
                self.assertFalse(report["platform"]["supported"])
                self.assertIn("have not been validated", report["platform"]["validation"])

    def test_old_python_is_core_unavailable(self):
        with patch("doctorlib.preflight.sys.version_info", (3, 7, 17)):
            code, output = self.cli("--format", "json")
        report = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "core_unavailable")
        self.assertFalse(report["core_ready"])
        self.assertEqual(report["python"], {"version": "3.7.17", "minimum": "3.8", "supported": False})

    def test_smoke_failure_is_nonzero_and_sanitized(self):
        for function in ("evaluate", "build_report", "markdown", "support_summary"):
            with self.subTest(function=function), patch("doctorlib.preflight." + function, side_effect=RuntimeError("private-synthetic-secret")):
                code, output = self.cli("--format", "json")
                report = json.loads(output)
                self.assertEqual(code, 1)
                self.assertFalse(report["core_ready"])
                self.assertEqual(report["smoke_test"]["status"], "failed")
                self.assertNotIn("private-synthetic-secret", output)

    def test_smoke_checks_actual_rule_and_summary_content(self):
        for target, returned in (("evaluate", []), ("markdown", ""), ("support_summary", "")):
            with self.subTest(target=target), patch("doctorlib.preflight." + target, return_value=returned):
                self.assertEqual(run_preflight()["smoke_test"]["status"], "failed")

    def test_required_collector_import_failure_is_nonzero_and_sanitized(self):
        original_import = builtins.__import__
        for failure in (ModuleNotFoundError("private-missing-module"),
                        SyntaxError("private-broken-module"),
                        RuntimeError("private-import-failure")):
            def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
                if (name == "collectors" and level == 1
                        and globals.get("__package__") == "doctorlib"):
                    raise failure
                return original_import(name, globals, locals, fromlist, level)

            with self.subTest(failure=type(failure).__name__), patch("builtins.__import__", side_effect=guarded_import):
                code, output = self.cli("--format", "json")
            report = json.loads(output)
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "core_unavailable")
            self.assertFalse(report["core_ready"])
            self.assertEqual(report["smoke_test"]["status"], "failed")
            self.assertNotIn("private-", output)

    def test_required_collector_entrypoint_must_be_callable(self):
        with patch("doctorlib.collectors.collect", None):
            code, output = self.cli("--format", "json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "core_unavailable")

    def test_json_cli_and_default_markdown(self):
        code, output = self.cli("--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "ready")
        code, output = self.cli()
        self.assertEqual(code, 0)
        self.assertTrue(output.startswith("# Milvus Doctor installation preflight"))
        self.assertIn("not AI-host Skill discovery or Milvus cluster health", output)

    def test_preflight_has_no_target_credentials_or_output_arguments(self):
        for args in (["--endpoint", "http://example.invalid"], ["--token-env", "PRIVATE_TOKEN"], ["--output-dir", "reports"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args(["preflight"] + args)

    def test_no_collectors_network_subprocess_credentials_or_report_files(self):
        with contextlib.ExitStack() as forbidden:
            for target in (
                    "doctorlib.collectors.collect", "doctorlib.cli.inventory",
                    "doctorlib.cli.load_json", "doctorlib.cli.register_secret",
                    "doctorlib.cli.save_report", "doctorlib.reports.save_report",
                    "subprocess.Popen", "subprocess.run", "socket.socket", "socket.create_connection",
                    "urllib.request.urlopen", "builtins.open", "pathlib.Path.open"):
                forbidden.enter_context(patch(target, side_effect=AssertionError("Forbidden operation: " + target)))
            code, output = self.cli("--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["smoke_test"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
