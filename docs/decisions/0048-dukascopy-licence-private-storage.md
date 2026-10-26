# 0048: Dukascopy bars: personal-use licence, private storage

Status: accepted (owner decision 2026-10-26; recorded by the data-engineer). Proposed 2026-10-26 with a private
repository; the owner chose, verbatim: "use local copy for the data, until we make the repo private" and "in the same
project, just add to gitignore". Deleting the existing public `data-v1` assets is a separate owner call, still open.

## Context
Release `data-v1` on this public repository holds Dukascopy XAUUSD 1m bars for 2010-2026 (17 assets, about 225 MB),
downloadable by anyone. Dukascopy's terms of use (https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/,
read 2026-10-26) say, in substance:
- downloads from the website are "licensed to you ... ONLY for your own personal, non-commercial use";
- "You may not recirculate, redistribute or publish ... without DUKASCOPY's prior written consent";
- the information "may not be used to construct a database of any kind", nor "stored ... in databases for access by
  any third party", nor used "to distribute any database ... containing all or part of" it.

Our 1m bars are a derived database of their quotes. A public release asset is redistribution to third parties. No
written consent exists. On 2026-10-26 the datafeed host also answered this Mac with HTTP 429 and a pointer to
https://www.dukascopy.com/wiki/en/development/data-export/, which now directs bulk history to a requester-pays S3 bucket
(`cfg-public-proper-wallaby`, eu-west-1; about 0.06 USD per instrument history in their own example). The free
datafeed route our workflow uses may be throttled further.

## Decision
1. Verdict: publishing Dukascopy-derived bars as public GitHub release assets is **not allowed** by their terms.
   Personal research and trading use by the owner is allowed.
2. New instruments (XAGUSD first) are built only into a **private** GitHub repository (repository variable
   `DATA_PRIVATE_REPO`, release `dukascopy-v1`, fine-grained token secret `DATA_PRIVATE_TOKEN` with contents:write on
   that repository only). The workflow refuses them while the variable is unset. Implemented in
   `.github/workflows/data-dukascopy.yml`.
3. Recommended for gold (owner decision, not taken here): create the private repository (free; GitHub Free includes
   unlimited private repositories, release assets have no storage charge), copy the 17 yearly assets there, point
   `goldbot/data/release.py` (`REPO`/tag for bars) and the VPS/brain sync at it with a read-only token in the keyring,
   then delete the public `data-v1` assets. The building workflow stays on this public repository, so Actions minutes
   stay free. Alternative if the owner prefers no second repository: keep the Parquet on the brain only (Oracle block
   volume, backed up), with the workflow uploading there through a pre-authenticated request.
4. Data that is public domain or openly licensed (CFTC COT: US government; FRED series that are not third-party
   copyrighted; World Bank Pink Sheet: CC BY 4.0 with attribution) stays on public releases.

## Owner decision (2026-10-26): local copy now, private repository later
Supersedes recommendation 3 for gold until this repository is made private.
1. **Where the data lives.** The owner's Mac keeps the yearly files in this project, in `raw/`
   (`raw/xauusd_1m_dukascopy_<year>.parquet`, the folder research already reads by default) and the store in `data/`.
   Both are gitignored (with `raw-*/`, `*.parquet`, `*.feather`, `*.arrow`, `*.bi5`). The pre-commit hook
   `no-market-data` (`scripts/check_no_market_data.py`) refuses Parquet/Arrow/bi5 files (by extension or magic bytes)
   and price-bar/tick CSVs outside the small synthetic fixture folders, so a forced `git add -f` is caught too;
   `tests/test_no_market_data.py` runs the same check over every tracked file in CI.
2. **No more public publishing.** `data-dukascopy.yml` asks the API whether this repository is private (an error
   counts as public) and builds no gold while it is public: a dispatch fails with the reason, the Saturday run is a
   no-op notice. The upload step refuses a public repository again before `gh release upload`. Workflow artifacts were
   rejected as a stop-gap: on a public repository any signed-in GitHub user can download them. Publishing to `data-v1`
   resumes by itself once the repository is private. Silver keeps its private-repository route (decision 2).
3. **How the brain gets bars.** `scripts/local_bars.sh weekly` on the Mac rebuilds the current year from Dukascopy into
   `raw/` (complete months kept, never shrinking, atomic rename) and pushes changed yearly files over ssh/scp to
   `/opt/goldbot/data/_inbox/bars` (sha256 checked, `.part` then rename, owner `goldbot`; no service touched).
   `goldbot.data.release.sync_release` (the Saturday retrain's refresh) loads the inbox when it holds any yearly file
   and only otherwise falls back to `data-v1`. Run it before Saturday 06:00 UTC; `seed` copies the frozen `data-v1`
   files into `raw/` once.
4. **Later.** When the repository goes private nothing in the code has to change for the workflow; the brain keeps
   preferring its inbox until the inbox folder is emptied.

## Consequences
- Until the owner acts, `data-v1` remains a licence exposure; the program director tracks it as an owner item.
- Research and the brain need a token to read bars once they move; `scripts/fetch_data_release.py` and
  `release.sync_release_bars` take `GH_TOKEN` already, so the change is the repository name and a keyring secret.
- Silver research waits on the private repository (owner step: create the repository and the token secret, about
  5 minutes).
- If Dukascopy throttles the free datafeed for runners too, the fallback is their requester-pays S3 bucket, which needs
  an AWS account and spend (owner-only); a measured quote goes to the CFO first.
- (2026-10-26) The weekly bar refresh now depends on the owner's Mac being on and able to reach Dukascopy (it saw
  HTTP 429 on 2026-10-26). Missed weeks show up as stale bars: adaptive limits switch the minimum-lot cap off after 9
  days (`limits_job.ATR_MAX_LAG_DAYS`). A launchd job for `local_bars.sh weekly` is the next step.
- (2026-10-26) The GitHub research workflows (`research.yml`, `research-arms-matrix.yml`, `count-owed-events.yml`,
  `preregister-quarter.yml`) keep reading the frozen `data-v1` snapshot; they lose their bars when the owner deletes
  those assets, and need the private repository (or local runs) from then on. Bars rebuilt locally can differ in
  digest from the release files a pre-registration pinned (`bar_digest_refusal` refuses them, as designed).
- (2026-10-26) Backups: `dukascopy` stays a release source in `goldbot/ops/backup.py`, so the brain's inbox is not in
  restic; the Mac's `raw/` is the master copy and must be in the owner's Mac backup.
