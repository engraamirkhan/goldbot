# YYYY-MM-DD: <one-line title>

Status: open | closed (every prevention item shipped or dated with the owner)
Severity: S1 exits or alerts lost | S2 a service down, no trading impact | S3 a deploy or bootstrap blocked, nothing running
Author: <role>. Reviewed by: <roles>.

## Summary
Two or three sentences: what broke, for how long, and what it cost.

## Impact
What did not work (trading, alerts, deploys), from when to when (UTC), the money or positions affected, and what was
safe all along (for example broker-side stops).

## Timeline (UTC)
| Time | Event |
| --- | --- |
| hh:mm | first sign |
| hh:mm | detected (how: a health check, an alert, a person) |
| hh:mm | mitigated |
| hh:mm | resolved |

## Root cause
The technical cause, and why the process let it through (no test, no check, an assumption).

## Detection
How it was found, how long that took, and how it should have been found (which check or alert).

## Recovery
The steps that restored service, as they now appear in docs/RUNBOOK.md.

## Prevention
| Item | Type (check / test / runbook / decision) | Where | Status |
| --- | --- | --- | --- |
| | | | shipped (commit) / open (owner, date) |

## Lessons
What went well, what did not, and where we were lucky.
