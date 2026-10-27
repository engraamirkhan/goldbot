"""Backlog 137 (docs/design/trades-ux-2026-10-27.md §7.5; ADR 0053): the ranking in every daily review and the weekly
digest. The morning Telegram section is at most 12 lines (the heading line, the top 5, the 2 lowest when 8 or more are
ranked, "N more ranked · M too few to rank yet" and the dashboard link); each line carries the lower bound, the mean, n, hit
rate, net money, edge vs random and the status, with the true minus and no tables. The weekly digest adds the rank
change since last week. Telegram, the dashboard and `GET /api/strategies/ranking` show the same numbers, and no line
carries an arm id, an account slug or a login."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import strategy_ranking as sr
from goldbot.api.app import create_app
from goldbot.api.strategy_labels import ArmInfo, Catalog, Labels, catalog
from goldbot.telegram import ranking_text as rt
from goldbot.telegram.digest import with_ranking
from goldbot.telegram.labs import ARM_ID, MINUS, guard
from tests.test_api_views import _login
from tests.test_strategy_ranking import NOW, _spread
from tests.test_trades_ledger import DAY, SCALP, _arm, _engine


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(SCALP, DAY, "vantage-demo")


def _cat(n: int) -> Catalog:
    infos = [ArmInfo(arm_id=f"A{i}", family=f"fam{i:02d}", timeframe="15m", style="intraday", unit=f"A{i}")
             for i in range(n)]
    return Catalog(labels=Labels(), arms={i.arm_id: i for i in infos}, reads={})


def _view(n_ranked: int, n_few: int = 0) -> Any:
    cat = _cat(n_ranked + n_few)
    rows = []
    for i in range(n_ranked):                                    # fam00 best, fam01 next, ...
        rows += _spread(f"A{i}", [0.5 - i * 0.05, -0.2 - i * 0.05] * 20, 10, family=f"fam{i:02d}")
    for j in range(n_few):
        i = n_ranked + j
        rows += _spread(f"A{i}", [0.3] * 5, 5, family=f"fam{i:02d}")
    return sr.rank(rows, cat, NOW)


def test_the_morning_section_has_the_heading_top_5_the_2_lowest_counts_and_link_in_at_most_12_lines():
    lines = rt.ranking_lines(_view(10, n_few=3))
    assert len(lines) <= rt.LINES_MAX
    assert lines[0] == f"🏁 {sr.HEADING}"
    ranked = [x for x in lines if re.match(r"^\d+\. ", x)]
    assert [x.split(" · ")[0] for x in ranked] == ["1. Fam00", "2. Fam01", "3. Fam02", "4. Fam03", "5. Fam04",
                                                   "9. Fam08", "10. Fam09"]
    assert lines[-2] == "3 more ranked · 3 too few to rank yet" and lines[-1] == rt.LINK


def test_under_8_ranked_the_lowest_are_not_repeated():
    lines = rt.ranking_lines(_view(6))
    assert [x.split(".")[0] for x in lines if re.match(r"^\d+\. ", x)] == ["1", "2", "3", "4", "5"]
    assert lines[-2] == "1 more ranked"                            # no "0 too few to rank"


def test_each_line_carries_the_bound_mean_n_hit_money_edge_and_status_with_the_true_minus_and_no_table():
    line = rt.ranking_lines(_view(2))[2]                         # second place: a negative bound
    assert re.search(rf"^2\. Fam01 · {MINUS}\d\.\d\d R/trade at least \(avg \+0\.10, 40 fills\) · hit 50% · "
                     rf"\+£\d+\.\d\d · early, not proven$", line), line
    assert "-" not in line and "|" not in line


def test_edge_vs_random_shows_with_its_source():
    cat = Catalog(labels=Labels(), arms={
        "A1": ArmInfo(arm_id="A1", family="alpha", timeframe="15m", unit="A1", control_arm="C1"),
        "C1": ArmInfo(arm_id="C1", family="random_bracket", timeframe="15m", unit="C1", is_control=True)}, reads={})
    rows = [*_spread("A1", [0.3, 0.1] * 20, 10), *_spread("C1", [-0.1, 0.1] * 20, 10, kind="control",
                                                         family="random_bracket")]
    assert "vs random +0.20 R (interim)" in rt.ranking_lines(sr.rank(rows, cat, NOW))[1]


def test_with_no_ranked_family_the_section_says_why_and_with_no_family_it_is_absent():
    assert rt.ranking_lines(_view(0, n_few=2))[1] == "None ranked yet: a strategy needs 30 fills on 10 days for a place"
    assert rt.ranking_lines(_view(0)) == []


def test_the_weekly_section_ranks_each_versions_life_with_its_move_and_the_weeks_top_three():
    cat = _cat(3)
    before = sr.rank([*_spread("A0", [0.4, 0.2] * 20, 10, family="fam00"),
                      *_spread("A1", [0.1, 0.0] * 20, 10, family="fam01")], cat, NOW)
    life = sr.rank([*_spread("A0", [0.0, 0.1] * 20, 10, family="fam00"),
                    *_spread("A1", [0.5, 0.3] * 20, 10, family="fam01"),
                    *_spread("A2", [0.2, 0.1] * 20, 10, family="fam02")], cat, NOW)
    lines = rt.weekly_lines(life, life, before)
    assert len(lines) <= rt.LINES_MAX and lines[1] == "Since each version began (move since last week):"
    assert lines[2].startswith("1. Fam01 (up 1) · ") and lines[3].startswith("2. Fam02 (new) · ")
    assert lines[4].startswith("3. Fam00 (down 2) · ")
    assert lines[5] == "This week:" and lines[-1] == rt.LINK


def test_the_section_follows_the_guard_so_a_family_named_like_an_arm_id_still_shows():
    """ADR 0053: the lab lines keep labs.guard; the ranking names families ("NR4 break" looks like an arm id)."""
    section = ["🏁 heading", "1. NR4 break · +0.03 R/trade at least (avg +0.12, 412 fills) · hit 48%"]
    assert guard(section[1]) == ""                                # the guard would have dropped it
    assert with_ranking("body", section).endswith(section[1])
    assert with_ranking("body", []) == "body"


# ------------------------------------------------------------------------------------------------ one builder
def _seed(state: Any) -> None:
    _engine(state, SCALP)
    for d in range(12):
        for k in range(4):
            sent = NOW - pd.Timedelta(days=d, hours=2 + k)
            _arm(state, f"a{d}-{k}", "S1A", sent=sent, pnl=2.4 * (0.6 if (d + k) % 3 else -0.5))
            _arm(state, f"b{d}-{k}", "S2A", sent=sent, pnl=2.4 * (0.2 if k % 2 else -0.1))
            _arm(state, f"c{d}-{k}", "S0", sent=sent, pnl=2.4 * (0.1 if (d + k) % 2 else -0.1))


def _same(api: dict[str, Any], view: Any) -> None:
    def strip(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            f = {k: v for k, v in r["figures"].items() if k not in ("last_7d", "last_30d", "trend")}
            out.append({**r, "figures": f, "variants": [v["figures"]["n"] for v in r["variants"]]})
        return out
    mine = view.model_dump(mode="json")
    assert strip(api["rows"]) == strip(mine["rows"]) and strip(api["controls"]) == strip(mine["controls"])


def test_telegram_the_dashboard_and_the_endpoint_show_the_same_numbers(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _seed(state)
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    assert c.get("/api/strategies/ranking").status_code == 401
    h = _login(c, app)["viewer"]
    res = c.get("/api/strategies/ranking", params={"window": "all"}, headers=h)
    assert res.status_code == 200
    body = res.json()
    view = rt.load_ranker(state, NOW)("all")
    assert [r["key"] for r in body["rows"]] == [r.key for r in view.rows] and body["k_ranked"] == view.k_ranked >= 1
    _same(body, view)
    lines = rt.ranking_lines(view)
    for r in view.rows[:5]:
        f = r.figures
        bound = rt.family_line(r).split(" · ")[1]
        assert bound.startswith(f"{rt._r(f.lower_adjusted)} R/trade at least" if f.lower_adjusted is not None
                                else r.unranked_reason or "")
        assert rt.family_line(r) in lines
    assert c.get("/api/strategies/ranking", params={"style": "nope"}, headers=h).status_code == 422
    one = c.get("/api/strategies/ranking", params={"account": SCALP, "window": "7d"}, headers=h).json()
    assert one["account"] == SCALP and one["window"] == "7d"


def test_no_line_carries_an_arm_id_an_account_slug_or_a_login(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _seed(state)
    text = "\n".join([*rt.ranking_lines(rt.load_ranker(state, NOW)("all")),
                      *rt.weekly(state, NOW)])
    assert text
    ids = set(catalog().arms)
    assert not [m for m in ARM_ID.findall(text) if m in ids]
    for slug in ("icm-", "vantage-", "-probe", "-demo", "-live"):
        assert slug not in text.lower(), slug
    assert not re.search(r"\d{6,}", text.replace(",", ""))


def test_the_morning_report_carries_the_ranking_after_its_lab_lines(tmp_path):
    from goldbot.telegram.digest import build_digest
    state = tmp_path / "state"
    state.mkdir()
    _seed(state)
    text = build_digest(state, NOW + timedelta(days=1), roadmap=tmp_path / "none.md")
    assert f"🏁 {sr.HEADING}" in text and text.rstrip().endswith(rt.LINK)
