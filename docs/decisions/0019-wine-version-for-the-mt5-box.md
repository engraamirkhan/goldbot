# 0019: Which Wine the MT5 box runs (pin or track)

Status: accepted 2026-10-16 (option 2). Proposed by the sre 2026-10-16; decided under the owner's 2026-10-16
delegation (below). Implementation: sre, by Sat 10-24 (tech-debt register TD-1, `docs/process/tech-debt.md`).

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
**Option 2.** Hold the installed and verified `winehq-devel` 11.19 (`apt-mark hold` on the `winehq-devel` package and
the `wine-devel*` packages it pulls in), and hold the verified MT5 terminal build. Both versions are shown in the
bridge `/health`. An upgrade of either happens only through a reviewed change (sre, with security review of the
bootstrap diff) that is first rehearsed on a VM: install the new version, start the terminal, run `bridge-smoke`, and
only then apply it to the MT5 box at the weekend with the market closed.

**MT5 build: detection, not only a hold.** The terminal is not an apt package and updates itself from the broker's
server, and `mt5setup.exe` is a rolling installer that cannot be hashed (TD-1). The sre records the verified build,
holds it by whatever means the terminal allows (and says plainly if none exists), and the bridge `/health` reports the
running build so a change is seen the day it happens.

Rationale:
- Wine 10.3-11.5 stopped the terminal outright on 16 Oct (postmortem above). A tracked development branch makes the
  next such stop a matter of when `apt-get upgrade` runs, and a bridge stop leaves open trades on broker-side stops
  only (ROADMAP risk 4). Option 1 keeps that exposure for no benefit we need: 11.19 runs the current build.
- Option 3 is not available (no `winehq-stable` release carries the bug 58043 fix yet).
- Pinning is a tightening: it removes uncontrolled change from the money path and loosens no limit, rail or
  threshold, so it is inside the delegation. Running the hold on the box stays an owner-approved deploy step, like
  any MT5-box command.

Decided 2026-10-16 by PD + PO + CTO (owner delegated).

**Reverse or revisit if:** a `winehq-stable` release with the 58043 fix exists (move to option 3 through the same
rehearsal); MetaQuotes publishes a supported Wine version; a security fix in Wine is needed (bump through the reviewed
change, not by lifting the hold); or a terminal self-update stops working on 11.19 (the build in `/health` shows it,
and the rehearsal path finds the Wine that runs it).

## Consequences
- `mt5_bootstrap.sh` installs the verified version explicitly and holds it; the comment above the install line points
  here. unattended-upgrades is confirmed off for `winehq-*` (sre records how it was checked).
- The bridge `/health` carries `wine_version` and `mt5_build`; health WARNs when either differs from the recorded
  verified pair and FAILs when Wine is in the known-bad range 10.3-11.5. This is an additive health field: no bridge
  `METHODS`, signature or `API_VERSION` change.
- RUNBOOK gains an "Upgrading Wine or the MT5 terminal" step: reviewed change, VM rehearsal with `bridge-smoke`,
  weekend apply, versions read back from `/health`.
- Fixes in newer Wine releases arrive only on purpose. The cost is a rehearsal per bump; the saving is no unattended
  bridge stop.
- Accountable: sre, implemented and verified on the box by Sat 10-24 (market closed), reviewed by security (bootstrap
  diff) and trading-safety (health field touches the bridge).

Options considered and why they lost are listed above; the decision is re-read at the weekly debt review.
