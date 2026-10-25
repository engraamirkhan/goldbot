# 1m/5m cost model by hour of day from scheduled probe fills (2026-10-25)

Source: brain `/opt/goldbot/state/aux.db` table `cost_probes`, JSON `body`, read-only, pulled 2026-10-25. 161 rows: 142 filled (retcode 10009), 19 rejected (-1: 9, -2: 10), excluded from cost stats. 137 from `icm-probe`, 5 from `icm-demo` (earlier rows, see caveats). Size 0.01 lot = 1 oz, so $ per oz equals $ per 0.01-lot trade. Date range of fills: from 2026-10-19 (first row), about one week. Read-only analysis; no trial consumed (descriptive, no hypothesis tested).

## Definitions (point in time, all from the fill record)
- spread = `spread_usd` at send. Entry slippage = side * (fill_price - requested_price), adverse positive. Exit slippage (only `cost_probe_hold` exits with `exit_bid/ask`, n=124) = distance of exit_price past the touch we should have hit (bid for longs, ask for shorts). Exit spread = exit_ask - exit_bid.
- Round trip vs mid ($/oz) = entry spread/2 + entry slip + exit spread/2 + exit slip + commission. Commission is the recorded `commission` = $0.06 on every fill; I treat it as the full round trip per 0.01 lot (consistent with a $7/lot raw-account round trip = $0.07; not verified against the broker statement, if it is per side add $0.06).
- GBP = $ x fx_acct_per_usd (mean 0.7469, range 0.7459 to 0.7479, n=137 rows carrying it).

## Pooled result (n=142)
| item | value |
|---|---|
| Entry spread | mean $0.079, p90 $0.12 |
| Entry slippage | mean -$0.055, 95% CI +/-0.054 (i.e. indistinguishable from zero or slightly favourable); max adverse $0.86 |
| Exit slippage vs touch (hold exits, n=124) | mean -$0.008, CI +/-0.012 |
| Exit spread | mean $0.089 |
| Commission | $0.06 |
| Latency | mean 475 ms, p90 557 ms |
| Round trip, central (slip floored at 0) | 0.040 + 0.044 + 0.06 = **$0.144/oz = GBP 0.108 per 0.01 lot** |
| Round trip, conservative (p90 spread both legs, +0.05 slip upper CI) | 0.12 + 0.05 + 0.06 = **$0.23/oz = GBP 0.17** |

## By UTC hour (hour of `sent_utc`; cells with n<20 are ALL cells at hourly level, so read the session pools below, not the hours)
| UTC hour | n | spread mean | spread p90 | entry slip mean | slip 95% CI +/- | exit spread | commission | RT $/oz (central) | RT GBP/0.01 lot |
|---|---|---|---|---|---|---|---|---|---|
| 00 | 8 | 0.087 | 0.120 | -0.044 | 0.423 | 0.114 | 0.06 | 0.177 | 0.132 |
| 01 | 13 | 0.080 | 0.110 | -0.106 | 0.271 | 0.088 | 0.06 | 0.189 | 0.141 |
| 02 | 10 | 0.083 | 0.120 | -0.032 | 0.289 | 0.092 | 0.06 | 0.147 | 0.110 |
| 03 | 8 | 0.081 | 0.120 | -0.129 | 0.270 | 0.092 | 0.06 | 0.147 | 0.110 |
| 04 | 8 | 0.080 | 0.120 | -0.001 | 0.111 | 0.084 | 0.06 | 0.142 | 0.106 |
| 05 | 8 | 0.099 | 0.120 | -0.070 | 0.121 | 0.116 | 0.06 | 0.172 | 0.128 |
| 06 | 6 | 0.075 | 0.110 | -0.102 | 0.234 | 0.080 | 0.06 | 0.138 | 0.103 |
| 07 | 6 | 0.065 | 0.085 | -0.020 | 0.175 | 0.078 | 0.06 | 0.146 | 0.109 |
| 08 | 5 | 0.062 | 0.070 | -0.048 | 0.227 | 0.075 | 0.06 | 0.129 | 0.096 |
| 09 | 4 | 0.065 | 0.092 | +0.017 | 0.277 | 0.072 | 0.06 | 0.164 | 0.122 |
| 10 | 5 | 0.092 | 0.110 | -0.066 | 0.166 | 0.108 | 0.06 | 0.160 | 0.119 |
| 11 | 4 | 0.075 | 0.100 | -0.005 | 0.262 | 0.085 | 0.06 | 0.185 | 0.138 |
| 12 | 4 | 0.070 | 0.088 | -0.332 | 0.475 | 0.078 | 0.06 | 0.144 | 0.107 |
| 13 | 4 | 0.065 | 0.088 | -0.047 | 0.530 | 0.083 | 0.06 | 0.134 | 0.100 |
| 14 | 4 | 0.077 | 0.097 | -0.017 | 0.167 | 0.073 | 0.06 | 0.149 | 0.111 |
| 15 | 4 | 0.077 | 0.094 | -0.055 | 0.369 | 0.085 | 0.06 | 0.141 | 0.105 |
| 16 | 4 | 0.087 | 0.104 | -0.030 | 0.203 | 0.092 | 0.06 | 0.150 | 0.112 |
| 17 | 4 | 0.070 | 0.098 | -0.152 | 0.314 | 0.070 | 0.06 | 0.130 | 0.097 |
| 18 | 8 | 0.065 | 0.096 | +0.043 | 0.201 | 0.093 | 0.06 | 0.191 | 0.143 |
| 19 | 8 | 0.077 | 0.110 | -0.040 | 0.143 | 0.082 | 0.06 | 0.140 | 0.105 |
| 20 | 6 | 0.080 | 0.110 | -0.043 | 0.114 | 0.083 | 0.06 | 0.142 | 0.106 |
| 22 | 6 | 0.095 | 0.110 | +0.075 | 0.182 | 0.103 | 0.06 | 0.237 | 0.177 |
| 23 | 5 | 0.082 | 0.132 | -0.116 | 0.126 | 0.082 | 0.06 | 0.142 | 0.106 |

UTC 21 has no filled probe (no data, treat as unknown; it is the rollover hour, assume spread blow-out and do not trade).

## Session pools (n>=11; the only cells I would rely on)

| session (UTC) | n | spread mean | spread p90 | entry slip mean | slip 95% CI +/- | exit spread | commission | RT $/oz (central) | RT GBP/0.01 lot |
|---|---|---|---|---|---|---|---|---|---|
| Asia 00-06 | 61 | 0.084 | 0.120 | -0.070 | 0.100 | 0.094 | 0.06 | 0.149 | 0.111 |
| London 07-11 | 24 | 0.072 | 0.110 | -0.027 | 0.088 | 0.083 | 0.06 | 0.140 | 0.104 |
| NY 12-16 | 20 | 0.075 | 0.100 | -0.096 | 0.159 | 0.083 | 0.06 | 0.139 | 0.104 |
| LateNY 17-20 | 26 | 0.073 | 0.110 | -0.033 | 0.091 | 0.084 | 0.06 | 0.138 | 0.103 |
| Rollover 21-23 | 11 | 0.089 | 0.120 | -0.012 | 0.124 | 0.094 | 0.06 | 0.153 | 0.114 |
| ALL | 142 | 0.079 | 0.120 | -0.055 | 0.054 | 0.089 | 0.06 | 0.144 | 0.107 |

Reading: hour and session differences in spread (0.062 to 0.099 hourly means, 0.072 to 0.089 session means) are within noise at these n; the only suggestive pattern is lower spread in London 07-09 (0.062-0.065) and higher in Asia and the 22-23/00-01 rollover tail (0.08-0.099). Slippage shows no hour effect at all (every CI spans zero). I do not claim a time-of-day cost edge from this data.

## Break-even gross R per trade
Break-even gross R = round-trip cost ($/oz) / stop distance ($). Three cost cases: central $0.144; conservative $0.23; stress $0.62 = conservative plus a stop-exit overshoot ($0.70, below) on half of trades. To trade it you want gross expectancy of 3x cost or more (my rule for a scalp), shown in the last two columns for the central case.

| arm | stop $ | central R | conservative R | stress R | gross R needed (3x central) | gross $/oz needed |
|---|---|---|---|---|---|---|
| 1m | 2 | 0.072 | 0.115 | 0.310 | 0.22 | 0.43 |
| 1m | 3 | 0.048 | 0.077 | 0.207 | 0.14 | 0.43 |
| 1m | 4 | 0.036 | 0.058 | 0.155 | 0.11 | 0.43 |
| 5m | 4 | 0.036 | 0.058 | 0.155 | 0.11 | 0.43 |
| 5m | 6 | 0.024 | 0.038 | 0.103 | 0.07 | 0.43 |
| 5m | 8 | 0.018 | 0.029 | 0.077 | 0.05 | 0.43 |

Thin-stop note: at the $2 end the 1m central cost is 0.07 R and the stress 0.31 R; at $4 it is 0.04 R / 0.16 R. The 0.2-0.4 R round trip assumed for scalps in the brief is only reached in the stress case at the tightest stops; the scheduled-fill spread/slippage alone is not the binding cost.

## What binds instead (stop-exit overshoot) and caveats
1. 14 of 142 probes exited by `stop`; the realised adverse distance exceeded the recorded `stop_loss` by up to $2.5 (mean overshoot $0.70 over all 14; 2 rows undershoot). The probe loop may poll rather than hold a resting broker stop, so this may be a probe artefact and not exchange slippage; unverified. If real, a $2-3 1m stop costs 0.3 R or more on every stopped trade, which is the real cost risk for 1m. Needs a resting-SL probe arm to settle (cheap: same probe, native stop order). Flag to the research director.
2. Probes fire at scheduled quiet times (about 4 per hour, same minute marks), not at signals or news. Spread and slippage at breakout/news moments are unobserved; gold spreads typically widen several-fold there. These numbers are a floor, not an expectation for event-driven scalps.
3. Entry slippage mean is slightly favourable (price improvement); the 5 `icm-demo` rows show +$0.21 adverse (older rows, stale requested_price likely), too few to conclude. Do not bank the negative slippage; central case floors it at 0.
4. Market orders only. No limit-order data, so the limit-vs-market saving (up to half the spread, $0.04) is untested.
5. One week, one broker (IC Markets demo raw account). Demo fills are not live fills.
6. Every hourly cell has n<20 (max 13); pooling to session as asked.

## Verdict: cost-viable arms/hours
- Spread plus commission only: both arms are cost-viable in every hour with data. 5m ($4-8 stops) is comfortable at 0.02-0.04 R central; 1m is viable at stops of $3 or more (<=0.05 R central, <=0.08 R conservative).
- 1m at $2 stops is marginal (0.07 R central, rises to 0.3 R if stop overshoot is real).
- Best hours by spread: 07-09 UTC (London open). Avoid 22-01 UTC and anything near 21 (no data).
- Not demonstrated: any cost advantage by hour; behaviour at signal/news times; resting-stop slippage.
