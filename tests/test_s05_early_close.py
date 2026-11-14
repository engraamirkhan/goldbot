"""BACKLOG 258: S05 exits at the exchange's early close. The spec exits at the T-1 cash close "with early closes
honoured"; since BACKLOG 237 the exchange calendar carries early closes (`early_closes`, point in time). On a T-1 that
is a published early close (known at the decision bar's close) the event carries `exit_at` = the early close instant,
and the simulation exits at the close of the last finer bar of the instrument that ends at or before it. A 13:00 ET
half day (24 Dec 2015, Christmas Friday) is the seeded case; an early close published after the decision is ignored."""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns
from goldbot.research.catalogue import core as K
from goldbot.research.catalogue import detectors_catalogue as DC
from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue.data import DAY, HOUR, Bars
from goldbot.research.catalogue.sim import TRADE_COLS, simulate

HALF_DAY = "2015-12-24 18:00"            # 13:00 ET (EST, UTC-5): NYSE closes early before Christmas


def _ns(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").value)


def _day(s: str) -> int:
    return _ns(s) // DAY


def _hourly(start: str, end: str) -> Bars:
    """1h US500 bars, a slow rise of 0.1 per hour (no stop is ever near)."""
    t = epoch_ns(pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left"))
    c = 100 + 0.1 * np.arange(1, len(t) + 1)
    o = c - 0.1
    z = np.zeros(len(t))
    return Bars("US500", "1h", t, t + HOUR, o, c + 0.05, o - 0.05, c, z, np.zeros(len(t), bool),
                np.zeros(len(t), np.int64), z)


def _daily_from(h: Bars) -> Bars:
    """Weekday daily bars built from the hourly ones (UTC days)."""
    df = pd.DataFrame({"t": h.t, "o": h.o, "h": h.h, "l": h.l, "c": h.c})
    df["d"] = df["t"] // DAY
    g = df.groupby("d")
    d = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last()}).reset_index()
    d = d[((d["d"] + 3) % 7) < 5].reset_index(drop=True)                # weekdays
    t = d["d"].to_numpy(np.int64) * DAY
    z = np.zeros(len(d))
    b = Bars("US500", "1d", t, t + DAY, d["o"].to_numpy(), d["h"].to_numpy(), d["l"].to_numpy(), d["c"].to_numpy(), z,
             np.zeros(len(d), bool), np.zeros(len(d), np.int64), z)
    b.meta.update(dec_from=int(t[0]), end=int(t[-1] + DAY))
    return b


def _ctx(early_published: str) -> K.Ctx:
    return K.Ctx(window="explore",
                 events={"holiday_US": np.array([_day("2015-12-25")]), "early_close_US": np.array([_ns(HALF_DAY)])},
                 avail={"holiday_US": np.array([_ns("2014-12-01")]), "early_close_US": np.array([_ns(early_published)])})


def _bars() -> tuple[Bars, Bars]:
    h = _hourly("2015-03-02", "2015-12-31")              # >= 200 daily bars for ATR/SMA warm-up
    return h, _daily_from(h)


def _t2(b: Bars) -> int:
    return int(np.flatnonzero((b.t // DAY) == _day("2015-12-23"))[0])


def test_s05_carries_the_published_early_close_of_t_minus_1() -> None:
    _, b = _bars()
    ev = DC.s05_preholiday(b, {}, _ctx("2014-12-01"))
    k = int(np.flatnonzero(ev.i == _t2(b))[0])
    assert ev.exit_at is not None and int(ev.exit_at[k]) == _ns(HALF_DAY)


def test_an_early_close_published_after_the_decision_is_not_read() -> None:
    """Seeded future release: known at the T-2 close (2015-12-24 00:00 UTC) it is read; one ns later it is not."""
    _, b = _bars()
    at = DC.s05_preholiday(b, {}, _ctx("2015-12-24 00:00"))
    assert at.exit_at is not None and int(at.exit_at[int(np.flatnonzero(at.i == _t2(b))[0])]) == _ns(HALF_DAY)
    after = DC.s05_preholiday(b, {}, _ctx("2015-12-24 00:00:00.000000001"))
    k = int(np.flatnonzero(after.i == _t2(b))[0])
    assert after.exit_at is None or int(after.exit_at[k]) == 0


def test_the_trade_exits_at_the_early_close_instant_on_the_finer_bars() -> None:
    h, b = _bars()
    ev = DC.s05_preholiday(b, {}, _ctx("2014-12-01"))
    tr = pd.DataFrame(simulate(b, ev, control="random_time", seed_key="S05", exit_bars=h), columns=TRADE_COLS)
    row = tr[tr["i"] == _t2(b)].iloc[0]
    assert int(row["t_exit"]) == _ns(HALF_DAY)
    k = int(np.flatnonzero(h.ta == _ns(HALF_DAY))[0])
    assert row["kind"] == 0 and np.isclose(row["gross"] * row["risk"], h.c[k] - row["fill"])
    # the same event without the early close exits at the daily close
    plain = DC.s05_preholiday(b, {}, _ctx("2015-12-24 12:00"))
    tp = pd.DataFrame(simulate(b, plain, control="random_time", seed_key="S05", exit_bars=h), columns=TRADE_COLS)
    assert int(tp[tp["i"] == _t2(b)].iloc[0]["t_exit"]) == _ns("2015-12-25 00:00")


def test_without_finer_bars_the_early_close_is_counted_unpriced_and_the_daily_close_kept() -> None:
    _, b = _bars()
    ev = DC.s05_preholiday(b, {}, _ctx("2014-12-01"))
    counts: dict[str, int] = {}
    tr = pd.DataFrame(simulate(b, ev, control="random_time", seed_key="S05", counts=counts), columns=TRADE_COLS)
    assert int(tr[tr["i"] == _t2(b)].iloc[0]["t_exit"]) == _ns("2015-12-25 00:00")
    assert counts.get("exit_at_unpriced", 0) >= 1


def test_the_dispatcher_adds_early_closes_point_in_time() -> None:
    ctx = K.Ctx(window="explore")
    D.add_holidays(ctx, end_ns=_ns("2016-01-01"), data_from_ns=_ns("2010-01-01"))
    close, av = ctx.events["early_close_US"], ctx.avail["early_close_US"]
    assert len(close) == len(av) > 0 and (av < _ns("2016-01-01")).all()
    assert _ns(HALF_DAY) in set(close.tolist())
