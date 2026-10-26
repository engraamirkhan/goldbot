---
number: 110
rank: 43
title: Central-bank gold reserves and ETF flows, free
stage: 'Data · research input'
size: M
status: 'proposed · accountable: data-engineer · reviews: macro-researcher'
---
- **110. Central-bank gold reserves and ETF flows, free.** Value: slow macro state (central-bank buying, ETF flows) for the swing lane. Target: WGC Goldhub central-bank reserves (IMF IFS based; monthly from 2002, two months behind, revised) and ETF holdings/flows (weekly and monthly), plus issuer files beyond GLD (IAU). Free avenues: IMF IFS API directly (public, no key) before Goldhub (free registration, owner sign-up only if IFS is not enough); issuer Excel downloads. Stamp: available_utc = publication date, vintage per download. Licence condition: only public-domain or openly licensed data (CC BY, ODC-PDDL, US government) goes to a public release; anything personal-use-only stays local (brain or a private repository, ADR 0048) with only code, schema, row counts and sha256 in the repo. Done = verified first date per series, the PIT stamp rule (available_utc) written and tested with a seeded future row, and the quality checks (coverage per month, gaps, price-scale sanity against known levels, spikes, weekend rows) run and reported.
