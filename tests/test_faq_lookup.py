"""Offline lookup, provenance, version routing, and safe FAQ excerpts."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from doctorlib import faq


def sample_catalog():
    catalog = {"schema_version": 1, "captured_on": "2026-09-14", "default_docs_version": "v3.0.x",
               "versions": [{"docs_version": version, "source_commit": commit}
                            for version, commit in (("v3.0.x", "a" * 40), ("v2.6.x", "b" * 40))],
               "sources": [], "entries": []}
    questions = [
        ("recall", "performance", "Why is search recall low?", "Compare nprobe and nlist with the measured recall under the same workload.", ["recall", "nprobe"], []),
        ("memory", "operational", "Why is QueryNode memory uneven?", "A delegator can keep more growing segment data than other nodes.", ["delegator", "uneven memory"], ["DOCKER_MEMORY_PRESSURE"]),
        ("dimension", "limits", "What is the maximum vector dimension?", "The dimension limit depends on the vector type and selected server version.", ["dimension", "limit"], ["VECTOR_DIMENSION_MISMATCH", "SCHEMA_VECTOR_DIMENSION_INVALID"]),
        ("primary", "product", "Can a primary key use a string?", "Compare VARCHAR primary-key support with the schema and matching documentation.", ["primary key", "varchar"], []),
        ("update", "product", "How can entities be updated?", "Compare supported upsert behavior with the versioned documentation.", ["upsert", "update"], []),
        ("dynamic", "product", "Can the schema use dynamic fields?", "Dynamic fields and JSON support depend on the version and declared schema.", ["dynamic", "schema", "json"], ["FIELD_TYPE_MISMATCH"]),
        ("recovery", "troubleshooting", "How can damaged storage be recovered?", "An old recovery recipe says to remove data.\n```sh\nrm -rf /var/lib/milvus\n```\nThis is destructive.", ["storage", "recovery"], ["DATA_INTEGRITY_ERROR"]),
        ("conflict", "product", "Does Milvus support SQL?", "A historical answer makes a stale claim about SQL support.", ["sql"], []),
    ]
    originals = {}
    for version, commit in (("v3.0.x", "a" * 40), ("v2.6.x", "b" * 40)):
        for category in faq.CATEGORIES:
            source_id = version + "/" + category
            relative = "sources/{}/{}_faq.md".format(version, category)
            source_url = "https://github.com/milvus-io/milvus-docs/blob/{}/site/en/faq/{}_faq.md".format(commit, category)
            docs_url = "https://milvus.io/docs/{}/{}_faq.md".format(version, category)
            section_questions = [item for item in questions if item[1] == category]
            source_body = ""
            for key, _, title, body, keywords, codes in section_questions:
                full_section = "## " + title + "\n\n" + body + "\n"
                start = len(source_body.splitlines()) + 1
                source_body += full_section
                end = len(source_body.splitlines())
                flags = ["version_sensitive"]
                if key == "recovery":
                    flags += ["destructive_recovery", "manual_change_only"]
                if key == "conflict":
                    flags += ["source_conflict"]
                catalog["entries"].append({
                    "id": version + "-" + key, "docs_version": version, "category": category,
                    "title": title, "breadcrumb": [category, title], "body": body.strip(),
                    "source_id": source_id, "source_url": source_url + "#L{}-L{}".format(start, end),
                    "docs_url": docs_url + "#" + key, "source_lines": {"start": start, "end": end},
                    "keywords": keywords, "risk_flags": flags,
                    "warnings": ["Verify the selected version before using this FAQ."],
                    "read_only_guidance": "Compare the declared server version and existing metadata with the official reference. Preserve data and request engineer review for recovery.",
                    "rule_codes": codes, "verify_urls": ["https://milvus.io/docs/limitations.md"],
                })
            encoded = source_body.encode("utf-8")
            originals[relative] = encoded
            catalog["sources"].append({"id": source_id, "docs_version": version,
                "category": category, "title": category.title() + " FAQ", "path": relative,
                "source_url": source_url, "docs_url": docs_url,
                "sha256": hashlib.sha256(encoded).hexdigest(), "section_count": len(section_questions)})
    return catalog, originals


class FaqLookupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "references" / "faq"
        self.root.mkdir(parents=True)
        self.catalog, originals = sample_catalog()
        for relative, payload in originals.items():
            selected = self.root / relative
            selected.parent.mkdir(parents=True, exist_ok=True)
            selected.write_bytes(payload)
        self.save()
        self.root_patch = patch.object(faq, "_FAQ_ROOT", self.root)
        self.path_patch = patch.object(faq, "_CATALOG_PATH", self.root / "catalog.json")
        self.root_patch.start()
        self.path_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.path_patch.stop)

    def save(self):
        (self.root / "catalog.json").write_text(json.dumps(self.catalog, ensure_ascii=False), encoding="utf-8")

    def entry(self, suffix, version="v3.0.x"):
        return next(item for item in self.catalog["entries"] if item["id"] == version + "-" + suffix)

    def update_source_body(self, suffix, body):
        entry = self.entry(suffix)
        entry["body"] = body.strip()
        source = next(item for item in self.catalog["sources"] if item["id"] == entry["source_id"])
        text = ""
        for item in self.catalog["entries"]:
            if item["source_id"] != source["id"]:
                continue
            start = len(text.splitlines()) + 1
            text += "## " + item["title"] + "\n\n" + item["body"] + "\n"
            item["source_lines"] = {"start": start, "end": len(text.splitlines())}
        encoded = text.encode("utf-8")
        (self.root / source["path"]).write_bytes(encoded)
        source["sha256"] = hashlib.sha256(encoded).hexdigest()
        self.save()

    def add_cpu_fragment(self, text="The matching build supports AVX2 instructions.\n"):
        self.update_source_body("memory", "Before include.\n{{fragments/cpu_support.md}}\nAfter include.")
        version = "v3.0.x"
        identity = version + "/fragments/cpu_support.md"
        relative = "sources/" + identity
        data = text.encode("utf-8")
        selected = self.root / relative
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_bytes(data)
        self.catalog["fragments"] = [{"id": identity, "docs_version": version, "path": relative,
            "source_url": "https://github.com/milvus-io/milvus-docs/blob/" + "a" * 40 + "/site/en/fragments/cpu_support.md",
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}]
        entry = self.entry("memory")
        entry["fragment_ids"] = [identity]
        entry["resolved_body"] = entry["body"].replace("{{fragments/cpu_support.md}}", text.strip())
        self.save()

    def test_chinese_queries_rank_the_relevant_title_first(self):
        for query, expected in (("召回率太低 nprobe", "recall"), ("内存不均 delegator", "memory"),
                                ("向量维度上限", "dimension"), ("主键支持字符串吗", "primary"),
                                ("更新数据 upsert", "update"), ("schema动态字段 JSON", "dynamic")):
            with self.subTest(query=query):
                result = faq.search_faq(query)
                self.assertEqual(result["results"][0]["id"], "v3.0.x-" + expected)
                self.assertEqual(result["version_status"], "unconfirmed_reference")
                self.assertIn("not confirmed", result["warnings"][0])

    def test_deterministic_results_and_no_unrelated_zero_score_matches(self):
        self.assertEqual(faq.search_faq("nprobe"), faq.search_faq("nprobe"))
        result = faq.search_faq("unfindablexyzzypqrst")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["total_matches"], 0)

    def test_patch_minor_and_prerelease_versions_route_explicitly(self):
        for value in ("2.6.17", "v2.6.x", "2.6", "2.6.17-rc.1", "v2.6.17-dev+abc1234", "2.6.17.dev3"):
            with self.subTest(value=value):
                result = faq.search_faq("nprobe", milvus_version=value)
                self.assertEqual(result["selected_docs_version"], "v2.6.x")
                self.assertEqual(result["version_status"], "matched_minor_version")
                self.assertTrue(all(item["docs_version"] == "v2.6.x" for item in result["results"]))

    def test_unsupported_version_never_falls_back(self):
        result = faq.search_faq("nprobe", milvus_version="2.4.23")
        self.assertEqual(result["status"], "version_not_bundled")
        self.assertEqual(result["version_status"], "version_not_bundled")
        self.assertEqual(result["selected_docs_version"], "v2.4.x")
        self.assertEqual(result["results"], [])
        self.assertEqual(faq.references_for_findings([{"code": "VECTOR_DIMENSION_MISMATCH"}], "2.4.23"), [])

    def test_lookup_identifiers_do_not_override_explicit_version(self):
        result = faq.get_faq("v2.6.x-recall", milvus_version="3.0.0")
        self.assertEqual(result["status"], "version_mismatch")
        self.assertIsNone(result["entry"])
        self.assertEqual(faq.get_faq("v2.6.x-recall", "2.4.23")["status"], "version_not_bundled")
        older = faq.get_faq("v2.6.x-recall")
        self.assertEqual(older["status"], "ok")
        self.assertEqual(older["selected_docs_version"], "v2.6.x")
        self.assertEqual(older["version_status"], "unconfirmed_reference")
        self.assertEqual(faq.get_faq("missing-id")["status"], "not_found")

    def test_categories_and_limits_filter_before_output(self):
        result = faq.search_faq("schema", category="limits", limit=1)
        self.assertTrue(all(item["category"] == "limits" for item in result["results"]))
        result = faq.search_faq("version", limit=1)
        self.assertEqual(len(result["results"]), 1)
        self.assertGreater(result["total_matches"], 1)
        self.assertTrue(result["truncated"])

    def test_bad_inputs_are_rejected_before_any_catalog_read(self):
        calls = [lambda: faq.search_faq(""), lambda: faq.search_faq("x" * 1001),
                 lambda: faq.search_faq(None), lambda: faq.search_faq("a", category="../sources"),
                 lambda: faq.get_faq("../../secret"), lambda: faq.get_faq("x" * 129)]
        calls += [lambda value=value: faq.search_faq("nprobe", limit=value) for value in (0, 11, True, 1.5, "2")]
        calls += [lambda value=value: faq.search_faq("nprobe", milvus_version=value)
                  for value in ("", "latest", "3.0.0; echo hi", "2.6/../../../", "x" * 101, 3)]
        with patch.object(faq, "load_catalog") as loader:
            for call in calls:
                with self.assertRaises(ValueError):
                    call()
            loader.assert_not_called()

    def test_query_redaction_happens_before_search_and_echo(self):
        result = faq.search_faq("nprobe password=DEMO_FAQ_SECRET user@example.org")
        serialized = json.dumps(result)
        self.assertNotIn("DEMO_FAQ_SECRET", serialized)
        self.assertNotIn("user@example.org", serialized)
        self.assertIn("[REDACTED]", result["query"])
        self.assertEqual(result["results"][0]["id"], "v3.0.x-recall")

    def test_destructive_and_conflicting_bodies_use_safe_guidance(self):
        for identity in ("v3.0.x-recovery", "v3.0.x-conflict"):
            with self.subTest(identity=identity):
                result = faq.get_faq(identity)["entry"]
                self.assertEqual(result["excerpt_source"], "read_only_guidance")
                self.assertEqual(result["excerpt"], result["read_only_guidance"])
                self.assertNotIn("rm -rf", json.dumps(result))
                self.assertNotIn("stale claim", json.dumps(result))
                self.assertNotIn("body", result)

    def test_unflagged_destructive_recipe_is_still_withheld(self):
        self.entry("recovery")["risk_flags"] = []
        self.save()
        result = faq.get_faq("v3.0.x-recovery")["entry"]
        self.assertEqual(result["excerpt_source"], "read_only_guidance")
        self.assertNotIn("rm -rf", json.dumps(result))

    def test_business_data_iterator_prose_is_withheld_from_default_excerpts(self):
        self.update_source_body("recall", "To get every distinct value, iterate all business rows using query_iterator and collect the complete dataset.")
        self.entry("recall")["risk_flags"] = ["business_data_access"]
        self.save()
        result = faq.get_faq("v3.0.x-recall")["entry"]
        self.assertEqual(result["excerpt_source"], "read_only_guidance")
        self.assertEqual(result["excerpt"], result["read_only_guidance"])
        self.assertNotIn("iterate all business rows", json.dumps(result))
        self.assertNotIn("query_iterator", json.dumps(result))

    def test_regular_excerpts_strip_code_links_and_mark_truncation(self):
        self.update_source_body("recall", "Useful summary. ![remote](https://example.org/asset.png) ![reference][picture] <img src='https://example.org/other.png'>\n```python\nsecret_looking_code()\n```\n" + "explanation " * 180 + "[bad](javascript:alert) <script>hidden()</script>")
        result = faq.get_faq("v3.0.x-recall")["entry"]
        self.assertEqual(result["excerpt_source"], "body")
        self.assertTrue(result["excerpt_truncated"])
        self.assertEqual(len(result["excerpt"]), faq.MAX_EXCERPT_CHARS)
        self.assertNotIn("secret_looking_code", result["excerpt"])
        self.assertNotIn("javascript:", result["excerpt"])
        self.assertNotIn("![", result["excerpt"])
        self.assertNotIn("https://example.org", result["excerpt"])
        self.assertNotIn("<img", result["excerpt"])

    def test_exact_rule_code_references_are_compact_and_do_not_echo_finding_text(self):
        findings = [{"code": "VECTOR_DIMENSION_MISMATCH", "evidence": ["DEMO_PRIVATE_NAME"], "summary": "private"},
                    {"code": "FIELD_TYPE_MISMATCH"}]
        result = faq.references_for_findings(findings, "2.6.17", limit=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["docs_version"], "v2.6.x")
        self.assertNotIn("excerpt", result[0])
        self.assertNotIn("DEMO_PRIVATE_NAME", json.dumps(result))
        self.assertEqual(result, faq.references_for_findings(list(reversed(findings)), "2.6.17", limit=1))
        self.assertEqual(faq.references_for_findings([{"code": "UNMAPPED_CODE", "summary": "nprobe"}]), [])

    def test_fragment_resolution_is_exact_and_searchable_with_provenance(self):
        self.add_cpu_fragment()
        result = faq.search_faq("AVX2", "3.0.0")
        self.assertEqual(result["results"][0]["id"], "v3.0.x-memory")
        item = result["results"][0]
        self.assertEqual(item["excerpt_source"], "resolved_body")
        self.assertIn("Before include. The matching build supports AVX2 instructions. After include.", item["excerpt"])
        self.assertNotIn("{{fragments", item["excerpt"])
        self.assertEqual(item["fragment_source_urls"], [self.catalog["fragments"][0]["source_url"]])
        self.assertEqual(faq.catalog_overview()["fragment_count"], 1)
        self.assertEqual(faq.search_faq("AVX2", "2.6.17")["results"], [])

    def test_fragment_errors_fail_closed_without_arbitrary_expansion(self):
        self.add_cpu_fragment()
        original = copy.deepcopy(self.catalog)
        memory_index = next(index for index, item in enumerate(self.catalog["entries"]) if item["id"] == "v3.0.x-memory")
        mutations = [lambda c: c["fragments"][0].update(path="sources/v3.0.x/fragments/../../outside.md"),
                     lambda c: c["fragments"][0].update(docs_version="v2.6.x"),
                     lambda c: c["fragments"][0].update(sha256="0" * 64),
                     lambda c: c["fragments"][0].update(bytes=1),
                     lambda c: c["fragments"][0].update(source_url="https://github.com/milvus-io/milvus-docs/blob/" + "b" * 40 + "/site/en/fragments/cpu_support.md"),
                     lambda c: c.update(fragments=[]),
                     lambda c: c["entries"][memory_index].update(fragment_ids=["v2.6.x/fragments/cpu_support.md"]),
                     lambda c: c["entries"][memory_index].update(resolved_body="Invented CPU support claim")]
        for mutate in mutations:
            self.catalog = copy.deepcopy(original)
            mutate(self.catalog)
            self.save()
            with self.assertRaises(faq.CatalogError):
                faq.load_catalog()
        self.catalog = copy.deepcopy(original)
        selected = self.root / self.catalog["fragments"][0]["path"]
        selected.write_text("Changed fragment bytes", encoding="utf-8")
        self.save()
        with self.assertRaises(faq.CatalogError):
            faq.load_catalog()

    def test_unknown_nested_and_unbacked_fragments_are_rejected(self):
        self.update_source_body("memory", "{{fragments/not-allowlisted.md}}")
        with self.assertRaises(faq.CatalogError):
            faq.load_catalog()
        self.add_cpu_fragment("Nested {{fragments/cpu_support.md}}")
        with self.assertRaises(faq.CatalogError):
            faq.load_catalog()
        self.add_cpu_fragment()
        self.entry("recall")["resolved_body"] = "Unbacked alternate text"
        self.save()
        with self.assertRaises(faq.CatalogError):
            faq.load_catalog()

    def test_resolved_destructive_fragment_cannot_become_guidance(self):
        self.add_cpu_fragment("The historical command is rm -rf /data; preserve data instead.")
        result = faq.get_faq("v3.0.x-memory")["entry"]
        self.assertEqual(result["excerpt_source"], "read_only_guidance")
        self.assertNotIn("rm -rf", json.dumps(result))

    def test_saved_references_reproject_forged_text_links_and_version_status(self):
        record = copy.deepcopy(self.entry("dimension"))
        record.update(title="DEMO_FORGED_TITLE", docs_url="javascript:DEMO_FORGED_URL",
                      read_only_guidance="DEMO_FORGED_GUIDANCE", warnings=["DEMO_FORGED_WARNING"],
                      version_status="verified_production_safe", risk_flags=[])
        result = faq.resolve_saved_references([record])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], self.entry("dimension")["title"])
        self.assertEqual(result[0]["docs_url"], self.entry("dimension")["docs_url"])
        self.assertEqual(result[0]["version_status"], "unconfirmed_reference")
        self.assertNotIn("DEMO_FORGED", json.dumps(result))
        self.assertEqual(faq.resolve_saved_references([record], "3.0.0")[0]["version_status"], "matched_minor_version")
        self.assertEqual(faq.resolve_saved_references([record], "2.6.17"), [])
        self.assertEqual(faq.resolve_saved_references([record], "2.4.23"), [])
        older = faq.resolve_saved_references([self.entry("dimension", "v2.6.x")])
        self.assertEqual(older[0]["docs_version"], "v2.6.x")
        self.assertEqual(older[0]["version_status"], "unconfirmed_reference")

    def test_saved_references_require_matching_provenance_and_bounded_identity_input(self):
        original = copy.deepcopy(self.entry("dimension"))
        for fields in ({"id": "unknown"}, {"source_url": "https://evil.example/data"},
                       {"docs_version": "v2.6.x"}, {"source_url": "x" * 1601},
                       {"id": []}, {"docs_version": []}):
            self.assertEqual(faq.resolve_saved_references([dict(original, **fields)]), [])
        self.assertEqual(len(faq.resolve_saved_references([original] * 20)), 1)
        self.assertEqual(len(faq.resolve_saved_references(self.catalog["entries"])), 3)
        self.assertEqual(faq.resolve_saved_references([None] * 10 + [original]), [])
        self.assertEqual(faq.resolve_saved_references([original], "invalid-version"), [])
        with patch.object(faq, "load_catalog") as loader:
            for records in (None, {}, [], [None], [{"id": "../../outside"}]):
                self.assertEqual(faq.resolve_saved_references(records), [])
            loader.assert_not_called()
        with patch.object(faq, "load_catalog", side_effect=faq.CatalogError("Missing bundle")):
            self.assertEqual(faq.resolve_saved_references([original]), [])

    def test_overview_has_all_five_categories_and_versions(self):
        result = faq.catalog_overview()
        self.assertEqual(set(result["categories"]), set(faq.CATEGORIES))
        self.assertEqual(result["source_count"], 10)
        self.assertEqual(result["entry_count"], 16)
        self.assertEqual(len(result["versions"]), 2)
        self.assertNotIn("body", json.dumps(result))

    def test_all_apis_are_offline_and_do_not_run_commands(self):
        with patch.object(socket, "socket", side_effect=AssertionError("network access")), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("command execution")), \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("network access")):
            self.assertTrue(faq.search_faq("nprobe")["results"])
            self.assertEqual(faq.get_faq("v3.0.x-recall")["status"], "ok")
            self.assertEqual(faq.catalog_overview()["source_count"], 10)
            self.assertTrue(faq.references_for_findings([{"code": "VECTOR_DIMENSION_MISMATCH"}]))
            self.assertTrue(faq.resolve_saved_references([self.entry("dimension")]))

    def test_invalid_catalog_shapes_fail_closed_without_exposing_payload(self):
        mutations = [lambda c: c.update(schema_version=True),
                     lambda c: c.update(entries=c["entries"] * 40),
                     lambda c: c.update(entries=[None]),
                     lambda c: c["entries"][0].update(title=42),
                     lambda c: c["entries"][0].update(keywords="nprobe"),
                     lambda c: c["entries"][0].update(risk_flags=["invented"]),
                     lambda c: c["entries"][0].update(source_lines={"start": 5, "end": 2}),
                     lambda c: c["entries"][0].update(body="Modified index text differs from the preserved original."),
                     lambda c: c["sources"][0].update(section_count=99),
                     lambda c: c["sources"].pop(),
                     lambda c: c.update(captured_on="2026-02-31"),
                     lambda c: c["entries"][0].update(source_id="missing"),
                     lambda c: c["entries"][0].update(docs_version="v2.4.x"),
                     lambda c: c["entries"][0].update(read_only_guidance="Run rm -rf /data"),
                     lambda c: c["entries"].append(copy.deepcopy(c["entries"][0]))]
        original = copy.deepcopy(self.catalog)
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.catalog = copy.deepcopy(original)
                mutate(self.catalog)
                self.save()
                with self.assertRaises(faq.CatalogError):
                    faq.search_faq("nprobe")

    def test_url_allowlist_rejects_queries_traversal_mutable_and_external_sources(self):
        original = copy.deepcopy(self.catalog)
        cases = [("docs_url", "javascript:alert(1)"),
                 ("docs_url", "file:///etc/passwd"),
                 ("docs_url", "https://milvus.io/docs/../../secrets.md"),
                 ("docs_url", "https://milvus.io/docs/%2e%2e/secrets.md"),
                 ("docs_url", "https://milvus.io/docs/product_faq.md?token=DEMO_PRIVATE_VALUE"),
                 ("docs_url", "https://milvus.io.evil.example/docs/product_faq.md"),
                 ("source_url", "https://github.com/milvus-io/milvus-docs/blob/master/site/en/faq/product_faq.md"),
                 ("source_url", "https://github.com/other/milvus-docs/blob/" + "a" * 40 + "/site/en/faq/product_faq.md")]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.catalog = copy.deepcopy(original)
                self.catalog["entries"][0][field] = value
                self.save()
                with self.assertRaises(faq.CatalogError) as error:
                    faq.load_catalog()
                self.assertNotIn("DEMO_PRIVATE_VALUE", str(error.exception))

    def test_source_path_traversal_and_modified_original_fail_closed(self):
        original = copy.deepcopy(self.catalog)
        for value in ("../outside.md", "/etc/passwd", "sources/v3.0.x/../../outside.md", "sources\\outside.md"):
            self.catalog = copy.deepcopy(original)
            self.catalog["sources"][0]["path"] = value
            self.save()
            with self.assertRaises(faq.CatalogError):
                faq.load_catalog()
        self.catalog = original
        self.save()
        source = self.root / self.catalog["sources"][0]["path"]
        source.write_text("modified local source", encoding="utf-8")
        with self.assertRaises(faq.CatalogError):
            faq.load_catalog()

    def test_missing_bundle_or_catalog_corruption_does_not_break_finding_references(self):
        selected = self.root / "catalog.json"
        for payload in (b"not-json", b"\xff", b'{"schema_version":1,"schema_version":1}', b"x" * (faq.MAX_CATALOG_BYTES + 1)):
            selected.write_bytes(payload)
            with self.assertRaises(faq.CatalogError):
                faq.load_catalog()
            self.assertEqual(faq.references_for_findings([{"code": "VECTOR_DIMENSION_MISMATCH"}]), [])
        selected.unlink()
        with self.assertRaises(faq.CatalogError):
            faq.load_catalog()

    def test_catalog_and_source_symlinks_are_rejected(self):
        selected = self.root / "catalog.json"
        backup = self.root / "catalog.saved"
        selected.rename(backup)
        selected.symlink_to(backup)
        with self.assertRaises(faq.CatalogError):
            faq.load_catalog()
        selected.unlink()
        backup.rename(selected)
        source_dir = self.root / "sources" / "v3.0.x"
        backup_dir = self.root / "sources" / "saved"
        source_dir.rename(backup_dir)
        source_dir.symlink_to(backup_dir, target_is_directory=True)
        with self.assertRaises(faq.CatalogError):
            faq.load_catalog()


class BundledFaqTests(unittest.TestCase):
    def test_real_bundle_is_complete_and_cross_version(self):
        overview = faq.catalog_overview()
        self.assertEqual(overview["source_count"], 10)
        self.assertGreaterEqual(overview["entry_count"], 160)
        self.assertEqual(overview["fragment_count"], 2)
        for version in ("v3.0.x", "v2.6.x"):
            categories = {source["category"] for source in overview["sources"] if source["docs_version"] == version}
            self.assertEqual(categories, set(faq.CATEGORIES))

    def test_real_chinese_queries_retrieve_relevant_official_questions(self):
        for query, title_fragment in (("召回率 nprobe", "nprobe"), ("召回率低", "factors affecting recall"),
                                      ("日志时间差", "time in the log files"),
                                      ("内存不均 delegator", "memory usage is unbalanced"),
                                      ("向量维度上限", "maximum vector dimension"),
                                      ("主键支持字符串吗", "primary keys"), ("更新数据 upsert", "update operation"),
                                      ("schema动态字段 JSON", "dynamic fields")):
            with self.subTest(query=query):
                result = faq.search_faq(query, milvus_version="2.6.17")
                self.assertIn(title_fragment, result["results"][0]["title"].casefold())
                self.assertEqual(result["results"][0]["docs_version"], "v2.6.x")

    def test_all_actual_guarded_sections_withhold_originals(self):
        catalog = faq.load_catalog()
        guarded = [item for item in catalog["entries"] if set(item["risk_flags"]) & {"destructive_recovery", "source_conflict", "business_data_access"}]
        self.assertTrue(any("destructive_recovery" in entry["risk_flags"] for entry in guarded))
        for entry in guarded:
            with self.subTest(identity=entry["id"]):
                result = faq.get_faq(entry["id"], entry["docs_version"])["entry"]
                self.assertEqual(result["excerpt_source"], "read_only_guidance")
                self.assertNotIn("body", result)
                self.assertNotIn("![", result["excerpt"])
                self.assertNotIn("```", result["excerpt"])


if __name__ == "__main__":
    unittest.main()
