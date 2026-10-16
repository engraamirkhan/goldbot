# 0019: Which Wine the MT5 box runs (pin or track)

Status: proposed (sre, 2026-10-16). **Owner decision**: no agent takes the pinning choice below.

## Context
The MT5 terminal runs under Wine on the Oracle MT5 box (`goldbot/ops/linux/mt5_bootstrap.sh`). Wine 10.3 to 11.5,
which includes `winehq-stable` 11.0, make the current MT5 build report "A debugger has been found running in your
system" and refuse to run (WineHQ bug 58043, fixed in 11.6; postmortem
`docs/incidents/2026-10-16-mt5-debugger-found-under-wine.md`). The bootstrap now installs `winehq-devel` (11.6 or
newer), unpinned. MetaQuotes' `mt5linux.sh` uses `winehq-staging`.

Tracking a development branch means any `apt-get upgrade` (by hand, or unattended-upgrades if enabled) can bring a
new Wine with a new regression that stops the terminal, and so the bridge. Exits then rely on broker-side stops until
someone acts. Pinning avoids surprise upgrades but also freezes out fixes, and MetaQuotes updates the terminal by
itself, so a terminal update can break a pinned Wine just the same.

## Options
1. **Track `winehq-devel`, unpinned (current).** Newest fixes; upgrades are uncontrolled.
2. **Hold the installed `winehq-devel` version** (`apt-mark hold` or apt preferences) and upgrade Wine only on
   purpose, at the weekend, with the bridge checked after. Predictable; needs a periodic owner-approved bump.
3. **Return to `winehq-stable` once a stable release of 11.6 or newer exists.** Slower cadence; not possible yet.

## Recommendation (sre)
Option 2 now, the same way deploys are owner-approved, and option 3 when a stable release carries the 58043 fix.
With any option, add a health warning when the Wine version is in a known-bad range.

## Decision
Pending the owner. Until then the script installs the newest `winehq-devel` and pins nothing; the comment above the
install line in `mt5_bootstrap.sh` points here.

## Consequences
- Option 1: no work; a Wine regression can stop the bridge without warning (detected only by `bridge:<account>` FAIL).
- Option 2: one `apt-mark hold` line in the bootstrap, a RUNBOOK "upgrading Wine" step, and the Wine version in the
  bridge `/health`; the owner approves each Wine bump.
- Option 3: a later change back to `winehq-stable`, with the same health warning.

What would change the recommendation: MetaQuotes publishing a supported Wine version for MT5, or a stable Wine release
with the fix.
