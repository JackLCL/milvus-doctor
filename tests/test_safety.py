import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib.safety import validate_command, run_readonly, redact, register_secret, validate_endpoint


class CommandBoundaryTests(unittest.TestCase):
    def test_mutations_and_escape_routes_are_rejected_before_spawn(self):
        commands = [
            ["docker", "exec", "db", "cat", "/etc/passwd"], ["docker", "restart", "db"],
            ["docker", "rm", "db"], ["docker", "compose", "up"], ["docker", "run", "alpine"],
            ["docker", "logs", "db"], ["docker", "stats", "db"], ["docker", "inspect"],
            ["sh", "-c", "echo test"], ["bash", "-c", "true"], ["sudo", "docker", "ps"],
            ["/tmp/docker", "ps"], ["helm", "upgrade", "db"], ["helm", "list", "-n", "db", "-o", "json"],
            ["kubectl", "get", "secrets", "-n", "db", "-o", "json"],
            ["kubectl", "get", "pods", "-A", "-o", "json"],
            ["kubectl", "get", "pods", "--raw", "/api/v1/secrets", "-n", "db", "-o", "json"],
            ["kubectl", "exec", "pod", "-n", "db", "-o", "json"],
            ["kubectl", "delete", "pods", "-n", "db", "-o", "json"],
            ["kubectl", "get", "pods", "-n", "db", "-o", "go-template={{.}}"],
            ["kubectl", "config", "view", "--raw"],
            ["ps", "aux"], ["ps", "-p", "1", "-o", "args="],
        ]
        with patch("doctorlib.safety.subprocess.Popen") as spawn:
            for cmd in commands:
                with self.subTest(cmd=cmd), self.assertRaises(ValueError): run_readonly(cmd)
            spawn.assert_not_called()

    def test_expected_read_operations(self):
        commands = [
            ["docker", "inspect", "--type", "container", "db"],
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", "db"],
            ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=doctor-test", "--format", "{{.ID}}"],
            ["kubectl", "--context", "kind-kind", "-n", "db", "--request-timeout=8s", "get", "pods,services", "-l", "app=milvus", "-o", "json"],
            ["kubectl", "--context", "kind-kind", "--namespace", "db", "get", "milvuses", "db", "-o", "json"],
            ["ps", "-p", "1", "-o", "pid=,comm=,pcpu=,pmem=,rss=,vsz="],
        ]
        for cmd in commands:
            with self.subTest(cmd=cmd): validate_command(cmd)

    def test_real_readonly_subprocess(self):
        result = run_readonly(["ps", "-p", str(os.getpid()), "-o", "pid=,comm=,pcpu=,pmem=,rss=,vsz="])
        self.assertEqual(result.returncode, 0)
        self.assertIn(str(os.getpid()), result.stdout)

    def test_output_limit_and_timeout_kill_process(self):
        real_popen = subprocess.Popen
        for code, exc in [("import time; time.sleep(5)", TimeoutError), ("print('a'*5000)", ValueError)]:
            with self.subTest(code=code):
                child = real_popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                with patch("doctorlib.safety.subprocess.Popen", return_value=child):
                    with self.assertRaises(exc): run_readonly(["docker", "ps"], timeout=0.2, max_bytes=1024)
                self.assertIsNotNone(child.poll())


class RedactionTests(unittest.TestCase):
    def test_nested_credentials_redacted(self):
        value = {"password": "abc", "nested": [{"api_key": "xyz", "token": "secretvalue"}], "safe": "value"}
        text = str(redact(value))
        for secret in ("abc", "xyz", "secretvalue"): self.assertNotIn(secret, text)
        self.assertIn("value", text)

    def test_freeform_credentials_and_identifiers(self):
        value = 'uri=https://admin:supersecret@database:19530 token=abc123 password="with spaces" Bearer aaaaa.bbbbb.ccccc john@example.com 10.1.2.3'
        text = redact(value)
        for secret in ("supersecret", "abc123", "with spaces", "aaaaa", "john@example.com", "10.1.2.3"):
            self.assertNotIn(secret, text)

    def test_registered_token_even_without_key(self):
        register_secret("some-opaque-runtime-credential")
        self.assertNotIn("some-opaque-runtime-credential", redact("error: some-opaque-runtime-credential"))

    def test_basic_auth_and_credential_spelling_variants(self):
        value = 'Authorization: Basic dXNlcjpwYXNzd29yZA== secret_key=synthetic-secret-value accessKeyID=synthetic-access-value clientSecret="hidden secret" aws_secret_access_key=aws-secret --token cli-secret'
        text = redact(value)
        for secret in ("dXNlcjpwYXNzd29yZA==", "synthetic-secret-value", "synthetic-access-value", "hidden secret", "aws-secret", "cli-secret"):
            self.assertNotIn(secret, text)

    def test_endpoint_validation(self):
        for url in ("file:///etc/passwd", "https://name:password@server", "http://server:19530?token=secret", "http://server:bad"):
            with self.subTest(url=url), self.assertRaises(ValueError): validate_endpoint(url)
        validate_endpoint("https://server:9091/healthz", "health_url")
        with self.assertRaises(ValueError): validate_endpoint("https://server/admin/delete", "health_url")


if __name__ == "__main__": unittest.main()
