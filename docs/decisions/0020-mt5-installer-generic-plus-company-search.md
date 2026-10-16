# 0020: The MT5 box keeps MetaQuotes' generic installer; the IC Markets servers are added at login

Status: accepted (sre, 2026-10-16). An ops engineering choice inside the sre's domain; no owner-only question
(budget, instruments, going live, gate thresholds) is involved.

## Context
`goldbot/ops/linux/mt5_bootstrap.sh` installs the terminal with MetaQuotes' generic `mt5setup.exe`
(`download.mql5.com/cdn/web/metaquotes.software.corp/...`). That installer ships no broker server list. During the first
login on 16 Oct, File > Login to Trade Account with server `ICMarketsSC-Demo` silently never connected: no journal
line, no TCP connection. File > Open an Account > search "ICMarkets" > Raw Trading Ltd > "Connect with an existing
trade account" fetched the IC Markets servers (`Config/servers.dat`) and logged in
(`docs/incidents/2026-10-16-mt5-login-generic-installer.md`).

The alternative is IC Markets' branded installer, which ships their servers.

## Options
1. **IC Markets' branded installer.** The server list is there from the start, so File > Login works first time.
   But: its download URL is published only on IC Markets' site and is not stable or verifiable from here (the
   obvious MetaQuotes CDN path, `cdn/web/raw.trading.ltd/mt5/icmarketssc5setup.exe`, answers 404 today); a wrong or
   moved URL fails the unattended bootstrap; and it brings the broker's name into a script that should also serve a
   second broker's terminal (Vantage) later.
2. **Generic installer + the documented company search (chosen).** One installer, from MetaQuotes' own host, for
   every broker. The server list is a one-time, per-prefix state that the owner's one-time VNC login (RUNBOOK 0.3)
   creates anyway; the login step now names the flow that works.

Integrity is the same either way: both are web stubs that download the current terminal build from MetaQuotes at
install time, so neither can be pinned by hash. What protects the box is TLS to a fixed host and the hard check that
`C:\MT5\ICMarkets\terminal64.exe` exists afterwards; both stay.

## Decision
Option 2. The bootstrap keeps the generic installer, says at the end that the login goes through File > Open an
Account, and RUNBOOK 0.3 gives the exact flow and the symptom. `tests/test_bootstrap_sh.py` fails if the installer
host changes or the flow disappears from the bootstrap or the RUNBOOK.

## Consequences
- A rebuilt MT5 box always needs the company search once (it needs the VNC login once anyway).
- Detection when the step is missed: the bridge refuses to start unless the terminal is logged in to the registered
  demo login on ICMarketsSC-Demo, so `bridge:<account>` FAILs and alerts on Telegram; nothing trades.
- What would change this: a stable, IC Markets-published installer URL that can be checked in CI, or a need to
  rebuild the MT5 box unattended (no VNC step). Then switch to option 1, keeping the terminal64.exe check.
