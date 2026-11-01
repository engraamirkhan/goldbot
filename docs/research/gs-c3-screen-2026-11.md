# Growth sprint C3 screen: intraday momentum, New York and London (2010-2017)

Run 2026-11-01 by the opportunity-researcher for the growth sprint (`growth-sprint-2026-11-01.md` §2, candidate C3,
PR #251). Two variants, one look each, on bars 2010-01-01..2017-12-31 only (local Dukascopy 1m, years 2018+ never
read; holdout sealed). Screen, not a registry trial: **K +2** of the sprint's 7 (3,002 -> 3,009 when all four screens
report). Section 1 was committed before any output was computed.

**Prior, stated up front.** C3 re-tests the shape of retired family **R-05** (intraday_momentum: NY -0.006 R gross,
t -0.50; London -0.028 R, t -1.48; retirement reconfirmed 2026-10-18) and sits next to R-17 (trend-day continuation,
gross -0.003 R). The new element is the Gao, Han, Li and Zhou (2018, *Journal of Financial Economics* 129(2),
US equity ETFs 1993-2013, gross of costs; single-paper for gold, practitioner only) construction itself: a short
opening window predicting a short closing window, not R-05's mid-session decision with a 3 ATR target. The research
director placed it in the sprint; my expectation is FAIL.

## 1. Frozen rule (committed before any data was read)

Sessions are `goldbot.data.calendar.LOCAL_SESSIONS` (DST from the zone): London 08:00-16:30 Europe/London, New York
08:30-16:00 America/New_York. Prices are the 1m mid (bid+ask)/2; costs below carry the spread.

| Item | V1 New York | V2 London |
|---|---|---|
| Formation return | mid close of the 08:59 bar minus mid open of the 08:30 bar (first 30 min) | close of 08:59 minus open of 08:00 (first 60 min) |
| Entry | open of the 15:00 NY bar (the last 60 min) | open of the 09:00 London bar (formation end) |
| Side | sign of the formation return; zero = no trade | same |
| Time exit | close of the last bar before 16:00 NY | close of the last bar before 16:30 London ("predicts London close") |
| Skip the day if | the opening, formation-end or entry minute is missing, or ATR is undefined | same |

- **Stop and R.** Protective stop 1.5 x ATR(14) of 15m mid bars that closed at or before the entry (point in time;
  same multiple as C2). 1 R = the stop distance. Stop checked on 1m mid high/low; a 1m open through the stop fills at
  the open (so a loss can exceed 1 R). No target.
- **Cost (the bar's 1.5 x).** `config/cost_of_record.yaml` `cor-2026-10-31.2`, icm, session cell: 1.5 x
  round_trip_usd (NY 0.166, London 0.164 $/oz) on every trade, plus 1.5 x overshoot_usd (NY 0.13, London 0.085) on
  stop exits. Swap 0 (flat within the session). Net R = gross R - cost / stop distance.
- **Matched random-entry twin.** Same day, entry minute, exit minute and stop distance; direction a fair coin. It is
  evaluated in expectation (the mean of the long and the short outcome, net), which equals the limit of infinitely
  many seeded draws. Excess_d = strategy net R - twin net R.
- **Statistics.** One trade a day, so the day bootstrap is an iid resample of days: 20,000 resamples, seed 20261101.
  90% lower bound = 5th percentile of the resampled mean. Excess one-sided p = share of resampled means <= 0.
- **Pass (all three, per variant):** (a) net mean R 90% LB > 0; (b) excess LB > 0 Holm-adjusted over the 7 sprint
  variants: p <= 0.05/7 = 0.0071 passes under any ordering; 0.0071 < p <= 0.05 is reported as "depends on the other
  screens"; p > 0.05 fails; (c) net mean R > 0 in >= 5 of the 8 years.
- **Reported, not in the rule:** gross mean and t, long/short split, stop share, cost in R, by-year table; virtual
  weekly ledger at f = 5/10/20% of equity per 1 R (equity x prod(1 + f r) over the week's trades in time order,
  per variant and for both together): P(week end >= 2x), P(week end <= 0.5x), and P(touch 0.5x within the week).
- **Reading rule.** PASS -> one confirm on 2018-01..2025-09, the director's call. FAIL -> the R-05 shape is closed a
  third time at 1m/15m: no further session-momentum timing, window or exit variant without new evidence (a new
  mechanism, not a new window). Losers are reported with winners.

## 2. Result (one look, run after section 1 was committed at `2b8acfbd`)

Bars read: 2,819,388 one-minute bars, 2010-01-01 00:02 .. 2017-12-29 21:56 UTC (assert: no 2018+ bar). Driver
code hash `0651cd5d4f6c` (appendix). Run time 3 s.

| Leg | V1 New York | V2 London | Bar |
|---|---|---|---|
| Trades (days) | 2,018 | 2,065 | |
| Gross mean R (t) | +0.007 (t +0.47) | **+0.170 (t +2.78)** | reported |
| Mean cost in R (1.5 x) | 0.102 | 0.178 | |
| **Net mean R [90% CI]** | **-0.095 [-0.120, -0.071]** | **-0.008 [-0.109, +0.094]** | LB > 0 |
| Twin net mean R | -0.099 | -0.111 | |
| **Excess over twin (90% LB), one-sided p** | +0.003 (LB -0.020), p 0.41 | +0.103 (LB +0.031), **p 0.0086** | p <= 0.0071 |
| **Net-positive years** | **0 of 8** | **3 of 8** | >= 5 |
| **Verdict** | **FAIL (all three legs)** | **FAIL (legs a and c; b depends on the other screens but is moot)** | |

Net R by year 2010..2017. V1: -0.10, -0.12, -0.03, -0.09, -0.12, -0.09, -0.10, -0.12. V2: -0.21, +0.01, -0.21,
+0.38, -0.06, +0.30, -0.07, -0.20. Gross V2 by year: -0.05, +0.14, -0.05, +0.52, +0.14, +0.50, +0.10, +0.05.

Reported, not in the rule (the shapes behind the verdict):

| | V1 New York | V2 London |
|---|---|---|
| Median stop ($/oz) | 2.83 | 1.99 |
| Stop-exit share | 12% | **77%** |
| Long / short net R | -0.091 / -0.099 | **-0.144 / +0.119** |
| Net R, median / max | -0.10 / +4.1 | -1.17 / **+29.3** |
| Net R without the 10 best trades | -0.110 | -0.093 |
| Net R, 1% trimmed each tail | -0.108 | -0.146 |
| Share of positive R from the top 10% of trades | 54% | 77% |

**Virtual weekly ledger** (end-of-week equity, prod(1 + f r) over the week's trades; 2010-2017 weeks):

| f per 1 R | Book | Weeks | P(week >= 2x) | P(week <= 0.5x) | P(touch 0.5x) | Median week |
|---|---|---|---|---|---|---|
| 5% | V1 NY | 418 | 0.0% | 0.0% | 0.0% | 0.97x |
| 5% | V2 London | 417 | 2.2% | 0.0% | 0.0% | 0.91x |
| 5% | both | 418 | 2.4% | 0.0% | 0.0% | 0.90x |
| 10% | V1 NY | 418 | 0.0% | 0.0% | 0.0% | 0.93x |
| 10% | V2 London | 417 | 5.0% | 1.0% | 1.0% | 0.81x |
| 10% | both | 418 | 4.6% | 15.1% | 17.7% | 0.77x |
| 20% | V1 NY | 418 | 0.0% | 2.9% | 2.9% | 0.86x |
| 20% | V2 London | 417 | 9.8% | 45.1% | 56.4% | 0.57x |
| 20% | both | 418 | 9.3% | 50.0% | 62.7% | 0.50x |

## 3. Reading (researcher, within the frozen rule)

1. **V1 (Gao's construction on the NY clock) has no signal at all.** Gross +0.007 R and an excess over a coin-flip
   twin of +0.003 R (p 0.41): the first 30 min of the NY session (it contains the 08:30 releases) says nothing about
   15:00-16:00 NY, which in spot gold is after the COMEX pit close and thin. This matches R-05 NY (-0.006 R) and is
   the fourth null read of the mechanism at intraday horizons (R-02, R-05, R-17, now C3 V1).
2. **V2's gross +0.17 R is a tail-and-drift artefact, not a tradable edge.** 77% of trades stop out (a 1.5 x ATR15
   stop held for 7.5 h); the median trade loses 1.17 R; the mean comes from a handful of +15..+29 R days, and the 1%
   trimmed mean is -0.15 R. The gross is concentrated in the two gold bear years (2013 +0.52, 2015 +0.50) and on the
   short side (+0.12 net vs -0.14 long): the London-morning sign co-moved with the secular down-trend. The twin
   removes direction drift in expectation, which is why the excess (+0.10, p 0.0086) is the most interesting number
   here, but it misses the 0.05/7 bar, and legs (a) and (c) fail independently: net is not above zero and only 3 of
   8 years are positive. **FAIL is final for this window; no tweak of the stop, window or side is allowed under the
   frozen rule.**
3. **The growth question.** No C3 book doubled in more than about 1 week in 10, and every setting that reaches 9-10%
   doubling weeks (f = 20%) halves the account in 45-50% of weeks; the median week shrinks the account at every f.
   This is the sprint doc's section 1 theorem observed on data: negative net edge plus a convex payoff gives lottery
   weeks, not growth.
4. **Consequences.** No confirm on 2018-2025-09. The R-05 shape (a session-open move predicting a later session
   window, NY and London) is closed a third time; re-test only via the R-05 reinstatement path with a new mechanism.
   A short-side London-morning drift in down-trend regimes would be a post-hoc subgroup of this screen, so it is
   *not* licensed here and I am not proposing it. K +2 recorded in `hypotheses.md` section C.

## Known-failed in this area (do not re-test without new evidence)

R-02 session-open breakout; R-05 intraday momentum (NY, London; now also the Gao 30/60-min form, GS-C3 V1/V2);
R-17 trend-day continuation on HAR-high days; H-16 tier-1 events and compression as range-forecast inputs.

## Appendix: driver (`~/tmp/gs_c3/gs_c3_screen.py`, sha256 prefix `0651cd5d4f6c`)

Run with the repo venv. Outputs (trade CSVs) stay in `~/tmp/gs_c3/`, outside the repo; no market data is committed.
The only post-run edit was one comment line (ATR description); no logic changed.

<details><summary>Code</summary>

```python
"""GS C3 screen (docs/research/gs-c3-screen-2026-11.md section 1). 2010-2017 only."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/aamirkhan/myproject/goldbot/.claude/worktrees/agent-a0d6b8f6495912cd3")
sys.path.insert(0, str(ROOT))
from goldbot.data.release import read_bar_files  # noqa: E402

RAW = Path("/Users/aamirkhan/myproject/goldbot/raw")
END = pd.Timestamp("2018-01-01", tz="UTC")
SEED = 20261101
NB = 20000
COST = {"ny": (0.166, 0.13), "ldn": (0.164, 0.085)}
VAR = {  # tz, open, formation end, entry, close (local HH:MM)
    "ny": ("America/New_York", "08:30", "09:00", "15:00", "16:00"),
    "ldn": ("Europe/London", "08:00", "09:00", "09:00", "16:30"),
}

b = read_bar_files(RAW, 2010, 2017)
ts = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True))
assert ts.max() < END, "a 2018+ bar was read"
m = pd.DataFrame({k: (b[f"bid_{k}"].to_numpy(float) + b[f"ask_{k}"].to_numpy(float)) / 2
                  for k in ("open", "high", "low", "close")}, index=ts).sort_index()
m = m[~m.index.duplicated()]
print("bars", len(m), m.index.min(), m.index.max())

# 15m ATR(14), Wilder smoothing (alpha 1/14), available at the 15m bar close
m15 = m.resample("15min", label="left", closed="left").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
pc = m15["close"].shift()
tr = pd.concat([m15["high"] - m15["low"], (m15["high"] - pc).abs(), (m15["low"] - pc).abs()], axis=1).max(axis=1)
atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
atr.index = atr.index + pd.Timedelta(minutes=15)  # available at the bar's close
atr_avail = atr

O, H, L, C = (m[k].to_numpy() for k in ("open", "high", "low", "close"))
pos = pd.Series(np.arange(len(m)), index=m.index)


def run(v: str) -> pd.DataFrame:
    tz, t_open, t_form, t_entry, t_close = VAR[v]
    rt, ov = COST[v]
    loc = m.index.tz_convert(tz)
    days = pd.Index(loc.normalize().unique())
    rows = []
    for d in days:
        if d.weekday() >= 5:
            continue
        def at(hm: str) -> pd.Timestamp:
            hh, mm = map(int, hm.split(":"))
            return (d + pd.Timedelta(hours=hh, minutes=mm)).tz_convert("UTC")
        t0, tf, te, tc = at(t_open), at(t_form), at(t_entry), at(t_close)
        lf = tf - pd.Timedelta(minutes=1)
        if t0 not in pos.index or lf not in pos.index or te not in pos.index:
            continue
        k0, kf, ke = pos[t0], pos[lf], pos[te]
        kc = pos.index.searchsorted(tc) - 1  # last bar before close
        if kc <= ke:
            continue
        fr = C[kf] - O[k0]
        if fr == 0:
            continue
        a = atr_avail.asof(te)
        if not np.isfinite(a) or a <= 0:
            continue
        stop = 1.5 * a
        e = O[ke]
        out = {}
        for side in (1, -1):
            hi, lo, op = H[ke:kc + 1], L[ke:kc + 1], O[ke:kc + 1]
            sp = e - side * stop
            hit = (lo <= sp) if side > 0 else (hi >= sp)
            if hit.any():
                j = int(np.argmax(hit))
                fill = op[j] if (side > 0 and op[j] < sp) or (side < 0 and op[j] > sp) else sp
                g = side * (fill - e) / stop
                c = 1.5 * (rt + ov) / stop
                st = True
            else:
                g = side * (C[kc] - e) / stop
                c = 1.5 * rt / stop
                st = False
            out[side] = (g, g - c, st, c)
        s = 1 if fr > 0 else -1
        g, n, st, c = out[s]
        twin = 0.5 * (out[1][1] + out[-1][1])
        rows.append(dict(day=d.date(), t_entry=te, side=s, gross=g, net=n, stopped=st, cost_r=c,
                         twin=twin, excess=n - twin, stop_usd=stop))
    return pd.DataFrame(rows)


def boot(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    idx = rng.integers(0, len(x), size=(NB, len(x)))
    return x[idx].mean(axis=1)


def ledger(df: pd.DataFrame, f: float) -> dict:
    df = df.sort_values("t_entry")
    wk = pd.DatetimeIndex(df["t_entry"]).tz_convert("UTC").to_period("W-SUN")
    res = []
    for _, g in df.groupby(wk):
        eq = np.cumprod(1 + f * g["net"].to_numpy())
        eq = np.maximum(eq, 0)
        res.append((eq[-1], eq.min()))
    r = np.array(res)
    return dict(weeks=len(r), p2=(r[:, 0] >= 2).mean(), p05=(r[:, 0] <= 0.5).mean(),
                touch05=(r[:, 1] <= 0.5).mean(), med=np.median(r[:, 0]))


all_df = {}
for v in ("ny", "ldn"):
    df = run(v)
    all_df[v] = df
    df.to_csv(f"/Users/aamirkhan/tmp/gs_c3/{v}_trades.csv", index=False)
    rng = np.random.default_rng(SEED)
    bn = boot(df["net"].to_numpy(), rng)
    rng = np.random.default_rng(SEED + 1)
    be = boot(df["excess"].to_numpy(), rng)
    yr = df.groupby(pd.to_datetime(df["day"]).dt.year)["net"].agg(["mean", "count"])
    gy = df.groupby(pd.to_datetime(df["day"]).dt.year)["gross"].mean()
    print(f"\n== {v} n={len(df)}")
    print(f"gross mean {df.gross.mean():+.4f} t {df.gross.mean()/df.gross.std()*np.sqrt(len(df)):+.2f}")
    print(f"net mean {df.net.mean():+.4f} LB90 {np.percentile(bn,5):+.4f} UB {np.percentile(bn,95):+.4f}")
    print(f"twin mean {df.twin.mean():+.4f}; excess mean {df.excess.mean():+.4f} LB90 {np.percentile(be,5):+.4f} "
          f"p1 {(be<=0).mean():.4f} LB@holm1 {np.percentile(be,100*0.05/7):+.4f}")
    print(f"cost_r mean {df.cost_r.mean():.4f} median stop ${df.stop_usd.median():.2f} stop share {df.stopped.mean():.3f}")
    print(f"long n {int((df.side>0).sum())} net {df[df.side>0].net.mean():+.4f}; short net {df[df.side<0].net.mean():+.4f}")
    print("years net:", {int(k): round(r["mean"], 4) for k, r in yr.iterrows()}, "pos", int((yr["mean"] > 0).sum()))
    print("years gross:", {int(k): round(x, 4) for k, x in gy.items()})
    print("years n:", {int(k): int(r["count"]) for k, r in yr.iterrows()})
    for f in (0.05, 0.10, 0.20):
        print(f"ledger f={f}", {k: (round(x, 4) if isinstance(x, float) else x) for k, x in ledger(df, f).items()})
both = pd.concat([all_df["ny"], all_df["ldn"]])
print("\n== both")
for f in (0.05, 0.10, 0.20):
    print(f"ledger f={f}", {k: (round(x, 4) if isinstance(x, float) else x) for k, x in ledger(both, f).items()})
```

</details>
