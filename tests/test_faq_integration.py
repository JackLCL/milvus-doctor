"""FAQ import and user-facing workflow regressions; no live targets or network."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import posixpath
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from doctorlib import cli, faq
from doctorlib.reports import build_report, markdown, support_summary


def module_from_script(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = module_from_script("faq_importer_integration", "scripts/sync_faq.py")
builder = module_from_script("faq_builder_integration", "scripts/build_release.py")


class FakeOfficialReader:
    """Serve only fixed public source paths, with deterministic synthetic content."""
    def __init__(self, *, include="{{fragments/cpu_support.md}}", license_ok=True,
                 notice=None, page_override=None):
        self.calls = []
        self.include = include
        self.license_ok = license_ok
        self.notice = notice
        self.page_override = page_override
        self.refs = {"v3.0.x": "a" * 40, "v2.6.x": "b" * 40}

    def page(self, category):
        if self.page_override is not None:
            return self.page_override.encode()
        suffix = "\n" + self.include if category == "operational" else ""
        return ("---\ntitle: Synthetic official fixture\n---\n"
                "# " + category.title() + "\n\nCategory introduction.\n\n"
                "  ## Selected question?\n\nRead existing metadata only."
                + suffix + "\n").encode()

    def __call__(self, url, maximum=524288, optional=False):
        self.calls.append((url, maximum, optional))
        prefix = "https://raw.githubusercontent.com/milvus-io/milvus-docs/"
        if not url.startswith(prefix):
            raise AssertionError("Unexpected official source URL")
        commit, path = url[len(prefix):].split("/", 1)
        if commit not in self.refs.values():
            raise AssertionError("Mutable or unexpected source commit")
        if path == "LICENSE":
            return b"Apache License\nVersion 2.0\n" if self.license_ok else b"Unreviewed license"
        if path == "NOTICE":
            if not optional:
                raise AssertionError("NOTICE must be optional")
            return self.notice
        if path == "site/en/fragments/cpu_support.md":
            return b"The selected fixture build supports AVX2.\n"
        for category, _, source_path in importer.PAGES:
            if path == source_path:
                return self.page(category)
        raise AssertionError("Unexpected source path: " + path)


class FaqImporterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="doctor-faq-import-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def build(self, name="bundle", reader=None, **kwargs):
        reader = reader or FakeOfficialReader()
        with patch.object(importer, "resolve", side_effect=AssertionError("Mutable ref lookup")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Network access")):
            return importer.build_bundle(self.root / name, refs=reader.refs,
                                         reader=reader, captured_on="2026-09-14", **kwargs)

    def test_sections_preserve_indented_headings_and_ignore_fenced_headings(self):
        source = ("---\ntitle: Test\n---\n# Root\nIntro.\n"
                  "  ## First {#first}\nBefore.\n```sh\n## Not a section\n```\nAfter.\n"
                  "   ### Nested\n~~~\n# Still code\n~~~\nNested answer.\n## Last\nEnd.\n")
        sections = importer.sections(source)
        self.assertEqual([s["title"] for s in sections], ["Root", "First", "Nested", "Last"])
        self.assertEqual(sections[2]["breadcrumb"], ["Root", "First", "Nested"])
        self.assertIn("## Not a section", sections[1]["body"])
        for item in sections:
            start, end = item["source_lines"]["start"], item["source_lines"]["end"]
            self.assertEqual(item["body"], "\n".join(source.splitlines()[start:end]).strip())

    def test_shorter_or_nonclosing_fences_do_not_create_false_sections(self):
        source = ("# Root\n````sh\n```\n## Within longer fence\n"
                  "```` not a closing fence\n## Still within fence\n````\n"
                  "## Actual question\nAnswer.\n")
        sections = importer.sections(source)
        self.assertEqual([s["title"] for s in sections], ["Root", "Actual question"])
        self.assertIn("## Still within fence", sections[0]["body"])

    def test_invalid_frontmatter_and_no_sections_are_rejected(self):
        for text in ("---\nunfinished frontmatter", "No heading here"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                importer.sections(text)

    def test_fixed_pages_cover_two_versions_with_verifiable_provenance(self):
        reader = FakeOfficialReader(notice=b"Synthetic upstream attribution.\n")
        catalog = self.build(reader=reader)
        self.assertEqual(len(catalog["sources"]), 10)
        self.assertEqual(len(catalog["entries"]), 20)
        self.assertEqual(len(catalog["fragments"]), 2)
        self.assertEqual(catalog["captured_on"], "2026-09-14")
        bundle = self.root / "bundle"
        self.assertEqual((bundle / "ATTRIBUTION.md").read_bytes(),
                         (ROOT / "references/faq/ATTRIBUTION.md").read_bytes())
        for version in importer.VERSIONS:
            self.assertEqual({s["category"] for s in catalog["sources"] if s["docs_version"] == version},
                             set(faq.CATEGORIES))
            self.assertEqual((bundle / "licenses" / (version + "-NOTICE.txt")).read_bytes(), reader.notice)
        for source in catalog["sources"]:
            data = (bundle / source["path"]).read_bytes()
            self.assertEqual(data, reader.page(source["category"]))
            self.assertEqual(source["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(source["bytes"], len(data))
            entries = [item for item in catalog["entries"] if item["source_id"] == source["id"]]
            self.assertEqual(len(entries), source["section_count"])
            self.assertIn(reader.refs[source["docs_version"]], source["source_url"])
            for item in entries:
                lines = item["source_lines"]
                self.assertEqual(item["body"], "\n".join(data.decode().splitlines()[lines["start"]:lines["end"]]).strip())
        provenance = json.loads((bundle / "provenance.json").read_text())
        self.assertTrue(provenance["source_files_unmodified"])
        self.assertEqual(provenance["repository"], "milvus-io/milvus-docs")
        self.assertTrue(all(row["notice_present"] for row in provenance["licenses"]))

    def test_reviewed_fragment_is_resolved_without_changing_source(self):
        catalog = self.build()
        entries = [item for item in catalog["entries"] if item.get("fragment_ids")]
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertIn("{{fragments/cpu_support.md}}", entry["body"])
            self.assertNotIn("{{", entry["resolved_body"])
            self.assertIn("supports AVX2", entry["resolved_body"])
        with patch.object(faq, "_FAQ_ROOT", self.root / "bundle"), \
                patch.object(faq, "_CATALOG_PATH", self.root / "bundle/catalog.json"):
            self.assertEqual(len(faq.load_catalog()["fragments"]), 2)

    def test_existing_directory_and_dangling_link_are_never_overwritten(self):
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("untouched")
        (self.root / "link").symlink_to(self.root / "missing", target_is_directory=True)
        for name in ("existing", "link"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "already exists"):
                self.build(name)
        self.assertEqual(marker.read_text(), "untouched")
        self.assertTrue((self.root / "link").is_symlink())
        self.assertFalse((self.root / "missing").exists())

    def test_unknown_fragment_leaves_no_partial_bundle(self):
        with self.assertRaisesRegex(ValueError, "Unreviewed FAQ fragment"):
            self.build(reader=FakeOfficialReader(include="{{fragments/unreviewed.md}}"))
        self.assertFalse((self.root / "bundle").exists())

    def test_unreviewed_license_or_empty_category_leaves_no_bundle(self):
        for reader in (FakeOfficialReader(license_ok=False), FakeOfficialReader(page_override="# Empty\n")):
            with self.subTest(license_ok=reader.license_ok), self.assertRaises(ValueError):
                self.build(reader=reader)
            self.assertFalse((self.root / "bundle").exists())

    def test_reproducible_pinned_import_has_identical_file_bytes(self):
        self.build("first")
        self.build("second")
        first = {str(p.relative_to(self.root / "first")): p.read_bytes()
                 for p in (self.root / "first").rglob("*") if p.is_file()}
        second = {str(p.relative_to(self.root / "second")): p.read_bytes()
                  for p in (self.root / "second").rglob("*") if p.is_file()}
        self.assertEqual(first, second)

    def test_reviewed_corpus_contains_every_section_and_both_cpu_fragments(self):
        catalog = faq.load_catalog()
        self.assertEqual(len(catalog["sources"]), 10)
        self.assertEqual(len(catalog["entries"]), 182)
        self.assertEqual(len(catalog["fragments"]), 2)
        for source in catalog["sources"]:
            parts = importer.sections((ROOT / "references/faq" / source["path"]).read_text())
            entries = [item for item in catalog["entries"] if item["source_id"] == source["id"]]
            self.assertEqual([p["body"] for p in parts], [item["body"] for item in entries])
            self.assertEqual(len(parts), source["section_count"])


class FaqCliIntegrationTests(unittest.TestCase):
    def run_cli(self, arguments):
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as error, \
                patch("urllib.request.urlopen", side_effect=AssertionError("Network access")), \
                patch("socket.create_connection", side_effect=AssertionError("Target connection")), \
                patch("subprocess.run", side_effect=AssertionError("Subprocess execution")), \
                patch("doctorlib.collectors.collect", side_effect=AssertionError("Live collection")):
            status = cli.main(["faq"] + arguments)
        return status, output.getvalue(), error.getvalue()

    def test_overview_needs_no_target_and_lists_five_categories(self):
        status, output, error = self.run_cli([])
        self.assertEqual((status, error), (0, ""))
        result = json.loads(output)
        self.assertEqual(result["source_count"], 10)
        self.assertEqual(set(result["categories"]), set(faq.CATEGORIES))

    def test_chinese_query_uses_requested_version_and_bounded_results(self):
        status, output, _ = self.run_cli(["--query", "召回率低", "--milvus-version", "2.6.17",
                                        "--category", "performance", "--limit", "2"])
        result = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(result["selected_docs_version"], "v2.6.x")
        self.assertEqual(result["version_status"], "matched_minor_version")
        self.assertTrue(1 <= len(result["results"]) <= 2)
        self.assertTrue(all(r["category"] == "performance" for r in result["results"]))

    def test_unsupported_version_does_not_fall_back(self):
        status, output, _ = self.run_cli(["--query", "维度上限", "--milvus-version", "2.4.23"])
        result = json.loads(output)
        self.assertEqual(status, 1)
        self.assertEqual(result["status"], "version_not_bundled")
        self.assertEqual(result["results"], [])

    def test_id_roundtrip_and_cross_version_rejection(self):
        result = faq.search_faq("召回率", "2.6.17", limit=1)["results"][0]
        status, output, _ = self.run_cli(["--id", result["id"], "--milvus-version", "2.6.17"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["entry"]["id"], result["id"])
        status, output, _ = self.run_cli(["--id", result["id"], "--milvus-version", "3.0.0"])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)["status"], "version_mismatch")
        self.assertIsNone(json.loads(output)["entry"])

    def test_unmatched_query_reports_no_result_not_a_diagnosis(self):
        status, output, _ = self.run_cli(["--query", "unmatchedzzfixtureqzv7291"])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)["results"], [])

    def test_invalid_filters_and_empty_explicit_selection_are_errors(self):
        selections = (["--category", "product"], ["--milvus-version", "2.6.17"],
                      ["--query", ""], ["--id", ""], ["--query", "   "],
                      ["--query", "recall", "--milvus-version", ""],
                      ["--query", "recall", "--milvus-version", "   "],
                      ["--query", "recall", "--limit", "0"],
                      ["--query", "recall", "--limit", "11"],
                      ["--id", "3.0-product-missing", "--category", "product"])
        for arguments in selections:
            with self.subTest(arguments=arguments):
                status, output, error = self.run_cli(arguments)
                self.assertEqual(status, 2)
                self.assertEqual(output, "")
                self.assertTrue(json.loads(error)["read_only"])

    def test_markdown_explains_unconfirmed_version_and_read_only_boundary(self):
        status, output, _ = self.run_cli(["--query", "etcd bad member ID", "--format", "markdown", "--limit", "1"])
        self.assertEqual(status, 0)
        self.assertIn("version is unconfirmed", output)
        self.assertIn("not a diagnosis or permission to execute", output)
        self.assertIn("Read-only guidance:", output)
        self.assertIn("github.com/milvus-io/milvus-docs/blob/", output)
        self.assertNotIn("kubectl delete", output)

    def test_missing_catalog_is_safe_error_without_network_fallback(self):
        with patch.object(faq, "load_catalog", side_effect=faq.CatalogError("Bundle unavailable")):
            status, output, error = self.run_cli(["--query", "recall"])
        self.assertEqual((status, output), (2, ""))
        self.assertTrue(json.loads(error)["read_only"])


def issue_report(*, version="2.6.17", include_faq=True):
    snapshot = {"milvus": {"version": version}, "sources": [{"name": "milvus", "status": "ok"}]}
    findings = [{"code": "COLLECTION_NOT_LOADED", "severity": "warning",
                 "summary": "Selected collection is not loaded", "route": "self_service"}]
    return build_report(snapshot, findings, include_faq=include_faq)


class FaqReportIntegrationTests(unittest.TestCase):
    def test_attaching_references_does_not_change_diagnostic_facts_or_status(self):
        without = issue_report(include_faq=False)
        with_refs = issue_report()
        for key in ("status", "coverage", "findings", "severity_counts", "observations", "evidence", "sources"):
            self.assertEqual(without[key], with_refs[key])
        self.assertNotIn("faq_knowledge", without)
        self.assertEqual(with_refs["faq_knowledge"]["status"], "references_available")
        self.assertTrue(with_refs["faq_knowledge"]["references"])
        self.assertTrue(all(r["docs_version"] == "v2.6.x" for r in with_refs["faq_knowledge"]["references"]))

    def test_default_build_and_no_findings_do_not_read_the_faq_bundle(self):
        with patch.object(faq, "load_catalog", side_effect=AssertionError("Unexpected FAQ read")):
            plain = issue_report(include_faq=False)
            empty = build_report({}, [], include_faq=True)
            self.assertNotIn("faq_knowledge", empty)
            self.assertNotIn("official FAQ", support_summary(plain))

    def test_missing_bundle_does_not_break_diagnosis_or_misstate_coverage(self):
        expected = issue_report(include_faq=False)
        with patch.object(faq, "load_catalog", side_effect=faq.CatalogError("Corrupt catalog")):
            report = issue_report()
        self.assertEqual(report["status"], expected["status"])
        self.assertEqual(report["coverage"], expected["coverage"])
        self.assertEqual(report["faq_knowledge"]["status"], "unavailable")
        self.assertIn("no network fallback", report["faq_knowledge"]["note"])
        self.assertIn("FAQ reference lookup unavailable", markdown(report))

    def test_unbundled_server_does_not_attach_other_versions(self):
        report = issue_report(version="2.4.23")
        self.assertEqual(report["faq_knowledge"]["references"], [])
        self.assertEqual(report["status"], "attention_required")

    def test_support_summary_keeps_reference_separate_from_checks_and_submission(self):
        report = issue_report()
        summary = support_summary(report)
        ref = report["faq_knowledge"]["references"][0]
        self.assertIn(ref["source_url"], summary)
        self.assertIn(ref["title"], summary)
        self.assertIn("NOT SUBMITTED", summary)
        self.assertIn("not diagnostic evidence", summary.lower())
        self.assertIn("Actions already performed by the user:\nNot provided", summary)
        self.assertIn("No user data", " ".join(report["limitations"]))

    def test_saved_titles_links_and_guidance_are_reprojected_from_trusted_bundle(self):
        report = issue_report()
        original = copy.deepcopy(report["faq_knowledge"]["references"][0])
        saved = report["faq_knowledge"]["references"][0]
        saved.update(title="FORGED-TITLE", docs_url="https://forged.invalid/upload",
                     read_only_guidance="FORGED-GUIDANCE", warnings=["FORGED-WARNING"])
        for rendered in (support_summary(report), markdown(report)):
            self.assertIn(original["title"], rendered)
            self.assertIn(original["source_url"], rendered)
            self.assertNotIn("FORGED", rendered)
            self.assertNotIn("forged.invalid", rendered)

    def test_forged_identity_or_changed_provenance_is_omitted_from_summary(self):
        for field, value in (("source_url", "https://forged.invalid/source"),
                             ("id", "3.0-product-not-a-bundled-id"),
                             ("docs_version", "v3.0.x")):
            with self.subTest(field=field):
                report = issue_report()
                ref = report["faq_knowledge"]["references"][0]
                genuine_url = ref["source_url"]
                ref[field] = value
                report["faq_knowledge"]["references"] = [ref]
                for rendered in (support_summary(report), markdown(report)):
                    self.assertNotIn(genuine_url, rendered)
                    self.assertNotIn("forged.invalid", rendered)
                    self.assertNotIn("not-a-bundled-id", rendered)

    def test_mismatched_saved_reference_cannot_become_version_verified(self):
        report = issue_report()
        genuine_url = report["faq_knowledge"]["references"][0]["source_url"]
        report["observations"]["milvus_version"] = "3.0.0"
        self.assertNotIn(genuine_url, support_summary(report))
        self.assertNotIn(genuine_url, markdown(report))

    def test_user_confirmed_version_reconciles_previously_unconfirmed_references(self):
        report = issue_report(version=None)
        original = copy.deepcopy(report)
        reference = report["faq_knowledge"]["references"][0]
        self.assertEqual(reference["docs_version"], "v3.0.x")
        self.assertEqual(reference["version_status"], "unconfirmed_reference")
        for version in ("2.4.23", "2.6.17"):
            with self.subTest(version=version):
                summary = support_summary(report, {"milvus_version": version})
                self.assertNotIn(reference["source_url"], summary)
                self.assertNotIn("Related official FAQ references", summary)
        matched = support_summary(report, {"milvus_version": "3.0.0"})
        self.assertIn(reference["source_url"], matched)
        self.assertIn("matched_minor_version", matched)
        self.assertNotIn("unconfirmed_reference", matched)
        self.assertNotIn("Milvus version is unconfirmed", matched)
        self.assertEqual(report, original)

    def test_observed_version_takes_precedence_over_conflicting_context_for_faq(self):
        report = issue_report(version="2.6.17")
        reference = report["faq_knowledge"]["references"][0]
        summary = support_summary(report, {"milvus_version": "3.0.0"})
        self.assertIn(reference["source_url"], summary)
        self.assertIn("v2.6.x | matched_minor_version", summary)
        self.assertIn("conflicts with report", summary)

    def test_lookup_and_summary_do_not_mutate_report_or_execute_external_actions(self):
        report = issue_report()
        original = copy.deepcopy(report)
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network access")), \
                patch("subprocess.run", side_effect=AssertionError("Command execution")), \
                patch("doctorlib.collectors.collect", side_effect=AssertionError("Target access")):
            support_summary(report)
            markdown(report)
        self.assertEqual(report, original)

    def test_surrogate_catalog_text_is_rejected_and_diagnosis_degrades_safely(self):
        original = faq.load_catalog()
        expected = issue_report(include_faq=False)
        with tempfile.TemporaryDirectory(prefix="doctor-faq-invalid-unicode-") as tmp:
            bundle = Path(tmp) / "faq"
            shutil.copytree(ROOT / "references/faq", bundle)
            selected = bundle / "catalog.json"
            with patch.object(faq, "_FAQ_ROOT", bundle), patch.object(faq, "_CATALOG_PATH", selected):
                self.assertEqual(faq.load_catalog(), original)
            for field in ("title", "read_only_guidance"):
                with self.subTest(field=field):
                    damaged = copy.deepcopy(original)
                    damaged["entries"][0][field] = "Malformed catalog text: \ud800"
                    # JSON escapes the surrogate; source file itself is valid UTF-8.
                    selected.write_text(json.dumps(damaged, ensure_ascii=True), encoding="utf-8")
                    with patch.object(faq, "_FAQ_ROOT", bundle), patch.object(faq, "_CATALOG_PATH", selected):
                        with self.assertRaises(faq.CatalogError):
                            faq.load_catalog()
                        report = issue_report()
                    self.assertEqual(report["faq_knowledge"]["status"], "unavailable")
                    self.assertEqual(report["status"], expected["status"])
                    self.assertEqual(report["findings"], expected["findings"])
                    self.assertEqual(report["coverage"], expected["coverage"])
                    self.assertNotIn("Malformed catalog text", support_summary(report))

    def test_safe_prose_never_exposes_commands_inside_longer_nested_fence(self):
        source = ("Read existing status.\n````sh\n```\n"
                  "kubectl scale deployment milvus --replicas=0\n"
                  "```` not a closing fence\n"
                  "kubectl scale deployment etcd --replicas=0\n"
                  "````\nPreserve deployment data.\n")
        prose = faq._prose(source)
        self.assertEqual(prose, "Read existing status. Preserve deployment data.")
        self.assertNotIn("kubectl", prose)
        self.assertNotIn("replicas", prose)

    def test_real_cli_offline_diagnose_save_and_support_summary_roundtrip(self):
        """Use existing synthetic deployment evidence through the actual CLI paths."""
        from doctorlib.rules import evaluate
        for fixture_name in ("rules-docker-standalone.json", "rules-kubernetes-cluster.json"):
            with self.subTest(fixture=fixture_name), tempfile.TemporaryDirectory(prefix="doctor-faq-journey-") as tmp:
                fixture = ROOT / "tests/fixtures" / fixture_name
                snapshot = json.loads(fixture.read_text())
                expected = build_report(snapshot, evaluate(snapshot))
                saved = Path(tmp) / "diagnosis"
                with contextlib.redirect_stdout(io.StringIO()) as output, \
                        contextlib.redirect_stderr(io.StringIO()), \
                        patch("urllib.request.urlopen", side_effect=AssertionError("Network access")), \
                        patch("subprocess.run", side_effect=AssertionError("Subprocess execution")), \
                        patch("doctorlib.collectors.collect", side_effect=AssertionError("Target access")):
                    status = cli.main(["diagnose", "--snapshot", str(fixture),
                                       "--output-dir", str(saved), "--format", "json"])
                self.assertEqual(status, 1)
                report = json.loads(output.getvalue())
                self.assertEqual(report["status"], expected["status"])
                self.assertEqual(report["findings"], expected["findings"])
                self.assertEqual(report, json.loads((saved / "report.json").read_text()))
                self.assertTrue(report["faq_knowledge"]["references"])
                self.assertLessEqual(len(report["faq_knowledge"]["references"]), 3)
                first_url = report["faq_knowledge"]["references"][0]["source_url"]
                for name in ("report.md", "support-summary.md", "support-request.md"):
                    self.assertIn(first_url, (saved / name).read_text())
                context_file = Path(tmp) / "context.json"
                context_file.write_text(json.dumps({"additional_information": "Synthetic user notes: began after a workload change."}))
                with contextlib.redirect_stdout(io.StringIO()) as output, \
                        patch("urllib.request.urlopen", side_effect=AssertionError("Network access")), \
                        patch("subprocess.run", side_effect=AssertionError("Subprocess execution")), \
                        patch("doctorlib.collectors.collect", side_effect=AssertionError("Target access")):
                    status = cli.main(["support-summary", "--report", str(saved / "report.json"),
                                       "--context", str(context_file)])
                self.assertEqual(status, 0)
                summary = output.getvalue()
                self.assertIn(first_url, summary)
                self.assertIn("Additional information (optional):", summary)
                self.assertIn("Synthetic user notes", summary)
                self.assertIn("NOT SUBMITTED", summary)
                self.assertIn("Actions already performed by the user:\nNot provided", summary)


class FaqPackageSelectionTests(unittest.TestCase):
    def test_runtime_selection_includes_all_offline_sources_and_licenses(self):
        names = [str(path.relative_to(ROOT)) for path in (ROOT / "references/faq").rglob("*") if path.is_file()]
        names += ["scripts/doctorlib/faq.py", "references/faq-guide.md"]
        self.assertGreaterEqual(len(names), 19)
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(builder.selected(name))

    def test_networked_maintainer_importer_is_excluded_from_runtime_package(self):
        self.assertFalse(builder.selected("scripts/sync_faq.py"))
        self.assertFalse(builder.selected("scripts/build_release.py"))

    def test_curated_zip_runs_faq_and_preflight_without_developer_source(self):
        """Commit only an isolated fixture repo, build, extract, and run its code."""
        with tempfile.TemporaryDirectory(prefix="doctor-faq-package-") as tmp:
            temporary = Path(tmp)
            repository = temporary / "fixture-repo"
            repository.mkdir()
            environment = os.environ.copy()
            environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                               GIT_AUTHOR_DATE="2026-09-14T12:00:00+00:00",
                               GIT_COMMITTER_DATE="2026-09-14T12:00:00+00:00")

            def git(*arguments):
                return subprocess.run(
                    ["git", "-C", str(repository), "-c", "user.name=FAQ Package Test",
                     "-c", "user.email=faq-package@example.invalid", "-c", "core.hooksPath=/dev/null",
                     "-c", "commit.gpgSign=false"] + list(arguments), env=environment,
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

            git("init", "-q")
            candidates = [ROOT / name for name in builder.ROOT_FILES | builder.LICENSE_FILES if (ROOT / name).is_file()]
            for tree in builder.RUNTIME_TREES:
                candidates.extend(path for path in (ROOT / tree).rglob("*") if path.is_file())
            for source in candidates:
                relative = str(source.relative_to(ROOT))
                if not builder.selected(relative):
                    continue
                self.assertFalse(source.is_symlink(), relative)
                destination = repository / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            # Track the known excluded maintainer files too, so ZIP exclusion is
            # exercised by the actual builder, not just fixture construction.
            for relative in ("scripts/sync_faq.py", "scripts/build_release.py"):
                (repository / relative).write_bytes((ROOT / relative).read_bytes())
            git("add", ".")
            git("commit", "-qm", "Isolated FAQ package fixture")
            commit = git("rev-parse", "HEAD").stdout.strip()
            result = builder.build(repository, commit, temporary / "package")
            self.assertEqual(result["status"], "built_not_published")
            self.assertEqual(result["git_commit"], commit)
            unpacked = temporary / "unpacked"
            with zipfile.ZipFile(result["archive"]) as archive:
                names = archive.namelist()
                for path in ("CHANGELOG.md", "references/faq/catalog.json", "references/faq/provenance.json",
                             "references/faq/licenses/v3.0.x-LICENSE.txt", "references/faq/licenses/v2.6.x-LICENSE.txt",
                             "references/faq/sources/v3.0.x/fragments/cpu_support.md",
                             "references/faq/sources/v2.6.x/fragments/cpu_support.md", "scripts/doctorlib/faq.py"):
                    self.assertIn("milvus-doctor/" + path, names)
                    self.assertEqual(archive.read("milvus-doctor/" + path), (ROOT / path).read_bytes())
                for path in ("scripts/sync_faq.py", "scripts/build_release.py"):
                    self.assertNotIn("milvus-doctor/" + path, names)
                self.assertFalse(any("/__pycache__/" in name or "/tests/" in name for name in names))
                self.assertTrue(all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names))
                # Runtime-facing docs must not link to files excluded from this
                # actual ZIP. Vendored upstream originals have separate provenance
                # and intentionally unbundled images/manuals; they are not UI docs.
                for name in names:
                    if not name.endswith('.md') or '/faq/sources/' in name:
                        continue
                    for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', archive.read(name).decode('utf-8')):
                        target = urlsplit(link)
                        if target.scheme or target.netloc or not target.path:
                            continue
                        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(target.path)))
                        self.assertIn(resolved, names, (name, link))
                archive.extractall(unpacked)

            # -I -S removes cwd, PYTHONPATH, site-packages and user-site hooks.
            # The only application path added is the extracted ZIP's scripts/.
            # Warm stdlib uuid first: Python 3.8's initial platform cache can run
            # uname. Child guards then reject Doctor's network/subprocess attempts.
            runner = """
import pathlib, runpy, socket, subprocess, sys, urllib.request, uuid
entry = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(entry.parent))
sys.argv = [str(entry)] + sys.argv[2:]
def deny(*args, **kwargs):
    raise AssertionError('Offline package test attempted an external action')
socket.socket = deny
socket.create_connection = deny
urllib.request.urlopen = deny
subprocess.Popen = deny
try:
    runpy.run_path(str(entry), run_name='__main__')
finally:
    for name, module in list(sys.modules.items()):
        if name == 'doctorlib' or name.startswith('doctorlib.'):
            origin = pathlib.Path(module.__file__).resolve()
            assert entry.parent in origin.parents, 'Developer source was imported'
"""
            entry = unpacked / "milvus-doctor/scripts/doctor.py"
            cases = (("faq", "--query", "召回率低", "--milvus-version", "2.6.17", "--limit", "1"),
                     ("preflight", "--format", "json"))
            for arguments in cases:
                with self.subTest(command=arguments[0]):
                    completed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", runner, str(entry)] + list(arguments),
                                               cwd=unpacked, env=environment, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True, timeout=30)
                    self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
                    response = json.loads(completed.stdout)
                    if arguments[0] == "faq":
                        self.assertEqual(response["status"], "ok")
                        self.assertEqual(response["selected_docs_version"], "v2.6.x")
                        self.assertEqual(len(response["results"]), 1)
                    else:
                        self.assertEqual(response["status"], "ready")
                        self.assertEqual(response["smoke_test"]["status"], "passed")
                        self.assertTrue(response["read_only"])
            self.assertEqual(git("status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
