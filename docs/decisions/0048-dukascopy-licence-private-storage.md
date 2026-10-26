# 0048: Dukascopy bars: personal-use licence, private storage

Status: proposed (data-engineer, 2026-10-26). Moving the existing gold assets is an owner decision (legal); this ADR
records the finding and the recommendation and applies the rule to new instruments only.

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

## Consequences
- Until the owner acts, `data-v1` remains a licence exposure; the program director tracks it as an owner item.
- Research and the brain need a token to read bars once they move; `scripts/fetch_data_release.py` and
  `release.sync_release_bars` take `GH_TOKEN` already, so the change is the repository name and a keyring secret.
- Silver research waits on the private repository (owner step: create the repository and the token secret, about
  5 minutes).
- If Dukascopy throttles the free datafeed for runners too, the fallback is their requester-pays S3 bucket, which needs
  an AWS account and spend (owner-only); a measured quote goes to the CFO first.
