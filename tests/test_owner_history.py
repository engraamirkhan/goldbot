"""Owner's MT5 trade history analysis (goldbot/research/owner_history.py).

Synthetic fixtures only: the real report holds personal account data and never enters this public repo. The fixtures
copy the MT5 "Trade History Report" layout (header block, Positions / Orders / Deals / Results sections, server-time
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
TRADES = [
    ("2026.10.01 10:05:12", "buy", 0.01, 2650.00, 2645.00, 2660.00, "2026.10.01 10:35:12", 2660.00, -0.20, 0.0),
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


def report_rows() -> list[list[str]]:
    """The logical table of a synthetic MT5 report (one list per table row)."""
    rows: list[list[str]] = [
        ["Trade History Report"],
        ["Name:", NAME], ["Account:", f"{ACCOUNT} (GBP, Demo-Server, demo, Hedge)"], ["Company:", COMPANY],
        ["Date:", "2026.10.09 18:00"], [],
        ["Positions"],
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P", "Time", "Price", "Commission",
         "Swap", "Profit"],
    ]
    for i, (ot, side, lots, entry, sl, tp, ct, ex, com, swp) in enumerate(TRADES):
        rows.append([ot, str(5000 + i), "XAUUSD", side, f"{lots:.2f}", _num(entry), _num(sl), _num(tp), ct,
                     _num(ex), _num(com), _num(swp), _num(_profit(side, lots, entry, ex))])
    rows.append(["2026.10.03 11:00:00", "6000", "EURUSD", "buy", "0.10", "1.08000", "", "", "2026.10.03 12:00:00",
                 "1.08100", "-0.50", "0.00", "8.00"])
    rows += [[], ["Orders"],
             ["Open Time", "Order", "Symbol", "Type", "Volume", "Price", "S / L", "T / P", "Time", "State", "Comment"],
             ["2026.10.01 10:05:12", "7000", "XAUUSD", "buy", "0.01 / 0.01", "market", "2 645.00", "2 660.00",
              "2026.10.01 10:05:12", "filled", ""],
             [], ["Deals"],
             ["Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price", "Order", "Commission", "Fee", "Swap",
              "Profit", "Balance", "Comment"],
             ["2026.09.30 09:00:00", "1", "", "balance", "", "", "", "", "0.00", "0.00", "0.00", "50.00", "50.00",
              "Deposit"],
             [], ["Results"],
             ["Total Net Profit:", "14.30", "Gross Profit:", "17.60", "Gross Loss:", "-3.30"],
             ["Balance:", "64.30", "Equity:", "64.30"]]
    return rows


SECTION_TITLES = {"Positions", "Orders", "Deals", "Results", "Trade History Report"}


def report_html(rows: list[list[str]]) -> bytes:
    """MT5-like markup: UTF-16 LE with BOM, label cells as <th>, a hidden colspan cell in every position row and a
    colspan=2 Profit column (header and values)."""
    out = ["<html><head><title>Trade History Report</title></head><body><div align=center>",
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
    assert first.sl == 2645.0 and first.tp == 2660.0 and first.exit == 2660.0
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
    assert t1.risk_source == "sl" and t1.risk == pytest.approx(4.0)        # 5 USD x 0.01 lot x 100 oz x 0.8
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


def test_bootstrap_ruin_is_deterministic_and_reflects_the_edge():
    rng = np.random.default_rng(1)
    losing = rng.normal(-0.05, 0.1, 200)
    winning = rng.normal(0.02, 0.01, 200)
    a = oh.bootstrap_ruin(losing, horizon=200, n_paths=400, seed=3)
    assert a == oh.bootstrap_ruin(losing, horizon=200, n_paths=400, seed=3)
    assert a.p_ruin > 0.9
    assert oh.bootstrap_ruin(winning, horizon=200, n_paths=400, seed=3).p_ruin == 0.0
    # fixed stakes: -10% of the start every trade is ruin within 9 trades on every path
    fixed = oh.bootstrap_ruin(np.full(20, -0.1), horizon=10, n_paths=50, compounding=False)
    assert fixed.p_ruin == 1.0 and fixed.median_end_multiple == 0.0


def test_ruin_is_reported_for_both_readings_of_the_owners_sizing(result):
    methods = [r.method for r in result.summary.ruin]
    assert len(methods) == 2 and "compounding" in methods[0] and "fixed stakes" in methods[1]
    assert all(0.0 <= r.p_ruin <= r.p_halved <= 1.0 for r in result.summary.ruin)


def test_hypothesis_seeds_are_labelled_in_sample(result):
    md = oh.render_markdown(result)
    assert "## Hypothesis seeds" in md and "in-sample" in md.lower() and "not evidence" in md.lower()
    assert all(s.status.startswith("seed") for s in result.seeds) and len(result.seeds) >= 4
    assert "| ID | Hypothesis | Status |" in md


# ---------------------------------------------------------------- privacy and outputs

def test_outputs_never_carry_account_identifiers(result, tmp_path):
    out = tmp_path / "private"
    paths = oh.write_outputs(result, out)
    for p in paths:
        text = p.read_text(encoding="utf-8")
        for secret in (NAME, ACCOUNT, COMPANY, "Demo-Server", "5000", "7000"):
            assert secret not in text, (p.name, secret)
    summary = json.loads(next(p for p in paths if p.suffix == ".json").read_text())
    assert summary["summary"]["n_trades"] == 6


def test_outputs_refuse_a_folder_inside_the_repository(result):
    repo = Path(oh.__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="inside the repository"):
        oh.write_outputs(result, repo / "trade-history")


def test_cli_reads_the_newest_report_in_a_folder_and_writes_next_to_it(html_file, capsys):
    rc = oh.main([str(html_file.parent), "--targets", "3,5", "--no-news"])
    assert rc == 0
    names = sorted(p.name for p in html_file.parent.iterdir())
    assert any(n.endswith(".md") and n.startswith("owner_history") for n in names)
    out = capsys.readouterr().out
    assert "trades" in out and ACCOUNT not in out and NAME not in out
