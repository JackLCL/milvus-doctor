#!/usr/bin/env python3
"""Maintainer-only import of official Milvus FAQ snapshots into a NEW folder.

Not invoked by diagnosis or lookup. Downloads only fixed official documentation
paths; does not execute their content, modify a cluster, or overwrite an old bundle.
"""
import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

REPOSITORY = "milvus-io/milvus-docs"
VERSIONS = ("v3.0.x", "v2.6.x")
PAGES = (
    ("performance", "Performance FAQs", "site/en/faq/performance_faq.md"),
    ("product", "Product FAQs", "site/en/faq/product_faq.md"),
    ("operational", "Operational FAQs", "site/en/faq/operational_faq.md"),
    ("limits", "Milvus Limits", "site/en/about/limitations.md"),
    ("troubleshooting", "Troubleshooting", "site/en/faq/troubleshooting.md"),
)
BASE_GUIDANCE = {
    "performance": "Correlate the documented scenario with the observed index/load state, workload and incident window. Defaults and tuning formulas are not universal prescriptions; benchmarks and changes belong to the user.",
    "product": "Use this as product reference, not proof about the selected deployment. Confirm server/SDK version and relevant schema or configuration; Doctor does not query business rows or execute writes.",
    "operational": "Compare the reference with selected status, configuration and approved redacted logs. Installation, configuration changes, restarts and workload execution remain user actions outside Doctor.",
    "limits": "Confirm the exact version, field type and effective configuration before comparing a workload with a documented limit. Defaults are not observed values; do not change settings or data to bypass a limit.",
    "troubleshooting": "Start with scoped status/events and approved redacted logs. Preserve existing data and separate a possible cause from established evidence. Recovery and mutations belong to the owner/engineering team.",
}


def fetch(url, maximum=524288, optional=False):
    request = urllib.request.Request(url, headers={"User-Agent": "milvus-doctor-faq-maintainer/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.geturl() != url:
                raise ValueError("Unexpected documentation redirect; inspect the source before importing")
            data = response.read(maximum + 1)
            if len(data) > maximum: raise ValueError("Official source exceeds import bound")
            return data
    except urllib.error.HTTPError as error:
        if optional and error.code == 404: return None
        raise


def resolve(version):
    value = json.loads(fetch("https://api.github.com/repos/" + REPOSITORY + "/commits/" + version))
    commit = value.get("sha")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit): raise ValueError("Invalid official commit identity")
    return commit


def sections(text):
    """Partition every nonempty Markdown section, ignoring fenced-code headings."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---": start = index + 1; break
        else: raise ValueError("Unterminated source frontmatter")
    headings = []; fence = None; breadcrumb = []
    for index in range(start, len(lines)):
        line = lines[index]
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            continue
        if marker and (marker[1][0] != '`' or '`' not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            continue
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            level = len(match[1]); title = re.sub(r"\s*\{#[^}]+\}\s*$", "", match[2]).strip()
            while breadcrumb and breadcrumb[-1][0] >= level: breadcrumb.pop()
            breadcrumb.append((level, title))
            headings.append((index, title, [value for _, value in breadcrumb]))
    if not headings: raise ValueError("No source sections found")
    result = []
    # Source bytes are retained separately; section extraction preserves content,
    # tables and fenced code without interpreting commands.
    for number, (index, title, parents) in enumerate(headings):
        end = headings[number + 1][0] if number + 1 < len(headings) else len(lines)
        body = "\n".join(lines[index + 1:end]).strip()
        if body:
            result.append({"title": title, "breadcrumb": parents, "body": body, "source_lines": {"start": index + 1, "end": end}})
    return result


def annotate(category, title, body):
    """Reviewed cautions are distinct from the unchanged official source text."""
    t = title.lower(); b = body.lower()
    flags = set(); warnings = []; urls = []; codes = []; tags = []
    guidance = BASE_GUIDANCE[category]
    if category == "limits" or re.search(r"default|maximum|minimum|limit|\bv?\d+\.\d+(?:\.\d+)?\b|supported|not support|annoy", t + " " + b):
        flags.add("version_sensitive")
        warnings.append("A documentation branch is not proof that every sentence applies to this server patch or configuration; verify version-sensitive statements.")
    if re.search(r"create_index|load_collection|load_partition|\bflush\(|pip install|docker (?:run|restart)|helm |restart|set config-etcd|change .*config|update .*config|create_collection|upsert\(", b):
        flags.add("manual_change_only")
    if "etcd" in " ".join([t, b]) and re.search(r"delete.*(?:pvc|member_id)|\bbbolt\b|modify database|restore the backup", b):
        flags.update(("destructive_recovery", "manual_change_only"))
        warnings.append("The original page includes destructive etcd/PVC/metadata recovery. Its commands are NOT approved Doctor actions or a validated recovery recipe.")
        guidance = "Preserve the selected etcd data and evidence. Have the data owner and engineering team review version, topology, backups and recovery prerequisites. Doctor must not enter Pods, remove member files/PVCs, scale workloads, copy or edit the etcd database, or run recovery commands."
        tags += ["etcd", "crash", "崩溃", "元数据", "恢复", "PVC", "member_id", "bad member ID"]
    if "query_iterator" in b or "all the unique" in t:
        flags.add("business_data_access")
        warnings.append("The source's iterator reads business rows and may be unbounded. This exceeds Doctor's metadata-only access; do not run it.")
        guidance = "Clarify whether the user needs distinct scalar values and check version-matched query capabilities. Do not iterate rows or deduplicate user data through Doctor; the application/data owner performs any approved data query."
    def caution(message, replacement, *pages):
        nonlocal guidance
        flags.update(("source_conflict", "version_sensitive")); warnings.append(message); guidance = replacement
        urls.extend("https://milvus.io/docs/" + page for page in pages)
    if "schema changes" in t:
        caution("The FAQ's 2.5.0 statement predates current schema-alteration support.",
                "Do not repeat the historical blanket ban on schema changes. Current schema-alteration documentation distinguishes nullable scalar additions (2.6.x), nullable vector additions (2.6.18+), and 3.0-specific capabilities. Confirm the exact patch and field type against that guide; all schema changes remain user actions.", "add-fields-to-an-existing-collection.md")
        tags += ["schema", "add field", "新增字段", "修改Schema", "schema变更"]
    if "limitations of using dynamic fields" in t:
        caution("The FAQ quotes a 2.5.1 indexing restriction; current dynamic-field/JSON indexing guides describe later support.",
                "Check the exact server patch, JSON path/type and index metadata against the version-matched JSON/dynamic-field guide. Do not claim dynamic fields universally lack indexing, and do not create or change an index through Doctor.", "enable-dynamic-field.md", "json-indexing.md")
        tags += ["dynamic field", "JSON index", "动态字段", "索引"]
    if "maximum length of self-defined entity primary keys" in t:
        caution("This FAQ's INT64-only assertion conflicts with the primary-field guide and another question on the same page.",
                "Milvus primary-field guidance covers INT64 and VARCHAR. Inspect the selected schema and configured VARCHAR length/ID policy; do not infer the primary-key type or range from this historical FAQ answer.", "primary-field.md")
        tags += ["primary key", "varchar", "字符串", "主键"]
        codes += ["FIELD_TYPE_MISMATCH"]
    if "treat it as an update" in t:
        caution("The source mixes ordinary duplicate-key insertion with a historical statement that updates are unsupported.",
                "Ordinary insert is not equivalent to explicit upsert. Check the version-matched upsert behavior and the user's actual API call; do not replay writes, query business rows or assume duplicate-key resolution from this FAQ.", "upsert-entities.md")
        tags += ["upsert", "duplicate primary key", "重复主键", "更新"]
    if "default values" in t:
        caution("The FAQ explicitly describes a historical 2.4.x limitation, not a universal current-version restriction.",
                "Current documentation supports default values for eligible scalar fields, with exclusions and constraints. Confirm version, field type and supplied schema against the default-value guide; do not infer default support for primary/vector/JSON/ARRAY fields or change the schema through Doctor.", "default-values.md")
        tags += ["default value", "默认值", "nullable"]
    if "maximum amount of data" in t or "create index for a segment before" in t or (category == "limits" and ("operations" in t or "rpc" in t)):
        caution("FAQ payload/batch statements differ from the Limits page; a recommendation is not the effective RPC ceiling.",
                "Confirm server/SDK version, effective RPC receive limits and the request's encoded byte size. Official FAQ pages quote different payload/batch figures; do not choose a universal limit or suggest a larger batch from those figures alone. Any batching/configuration change is a user action.", "limitations.md", "configure_proxy.md")
        tags += ["insert", "batch", "RPC", "payload", "64MB", "1024MB", "批量", "写入大小", "请求过大"]
        codes += ["REQUEST_SIZE_EXCEEDED"]
    if "total number of collections" in t or (category == "limits" and ("resources of a milvus instance" in t or t == "number of resources")):
        caution("FAQ collection-count numbers and capacity-unit explanations require reconciliation with the dedicated limits guide.",
                "Distinguish collection count, per-database count and shard-times-partition capacity units (maxGeneralCapacity). Check the effective quota settings and version-matched limits; do not conflate these quantities or treat FAQ defaults as observed configuration.", "limit_collection_counts.md", "limitations.md")
        tags += ["collection limit", "partition", "shard", "集合数量", "分区", "配额"]
    if category == "limits" and "resources in a collection" in t:
        caution("The table's index count can be misread as one index for the entire collection; index guidance discusses per-field limits.",
                "Read index limits with field scope and compare the actual schema and index metadata with the version-matched guide. Do not conclude that a collection may contain only one indexed field from this table.", "index-vector-fields.md")
    if "configuration files not take effect" in t:
        caution("The blanket restart statement does not distinguish file configuration from supported dynamic changes.",
                "Identify the setting, version, effective value and configuration source. Some supported settings can be changed dynamically; others require the owner's rollout/restart procedure. Doctor only compares selected evidence and does not change configuration or restart services.", "dynamic_config.md")
        tags += ["configuration", "restart", "配置不生效", "热更新"]
    if "smaller datasets" in t or "simultaneously inserting" in t:
        caution("The brute-force/threshold explanation is conditional; current QueryNode documentation includes interim-index behavior.",
                "Inspect index/load state and effective interim-index settings, then correlate with the workload. A small dataset alone does not establish brute-force search or the cause of latency. Do not force index creation/flush or apply a historical threshold as a repair.", "configure_querynode.md")
        tags += ["small dataset", "latency", "interim index", "小数据", "查询慢", "延迟"]
        codes += ["COLLECTION_NO_VECTOR_INDEX", "LOG_INDEX_MISSING"]
    if "cpu usage" in t:
        warnings.append("The Annoy-specific assertion is historical/version-dependent; verify the available index types and workload rather than generalizing it.")
        tags += ["CPU", "性能", "利用率"]
    if "failed to pull" in t:
        flags.update(("version_sensitive", "manual_change_only", "source_conflict"))
        warnings.append("Historical registry-mirror examples require independent availability/trust verification; do not configure a mirror or daemon automatically.")
        guidance = "Check the selected image reference, error and approved registry connectivity/authentication. Ask the environment owner to validate any mirror against organizational policy; Doctor must not edit Docker daemon configuration or install images as a repair."
        tags += ["ImagePullBackOff", "ErrImagePull", "镜像拉取"]
        codes += ["K8S_IMAGE_PULL_FAILURE"]
    if "started successfully" in t:
        flags.add("version_sensitive")
        warnings.append("A three-container example is deployment-specific; container count/running state alone is not readiness or health.")
        guidance = "Use the user's actual deployment mode and selected health/readiness evidence. Do not require exactly three containers or treat running processes as proof of service health."
        codes += ["HEALTH_CHECK_FAILED", "DOCKER_NOT_RUNNING", "DOCKER_UNHEALTHY", "K8S_POD_NOT_READY"]
    if "illegal instruction" in t or "cpu supports" in t or "non-x86" in t or "apple m1" in t:
        flags.add("version_sensitive")
        warnings.append("Architecture, build and CPU-feature support are version-specific. An illegal-instruction marker alone is not complete hardware diagnosis.")
        tags += ["CPU", "ARM", "SIMD", "illegal instruction", "非法指令", "架构"]
    if "delegator" in t or "memory usage is unbalanced" in t:
        tags += ["delegator", "querynode", "memory", "内存不均", "内存倾斜", "内存不平衡"]
        if "unbalanced" in t:
            flags.add("manual_change_only")
            guidance = "Compare per-node memory and the incident/workload context before attributing imbalance to a delegator. Birdwatcher/config-etcd changes in the FAQ are outside Doctor; ask the owner/engineer to review sizing or balancing, without modifying etcd or configuration."
            codes += ["DOCKER_MEMORY_PRESSURE", "MEMORY_QUOTA_EXCEEDED"]
    if "nlist" in t or "recall" in t or "fewer" in t or "topk" in t:
        tags += ["recall", "nprobe", "nlist", "ef", "召回率", "结果不足", "搜不到"]
    if "maximum vector dimension" in t or (category == "limits" and ("field" in t or "dimension" in t)):
        tags += ["dimension", "vector", "limit", "维度", "上限"]
        codes += ["VECTOR_DIMENSION_MISMATCH", "SCHEMA_VECTOR_DIMENSION_INVALID"]
    if "load the entire collection" in t:
        tags += ["load", "partition", "collection not loaded", "未加载"]
        codes += ["COLLECTION_NOT_LOADED", "LOG_COLLECTION_NOT_LOADED"]
    if "logs generated" in t or "boot issues" in t:
        tags += ["logs", "startup", "启动失败", "日志"]
        guidance = "For approved stdout/stderr, use Doctor's bounded export-logs workflow with exact targets, time window and optional previous-container logs. For file-persisted logs ask the owner for a selected local excerpt; never use exec/cp or change log configuration to obtain them."
        codes += ["INTERNAL_PANIC", "K8S_CRASH_LOOP", "K8S_CONTAINER_START_FAILURE"]
    if "length exceeds max length" in t:
        tags += ["VARCHAR", "JSON", "UTF-8", "bytes", "长度超限", "字节", "中文"]
        codes += ["FIELD_TYPE_MISMATCH", "REQUEST_SIZE_EXCEEDED"]
    if "etcd pod pending" in t:
        tags += ["PVC", "Pending", "StorageClass", "存储", "调度"]
        codes += ["K8S_PVC_NOT_BOUND"]
    if "runtime issues" in t:
        tags += ["compatibility", "SDK", "运行异常", "兼容"]
        codes += ["CONNECTION_REFUSED", "CONNECTION_TIMEOUT"]
    if "milvus_lite" in t or "illegal uri" in t:
        tags += ["Lite", "SDK", "illegal uri", "本地数据库", "连接"]
        warnings.append("Do not open a Lite database through MilvusClient for diagnosis; it may create/modify local database state. SDK installation/upgrade is a separate user-approved local setup action.")
    if "windows" in t:
        warnings.append("Milvus/SDK platform statements do not establish Milvus Doctor support. Doctor runtime is validated on Linux only.")
    return {"risk_flags": sorted(flags), "warnings": list(dict.fromkeys(warnings)), "verify_urls": list(dict.fromkeys(urls)),
            "read_only_guidance": guidance, "keywords": sorted(set(tags)), "rule_codes": sorted(set(codes))}


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as stream: stream.write(data)


def build_bundle(output, versions=VERSIONS, refs=None, reader=fetch, captured_on=None):
    output = Path(output)
    if os.path.lexists(output): raise ValueError("Output already exists; import into a fresh directory and review before adoption")
    if not output.parent.is_dir(): raise ValueError("Output parent must already exist")
    if not versions or len(set(versions)) != len(versions) or any(v not in VERSIONS for v in versions): raise ValueError("Unsupported documentation versions")
    refs = refs or {}; captured_on = captured_on or date.today().isoformat()
    catalog = {"schema_version": 1, "captured_on": captured_on, "default_docs_version": versions[0], "versions": [], "sources": [], "entries": [], "fragments": []}
    files = {}; licenses = []
    for version in versions:
        commit = refs.get(version) or resolve(version)
        if not re.fullmatch(r"[0-9a-f]{40}", commit): raise ValueError("Import refs must be full immutable commits")
        catalog["versions"].append({"docs_version": version, "source_commit": commit})
        raw_base = "https://raw.githubusercontent.com/" + REPOSITORY + "/" + commit + "/"
        license_bytes = reader(raw_base + "LICENSE", maximum=32768)
        if not license_bytes or b"Apache License" not in license_bytes or b"Version 2.0" not in license_bytes:
            raise ValueError("Upstream license requires explicit review; no FAQ content imported")
        files["licenses/" + version + "-LICENSE.txt"] = license_bytes
        notice = reader(raw_base + "NOTICE", maximum=32768, optional=True)
        if notice is not None: files["licenses/" + version + "-NOTICE.txt"] = notice
        licenses.append({"docs_version": version, "source_commit": commit, "spdx": "Apache-2.0", "notice_present": notice is not None})
        for category, title, path in PAGES:
            data = reader(raw_base + path, maximum=131072)
            text = data.decode("utf-8"); parts = sections(text)
            if not parts: raise ValueError("Empty FAQ category: " + category)
            relative = "sources/" + version + "/" + Path(path).name
            files[relative] = data
            source_id = version + "/" + category
            docs_url = "https://milvus.io/docs/" + version + "/" + Path(path).name
            source_url = "https://github.com/" + REPOSITORY + "/blob/" + commit + "/" + path
            catalog["sources"].append({"id": source_id, "docs_version": version, "category": category, "title": title,
                "path": relative, "source_url": source_url, "docs_url": docs_url, "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data), "section_count": len(parts), "license": "Apache-2.0"})
            for part in parts:
                identity = hashlib.sha256(" / ".join(part["breadcrumb"]).encode()).hexdigest()[:10]
                entry = {"id": version[1:-2] + "-" + category + "-" + identity, "docs_version": version, "category": category,
                         "source_id": source_id, "source_url": source_url, "docs_url": docs_url, **part}
                include_tokens = re.findall(r"\{\{([^}]+)\}\}", part["body"])
                resolved_body = part["body"]
                if include_tokens:
                    if set(include_tokens) != {"fragments/cpu_support.md"}:
                        raise ValueError("Unreviewed FAQ fragment; extend the fixed importer after review")
                    fragment_path = "site/en/fragments/cpu_support.md"
                    fragment_data = reader(raw_base + fragment_path, maximum=32768)
                    fragment_id = version + "/fragments/cpu_support.md"
                    stored_path = "sources/" + version + "/fragments/cpu_support.md"
                    files[stored_path] = fragment_data
                    catalog["fragments"].append({"id": fragment_id, "docs_version": version, "path": stored_path,
                        "source_url": "https://github.com/" + REPOSITORY + "/blob/" + commit + "/" + fragment_path,
                        "sha256": hashlib.sha256(fragment_data).hexdigest(), "bytes": len(fragment_data)})
                    resolved_body = resolved_body.replace("{{fragments/cpu_support.md}}", fragment_data.decode("utf-8").strip())
                    entry.update(fragment_ids=[fragment_id], resolved_body=resolved_body)
                entry.update(annotate(category, part["title"], resolved_body))
                catalog["entries"].append(entry)
    ids = [entry["id"] for entry in catalog["entries"]]
    if len(ids) != len(set(ids)) or len(ids) > 500: raise ValueError("Duplicate/excessive FAQ section identity; inspect source structure")
    payload = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode()
    if len(payload) > 2 * 1024 * 1024: raise ValueError("FAQ catalog exceeds the runtime bound")
    files["catalog.json"] = payload
    files["provenance.json"] = (json.dumps({"repository": REPOSITORY, "captured_on": captured_on, "licenses": licenses,
        "source_files_unmodified": True, "derived_catalog": "Section extraction, retrieval keywords, risk flags and read-only guidance added by Milvus Doctor. No upstream commands executed.",
        "scope": "Five English FAQ/sidebar pages for each listed version; not every historical version/language or all linked manuals/images."}, indent=2) + "\n").encode()
    files["ATTRIBUTION.md"] = ("# Third-party Milvus FAQ material\n\n"
        "The files under sources/ are unmodified Markdown from https://github.com/milvus-io/milvus-docs, copyright their original authors/contributors, licensed under Apache License 2.0.\n\n"
        "The exact versions, commits and hashes are recorded in catalog.json/provenance.json. Original license text and any upstream NOTICE files are included under licenses/. Remote images/linked manuals are not copied.\n\n"
        "catalog.json is a derived section index with Milvus Doctor keywords, risk annotations and read-only guidance. These annotations are not upstream text, do not certify FAQ accuracy and do not authorize execution of examples. Original text is retained for review; known conflicting/destructive text is not the default answer.\n\n"
        "This third-party notice applies to the documentation material only; it does not choose a license for Milvus Doctor's own code.\n").encode()
    output.mkdir()
    for path, data in files.items(): write_new(output / path, data)
    return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--docs-version", action="append", choices=VERSIONS)
    parser.add_argument("--ref", action="append", default=[], help="Reproducible VERSION=40-hex-commit override; repeat per version")
    args = parser.parse_args()
    refs = {}
    for item in args.ref:
        key, separator, value = item.partition('=')
        if not separator or key not in VERSIONS or key in refs: parser.error("Use each supported VERSION=COMMIT once")
        refs[key] = value
    try:
        catalog = build_bundle(args.output_dir, tuple(args.docs_version or VERSIONS), refs)
    except (ValueError, OSError, urllib.error.URLError) as error:
        parser.exit(2, "FAQ import failed (" + type(error).__name__ + "); inspect the selected official source/output path. No existing bundle was overwritten.\n")
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "sources": len(catalog["sources"]), "entries": len(catalog["entries"]), "versions": catalog["versions"]}, indent=2))


if __name__ == "__main__": main()
