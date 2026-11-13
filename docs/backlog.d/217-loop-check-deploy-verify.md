---
number: 217
rank: 77
title: Deploy R140, publish the waiting registry outbox, verify feedback_loop clears
stage: 'ops (follow-up of R140, 2026-11-13)'
size: XS
status: 'proposed · accountable: sre (Claude-run) · reviews: none'
---
- **217. Deploy R140, publish the waiting registry outbox, verify feedback_loop clears.** Value: proves the detection on the real host and closes incident 2026-11-13. Acceptance: after the deploy, `python -m goldbot.ops.run health` on the brain shows `feedback_loop` FAIL naming the registry outbox (rows waiting since 2026-11-07) and the unread weekly proposals; `loop_check` comments on a `ci-loop` issue (no login or host in it); `scripts/registry_publish.sh` run from the Mac; after the next sync the registry writer is ok; results recorded in the incident file and its status set to closed.
