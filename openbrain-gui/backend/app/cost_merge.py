# app/cost_merge.py
"""The "All" path for the Cost page: loop every Hermes profile, call the
data_dir-parametrized hermes_usage functions per profile, and combine.

Rule: sum raw counters, then RECOMPUTE derived metrics (hit rate, per-call
averages, weighted-mean prompt sizes). Never average an average.

When a profile's state.db is unreadable it is dropped from the merge (rather
than 500-ing the whole page) and its key is reported in `skipped_profiles`, so
the caller can say the merged total is a lower bound -- same ethos as
`hermes_usage`'s `unpriced`."""
from collections.abc import Iterable

from app import hermes_usage, profiles
from app.hermes_usage import HermesDataUnavailable

_COUNTER_KEYS = ("sessions", "api_calls", "input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
                 "cost_usd")


def _sum_counters(dicts: Iterable[dict], keys: Iterable[str]) -> dict:
    """Element-wise sum of the named raw counters across per-bot dicts -- the
    only safe merge for raw numbers; derived metrics are recomputed from these."""
    keys = tuple(keys)
    out = {k: 0 for k in keys}
    for d in dicts:
        for k in keys:
            out[k] += d.get(k) or 0
    return out


def _weighted_mean(pairs: Iterable[tuple[float | None, float | None]]) -> float | None:
    """Pooled mean of per-bot averages: `avg_i` reconstructs its own total as
    `avg_i * n_i`, so `sum(avg_i * n_i) / sum(n_i)` is the true combined mean --
    not the naive mean of the averages.

    Pairs whose value is None (a bucket that genuinely had no data -- e.g. the
    pruned-session platform, where `avg_messages_per_session` is None) or whose
    weight is None/0 are dropped, so they cannot drag the result toward 0."""
    usable = [(v, w) for v, w in pairs if v is not None and w]
    den = sum(w for _, w in usable)
    return sum(v * w for v, w in usable) / den if den else None


def _each_dashboard(days: int, limit: int, *, now: float | None = None
                    ) -> tuple[list[tuple[dict, dict]], list[str]]:
    """Per-profile `(profile, dashboard())` pairs, plus the keys of profiles
    whose state.db could not be read. One torn snapshot degrades the merge to a
    partial total instead of raising -- the gap is reported via
    `skipped_profiles`."""
    got: list[tuple[dict, dict]] = []
    skipped: list[str] = []
    for p in profiles.list_profiles():
        try:
            got.append((p, hermes_usage.dashboard(data_dir=p["data_dir"], days=days,
                                                  now=now, limit=limit)))
        except HermesDataUnavailable:
            skipped.append(p["key"])
    return got, skipped


def _merge_summaries(summaries: list[dict]) -> dict:
    summary = _sum_counters(summaries, _COUNTER_KEYS)
    denom = (summary["cache_read_tokens"] + summary["cache_write_tokens"]
             + summary["input_tokens"])
    summary["cache_hit_rate"] = summary["cache_read_tokens"] / denom if denom else None
    summary["cost_status"] = "estimated"
    up = [s.get("unpriced") or {} for s in summaries]
    summary["unpriced"] = {
        "api_calls": sum(u.get("api_calls") or 0 for u in up),
        "tokens": sum(u.get("tokens") or 0 for u in up),
        "models": sorted({m for u in up for m in (u.get("models") or [])}),
    }
    return summary


def summary_all(*, days: int = 30, now: float | None = None) -> dict:
    """Just the header-tile figures, summed across bots -- cheaper than
    dashboard_all (no by_session / by_model / tools work). Used by /cost/summary.

    `skipped_profiles` lists the keys of bots whose state.db was unreadable;
    when non-empty the returned figures are a lower bound."""
    got: list[dict] = []
    skipped: list[str] = []
    for p in profiles.list_profiles():
        try:
            got.append(hermes_usage.summary(data_dir=p["data_dir"], days=days, now=now))
        except HermesDataUnavailable:
            skipped.append(p["key"])
    if not got:
        raise HermesDataUnavailable("no readable Hermes profile")
    merged = _merge_summaries(got)
    merged["skipped_profiles"] = skipped
    return merged


def dashboard_all(*, days: int = 30, limit: int = 50, now: float | None = None) -> dict:
    got, skipped = _each_dashboard(days, limit, now=now)
    if not got:
        raise HermesDataUnavailable("no readable Hermes profile")

    summary = _merge_summaries([d["summary"] for _, d in got])
    by_model = _merge_grouped((d["by_model"] for _, d in got), "model")
    by_platform = _merge_grouped((d["by_platform"] for _, d in got), "platform")

    sessions = []
    for p, d in got:
        for row in d["by_session"]:
            sessions.append({**row, "profile": p["key"]})
    sessions.sort(key=lambda r: r.get("cost_usd") or 0, reverse=True)
    by_session = sessions[:limit]

    efficiency = _merge_efficiency(d["efficiency"] for _, d in got)
    prompt_budget = _merge_prompt_budget(d["prompt_budget"] for _, d in got)
    top_tools = _merge_tools(d["top_tools"] for _, d in got)

    return {"summary": summary, "by_model": by_model, "by_platform": by_platform,
            "by_session": by_session, "efficiency": efficiency,
            "prompt_budget": prompt_budget, "top_tools": top_tools,
            "skipped_profiles": skipped}


def _merge_grouped(groups_iter: Iterable[list[dict]], key: str) -> list[dict]:
    """Union the per-bot `by_model` / `by_platform` rows on `key`, summing raw
    counters within each group. Same model or platform seen in two bots
    becomes one row."""
    acc: dict = {}
    for rows in groups_iter:
        for r in rows:
            k = r[key]
            tgt = acc.setdefault(k, {key: k, **{c: 0 for c in _COUNTER_KEYS}})
            for c in _COUNTER_KEYS:
                tgt[c] += r.get(c) or 0
    return sorted(acc.values(), key=lambda r: r["cost_usd"], reverse=True)


def _merge_efficiency(effs_iter: Iterable[list[dict]]) -> list[dict]:
    """Merge per-bot per-platform efficiency rows: sum the raw counters, then
    recompute every per-call ratio from the summed totals and pool
    `avg_messages_per_session` as a session-weighted mean."""
    acc: dict = {}
    for rows in effs_iter:
        for r in rows:
            k = r["platform"]
            tgt = acc.setdefault(k, {"platform": k, **{c: 0 for c in _COUNTER_KEYS},
                                     "_msg_pairs": []})
            for c in _COUNTER_KEYS:
                tgt[c] += r.get(c) or 0
            tgt["_msg_pairs"].append((r.get("avg_messages_per_session"), r.get("sessions")))
    out = []
    for r in acc.values():
        calls = r["api_calls"]
        toks = (r["input_tokens"] + r["output_tokens"]
                + r["cache_read_tokens"] + r["cache_write_tokens"])
        pairs = r.pop("_msg_pairs")
        out.append({**r,
                    "tokens_per_call": toks / calls if calls else None,
                    "cache_write_per_call": r["cache_write_tokens"] / calls if calls else None,
                    "cost_per_call": r["cost_usd"] / calls if calls else None,
                    "avg_messages_per_session": _weighted_mean(pairs)})
    out.sort(key=lambda r: r["cost_usd"], reverse=True)
    return out


def _merge_prompt_budget(pbs_iter: Iterable[list[dict]]) -> list[dict]:
    """Merge per-bot per-platform prompt-budget rows: sessions add, the max
    system-prompt size is the max of the maxes, and the average is pooled as a
    session-weighted mean (not the mean of the per-bot averages)."""
    acc: dict = {}
    for rows in pbs_iter:
        for r in rows:
            k = r["platform"]
            tgt = acc.setdefault(k, {"platform": k, "sessions": 0,
                                     "_avg_pairs": [], "max_system_prompt_chars": 0})
            tgt["sessions"] += r.get("sessions") or 0
            tgt["_avg_pairs"].append((r.get("avg_system_prompt_chars"), r.get("sessions")))
            tgt["max_system_prompt_chars"] = max(tgt["max_system_prompt_chars"],
                                                 r.get("max_system_prompt_chars") or 0)
    out = []
    for r in acc.values():
        pairs = r.pop("_avg_pairs")
        out.append({**r, "avg_system_prompt_chars": _weighted_mean(pairs)})
    out.sort(key=lambda r: (r["avg_system_prompt_chars"] or 0), reverse=True)
    return out


def _merge_tools(tools_iter: Iterable[dict]) -> dict:
    """Sum tool call counts across bots and keep the top 15.

    Each bot's list is ALREADY truncated to its own top 15 by
    `hermes_usage.top_tools`, so a tool ranked ~16th in every bot but high in
    aggregate can be missed. Accepted: these counts are informational, not
    billed."""
    acc: dict = {}
    for t in tools_iter:
        for row in t["tools"]:
            acc[row["tool_name"]] = acc.get(row["tool_name"], 0) + row["calls"]
    tools = [{"tool_name": n, "calls": c} for n, c in
             sorted(acc.items(), key=lambda kv: kv[1], reverse=True)][:15]
    return {"tools": tools, "token_attribution_available": False}


def per_bot_breakdown(*, days: int = 30, now: float | None = None) -> list[dict]:
    rows = []
    for p in profiles.list_profiles():
        try:
            s = hermes_usage.summary(data_dir=p["data_dir"], days=days, now=now)
            rows.append({
                "key": p["key"], "label": p["label"],
                "sessions": s.get("sessions") or 0,
                "api_calls": s.get("api_calls") or 0,
                "tokens": (s.get("input_tokens") or 0) + (s.get("output_tokens") or 0)
                          + (s.get("cache_read_tokens") or 0) + (s.get("cache_write_tokens") or 0),
                "cost_usd": s.get("cost_usd") or 0,
                "unavailable": False,
            })
        except HermesDataUnavailable:
            rows.append({"key": p["key"], "label": p["label"], "sessions": 0,
                         "api_calls": 0, "tokens": 0, "cost_usd": 0, "unavailable": True})
    total = sum(r["cost_usd"] for r in rows) or 1.0
    for r in rows:
        r["pct"] = r["cost_usd"] / total
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)
    return rows


def config_all() -> list[dict]:
    out = []
    for p in profiles.list_profiles():
        try:
            snap = hermes_usage.config_snapshot(data_dir=p["data_dir"])
            out.append({"key": p["key"], "label": p["label"], **snap})
        except HermesDataUnavailable:
            out.append({"key": p["key"], "label": p["label"], "unavailable": True})
    return out
