---
number: 257
rank: 83
title: Terminate the stopped old icm-demo box (goldbot-mt5) on 2026-11-21
stage: Ops (MT5 box re-host, RUNBOOK 0.3e R13)
size: S
status: proposed (due 2026-11-21; SRE owns, owner runs the OCI terminate)
---
- **257. Terminate the stopped old icm-demo box (goldbot-mt5) on 2026-11-21.** Value: icm-demo is being re-hosted (started 2026-11-14) from the VM.Standard.E2.1.Micro `goldbot-mt5` (1 GB, 39-63 % CPU steal) to the E5.Flex 1 OCPU / 2 GB / BASELINE_1_2 box `goldbot-mt5-icm2` (CTO + CFO decision 2026-11-14 under the owner's delegation, +GBP 11.08/month). The old VM is kept STOPPED, not terminated, for 7 days after the cut-over as the RUNBOOK 0.3e rollback; while it exists its boot volume bills above the free 200 GB of block storage. Acceptance: on or after 2026-11-21 (7 days after the cut-over; later if the cut-over slipped), only if icm-demo ran on the new box for the whole 7 days with no rollback (`bridge:icm-demo`, `terminal:icm-demo` ok in `goldbot run health`; no RUNBOOK 0.3e rollback in the deploy note): the old instance and its boot volume are terminated by the owner's one-line OCI script (no OCID printed); the `goldbot-mt5-old` alias is removed from `~/.ssh/goldbot.conf`; the brain's `/etc/goldbot/bridge.env.old-box` is deleted; `oci compute instance list` shows no non-terminated `goldbot-mt5`; block storage back to at most 197 of 200 GB; the finance-auditor records the end of the overlap in the November ledger. If a rollback happened in the 7 days, this item moves to "blocked" with the new date instead.
