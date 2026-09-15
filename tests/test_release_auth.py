"""Preserve safe gRPC auth categories hidden by generic PyMilvus wrappers."""
import json
import sys
import types
import unittest
from argparse import Namespace
from enum import Enum
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib import collectors as c


class RpcStatus(Enum):
    UNAUTHENTICATED = 16
    PERMISSION_DENIED = 7
    UNAVAILABLE = 14


class TransportError(Exception):
    def __init__(self, status):
        super().__init__("private payload token=never-include-this")
        self.status = status

    def code(self):
        return self.status


class GenericSDKError(Exception):
    code = 2


def wrapped(status):
    outer = GenericSDKError("Fail connecting: illegal connection params or server unavailable")
    outer.__cause__ = TransportError(status)
    return outer


class ReleaseAuthTest(unittest.TestCase):
    def test_wrapped_unauthenticated_is_explicit_without_payload(self):
        error = wrapped(RpcStatus.UNAUTHENTICATED)
        self.assertEqual(c._auth_failure_reason(error), "authentication_failed")
        detail = c._error_detail(error, "SDK unavailable")
        self.assertIn("UNAUTHENTICATED", detail)
        self.assertNotIn("never-include-this", detail)
        self.assertNotIn("private payload", detail)

    def test_wrapped_permission_denied_is_distinct(self):
        error = wrapped(RpcStatus.PERMISSION_DENIED)
        self.assertEqual(c._auth_failure_reason(error), "permission_denied")
        self.assertIn("PERMISSION_DENIED", c._error_detail(error, "SDK unavailable"))

    def test_numeric_sdk_code_two_does_not_imply_authentication(self):
        error = GenericSDKError("Fail connecting: illegal connection params or server unavailable")
        self.assertIsNone(c._auth_failure_reason(error))
        self.assertNotIn("Authentication failed", c._error_detail(error, "SDK unavailable"))

    def test_wrapped_unavailable_is_not_misclassified_as_authentication(self):
        error = wrapped(RpcStatus.UNAVAILABLE)
        self.assertIsNone(c._auth_failure_reason(error))
        self.assertNotIn("Authentication failed", c._error_detail(error, "SDK unavailable"))

    def test_context_chain_and_cycle_are_bounded(self):
        error = GenericSDKError("unknown")
        middle = GenericSDKError("unknown")
        error.__context__ = middle
        middle.__context__ = error
        self.assertIsNone(c._auth_failure_reason(error))
        middle.__cause__ = TransportError(RpcStatus.UNAUTHENTICATED)
        self.assertEqual(c._auth_failure_reason(error), "authentication_failed")

    def test_broken_status_accessor_does_not_break_reporting(self):
        class Broken(Exception):
            def code(self):
                raise ValueError("private token=never-include-this")
        error = Broken("unknown")
        self.assertIsNone(c._auth_failure_reason(error))
        self.assertNotIn("never-include-this", c._error_detail(error, "SDK unavailable"))

    def test_constructor_auth_failure_reaches_coverage_with_safe_reason(self):
        module = types.ModuleType("pymilvus")
        module.__version__ = "2.6.17"
        class FakeClient:
            def __init__(self, **kwargs):
                raise wrapped(RpcStatus.UNAUTHENTICATED)
        module.MilvusClient = FakeClient
        with patch.dict(sys.modules, {"pymilvus": module}):
            result = c.collect(Namespace(endpoint="http://127.0.0.1:19530"))
        failure = next(s for s in result["sources"] if s["name"] == "milvus" and s["status"] == "error")
        self.assertEqual(failure["reason"], "authentication_failed")
        self.assertIn("UNAUTHENTICATED", failure["detail"])
        self.assertNotIn("never-include-this", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
