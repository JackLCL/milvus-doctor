"""Distinguish intentionally unselected checks from unavailable requested evidence."""

INTENTIONAL_SKIP_REASONS = frozenset({"not_requested", "scope_excluded"})


def is_intentional_skip(source):
    return (isinstance(source, dict) and source.get("status") == "skipped"
            and isinstance(source.get("reason"), str) and source.get("reason") in INTENTIONAL_SKIP_REASONS)


def summarize_coverage(sources):
    sources = sources if isinstance(sources, list) else []
    successful = sum(isinstance(s, dict) and s.get("status") == "ok" for s in sources)
    errors = sum(isinstance(s, dict) and s.get("status") == "error" for s in sources)
    unavailable = sum(not isinstance(s, dict) or (s.get("status") not in {"ok", "error"}
                       and not is_intentional_skip(s)) for s in sources)
    return {
        "complete_for_selected_scope": successful > 0 and errors == 0 and unavailable == 0,
        "successful": successful, "errors": errors, "unavailable": unavailable,
        "not_requested": sum(is_intentional_skip(s) and s.get("reason") == "not_requested" for s in sources),
        "scope_excluded": sum(is_intentional_skip(s) and s.get("reason") == "scope_excluded" for s in sources),
    }
