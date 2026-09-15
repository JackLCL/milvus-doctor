"""Bounded, offline references to bundled official FAQ snapshots.

These references do not run checks, establish a diagnosis, or authorize changes.
Only the fixed package catalog and its bundled sources are read. Search ranking
is lexical and deterministic; no model, network, SDK, or subprocess is involved.
"""
import hashlib
import json
from datetime import date
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

from .local_files import read_regular_file
from .safety import redact


_FAQ_ROOT = Path(__file__).resolve().parents[2] / "references" / "faq"
_CATALOG_PATH = _FAQ_ROOT / "catalog.json"
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024
MAX_FRAGMENT_BYTES = 64 * 1024
MAX_ENTRIES = 500
MAX_EXCERPT_CHARS = 1200
CATEGORIES = ("performance", "product", "operational", "limits", "troubleshooting")
_RISKS = {"version_sensitive", "source_conflict", "manual_change_only",
          "destructive_recovery", "business_data_access"}
_DOC_VERSION = re.compile(r"v[0-9]{1,3}\.[0-9]{1,3}\.x")
_INPUT_VERSION = re.compile(
    r"v?([0-9]{1,3})\.([0-9]{1,3})(?:\.(?:[0-9]{1,5}|x))?"
    r"(?:(?:-?(?:dev|alpha|beta|rc|a|b)|\.(?:dev|post))[.-]?[0-9]*)?"
    r"(?:\+[A-Za-z0-9.-]{1,64})?", re.I)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_RULE_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,79}")
_CPU_INCLUDE = "{{fragments/cpu_support.md}}"
_DANGEROUS_COMMAND = re.compile(
    r"\brm\s+-(?:[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)\b|"
    r"\b(?:docker(?:-compose)?|kubectl|helm)\s+(?:compose\s+)?"
    r"(?:down|delete|uninstall|remove|rm)\b|\betcdctl\s+(?:del|delete)\b|"
    r"\bdrop[ _](?:collection|database)\b|\bdelete_collection\s*\(", re.I)
_REFERENCE_WARNING = "FAQ references are not diagnostic evidence and do not establish a cause or authorize a change."
_UNCONFIRMED_WARNING = (
    "Milvus version is unconfirmed. This is the default bundled documentation snapshot; "
    "its applicability to the deployed version is not confirmed.")
_MATCHED_WARNING = (
    "Documentation matches the requested major/minor version only; verify patch-specific "
    "behavior and deployment details before applying any recommendation.")


class CatalogError(ValueError):
    """The packaged reference data is missing, unsafe, or malformed."""


def _require(condition):
    if not condition:
        raise CatalogError("Bundled FAQ catalog is invalid; reinstall or rebuild the reviewed FAQ bundle.")


def _string(value, maximum, empty=False):
    _require(isinstance(value, str) and len(value) <= maximum and (empty or bool(value.strip())))
    _require(not any(ord(char) < 32 and char not in "\n\r\t" for char in value))
    _require(not any(0xD800 <= ord(char) <= 0xDFFF for char in value))
    return value


def _strings(value, count, length, pattern=None):
    _require(isinstance(value, list) and len(value) <= count)
    for item in value:
        _string(item, length)
        if pattern is not None:
            _require(pattern.fullmatch(item) is not None)
    return value


def _url(value, source=False):
    _string(value, 1600)
    _require(not any(char.isspace() or char in "\\%<>\"'" for char in value))
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise CatalogError("Bundled FAQ catalog contains an invalid reference URL.") from None
    _require(parsed.scheme == "https" and not parsed.query and not parsed.username and not parsed.password)
    _require(all(part not in (".", "..") for part in parsed.path.split("/")))
    if source:
        _require(parsed.netloc == "github.com" and bool(re.fullmatch(
            r"/milvus-io/milvus-docs/blob/[0-9a-f]{40}/site/en/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.md",
            parsed.path)))
        _require(not parsed.fragment or bool(re.fullmatch(r"L[0-9]+(?:-L[0-9]+)?", parsed.fragment)))
    else:
        _require(parsed.netloc == "milvus.io" and bool(re.fullmatch(
            r"/docs/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_-]+\.(?:md|html)", parsed.path)))
        _require(not parsed.fragment or bool(re.fullmatch(r"[A-Za-z0-9_-]{1,500}", parsed.fragment)))
    return value


def _bundle_path(relative):
    """Reject leaf and ancestor links inside the fixed FAQ directory."""
    _require(isinstance(relative, str) and len(relative) <= 240)
    path = PurePosixPath(relative)
    _require(not path.is_absolute() and "\\" not in relative and all(part not in (".", "..") for part in relative.split("/")))
    root = _FAQ_ROOT
    # The package itself may be installed through a normal ancestor symlink,
    # but no reference directory or file may redirect outside that package.
    for ancestor in (root.parent, root):
        _require(not ancestor.is_symlink())
    selected = root
    for part in path.parts:
        selected = selected / part
        _require(not selected.is_symlink())
    try:
        selected.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        raise CatalogError("Bundled FAQ files are missing or outside the fixed reference directory.") from None
    return selected


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _validate_catalog(catalog):
    _require(isinstance(catalog, dict) and type(catalog.get("schema_version")) is int and catalog["schema_version"] == 1)
    _require(bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", _string(catalog.get("captured_on"), 10))))
    date.fromisoformat(catalog["captured_on"])
    versions = catalog.get("versions")
    _require(isinstance(versions, list) and 1 <= len(versions) <= 10)
    commits = {}
    for item in versions:
        _require(isinstance(item, dict))
        version = _string(item.get("docs_version"), 16)
        _require(_DOC_VERSION.fullmatch(version) is not None and version not in commits)
        commit = _string(item.get("source_commit"), 40)
        _require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None)
        commits[version] = commit
    _require(catalog.get("default_docs_version") in commits)
    sources = catalog.get("sources")
    _require(isinstance(sources, list) and 1 <= len(sources) <= 50)
    source_map = {}
    source_lines = {}
    for source in sources:
        _require(isinstance(source, dict))
        identity = _string(source.get("id"), 128)
        version = source.get("docs_version")
        _require(version in commits and source.get("category") in CATEGORIES)
        _require(identity == version + "/" + source["category"] and identity not in source_map)
        _string(source.get("title"), 500)
        relative = _string(source.get("path"), 240)
        _require(re.fullmatch(r"sources/" + re.escape(version) + r"/[A-Za-z0-9_-]+\.md", relative) is not None)
        _url(source.get("source_url"), source=True)
        _require("/blob/" + commits[version] + "/" in source["source_url"])
        _url(source.get("docs_url"))
        _require(source["docs_url"] == "https://milvus.io/docs/" + version + "/" + PurePosixPath(relative).name)
        _require(urlsplit(source["source_url"]).path.rsplit("/", 1)[-1] == PurePosixPath(relative).name)
        digest = _string(source.get("sha256"), 64)
        _require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
        _require(type(source.get("section_count")) is int and 1 <= source["section_count"] <= MAX_ENTRIES)
        # Hash the bounded local originals: missing or edited snapshots cannot
        # silently retain an immutable-source provenance claim.
        data = read_regular_file(_bundle_path(relative), MAX_SOURCE_BYTES)
        _require(hashlib.sha256(data).hexdigest() == digest)
        source_lines[identity] = data.decode("utf-8").splitlines()
        source_map[identity] = source
    _require(all({source["category"] for source in sources if source["docs_version"] == version} == set(CATEGORIES)
                 for version in commits))
    fragments = catalog.get("fragments", [])
    _require(isinstance(fragments, list) and len(fragments) <= len(versions))
    fragment_texts = {}
    for fragment in fragments:
        _require(isinstance(fragment, dict))
        version = fragment.get("docs_version")
        _require(version in commits)
        identity = _string(fragment.get("id"), 128)
        _require(identity == version + "/fragments/cpu_support.md" and identity not in fragment_texts)
        relative = _string(fragment.get("path"), 240)
        _require(relative == "sources/" + identity)
        _url(fragment.get("source_url"), source=True)
        _require(fragment["source_url"] == "https://github.com/milvus-io/milvus-docs/blob/" + commits[version] + "/site/en/fragments/cpu_support.md")
        digest = _string(fragment.get("sha256"), 64)
        _require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
        _require(type(fragment.get("bytes")) is int and 1 <= fragment["bytes"] <= MAX_FRAGMENT_BYTES)
        data = read_regular_file(_bundle_path(relative), MAX_FRAGMENT_BYTES)
        _require(len(data) == fragment["bytes"] and hashlib.sha256(data).hexdigest() == digest)
        text = data.decode("utf-8").strip()
        _string(text, 65536)
        _require("{{" not in text and "}}" not in text)  # No recursive includes.
        fragment_texts[identity] = text
    entries = catalog.get("entries")
    _require(isinstance(entries, list) and 1 <= len(entries) <= MAX_ENTRIES)
    identities = set()
    section_counts = {key: 0 for key in source_map}
    for entry in entries:
        _require(isinstance(entry, dict))
        identity = _string(entry.get("id"), 128)
        _require(_ID.fullmatch(identity) is not None and identity not in identities)
        identities.add(identity)
        source = source_map.get(entry.get("source_id"))
        _require(source is not None)
        _require(entry.get("docs_version") == source["docs_version"] and entry.get("category") == source["category"])
        _string(entry.get("title"), 500)
        _strings(entry.get("breadcrumb"), 10, 500)
        _string(entry.get("body"), 65536)
        _url(entry.get("source_url"), source=True)
        _url(entry.get("docs_url"))
        _require(entry["source_url"].split("#", 1)[0] == source["source_url"].split("#", 1)[0])
        _require(entry["docs_url"].split("#", 1)[0] == source["docs_url"].split("#", 1)[0])
        lines = entry.get("source_lines")
        _require(isinstance(lines, dict) and type(lines.get("start")) is int and type(lines.get("end")) is int
                 and 1 <= lines["start"] <= lines["end"] <= 100000)
        original_lines = source_lines[entry["source_id"]]
        _require(lines["end"] <= len(original_lines))
        _require(entry["body"] == "\n".join(original_lines[lines["start"]:lines["end"]]).strip())
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", original_lines[lines["start"] - 1])
        _require(heading is not None and entry["title"] == re.sub(r"\s*\{#[^}]+\}\s*$", "", heading[2]).strip())
        fragment_ids = entry.get("fragment_ids", [])
        _strings(fragment_ids, 1, 128)
        if _CPU_INCLUDE in entry["body"]:
            fragment_id = entry["docs_version"] + "/fragments/cpu_support.md"
            _require(fragment_ids == [fragment_id] and fragment_id in fragment_texts)
            resolved = _string(entry.get("resolved_body"), 65536)
            _require(resolved == entry["body"].replace(_CPU_INCLUDE, fragment_texts[fragment_id]))
        else:
            _require(not fragment_ids and "resolved_body" not in entry)
        _require("{{fragments/" not in entry["body"].replace(_CPU_INCLUDE, ""))
        section_counts[entry["source_id"]] += 1
        _strings(entry.get("keywords"), 100, 160)
        _strings(entry.get("risk_flags"), len(_RISKS), 64)
        _require(set(entry["risk_flags"]).issubset(_RISKS))
        _strings(entry.get("warnings"), 12, 2000)
        guidance = _string(entry.get("read_only_guidance"), 4000)
        _require(not _DANGEROUS_COMMAND.search(guidance) and "```" not in guidance and "~~~" not in guidance)
        _strings(entry.get("rule_codes"), 50, 80, _RULE_CODE)
        _strings(entry.get("verify_urls"), 12, 1600)
        for url in entry["verify_urls"]:
            _url(url)
    _require(all(section_counts[key] == source["section_count"] for key, source in source_map.items()))
    return catalog


def load_catalog():
    """Load and validate the one fixed bundle; there is no arbitrary-path API."""
    try:
        _require(_CATALOG_PATH == _FAQ_ROOT / "catalog.json")
        payload = read_regular_file(_bundle_path("catalog.json"), MAX_CATALOG_BYTES)
        catalog = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
        return _validate_catalog(catalog)
    except CatalogError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError):
        raise CatalogError("Bundled FAQ catalog is unavailable or invalid; reinstall or rebuild the reviewed FAQ bundle.") from None


def _requested_version(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("milvus_version must be a major/minor or patch version, for example 2.6.17 or v3.0.x")
    match = _INPUT_VERSION.fullmatch(value.strip())
    if not match:
        raise ValueError("milvus_version must be a major/minor or patch version, for example 2.6.17 or v3.0.x")
    return "v{}.{}.x".format(int(match[1]), int(match[2]))


def _version_metadata(catalog, requested):
    available = [item["docs_version"] for item in catalog["versions"]]
    selected = requested or catalog["default_docs_version"]
    if selected not in available:
        status = "version_not_bundled"
        warning = "No bundled FAQ snapshot matches the requested Milvus major/minor version; no cross-version fallback was used."
    elif requested is None:
        status = "unconfirmed_reference"
        warning = _UNCONFIRMED_WARNING
    else:
        status = "matched_minor_version"
        warning = _MATCHED_WARNING
    return {"selected_docs_version": selected, "version_status": status,
            "available_docs_versions": available, "captured_on": catalog["captured_on"],
            "warnings": [warning, _REFERENCE_WARNING]}


def _limit(value):
    if type(value) is not int or not 1 <= value <= 10:
        raise ValueError("FAQ limit must be an integer from 1 to 10")
    return value


_STOP_WORDS = set("a an and are as at be can do does for from how i in is it me milvus my of on or please the this to use using what when where which why with you".split())
_ALIASES = (
    (("召回", "召回率", "recall"), ("recall",)),
    (("nprobe", "nlist"), ("recall", "nprobe", "nlist")),
    (("内存不均", "内存分布", "内存不平衡", "内存不一致", "uneven", "imbalanced", "unbalanced"),
     ("uneven", "unbalanced", "memory usage is unbalanced", "memory", "delegator", "querynode")),
    (("内存", "oom", "memory"), ("memory", "oom")),
    (("维度", "dimension", "dimensions"), ("dimension",)),
    (("上限", "限制", "最大", "limit", "maximum"), ("limit", "maximum")),
    (("主键", "primary key", "primarykey"), ("primary key", "varchar")),
    (("字符串", "string", "varchar"), ("string", "varchar")),
    (("更新", "upsert", "update"), ("update", "upsert")),
    (("动态字段", "动态列", "dynamic field", "dynamic schema"), ("dynamic", "schema", "json")),
    (("字段", "模式", "schema"), ("field", "schema")),
    (("json",), ("json", "dynamic")),
    (("索引", "index"), ("index", "indexing")),
    (("加载", "load", "loaded"), ("load", "loaded", "loading")),
    (("删除", "delete", "deletion"), ("delete", "deletion")),
    (("连接", "连接失败", "connection", "connect"), ("connection", "connect")),
    (("持久化", "存储", "storage"), ("storage", "persistence")),
    (("日志时间差", "日志时间不一致", "日志时间", "时区", "utc", "time difference", "time zone", "timezone"),
     ("log", "time", "system time", "utc", "time difference")),
    (("查询", "搜索", "search", "query"), ("search", "query")),
    (("性能", "慢", "延迟", "performance", "latency"), ("performance", "latency", "slow")),
    (("距离", "相似度", "metric", "distance"), ("metric", "distance", "similarity")),
)


def _contains(text, term):
    if re.search(r"[\u3400-\u9fff]", term):
        return term in text
    plural = r"s?" if term.endswith((" key", "dimension", "field", "vector", "string", "limit")) else ""
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + plural + r"(?![a-z0-9])", text) is not None


def _query_terms(query):
    normalized = query.casefold()
    literal = {term for term in re.findall(r"[a-z0-9][a-z0-9_.-]*|[\u3400-\u9fff]+", normalized)
               if term not in _STOP_WORDS and len(term) > 1 and term != "redacted"}
    aliases = set()
    for triggers, words in _ALIASES:
        if any(_contains(normalized, trigger) for trigger in triggers):
            aliases.update(words)
    return normalized, literal, aliases - literal


def _score(entry, terms):
    normalized, literal, aliases = terms
    title = entry["title"].casefold()
    keywords = " ".join(entry["keywords"] + entry["rule_codes"]).casefold()
    breadcrumb = " ".join(entry["breadcrumb"]).casefold()
    body = entry.get("resolved_body", entry["body"]).casefold()
    score = 40 if (literal or aliases) and len(normalized) > 2 and normalized in title else 0
    for values, weights in ((literal, (14, 10, 4, 1)), (aliases, (9, 6, 2, 0.5))):
        for term in values:
            score += sum(weight for text, weight in zip((title, keywords, breadcrumb, body), weights)
                         if _contains(text, term))
    return score


def _prose(text):
    lines = []
    fenced = None
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fenced is not None:
            if marker and marker[1][0] == fenced[0] and len(marker[1]) >= fenced[1] and not marker[2].strip():
                fenced = None
            continue
        if marker and (marker[1][0] != '`' or '`' not in marker[2]):
            fenced = (marker[1][0], len(marker[1]))
            continue
        if not re.match(r"^(?: {4}|\t)", line):
            lines.append(line)
    text = "\n".join(lines)
    # Reference URLs are emitted only in validated metadata, never copied from
    # arbitrary Markdown/HTML links contained in the upstream prose.
    text = re.sub(r"!?\[([^\]\n]*)\]\([^\n)]*\)", r"\1", text)
    text = text.replace("![", "[")  # Also disable reference-style Markdown images.
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"https?://[^\s<>]+", "[source link omitted]", text)
    return re.sub(r"\s+", " ", text).strip()


def _reference(entry, metadata, compact=False):
    warnings = list(dict.fromkeys(metadata["warnings"] + entry["warnings"]))
    flags = list(entry["risk_flags"])
    body = entry.get("resolved_body", entry["body"])
    guarded = bool(set(flags) & {"source_conflict", "destructive_recovery", "business_data_access"}) or bool(_DANGEROUS_COMMAND.search(body))
    if guarded and "Source commands or conflicting claims are withheld; use the read-only guidance and verify the linked versioned sources." not in warnings:
        warnings.append("Source commands or conflicting claims are withheld; use the read-only guidance and verify the linked versioned sources.")
    guidance = _prose(entry["read_only_guidance"])
    result = {"id": entry["id"], "title": entry["title"], "docs_version": entry["docs_version"],
              "docs_url": entry["docs_url"], "source_url": entry["source_url"],
              "version_status": metadata["version_status"], "risk_flags": flags,
              "warnings": warnings, "read_only_guidance": guidance}
    if entry.get("fragment_ids"):
        result.update(fragment_ids=entry["fragment_ids"],
                      fragment_source_urls=[entry["source_url"].split("/site/en/", 1)[0] + "/site/en/fragments/cpu_support.md"])
    if not compact:
        prose = _prose(body)
        text = guidance if guarded else prose
        if not text:
            text = guidance
        result.update({"category": entry["category"], "breadcrumb": entry["breadcrumb"],
                       "excerpt": text[:MAX_EXCERPT_CHARS], "excerpt_truncated": len(text) > MAX_EXCERPT_CHARS,
                       "excerpt_source": "read_only_guidance" if guarded or not prose else ("resolved_body" if "resolved_body" in entry else "body"),
                       "source_id": entry["source_id"], "source_lines": entry["source_lines"],
                       "rule_codes": entry["rule_codes"], "verify_urls": entry["verify_urls"]})
    return redact(result)


def search_faq(query, milvus_version=None, category=None, limit=5):
    """Return bounded lexical references, never a diagnosis or a repair plan."""
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("FAQ query must contain 1..1000 characters")
    if category is not None and category not in CATEGORIES:
        raise ValueError("FAQ category must be one of: " + ", ".join(CATEGORIES))
    _limit(limit)
    requested = _requested_version(milvus_version)
    catalog = load_catalog()
    metadata = _version_metadata(catalog, requested)
    # Redaction occurs before both ranking and output, preventing a secret in a
    # pasted error from being echoed or used as an identifier in the result.
    safe_query = redact(query.strip())
    result = dict(metadata, status="ok", query=safe_query, category=category,
                  results=[], total_matches=0, limit=limit, truncated=False)
    if metadata["version_status"] == "version_not_bundled":
        result["status"] = "version_not_bundled"
        return result
    terms = _query_terms(safe_query)
    ranked = []
    for entry in catalog["entries"]:
        if entry["docs_version"] != metadata["selected_docs_version"] or (category is not None and entry["category"] != category):
            continue
        score = _score(entry, terms)
        if score > 0:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], item[1]["title"].casefold(), item[1]["id"]))
    result.update(results=[_reference(entry, metadata) for _, entry in ranked[:limit]],
                  total_matches=len(ranked), truncated=len(ranked) > limit)
    return result


def get_faq(entry_id, milvus_version=None):
    """Return one safe excerpt; never expose the unrestricted original body."""
    if not isinstance(entry_id, str) or _ID.fullmatch(entry_id) is None:
        raise ValueError("FAQ entry_id must be a bundled identifier of at most 128 characters")
    requested = _requested_version(milvus_version)
    catalog = load_catalog()
    entry = next((item for item in catalog["entries"] if item["id"] == entry_id), None)
    metadata = _version_metadata(catalog, requested)
    result = dict(metadata, status="not_found", entry=None)
    if metadata["version_status"] == "version_not_bundled":
        result["status"] = "version_not_bundled"
    elif entry is not None:
        if requested is not None and requested != entry["docs_version"]:
            result.update(status="version_mismatch", entry_docs_version=entry["docs_version"])
            result["warnings"].append("The FAQ identifier belongs to a different documentation version; its content was not returned.")
        else:
            # A bundled identifier can select an older snapshot, but absence of
            # the deployed version must still remain visibly unconfirmed.
            if requested is None:
                metadata["selected_docs_version"] = entry["docs_version"]
                metadata["warnings"][0] = "Milvus version is unconfirmed. The identifier selects a bundled documentation snapshot; its applicability to the deployed version is not confirmed."
                result.update(metadata)
            result.update(status="ok", entry=_reference(entry, metadata))
    return result


def catalog_overview():
    catalog = load_catalog()
    return {"status": "ok", "schema_version": catalog["schema_version"],
            "captured_on": catalog["captured_on"], "default_docs_version": catalog["default_docs_version"],
            "versions": catalog["versions"], "categories": list(CATEGORIES),
            "source_count": len(catalog["sources"]), "entry_count": len(catalog["entries"]),
            "fragment_count": len(catalog.get("fragments", [])),
            "sources": [{key: source[key] for key in ("id", "docs_version", "category", "title", "source_url", "docs_url", "section_count")}
                        for source in catalog["sources"]], "warnings": [_REFERENCE_WARNING]}


def references_for_findings(findings, milvus_version=None, limit=3):
    """Attach compact versioned references by exact rule codes, not free text."""
    _limit(limit)
    requested = _requested_version(milvus_version)
    if not isinstance(findings, list):
        return []
    codes = {item.get("code") for item in findings[:MAX_ENTRIES]
             if isinstance(item, dict) and isinstance(item.get("code"), str) and _RULE_CODE.fullmatch(item["code"])}
    if not codes:
        return []
    try:
        catalog = load_catalog()
    except CatalogError:
        return []
    metadata = _version_metadata(catalog, requested)
    if metadata["version_status"] == "version_not_bundled":
        return []
    entries = [entry for entry in catalog["entries"]
               if entry["docs_version"] == metadata["selected_docs_version"] and codes.intersection(entry["rule_codes"])]
    entries.sort(key=lambda entry: (-len(codes.intersection(entry["rule_codes"])), entry["id"]))
    return [_reference(entry, metadata, compact=True) for entry in entries[:limit]]


def resolve_saved_references(records, milvus_version=None):
    """Reproject up to three saved references from at most ten identity records.

    Saved titles, links, warnings, guidance, and version-status claims are never
    rendered. Only known IDs with matching immutable provenance are accepted.
    """
    if not isinstance(records, list) or not records:
        return []
    candidates = [item for item in records[:10]
                  if isinstance(item, dict) and isinstance(item.get("id"), str)
                  and _ID.fullmatch(item["id"]) and isinstance(item.get("docs_version"), str)
                  and _DOC_VERSION.fullmatch(item["docs_version"])
                  and isinstance(item.get("source_url"), str) and len(item["source_url"]) <= 1600]
    if not candidates:
        return []
    try:
        requested = _requested_version(milvus_version)
        catalog = load_catalog()
    except ValueError:
        return []
    metadata = _version_metadata(catalog, requested)
    if metadata["version_status"] == "version_not_bundled":
        return []
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    resolved = []
    seen = set()
    for record in candidates:
        entry = entries.get(record["id"])
        if entry is None or entry["id"] in seen:
            continue
        if record["source_url"] != entry["source_url"] or record["docs_version"] != entry["docs_version"]:
            continue
        if requested is not None and requested != entry["docs_version"]:
            continue
        entry_metadata = dict(metadata, warnings=list(metadata["warnings"]))
        if requested is None:
            entry_metadata["selected_docs_version"] = entry["docs_version"]
            entry_metadata["warnings"][0] = "Milvus version is unconfirmed. The saved identifier selects a bundled documentation snapshot; its applicability to the deployed version is not confirmed."
        resolved.append(_reference(entry, entry_metadata, compact=True))
        seen.add(entry["id"])
        if len(resolved) == 3:
            break
    return resolved
