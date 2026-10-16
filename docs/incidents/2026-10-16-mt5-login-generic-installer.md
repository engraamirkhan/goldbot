# 2026-10-16: MT5 login to ICMarketsSC-Demo silently never connects (generic installer, no server list)

Status: closed (prevention items shipped; installer choice recorded in ADR 0020)
Severity: S3 (the first demo deploy was delayed; nothing was running or trading)
Author: sre. Reviewed by: pending (program director).

## Summary
During the one-time MT5 login on the new Oracle MT5 box, File > Login to Trade Account with server
`ICMarketsSC-Demo` did nothing: no Journal line and no TCP connection. The terminal had been installed from MetaQuotes'
generic `mt5setup.exe` ("started for MetaQuotes Ltd."), which carries no IC Markets server list, so the server name
resolved to nothing. Adding the company through File > Open an Account fixed it. A second, separate snag followed:
the MT5 Python API attached to the running terminal only with `initialize(path=..., timeout=60000)`; without
`path=` it failed with IPC timeout -10005.

## Impact
- The demo deploy's login step took part of an evening longer. No engine, bridge or position existed yet; demo only.
- Had it gone unnoticed, the bridge would have refused to start (it checks the terminal is logged in to the
  registered demo login), so `bridge:<account>` would FAIL on Telegram. No silent trading risk.

## Timeline (UTC, 2026-10-16, approximate)
| Time | Event |
| --- | --- |
| evening | MT5 box bootstrapped (`mt5_bootstrap.sh`, generic MetaQuotes installer); terminal opened over VNC |
| evening | File > Login, server ICMarketsSC-Demo: no Journal line, no connection, no error |
| evening | File > Open an Account > "ICMarkets" > Raw Trading Ltd > Connect with an existing trade account > login: connected; `Config/servers.dat` written |
| evening | Hand test of the MT5 Python API: `initialize()` without `path=` -> IPC timeout -10005; with `path=r"C:\MT5\ICMarkets\terminal64.exe", timeout=60000` -> attached |
| ~19:30 | sre lane: RUNBOOK 0.3, ADR 0020, bootstrap hint, adapter passes an explicit timeout, tests |

## Root cause
1. **The installer carries no broker servers.** MetaQuotes' generic `mt5setup.exe` ships only MetaQuotes' own
   servers. The terminal does not report an unknown server name on File > Login; it simply never connects. Broker
   server lists arrive either with a broker-branded installer or through File > Open an Account (company search).
2. **The RUNBOOK assumed a branded terminal.** RUNBOOK 0.3 told the owner to use File > Login with the server name,
   which only works when the server list exists. That step had never been run on a box built by the script.
3. **Python attach (separate):** the bridge code already passes `path=terminal_path` (`goldbot/execution/mt5_adapter.py`,
   `MT5Broker.__init__`), so the bridge was not affected; the -10005 came from a hand test without `path=`. The
   timeout relied on the package default; it is now explicit.

## Detection
By the person logging in (nothing happened). Automated: the bridge's start-up check (logged in to the registered
login, on ICMarketsSC-Demo, demo mode) refuses to start, so `bridge:<account>` FAILs and alerts. Nothing earlier
could see it: the bootstrap cannot log in (no credentials on the box by design).

## Recovery
RUNBOOK 0.3 step 4: File > Open an Account > search `ICMarkets` > Raw Trading Ltd > Connect with an existing trade
account > login, password, server ICMarketsSC-Demo, Save password.

## Prevention
| Item | Type | Where | Status |
| --- | --- | --- | --- |
| RUNBOOK 0.3 gives the Open an Account flow, the symptom, and the `initialize(path=..., timeout=60000)` note | runbook + test | `docs/RUNBOOK.md`, `tests/test_bootstrap_sh.py::test_the_installer_is_the_generic_metaquotes_one_and_the_login_flow_is_named` | shipped |
| Decide generic vs IC Markets installer | decision | `docs/decisions/0020-mt5-installer-generic-plus-company-search.md` (generic kept) | shipped |
| The bootstrap ends by naming the Open an Account flow; the installer host is locked by a test | check + test | `goldbot/ops/linux/mt5_bootstrap.sh`, same test | shipped |
| `initialize()` gets `path=` and an explicit `timeout=60000` (`MT5_INIT_TIMEOUT_MS`) | fix + test | `goldbot/execution/mt5_adapter.py`, `tests/test_mt5_initialize.py` | shipped |
| Requirements header no longer shows a bare `wine python -m pip` | fix + test | `scripts/bridge_requirements.py`, `tests/test_bridge_requirements.py` | shipped |

## Lessons
- Went well: the owner-run login step caught it immediately, and the bridge's login check would have caught it anyway.
- Went badly: a RUNBOOK step for a GUI flow had never been walked on a box built by the script (same class as the
  Wine incident the same day: rehearse root-run and GUI steps on a throwaway VM, open item in that postmortem).
- Lucky: nothing; the failure was loud enough because nothing else could proceed.
