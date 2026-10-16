# 2026-10-16: MT5 refuses to run under wine-stable ("A debugger has been found"), bootstrap blocked

Status: open (script fixes shipped; the Wine version decision is the owner's, ADR 0019)
Severity: S3 (the first deploy of the MT5 box was blocked; nothing was running or trading)
Author: sre. Reviewed by: pending (security-reviewer for the root-run script changes).

## Summary
On the first real run of `goldbot/ops/linux/mt5_bootstrap.sh` on the Oracle MT5 box, the MetaTrader 5 terminal
installed under `winehq-stable` (Wine 11.0) refused to run with "A debugger has been found running in your system".
The bootstrap also stopped on three unattended-install problems. The box was finished by hand the same day, and the
script now reproduces those manual steps.

## Impact
- The first demo deploy was delayed by part of a day. The MT5 box was completed by hand, not by the script.
- No trading impact: no engine, bridge or position existed yet. Demo account only.
- The risk if it had reached a running box: an `apt-get upgrade` that moves Wine into 10.3-11.5 stops the terminal,
  and so the bridge, until Wine is changed. Exits then rely on broker-side stops only (`bridge:<account>` FAILs and
  alerts on Telegram).

## Timeline (UTC, 2026-10-16, approximate)
| Time | Event |
| --- | --- |
| afternoon | Servers created; `mt5_bootstrap.sh` run on the MT5 box (Claude-run deploy) |
| afternoon | `wineboot -i` stops on the Wine Mono download dialog (no one can click it on a headless box) |
| afternoon | `wine python -m pip` runs the Python *installer* (saved in /tmp as `python.exe`; Windows looks in the current directory first) and blocks on its Modify Setup dialog |
| afternoon | `mt5setup.exe /auto` exits 1 although the terminal is installed, so `set -e` aborts the script |
| afternoon | The installed terminal shows "A debugger has been found running in your system" under Wine 11.0 |
| ~19:15 | Fixed by hand: `winehq-devel`, `WINEDLLOVERRIDES=mscoree=`, installer renamed, absolute python path, the mt5setup exit code ignored with a terminal64.exe check; the diff was saved |
| evening | The script brought in line (`fix:` commit on the `claude/gifted-goldberg-lcb7pl` lane) |

## Root cause
1. **A Wine regression.** Wine 10.3 to 11.5 report a debugger to the current MT5 build's protection (WineHQ bug
   58043, fixed in 11.6). The script installed `winehq-stable`, which is 11.0, inside that range. MetaQuotes' own
   `mt5linux.sh` uses `winehq-staging`.
2. **Unattended-install assumptions never tried on a real host.** The script was checked by shellcheck and static
   tests only; no test ran Wine. Three assumptions were wrong: that wineboot is silent, that `python` resolves
   through PATH as on Unix (Windows tries the current directory first), and that mt5setup's exit code means success.
3. **Process.** The first real run of a root-run bootstrap happened on the box that will trade, with no rehearsal VM.

## Detection
By the person running the bootstrap, from the hung commands and the terminal's dialog. Nothing automated would have
seen it: the bootstrap had no post-install check that the terminal exists (it has one now). On a running box the same
regression shows as `bridge:<account>` FAIL on Telegram within the alert window.

## Recovery
The steps the script now runs (RUNBOOK 0.3). For a box bootstrapped with the old script, the handoff note
`mt5-bootstrap-wine-devel` lists the steps: install `winehq-devel`, reinstall `goldbot-bridge@.service` and
`goldbot-mt5` from root's own clone at the commit, `daemon-reload`, restart the bridge.

## Prevention
| Item | Type | Where | Status |
| --- | --- | --- | --- |
| Install `winehq-devel` (11.6 or newer) instead of `winehq-stable` | fix + test | `goldbot/ops/linux/mt5_bootstrap.sh`, `tests/test_bootstrap_sh.py` | shipped |
| `WINEDLLOVERRIDES=mscoree=` for `wineboot` (no Mono dialog) | fix + test | same | shipped |
| Installer saved as `python-installer.exe`; the Windows Python always called by absolute path (bootstrap, bridge unit, `goldbot-mt5`, `goldbot-deploy`, RUNBOOK) | fix + test | same | shipped |
| mt5setup's exit code ignored; a hard check that `C:\MT5\ICMarkets\terminal64.exe` exists | check + test | same | shipped |
| Decide whether to pin the Wine version | decision | `docs/decisions/0019-wine-version-for-the-mt5-box.md` | open (owner) |
| Health warning when the MT5 box's Wine version is in a known-bad range | check | bridge `/health` + `goldbot/ops/health.py` | open (sre, after ADR 0019) |
| Rehearse root-run bootstraps on a throwaway VM before the real box | process | `docs/deploy/first-deploy.md` | open (sre) |

## Lessons
- Went well: the manual fix was saved as a diff, so the script could be brought in line exactly.
- Went badly: a script that drives GUI installers under Wine was trusted on static checks alone.
- Lucky: it happened before any account traded; the same regression on a running box would stop the bridge.
