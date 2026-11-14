---
number: 256
rank: 83
title: 'MT5 boxes: CPU steal visible on the brain'
stage: Ops (observability) · PROTECT
size: S
status: 'open · accountable: sre · reviews: code-reviewer, security-reviewer'
---
- **256. MT5 boxes: CPU steal visible on the brain.** Value: the 2026-11-12..14 outage (~205 entry-blocked minutes) was hypervisor CPU steal of 35-58% on the MT5 boxes; the brain's new `cpu_steal` check (R151, goldbot/ops/checks/host.py) only sees the brain's own /proc/stat, and nothing on the brain reads an MT5 box's OS. Acceptance: the MT5 bridge's GET /health carries the box's `cpu_steal` block (the same `StealSampler`, sampled in the bridge process on the Linux box, the Wine side reads the host's /proc/stat through Z:), the brain's health shows `cpu_steal:<account>` per bridge and warns on the same rule (> 20% in every minute for 10 min), warn-only; an older bridge without the block omits the check; no host name or address in the reason; a test per path.
