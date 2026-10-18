"""Feeds and data health checks: cost tables, data quality, drift, model watch, feature versions, news, feed reconciliation."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from goldbot.data.crossfeed import check_reconciliation
from goldbot.data.ticks import check_tick_record
from goldbot.ops.checks._base import (
    COSTS_FAIL_DAYS,
    COSTS_PUBLISHED_WARN_DAYS,
    COSTS_WARN_DAYS,
    DQ_WINDOW_HOURS,
    NEWS_STALE_POLLS,
    Check,
    Status,
    _age,
    _read_json,
    _skipped,
)
from goldbot.ops.checks.registry import (
    BLOCK_COSTS,
    BLOCK_COSTS_PUBLISHED,
    BLOCK_RECONCILIATION,
    BLOCK_TAIL,
    register_check,
)
from goldbot.ops.tv_pull_health import check_tv_pull

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext


def _publishes_costs(ctx: HealthContext) -> bool:
    """check_costs_published runs only with `costs.publish_release` on."""
    return ctx.settings is not None and ctx.settings.costs.publish_release


@register_check("account", block=BLOCK_COSTS, pos=10)
def check_costs(ctx: HealthContext, account_id: str) -> Check:
    name = f"costs:{account_id}"
    f = ctx.state_dir / f"costs_{account_id}.json"
    if not f.exists():
        return Check(name=name, status="warn", reason="no cost table yet (nightly_costs has not run): engine uses priors")
    try:
        raw = _read_json(f)
        built = pd.Timestamp(raw["built_utc"])
        built = built.tz_localize("UTC") if built.tzinfo is None else built
    except (ValueError, OSError, KeyError) as exc:
        return Check(name=name, status="fail", reason=f"cost table unreadable: {exc}")
    days = (ctx.now - built).total_seconds() / 86400
    msg = f"built {days:.1f} d ago"
    if days > COSTS_FAIL_DAYS:
        return Check(name=name, status="fail", reason=msg + f" (> {COSTS_FAIL_DAYS:.0f} d)")
    if days > COSTS_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg)
    if raw.get("swap_long_usd_per_lot") is None or raw.get("swap_short_usd_per_lot") is None:
        # research and retraining then charge the settings' swap prior (see state/broker_terms_<account>.json)
        return Check(name=name, status="warn", reason=msg + "; no measured swap from the terminal: swap prior in use")
    return Check(name=name, status="ok", reason=msg)


# finance audit GAP-05: closed trades whose P&L is not net (costs not recorded) are left out of every net figure; above
# one in five over a month the net totals rest on too thin a part of the trading to be read as the month's result.
# A data-quality warning only, never a fail: nothing trades differently because of it
PNL_BASIS_WINDOW_DAYS = 30
PNL_BASIS_GROSS_WARN_PCT = 20.0


@register_check("account", block=BLOCK_COSTS, pos=20)
def check_pnl_basis(ctx: HealthContext, account_id: str) -> Check:
    """Share of this account's closed trades over the last PNL_BASIS_WINDOW_DAYS (by exit) that are not net
    (gates_phase.pnl_basis: "gross", or a legacy row without both commission and swap). The reports leave those out
    of every net figure ("nothing gross is labelled net"), so WARN when more than PNL_BASIS_GROSS_WARN_PCT of them are;
    never FAIL, even on an unreadable record: it is a reporting-quality signal, not a trading fault."""
    from goldbot.ops.gates_phase import load_closed_trades, pnl_basis
    name = f"pnl_basis:{account_id}"
    try:
        trades, err = load_closed_trades(ctx.state_dir)
    except (OSError, ValueError) as exc:
        return Check(name=name, status="warn", reason=f"closed-trade record unreadable: {type(exc).__name__}")
    since = ctx.now - pd.Timedelta(days=PNL_BASIS_WINDOW_DAYS)
    rows = [t for t in trades if t.account_id == account_id and since <= t.exit_utc <= ctx.now]
    note = f"; {err}" if err else ""
    if not rows:
        return Check(name=name, status="warn" if err else "ok",
                     reason=f"no closed trades in the last {PNL_BASIS_WINDOW_DAYS} d{note}"[:400])
    gross = sum(pnl_basis(t) != "net" for t in rows)
    pct = 100.0 * gross / len(rows)
    msg = (f"{gross} of {len(rows)} closed trades in the last {PNL_BASIS_WINDOW_DAYS} d ({pct:.0f}%) are gross only "
           f"(costs not recorded)")
    if pct > PNL_BASIS_GROSS_WARN_PCT:
        return Check(name=name, status="warn", reason=(f"{msg}, > {PNL_BASIS_GROSS_WARN_PCT:.0f}%: the net totals leave "
                                                       f"them out (check the broker's deal history){note}")[:400])
    return Check(name=name, status="warn" if err else "ok", reason=(msg + note)[:400])


@register_check("global", block=BLOCK_COSTS_PUBLISHED, pos=10, when=_publishes_costs)
def check_costs_published(ctx: HealthContext) -> Check:
    """With `costs.publish_release` on, research.yml charges the table on release costs-v1: warn when it was never
    published or is older than COSTS_PUBLISHED_WARN_DAYS (research is then on stale costs or the priors)."""
    name = "costs:published"
    f = ctx.state_dir / "costs_published.json"     # goldbot.ops.jobs.COSTS_PUBLISHED_FILE
    try:
        raw = _read_json(f) if f.exists() else {}
    except (ValueError, OSError) as exc:
        return Check(name=name, status="warn", reason=f"publish record unreadable: {exc}")
    last = f"; last attempt: {raw['last_result']}" if raw.get("last_result") else ""
    if not raw.get("published_utc"):
        return Check(name=name, status="warn",
                     reason="cost table never published to release costs-v1: research charges the priors" + last)
    published = pd.Timestamp(raw["published_utc"])
    published = published.tz_localize("UTC") if published.tzinfo is None else published
    days = (ctx.now - published).total_seconds() / 86400
    msg = f"published {days:.1f} d ago"
    if days > COSTS_PUBLISHED_WARN_DAYS:
        return Check(name=name, status="warn", reason=msg + f" (> {COSTS_PUBLISHED_WARN_DAYS:.0f} d): research uses a "
                                                            "stale table" + last)
    return Check(name=name, status="ok", reason=msg)


@register_check("global", block=BLOCK_TAIL, pos=10)
def check_data_quality(ctx: HealthContext) -> Check:
    """Design (Data quality): errors quarantine the batch and alert. Error-severity events (duplicate stamps, bid >
    ask, out-of-order bars) recorded by the loaders or the engines in the last DQ_WINDOW_HOURS warn here; their rows
    are in `bars_quarantine`, not in the bar tables, and an engine blocks entries while its feed is in error."""
    if ctx.settings is None:
        return Check(name="data_quality", status="warn", reason="settings unreadable: data root unknown")
    root = Path(ctx.settings.data_root)
    if not (root / "dq_events").exists():
        return Check(name="data_quality", status="ok", reason="no data-quality events recorded")
    from goldbot.data.store import Store
    try:
        ev = Store(root).read("dq_events", start=ctx.now - pd.Timedelta(hours=DQ_WINDOW_HOURS), end=ctx.now)
    except (ValueError, OSError) as exc:
        return Check(name="data_quality", status="warn", reason=f"dq_events unreadable: {exc}")
    errs = ev[ev["severity"] == "error"] if not ev.empty and "severity" in ev.columns else ev.iloc[0:0]
    if errs.empty:
        return Check(name="data_quality", status="ok", reason=f"no error events in {DQ_WINDOW_HOURS} h ({len(ev)} warnings)")
    kinds = ", ".join(f"{k} x{n}" for k, n in errs["check"].value_counts().items())
    return Check(name="data_quality", status="warn",
                 reason=f"{len(errs)} error events in {DQ_WINDOW_HOURS} h ({kinds}): rows quarantined, see bars_quarantine")


@register_check("global", block=BLOCK_TAIL, pos=20)
def check_drift(ctx: HealthContext) -> Check:
    """state/drift.json (daily drift_watch): system halt fails, agent halts and size-downs warn, a stale file warns."""
    f = ctx.state_dir / "drift.json"
    if not f.exists():
        return Check(name="drift", status="ok", reason="no drift report yet (no champion, or drift_watch not run)")
    try:
        d = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="drift", status="fail", reason=f"drift.json unreadable (entries halted): {exc}")
    if d.get("system_halt"):
        why = "; ".join(d["system_halt"].get("reasons") or [])
        return Check(name="drift", status="fail",
                     reason=f"SYSTEM HALT pending review ({why}): python -m goldbot.ops.run drift-review")
    parts, status = [], "ok"
    if d.get("halted"):
        status = "warn"
        parts.append(f"halted agents: {', '.join(sorted(d['halted']))}")
    if d.get("size_factor"):
        status = "warn"
        parts.append(f"sized down: {', '.join(sorted(d['size_factor']))}")
    age_h = (ctx.now - pd.Timestamp(d.get("ts"))).total_seconds() / 3600 if d.get("ts") else float("inf")
    if age_h > 72:
        status = "warn"
        parts.append(f"report {age_h:.0f} h old")
    return Check(name="drift", status=cast(Status, status), reason="; ".join(parts) or "no drift")


@register_check("global", block=BLOCK_TAIL, pos=30)
def check_model_watch(ctx: HealthContext) -> Check:
    """state/model_watch.json (daily model_watch): a new champion whose watch has too few trades for any CUSUM alarm
    at the 5% rate (`cannot_alarm`) warns, so a silent "no alarm" is never mistaken for a healthy watch."""
    f = ctx.state_dir / "model_watch.json"          # goldbot.ops.jobs.MODEL_WATCH_FILE
    if not f.exists():
        return Check(name="model_watch", status="ok", reason="no champion watch report yet")
    try:
        agents = (_read_json(f) or {}).get("agents") or {}
    except (ValueError, OSError, AttributeError) as exc:
        return Check(name="model_watch", status="warn", reason=f"model_watch.json unreadable: {exc}"[:300])
    blind = sorted(a for a, v in agents.items() if isinstance(v, dict) and v.get("action") == "cannot_alarm")
    if blind:
        return Check(name="model_watch", status="warn",
                     reason=f"champion watch cannot alarm (too few trades) for {', '.join(blind)}: relies on "
                            "drift_watch and the drawdown halt")
    return Check(name="model_watch", status="ok", reason=f"{len(agents)} champion(s) watched")


@register_check("global", block=BLOCK_TAIL, pos=40)
def check_feature_versions(ctx: HealthContext) -> Check:
    """Active models per feature version: champions, challengers and previous champions (the model_watch rollback
    target, which may become champion again). Each version is current (the default set, or the default set plus the
    macro drivers), legacy (rebuilt by goldbot/features/legacy.py) or cannot be rebuilt (never scored: warns).
    goldbot/features/legacy.py can be deleted once no legacy count remains. The registry file is read, never created."""
    if ctx.settings is None:
        return _skipped("feature_versions", "settings unreadable")
    f = Path(ctx.settings.research.models_dir) / "registry.json"
    if not f.exists():
        return Check(name="feature_versions", status="ok", reason="no model registry yet")
    try:
        rows = [r for r in (_read_json(f) or []) if r.get("status") in ("champion", "challenger", "previous")]
    except (ValueError, OSError, AttributeError) as exc:
        return Check(name="feature_versions", status="warn", reason=f"model registry unreadable: {exc}"[:300])
    from goldbot.features.registry import feature_version, versions_for
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, MACRO_FEATURE_NAMES
    sets = [list(DEFAULT_FEATURE_NAMES), [*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES]]
    current = {feature_version(names) for names in sets}
    counts: dict[str, int] = {}
    for r in rows:
        counts[str(r.get("feature_version") or "")] = counts.get(str(r.get("feature_version") or ""), 0) + 1
    parts, status = [], "ok"
    for fv, n in sorted(counts.items()):
        if fv in current:
            kind = "current"
        elif fv == "":
            kind = "unversioned"
        elif any(versions_for(names, fv) is not None for names in sets):
            kind = "legacy"
        else:
            kind, status = "cannot be rebuilt: not scored", "warn"
        parts.append(f"{fv or '-'} ({kind}) {n}")
    return Check(name="feature_versions", status=cast(Status, status),
                 reason="active models by feature version: " + ("; ".join(parts) or "none"))


@register_check("global", block=BLOCK_TAIL, pos=90)
def check_news(ctx: HealthContext) -> Check:
    if ctx.settings is not None and not ctx.settings.news.feeds:
        return Check(name="news", status="ok", reason="no feeds configured")
    f = ctx.state_dir / "news_feeds.json"
    if not f.exists():
        return Check(name="news", status="warn", reason="no news_feeds.json yet: news service not started")
    try:
        feeds: dict[str, dict[str, Any]] = _read_json(f)
    except (ValueError, OSError) as exc:
        return Check(name="news", status="fail", reason=f"news_feeds.json unreadable: {exc}")
    if not feeds:
        return Check(name="news", status="warn", reason="news_feeds.json lists no feeds")
    poll = ctx.settings.news.poll_seconds if ctx.settings is not None else 300
    last = max(pd.Timestamp(h["ts"]) for h in feeds.values() if h.get("ts")) if any(h.get("ts") for h in feeds.values()) else None
    age = (ctx.now - last).total_seconds() if last is not None else float("inf")
    bad = sorted(n for n, h in feeds.items() if not h.get("ok"))
    if age > NEWS_STALE_POLLS * poll:
        return Check(name="news", status="fail", reason=f"last poll {_age(age)} ago (> {NEWS_STALE_POLLS} polls): news service down")
    if len(bad) == len(feeds):
        return Check(name="news", status="fail", reason=f"all {len(feeds)} feeds failing: no shock blackout possible")
    if bad:
        errs = "; ".join(f"{n}: {str(feeds[n].get('error', '?'))[:80]}" for n in bad)
        return Check(name="news", status="warn", reason=f"{len(bad)}/{len(feeds)} feeds failing ({errs})")
    return Check(name="news", status="ok", reason=f"{len(feeds)} feeds ok, last poll {_age(age)} ago")


# D12: the feed reconciliation check lives with the reconciliation (goldbot/data/crossfeed.py)
register_check("account", block=BLOCK_RECONCILIATION, pos=10)(check_reconciliation)
# BACKLOG 79: the broker tick record lives with the record (goldbot/data/ticks.py); WARN only, research data
register_check("account", block=BLOCK_RECONCILIATION, pos=20)(check_tick_record)
# TradingView sign-in / pull state lives with the pull's health module (goldbot/ops/tv_pull_health.py); WARN only
register_check("global", block=BLOCK_TAIL, pos=85)(check_tv_pull)
