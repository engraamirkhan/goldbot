"""Owner's MT5 trade history analysis (goldbot/research/owner_history.py).

Synthetic fixtures only: the real report holds personal account data and never enters this public repo. The fixtures
copy the MT5 trade-history report layout (header block, Positions / Orders / Deals / Results sections, server-time
stamps `YYYY.MM.DD HH:MM:SS`, space thousands separators, a UTF-16 HTML file with hidden and colspan cells).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import pytest

from goldbot.research import owner_history as oh

NAME, ACCOUNT, COMPANY = "Test Trader", "99999999", "Some Broker Ltd"
RATE = 0.8          # USD -> GBP implied by every synthetic profit
CONTRACT = 100.0

# (open server time, side, lots, entry, sl, tp, close server time, exit, commission, swap)
TITLE = "Trade History " + "Report"        # split so the repo's report-content hook never matches this file
TICKET0 = 71234560                          # synthetic position/order tickets; must never reach an output
# T1's stop was moved into profit (final S/L 2655 above a 2650 buy); its initial stop 2645 is in the Orders section
INITIAL_SL = [2645.00, 2660.50, None, 2663.00, 2697.00, 2700.00]
TRADES = [
    ("2026.10.01 10:05:12", "buy", 0.01, 2650.00, 2655.00, 2660.00, "2026.10.01 10:35:12", 2660.00, -0.20, 0.0),
    ("2026.10.01 16:00:00", "sell", 0.02, 2655.50, 2660.50, None, "2026.10.01 16:20:00", 2658.00, -0.40, 0.0),
    ("2026.10.02 03:30:00", "buy", 0.01, 2640.00, None, None, "2026.10.02 05:00:00", 2639.50, -0.20, 0.0),
    ("2026.10.02 15:31:00", "sell", 0.01, 2660.00, 2663.00, None, "2026.10.02 15:50:00", 2663.00, -0.20, 0.0),
    ("2026.10.05 10:10:00", "buy", 0.02, 2700.00, 2697.00, None, "2026.10.05 12:00:00", 2706.00, -0.40, 0.0),
    ("2026.10.05 18:00:00", "buy", 0.01, 2705.00, 2700.00, None, "2026.10.06 02:00:00", 2702.00, -0.20, -0.50),
]


def _profit(side: str, lots: float, entry: float, exit_: float) -> float:
    d = 1.0 if side == "buy" else -1.0
    return round((exit_ - entry) * d * lots * CONTRACT * RATE, 2)


def _num(x: float | None) -> str:
    if x is None:
        return ""
    s = f"{x:,.2f}".replace(",", " ")      # MT5 groups thousands with a space
    return s


def report_rows(*, orders: bool = True, currency: str = "GBP",
                extra_cash: list[tuple[str, str]] | None = None) -> list[list[str]]:
    """The logical table of a synthetic MT5 report (one list per table row)."""
    rows: list[list[str]] = [
        [TITLE],
        ["Name:", NAME], ["Account:", f"{ACCOUNT} ({currency}, Demo-Server, demo, Hedge)"], ["Company:", COMPANY],
        ["Date:", "2026.10.09 18:00"], [],
        ["Positions"],
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P", "Time", "Price", "Commission",
         "Swap", "Profit"],
    ]
    for i, (ot, side, lots, entry, sl, tp, ct, ex, com, swp) in enumerate(TRADES):
        rows.append([ot, str(TICKET0 + i), "XAUUSD", side, f"{lots:.2f}", _num(entry), _num(sl), _num(tp), ct,
                     _num(ex), _num(com), _num(swp), _num(_profit(side, lots, entry, ex))])
    rows.append(["2026.10.03 11:00:00", str(TICKET0 + 50), "EURUSD", "buy", "0.10", "1.08000", "", "",
                 "2026.10.03 12:00:00", "1.08100", "-0.50", "0.00", "8.00"])
    if orders:
        rows += [[], ["Orders"],
                 ["Open Time", "Order", "Symbol", "Type", "Volume", "Price", "S / L", "T / P", "Time", "State",
                  "Comment"]]
        for i, (ot, side, lots, _e, _sl, tp, ct, _x, _c, _s) in enumerate(TRADES):
            rows.append([ot, str(TICKET0 + i), "XAUUSD", side, f"{lots:.2f} / {lots:.2f}", "market",
                         _num(INITIAL_SL[i]), _num(tp), ot, "filled", ""])
            rows.append([ct, str(TICKET0 + 100 + i), "XAUUSD", "sell" if side == "buy" else "buy",
                         f"{lots:.2f} / {lots:.2f}", "market", "", "", ct, "filled", ""])
    cash = [("2026.09.30 09:00:00", "50.00")] + (extra_cash or [])
    rows += [[], ["Deals"],
             ["Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price", "Order", "Commission", "Fee", "Swap",
              "Profit", "Balance", "Comment"]]
    rows += [[t, str(i + 1), "", "balance", "", "", "", "", "0.00", "0.00", "0.00", amt, "", "Deposit"]
             for i, (t, amt) in enumerate(cash)]
    rows += [[], ["Results"],
             ["Total Net Profit:", "14.30", "Gross Profit:", "17.60", "Gross Loss:", "-3.30"],
             ["Balance:", "64.30", "Equity:", "64.30"]]
    return rows


SECTION_TITLES = {"Positions", "Orders", "Deals", "Results", TITLE}


def report_html(rows: list[list[str]]) -> bytes:
    """MT5-like markup: UTF-16 LE with BOM, label cells as <th>, a hidden colspan cell in every position row and a
    colspan=2 Profit column (header and values)."""
    out = [f"<html><head><title>{TITLE}</title></head><body><div align=center>",
           "<table cellspacing=1 cellpadding=3 border=0>"]
    section = ""
    for r in rows:
        if not r:
            out.append('<tr><td colspan=13 style="height: 10px"></td></tr>')
        elif len(r) == 1:
            section = r[0] if r[0] in SECTION_TITLES else section
            out.append(f'<tr align=center><th colspan=13><div><b>{escape(r[0])}</b></div></th></tr>')
        elif r[0].endswith(":") and section != "Results":
            out.append(f'<tr align=left><th colspan=3 nowrap align=right>{escape(r[0])}</th>'
                       f'<th colspan=10 nowrap align=left><b>{escape(r[1])}</b></th></tr>')
        elif section == "Positions":
            cells = [f"<td>{escape(c)}</td>" for c in r]
            is_header = r[0] == "Time"
            if not is_header:
                cells.insert(4, '<td class="hidden" colspan=8>comment</td>')
            cells[-1] = cells[-1].replace("<td>", "<td colspan=2>")
            out.append('<tr bgcolor="#F7F7F7" align=right>' + "".join(cells) + "</tr>")
        else:
            out.append("<tr align=right>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>")
    out.append("</table></div></body></html>")
    return "\n".join(out).encode("utf-16")


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def report_xlsx(rows: list[list[str]], path: Path) -> None:
    """A minimal real .xlsx: shared strings for text, numeric cells for plain numbers (as Excel stores them)."""
    strings: list[str] = []
    sheet_rows = []
    for ri, r in enumerate(rows, start=1):
        cells = []
        for ci, v in enumerate(r):
            ref = f"{_col(ci)}{ri}"
            try:
                num = float(v.replace(" ", "")) if v and ":" not in v and "/" not in v else None
            except ValueError:
                num = None
            if num is not None:
                cells.append(f'<c r="{ref}"><v>{num}</v></c>')
            elif v:
                strings.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        sheet_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"></Types>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook {ns} {rel_ns}><sheets>'
                   '<sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '</Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0"?><worksheet {ns}><sheetData>'
                   + "".join(sheet_rows) + "</sheetData></worksheet>")
        z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst {ns}>'
                   + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")


@pytest.fixture
def html_file(tmp_path: Path) -> Path:
    p = tmp_path / "ReportHistory-synthetic.html"
    p.write_bytes(report_html(report_rows()))
    return p


@pytest.fixture
def xlsx_file(tmp_path: Path) -> Path:
    p = tmp_path / "ReportHistory-synthetic.xlsx"
    report_xlsx(report_rows(), p)
    return p


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("text,value", [
    ("1 234.56", 1234.56), ("1 234,56", 1234.56), ("1,234.56", 1234.56), ("1.234,56", 1234.56),
    ("-12.30", -12.3), ("−12.30", -12.3), ("0.01 / 0.01", 0.01), ("2 645.00", 2645.0), ("", None), ("12,5", 12.5),
])
def test_numbers_parse_in_any_locale_format(text, value):
    assert oh.parse_number(text) == value


def test_html_report_parses_xauusd_positions_and_converts_server_time_to_utc(html_file):
    rep = oh.load_report(html_file)
    assert rep.currency == "GBP"
    pos = oh.xauusd_positions(rep)
    assert len(pos) == 6                                     # the EURUSD position is dropped
    first = pos[0]
    # 10:05:12 Athens summer time (UTC+3) is 07:05:12 UTC
    assert first.open_utc == pd.Timestamp("2026-10-01 07:05:12", tz="UTC")
    assert first.side == "buy" and first.lots == 0.01 and first.entry == 2650.0
    assert first.sl == 2655.0 and first.initial_sl == 2645.0 and first.tp == 2660.0 and first.exit == 2660.0
    assert first.profit == 8.0 and first.commission == -0.2
    assert pos[2].sl is None
    assert [d.amount for d in rep.deposits] == [50.0]


def test_xlsx_export_parses_to_the_same_positions_as_the_html_report(html_file, xlsx_file):
    a = oh.xauusd_positions(oh.load_report(html_file))
    b = oh.xauusd_positions(oh.load_report(xlsx_file))
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]
    assert oh.load_report(xlsx_file).currency == "GBP"


def test_report_without_positions_section_pairs_deals_into_trades(tmp_path):
    p = tmp_path / "deals.csv"
    p.write_text("Time;Deal;Symbol;Type;Direction;Volume;Price;Order;Commission;Fee;Swap;Profit;Balance;Comment\n"
                 "2026.09.30 09:00:00;1;;balance;;;;;0;0;0;50,00;50,00;Deposit\n"
                 "2026.10.01 10:05:12;2;XAUUSD;buy;in;0,02;2 650,00;10;-0,20;0;0;0;50,00;\n"
                 "2026.10.01 10:20:00;3;XAUUSD;sell;out;0,01;2 655,00;11;-0,10;0;0;4,00;54,00;\n"
                 "2026.10.01 10:35:12;4;XAUUSD;sell;out;0,01;2 660,00;12;-0,10;0;0;8,00;62,00;\n",
                 encoding="utf-8")
    pos = oh.xauusd_positions(oh.load_report(p))
    assert [(x.lots, x.entry, x.exit, x.profit) for x in pos] == [(0.01, 2650.0, 2655.0, 4.0),
                                                                 (0.01, 2650.0, 2660.0, 8.0)]
    assert pos[0].commission == pytest.approx(-0.2)        # half the entry commission plus the exit's
    assert pos[0].sl is None


def test_unrecognised_file_fails_with_a_clear_message(tmp_path):
    p = tmp_path / "x.html"
    p.write_text("<html><body><p>nothing</p></body></html>")
    with pytest.raises(ValueError, match="no XAUUSD trades|Positions"):
        oh.analyse(oh.load_report(p))


# ---------------------------------------------------------------- analysis

@pytest.fixture
def result(html_file):
    return oh.analyse(oh.load_report(html_file), targets=(3.0, 5.0), n_paths=500)


def test_risk_and_r_multiples_use_the_stop_and_the_implied_usd_rate(result):
    t1 = result.trades[0]
    assert result.summary.usd_to_ccy == pytest.approx(RATE)
    # 1R from the INITIAL stop (Orders section): 5 USD x 0.01 lot x 100 oz x 0.8
    assert t1.risk_source == "sl_initial" and t1.risk == pytest.approx(4.0)
    assert t1.net == pytest.approx(7.8) and t1.r_multiple == pytest.approx(1.95)
    assert t1.holding_min == pytest.approx(30.0)
    assert result.trades[2].risk_source == "proxy_avg_loss"
    assert t1.leverage == pytest.approx(0.01 * 100 * 2650 * RATE / 50.0)   # notional / equity at entry


def test_headline_statistics(result):
    s = result.summary
    assert s.n_trades == 6 and s.win_rate == pytest.approx(2 / 6)
    assert s.total_net == pytest.approx(3.4 - 3.2 + 6.1)        # the three daily totals
    assert s.avg_win == pytest.approx((7.8 + 9.2) / 2)
    assert s.profit_factor == pytest.approx(17.0 / (4.4 + 0.6 + 2.6 + 3.1))
    assert s.longest_losing_streak == 3
    assert s.max_drawdown == pytest.approx(57.8 - 50.2)
    assert s.max_drawdown_pct == pytest.approx((57.8 - 50.2) / 57.8)


def test_daily_pnl_distribution_and_target_shares(result):
    d = result.summary.daily
    assert d.n_days == 3
    assert d.worst_day == "2026-10-02" and d.worst == pytest.approx(-3.2)
    assert d.share_at_least == {"3": pytest.approx(2 / 3), "5": pytest.approx(1 / 3)}


def test_breakdowns_by_session_side_and_entry_context(result):
    sess = {g.key: g for g in result.summary.by_session}
    assert sess["london"].n == 2 and sess["newyork"].n == 3 and sess["asia"].n == 1
    side = {g.key: g.n for g in result.summary.by_side}
    assert side == {"buy": 4, "sell": 2}
    assert result.summary.share_near_session_open == pytest.approx(3 / 6)
    assert result.summary.share_near_round_number == pytest.approx(4 / 6)
    assert result.summary.news is None                     # no calendar given
    assert {g.key for g in result.summary.by_weekday} == {"Thu", "Fri", "Mon"}


def test_news_proximity_uses_tier_one_events_when_given(html_file):
    ev = pd.DataFrame({"ts_utc": pd.to_datetime(["2026-10-02 12:30"], utc=True), "tier": [1]})
    r = oh.analyse(oh.load_report(html_file), events=ev, n_paths=200)
    assert r.summary.news is not None
    near = {g.key: g.n for g in r.summary.news}
    assert near == {"within 30 min of tier-1": 1, "other": 5}




def test_session_opens_follow_each_dates_local_clock():
    # London opens 08:00 Europe/London: 07:00 UTC in summer (BST), 08:00 UTC in winter (GMT)
    idx = pd.DatetimeIndex(["2026-10-01 07:05", "2026-12-01 07:05", "2026-12-01 08:05", "2026-12-01 13:35",
                            "2026-10-01 12:35"], tz="UTC")
    assert oh.session_open_flags(idx).tolist() == [True, False, True, True, True]


def test_session_open_base_rate_comes_from_the_owners_active_hours(result):
    # active UTC hours {0, 7, 12, 13, 15}: London 07:00-07:30 and NY 12:30-13:00 fall inside, Asia 23:00 does not
    assert result.summary.session_open_base_rate == pytest.approx(60 / 300)


# ---------------------------------------------------------------- R from the initial stop

def test_a_stop_moved_into_profit_without_the_orders_section_is_not_used_as_one_r(tmp_path):
    p = tmp_path / "no-orders.html"
    p.write_bytes(report_html(report_rows(orders=False)))
    r = oh.analyse(oh.load_report(p), n_paths=200)
    t1 = r.trades[0]
    assert t1.risk_source == "sl_moved" and t1.r_multiple is None and t1.risk is None
    assert r.summary.n_r == 4                                # T1 (moved) and T3 (no stop) are out of the R stats
    assert r.trades[1].risk_source == "sl_final"
    assert any("final" in n.lower() for n in r.summary.notes)


def test_r_statistics_count_only_trades_whose_one_r_is_a_real_stop(result):
    assert result.summary.n_r == 5
    assert {t.risk_source for t in result.trades} == {"sl_initial", "proxy_avg_loss"}


# ---------------------------------------------------------------- ruin

def test_bootstrap_ruin_is_deterministic_and_reflects_the_edge():
    rng = np.random.default_rng(1)
    losing = rng.normal(-0.05, 0.1, 200)
    winning = rng.normal(0.02, 0.01, 200)
    a = oh.bootstrap_ruin(losing, horizon=200, n_paths=400, seed=3)
    assert a == oh.bootstrap_ruin(losing, horizon=200, n_paths=400, seed=3)
    assert a.p_ruin > 0.9 and "block" in a.method and a.block_length >= 5
    assert oh.bootstrap_ruin(winning, horizon=200, n_paths=400, seed=3).p_ruin == 0.0
    fixed = oh.bootstrap_ruin(np.full(20, -0.1), horizon=10, n_paths=50, compounding=False)
    assert fixed.p_ruin == 1.0


def test_ruin_never_reports_infinity_even_for_a_wipe_out_trade():
    est = oh.bootstrap_ruin(np.array([0.5, 0.4, -1.5, 0.3, 0.2]), horizon=50, n_paths=100)
    assert np.isfinite(est.median_end_log10)
    assert all(np.isfinite(v) for v in est.model_dump().values() if isinstance(v, float))


def test_ruin_rows_cover_both_sizings_with_and_without_the_samples_edge(result):
    methods = [r.method for r in result.summary.ruin]
    assert len(methods) == 4
    assert "compounding" in methods[0] and "zero edge" not in methods[0]
    assert "fixed stakes" in methods[1] and "zero edge" not in methods[1]
    assert "compounding" in methods[2] and "zero edge" in methods[2]
    assert "fixed stakes" in methods[3] and "zero edge" in methods[3]
    assert all(0.0 <= r.p_ruin <= r.p_halved <= 1.0 for r in result.summary.ruin)
    assert "(compounding / fixed / zero edge compounding / zero edge fixed)" in oh.console_summary(result)


# ---------------------------------------------------------------- statistics added in review

def test_daily_mean_carries_a_t_stat_and_interval_and_a_london_day_view(result):
    d = result.summary.daily
    assert d.t_stat is not None and d.ci95[0] < d.mean < d.ci95[1]
    # T6 closes 23:00 UTC on 5 Oct = 00:00 London on 6 Oct, so the London view moves it to its own day
    assert result.summary.daily_london.n_days == 4
    assert "2026-10-06" in result.summary.daily_london.days
    assert result.summary.return_on_equity_t is not None


def test_drawdown_ignores_a_withdrawal(tmp_path):
    p = tmp_path / "withdrawal.html"
    p.write_bytes(report_html(report_rows(extra_cash=[("2026.10.03 09:00:00", "-20.00")])))
    s = oh.analyse(oh.load_report(p), n_paths=200).summary
    assert s.max_drawdown == pytest.approx(57.8 - 50.2)
    assert s.max_drawdown_pct == pytest.approx((57.8 - 50.2) / 57.8)


def test_a_usd_account_whose_trades_imply_another_rate_is_flagged(tmp_path):
    p = tmp_path / "usd.html"
    p.write_bytes(report_html(report_rows(currency="USD")))
    s = oh.analyse(oh.load_report(p), n_paths=200).summary
    assert s.usd_to_ccy == pytest.approx(RATE)
    assert any("USD account" in n for n in s.notes)


# ---------------------------------------------------------------- seeds

def test_hypothesis_seeds_are_labelled_in_sample(result):
    md = oh.render_markdown(result)
    assert "## Hypothesis seeds" in md and "in-sample" in md.lower() and "not evidence" in md.lower()
    assert all(s.status.startswith("seed") for s in result.seeds) and len(result.seeds) >= 4
    assert "| ID | Hypothesis | Status |" in md


def test_seeds_state_the_slices_examined_and_the_bonferroni_bar(result):
    md = oh.render_markdown(result)
    k = result.summary.slices_examined
    assert k >= 10 and f"K = {k} slices examined" in md and "Bonferroni" in md and "Student's t" in md
    assert result.summary.bonferroni_t > 2.5
    assert any("differ from the owner's other entries" in s.hypothesis for s in result.seeds)


def test_seed_rationales_carry_no_money_amounts(result):
    for s in result.seeds:
        text = s.hypothesis + s.rationale
        assert "£" not in text and "$" not in text.replace("$1 of a $10", ""), s.id
    md = oh.render_markdown(result)
    assert "do not paste amounts into the repo" in md.lower()


# ---------------------------------------------------------------- privacy and outputs

def test_outputs_never_carry_account_identifiers(result, tmp_path):
    out = tmp_path / "private"
    paths = oh.write_outputs(result, out)
    for p in paths:
        text = p.read_text(encoding="utf-8")
        for secret in (NAME, ACCOUNT, COMPANY, "Demo-Server", str(TICKET0), str(TICKET0 + 1), str(TICKET0 + 100)):
            assert secret not in text, (p.name, secret)
    summary = json.loads(next(p for p in paths if p.suffix == ".json").read_text())
    assert summary["summary"]["n_trades"] == 6


def test_outputs_refuse_a_folder_inside_the_repository(result):
    repo = Path(oh.__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="git work tree"):
        oh.write_outputs(result, repo / "trade-history")


def test_outputs_refuse_a_case_variant_of_the_repository_path(result):
    repo = Path(oh.__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="git work tree"):
        oh.write_outputs(result, Path(str(repo).upper()) / "trade-history")


def test_outputs_refuse_a_sibling_worktree_or_any_other_git_tree(result, tmp_path):
    wt = tmp_path / "sibling-worktree"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")     # how a linked worktree marks itself
    with pytest.raises(ValueError, match="git work tree"):
        oh.write_outputs(result, wt / "deep" / "trade-history")
    other = tmp_path / "other-clone"
    (other / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="git work tree"):
        oh.write_outputs(result, other / "out")


def test_oversized_input_and_zip_members_are_refused(tmp_path, monkeypatch, html_file, xlsx_file):
    monkeypatch.setattr(oh, "MAX_INPUT_BYTES", 1000)
    with pytest.raises(ValueError, match="too large"):
        oh.load_report(html_file)
    monkeypatch.setattr(oh, "MAX_INPUT_BYTES", 50 * 2**20)
    monkeypatch.setattr(oh, "MAX_ZIP_MEMBER_BYTES", 100)
    with pytest.raises(ValueError, match="too large"):
        oh.load_report(xlsx_file)


def test_a_missing_report_is_reported_without_its_path(tmp_path):
    missing = tmp_path / "secret-folder" / "ReportHistory-123.html"
    with pytest.raises(SystemExit) as e:
        oh.main([str(missing), "--no-news"])
    assert "secret-folder" not in str(e.value) and "123" not in str(e.value) and ".html" in str(e.value)


def test_cli_reads_the_newest_report_in_a_folder_and_writes_next_to_it(html_file, capsys):
    rc = oh.main([str(html_file.parent), "--targets", "3,5", "--no-news"])
    assert rc == 0
    names = sorted(p.name for p in html_file.parent.iterdir())
    assert any(n.endswith(".md") and n.startswith("owner_history") for n in names)
    out = capsys.readouterr().out
    assert "trades" in out and ACCOUNT not in out and NAME not in out


# ---------------------------------------------------------------- repository guard (pre-commit hook)

def _hook():
    import importlib.util
    path = Path(oh.__file__).resolve().parents[2] / "scripts" / "check_no_mt5_report.py"
    spec = importlib.util.spec_from_file_location("check_no_mt5_report", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_commit_hook_rejects_mt5_reports_and_account_lines(tmp_path, html_file, xlsx_file):
    hook = _hook()
    acct = tmp_path / "notes.md"
    acct.write_text("Account: " + "12345678 (GBP, X, demo)\n")
    clean = tmp_path / "clean.py"
    clean.write_text("print('hello')\n")
    bad = hook.offending([str(html_file), str(xlsx_file), str(acct), str(clean)])
    assert bad == [str(html_file), str(xlsx_file), str(acct)]
    assert hook.main([str(clean)]) == 0 and hook.main([str(acct)]) == 1


def test_the_commit_hook_passes_this_repositorys_own_sources():
    hook = _hook()
    root = Path(oh.__file__).resolve().parents[2]
    files = [str(p) for p in (root / "goldbot").rglob("*.py")] + [str(p) for p in (root / "tests").rglob("*.py")]
    files += [str(root / "docs" / "OWNER_GUIDE.md"), str(root / "HANDOFF.md")]
    assert hook.offending(files) == []


def test_the_commit_hook_passes_every_tracked_file_in_the_repository():
    import subprocess
    hook = _hook()
    root = Path(oh.__file__).resolve().parents[2]
    done = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    files = [str(root / f) for f in done.stdout.decode().split("\0") if f and (root / f).is_file()]
    assert len(files) > 100
    assert hook.offending(files) == []


STAMPS = [f"2026.0{m}.1{d} 1{d}:0{m}:00" for m in range(1, 4) for d in range(3)]   # 9 MT5 server stamps
RU_HEADER = ["Время", "Позиция", "Символ", "Тип", "Объем", "Цена", "S / L", "T / P", "Время", "Цена", "Комиссия",
             "Своп", "Прибыль"]


def _translated_rows(section: str, header: list[str], width: int = 13) -> list[list[str]]:
    """A section title, a header row and six position rows (two server stamps each), cut to `width` columns."""
    rows: list[list[str]] = [[section], header]
    for i, s in enumerate(STAMPS[:6]):
        rows.append([s, str(TICKET0 + i), "XAUUSD", "buy", "0.01", "2 650.00", "", "", STAMPS[i + 1], "2 651.00",
                     "0.00", "0.00", "0.80"][:width])
    return rows


def _plain_html(rows: list[list[str]]) -> bytes:
    body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<html><body><table>{body}</table></body></html>".encode("utf-16")


@pytest.mark.parametrize("section", ["Позиции", "Сделки", "Positionen", "Posiciones", "Transacciones"])
def test_the_commit_hook_rejects_a_translated_html_report(tmp_path, section):
    hook = _hook()
    p = tmp_path / "history.html"
    p.write_bytes(_plain_html(_translated_rows(section, RU_HEADER, width=6)))     # narrow rows: the title decides
    assert hook.offending([str(p)]) == [str(p)]


def test_the_commit_hook_rejects_a_wide_dated_table_whatever_its_language(tmp_path):
    hook = _hook()
    p = tmp_path / "history.htm"
    p.write_bytes(_plain_html(_translated_rows("Ιστορικό", ["x"] * 13)))     # unknown title, MT5-shaped rows
    assert hook.offending([str(p)]) == [str(p)]


def test_the_commit_hook_rejects_a_translated_xlsx_report(tmp_path):
    hook = _hook()
    p = tmp_path / "history.xlsx"
    report_xlsx(_translated_rows("Позиции", RU_HEADER), p)
    assert hook.offending([str(p)]) == [str(p)]


@pytest.mark.parametrize("label", ["Счет", "Счёт", "Konto", "Cuenta"])
def test_the_commit_hook_rejects_a_translated_account_label_with_a_number(tmp_path, label):
    hook = _hook()
    p = tmp_path / "notes.txt"
    p.write_text(f"{label}: " + "12345678 (EUR, X, demo)\n", encoding="utf-8")
    assert hook.offending([str(p)]) == [str(p)]


def test_the_commit_hook_ignores_a_table_with_too_few_stamps_or_no_report_shape(tmp_path):
    hook = _hook()
    few = tmp_path / "few.html"
    few.write_bytes(_plain_html(_translated_rows("Позиции", RU_HEADER, width=6)[:6]))    # 4 stamped rows
    narrow = tmp_path / "narrow.html"
    narrow.write_bytes(_plain_html([["Changelog"]] + [[s, "release"] for s in STAMPS]))  # stamps, no MT5 shape
    assert hook.offending([str(few), str(narrow)]) == []


# ---------------------------------------------------------------- follow-up review (t bar, DST sessions, matching)

def test_a_slices_bonferroni_bar_is_students_t_on_its_own_degrees_of_freedom():
    import statistics
    k = 30
    normal = statistics.NormalDist().inv_cdf(1 - oh.FAMILY_ALPHA / (2 * k))
    assert oh.slice_bar(12, k) > normal + 0.5                 # 11 degrees of freedom: a much higher bar
    assert oh.slice_bar(10, k) > oh.slice_bar(40, k) > oh.slice_bar(400, k) > normal
    assert oh.slice_bar(10**7, k) == pytest.approx(normal, abs=1e-3)


def test_a_small_slice_between_the_normal_and_the_t_bar_is_reported_below_it():
    import statistics
    k = 30
    normal = statistics.NormalDist().inv_cdf(1 - oh.FAMILY_ALPHA / (2 * k))
    t_mid = (normal + oh.slice_bar(12, k)) / 2
    g = oh.GroupStat(key="london", n=12, net=1.0, mean=0.1, win_rate=0.6, t_stat=t_mid, n_r=12, mean_r=0.3)
    text = oh._slice(g, 40, k)
    assert "below" in text and "clears" not in text and f"{oh.slice_bar(12, k):.2f}" in text
    big = g.model_copy(update={"n": 5000, "t_stat": oh.slice_bar(5000, k) + 0.01})
    assert "clears" in oh._slice(big, 6000, k)


def _position(seq: int, open_utc: str, close_utc: str) -> oh.Position:
    return oh.Position(seq=seq, symbol="XAUUSD", side="buy", lots=0.01, open_utc=pd.Timestamp(open_utc, tz="UTC"),
                       close_utc=pd.Timestamp(close_utc, tz="UTC"), entry=2650.0, exit=2651.0, sl=2645.0,
                       profit=0.8)


def test_by_session_labels_follow_each_dates_dst_not_a_fixed_utc_table():
    # 07:30 UTC is after the London open in July (08:00 BST = 07:00 UTC) but before it in December (08:00 GMT);
    # 12:40 UTC is after the New York open in July (08:30 EDT = 12:30 UTC) but before it in December (13:30 UTC)
    rep = oh.Report(currency="USD", source_format="test", positions=[
        _position(1, "2026-07-01 07:30", "2026-07-01 07:50"), _position(2, "2026-12-01 07:30", "2026-12-01 07:50"),
        _position(3, "2026-07-01 12:40", "2026-07-01 12:50"), _position(4, "2026-12-01 12:40", "2026-12-01 12:50")])
    r = oh.analyse(rep, start_balance=50.0, n_paths=50)
    assert {t.seq: t.session for t in r.trades} == {1: "london", 2: "asia", 3: "newyork", 4: "london"}
    assert "By session (local exchange clocks, DST per date)" in oh.render_markdown(r)


OPEN_A, OPEN_B = "2026.10.01 10:05:12", "2026.10.01 11:00:00"


def _two_buys(position_opens: tuple[str, str], order_opens: tuple[str, str]) -> list[list[str]]:
    """Two XAUUSD buys (final stops 2655 / 2656, above the entry) and two filled opening orders with stops 2640 /
    2645 whose tickets do not match the positions', so only the (symbol, side, open time) fallback can pair them."""
    rows: list[list[str]] = [["Positions"],
                             ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P", "Time",
                              "Price", "Commission", "Swap", "Profit"]]
    for i, (ot, final_sl) in enumerate(zip(position_opens, ("2655.00", "2656.00"))):
        rows.append([ot, str(TICKET0 + i), "XAUUSD", "buy", "0.01", "2 650.00", final_sl, "", "2026.10.01 12:00:00",
                     "2 660.00", "0.00", "0.00", _num(_profit("buy", 0.01, 2650.0, 2660.0))])
    rows += [[], ["Orders"], ["Open Time", "Order", "Symbol", "Type", "Volume", "Price", "S / L", "T / P", "Time",
                              "State", "Comment"]]
    for i, (ot, sl) in enumerate(zip(order_opens, ("2 640.00", "2 645.00"))):
        rows.append([ot, str(i + 1), "XAUUSD", "buy", "0.01 / 0.01", "market", sl, "", ot, "filled", ""])
    return rows


def _report_with(position_opens: tuple[str, str], order_opens: tuple[str, str], tmp_path: Path) -> oh.Report:
    p = tmp_path / "orders.html"
    p.write_bytes(report_html(_two_buys(position_opens, order_opens)))
    return oh.load_report(p)


def _initial(rep: oh.Report) -> list[float | None]:
    return [x.initial_sl for x in sorted(rep.positions, key=lambda x: x.sl or 0.0)]


def test_an_open_time_shared_by_two_orders_falls_back_to_the_final_stop(tmp_path):
    rep = _report_with((OPEN_A, OPEN_B), (OPEN_A, OPEN_A), tmp_path)
    assert _initial(rep) == [None, None]
    r = oh.analyse(rep, start_balance=50.0, n_paths=50)
    assert {t.risk_source for t in r.trades} == {"sl_moved"}       # both final stops sit above the entry


def test_an_open_time_shared_by_two_positions_is_not_paired_with_one_order(tmp_path):
    assert _initial(_report_with((OPEN_A, OPEN_A), (OPEN_A, OPEN_B), tmp_path)) == [None, None]


def test_a_unique_open_time_still_pairs_a_position_with_its_order(tmp_path):
    assert _initial(_report_with((OPEN_A, OPEN_B), (OPEN_A, OPEN_B), tmp_path)) == [2640.0, 2645.0]


def test_an_order_without_a_stop_still_makes_its_open_time_ambiguous():
    cols = {c: i for i, c in enumerate(("time", "order", "symbol", "type", "sl", "state"))}
    t = "2026.10.01 10:05:12"
    _, by_open = oh._initial_stops(cols, [[t, "1", "XAUUSD", "buy", "2640", "filled"],
                                          [t, "2", "XAUUSD", "buy", "", "filled"]])
    assert by_open == {}
