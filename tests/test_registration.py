import importlib.util
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("register_skill", ROOT/"scripts/register_skill.py")
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)


class RegistrationTests(unittest.TestCase):
    def test_plan_register_and_idempotence(self):
        with tempfile.TemporaryDirectory(prefix="doctor project ") as name:
            project = Path(name)
            result = registration.register(ROOT, "codex", project=project)
            self.assertEqual(result["status"], "planned")
            self.assertFalse((project/".agents").exists())
            self.assertIn(str(project), shlex.split(result["user_command"]))
            result = registration.register(ROOT, "codex", project=project, apply=True)
            self.assertEqual(result["status"], "registered")
            self.assertFalse(result["host_discovery_verified"])
            link = Path(result["target"])
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), ROOT)
            self.assertEqual(registration.register(ROOT, "codex", project=project, apply=True)["status"], "already_registered")

    def test_existing_and_dangling_targets_preserved(self):
        for dangling in (False, True):
            with tempfile.TemporaryDirectory() as name:
                target = Path(name)/".claude/skills/milvus-doctor"
                target.parent.mkdir(parents=True)
                if dangling:
                    target.symlink_to(Path(name)/"not-there")
                else:
                    target.mkdir(); (target/"user.txt").write_text("keep")
                self.assertEqual(registration.register(ROOT, "claude-code", project=name, apply=True)["status"], "conflict")
                self.assertTrue(target.is_symlink() if dangling else (target/"user.txt").read_text() == "keep")

    def test_protected_path_has_user_command_no_permission_changes(self):
        with tempfile.TemporaryDirectory() as name, patch.object(Path, "mkdir", side_effect=PermissionError("protected")), patch("os.chmod", side_effect=AssertionError("chmod")):
            result = registration.register(ROOT, "codex", project=name, apply=True)
            self.assertEqual(result["status"], "requires_user_action")
            self.assertIn("--apply", result["user_command"])
            self.assertFalse(result["cluster_access"])

    def test_parent_link_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            (Path(a)/".agents").symlink_to(b, target_is_directory=True)
            self.assertEqual(registration.register(ROOT, "codex", project=a, apply=True)["status"], "conflict")
            self.assertFalse((Path(b)/"skills").exists())

    def test_explicit_custom_claude_user_location(self):
        with tempfile.TemporaryDirectory() as name:
            result = registration.register(ROOT, "claude-code", scope="user", claude_dir=name, apply=True)
            self.assertEqual(result["status"], "registered")
            self.assertEqual(Path(result["target"]), Path(name)/"skills/milvus-doctor")

    def test_source_validation_and_missing_scope(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError): registration.register(name, "codex", project=name)
            with self.assertRaises(ValueError): registration.register(ROOT, "codex")


if __name__ == "__main__": unittest.main()
