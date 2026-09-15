import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from doctorlib.cli import main, parser
from doctorlib.collectors import collect
from doctorlib.coverage import summarize_coverage
from doctorlib.reports import build_report
from doctorlib.rules import evaluate


class WorkflowTests(unittest.TestCase):
    def test_unselected_checks_are_not_incomplete_but_unavailable_are(self):
        snapshot = {"sources": [{"name":"manifest.0","status":"ok"},
                    {"name":"metrics","status":"skipped","reason":"not_requested"}]}
        self.assertTrue(summarize_coverage(snapshot["sources"])["complete_for_selected_scope"])
        self.assertNotIn("COVERAGE_INCOMPLETE", [x["code"] for x in evaluate(snapshot)])
        self.assertNotEqual(build_report(snapshot,evaluate(snapshot))["status"],"incomplete")
        for status, reason in (("error","not_requested"),("skipped","dependency_missing"),("skipped",None),("skipped",[])):
            snapshot["sources"][1].update(status=status,reason=reason)
            self.assertFalse(summarize_coverage(snapshot["sources"])["complete_for_selected_scope"])
            self.assertEqual(build_report(snapshot,evaluate(snapshot))["status"],"incomplete")

    def test_all_unselected_is_not_success(self):
        sources=[{"name":"metrics","status":"skipped","reason":"not_requested"}]
        self.assertFalse(summarize_coverage(sources)["complete_for_selected_scope"])

    def test_endpoint_only_is_not_native(self):
        args=parser().parse_args(["diagnose","--endpoint","http://example.invalid:19530"])
        with patch("doctorlib.collectors.collect_milvus"), patch("doctorlib.collectors.collect_http"), patch("doctorlib.collectors.collect_native",side_effect=AssertionError("process inspection")):
            result=collect(args)
        self.assertEqual(result["deployment"]["method"],"unknown")
        self.assertEqual(result["deployment"]["collection_method"],"endpoint")

    def test_explicit_native_keeps_user_provenance(self):
        args=parser().parse_args(["diagnose","--deployment","native","--endpoint","http://example.invalid:19530"])
        with patch("doctorlib.collectors.collect_milvus"),patch("doctorlib.collectors.collect_http"),patch("doctorlib.collectors.collect_native"):
            result=collect(args)
        self.assertEqual(result["deployment"]["method"],"native")
        self.assertEqual(result["deployment"]["provenance"],"explicit_selection")

    def test_only_named_collection_without_inventory(self):
        class NamedClient:
            def __init__(self, **kwargs): pass
            def list_collections(self,**kwargs): raise AssertionError("unrequested inventory")
            def get_server_version(self,**kwargs): return "2.6.17"
            def describe_collection(self,**kwargs): return {"fields":[{"name":"embedding","type":101,"params":{"dim":8}}]}
            def get_collection_stats(self,**kwargs): return {"row_count":0}
            def get_load_state(self,**kwargs): return {"state":1}
            def list_indexes(self,**kwargs): return []
            def close(self): pass
        args=parser().parse_args(["diagnose","--endpoint","http://example.invalid:19530","--collection","selected"])
        with patch.dict(sys.modules,{"pymilvus":types.SimpleNamespace(MilvusClient=NamedClient,__version__="2.6.17")}),patch("doctorlib.collectors.collect_http"):
            result=collect(args)
        self.assertEqual([x["name"] for x in result["milvus"]["collections"]],["selected"])
        self.assertEqual(result["collector_runtime"]["pymilvus_version"],"2.6.17")

    def test_read_evidence_and_show_evidence_are_local_only(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); log=root/"error.log"
            log.write_text("problem\ntoken=do-not-share\nend\n")
            with patch("doctorlib.collectors.collect",side_effect=AssertionError("live")),patch("subprocess.Popen",side_effect=AssertionError("subprocess")),patch("urllib.request.urlopen",side_effect=AssertionError("network")),contextlib.redirect_stdout(io.StringIO()) as out:
                rc=main(["read-evidence","--file",str(log),"--format","json"])
            self.assertEqual(rc,0); self.assertNotIn("do-not-share",out.getvalue())
            report=root/"report.json"
            report.write_text(json.dumps(build_report({"sources":[{"name":"logs","status":"ok"}]},[])))
            with patch("doctorlib.collectors.collect",side_effect=AssertionError("live")),contextlib.redirect_stdout(io.StringIO()) as out:
                rc=main(["show-evidence","--report",str(report)])
            self.assertEqual(rc,0); self.assertEqual(json.loads(out.getvalue())["kind"],"diagnostic_evidence")

    def test_summary_output_never_overwrites(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); report=root/"report.json"; output=root/"reviewed.md"
            report.write_text(json.dumps(build_report({},[])))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["support-summary","--report",str(report),"--output",str(output)]),0)
            before=output.read_bytes()
            self.assertEqual(output.stat().st_mode & 0o777,0o600)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["support-summary","--report",str(report),"--output",str(output)]),2)
            self.assertEqual(output.read_bytes(),before)


if __name__=="__main__": unittest.main()
