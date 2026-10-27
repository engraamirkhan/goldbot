---
number: 119
rank: 39
title: Free real COMEX gold volume and order flow
stage: Data · research input
size: M
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer, cfo for any spend'
---
- **119. Free real COMEX gold volume and order flow.** Value: H-40's volume flags died on tick volume (a quote-activity proxy); real traded volume and trades can re-test break-with-volume and the SMC mechanism claims (survey section 3). Target: COMEX GC daily volume and open interest from as early as free sources allow, then 1m volume/trades. Free avenues: CME's free delayed daily settlement/volume/OI pages and its free historical volume reports; CFTC open interest (already in positioning-v1, legacy from 1986); exchange daily bulletin archives; free academic tiers (WRDS, university access) if the owner has one; Yahoo GC=F daily volume (personal use, local); Databento's 125 USD free credit (owner sign-up and API key needed: note only, never signed up by an agent; quote via metadata.get_cost first). Licence condition: only public-domain or openly licensed data (CC BY, ODC-PDDL, US government) goes to a public release; anything personal-use-only stays local (brain or a private repository, ADR 0048) with only code, schema, row counts and sha256 in the repo. Done = verified first date per series, the PIT stamp rule (available_utc) written and tested with a seeded future row, and the quality checks (coverage per month, gaps, price-scale sanity against known levels, spikes, weekend rows) run and reported.
