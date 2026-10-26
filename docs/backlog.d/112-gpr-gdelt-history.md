---
number: 112
rank: 45
title: Geopolitical risk index and GDELT, point in time
stage: 'Data · research input'
size: S
status: 'proposed · accountable: data-engineer · reviews: macro-researcher'
---
- **112. Geopolitical risk index and GDELT, point in time.** Value: a news/risk regime proxy with history. Target: Caldara-Iacoviello GPR (daily AI-GPR from 1960, monthly from 1900/1985; citation requested) and GDELT event counts (redistribution allowed with citation, verify at gdeltproject.org/about.html). Both are rebuilt after the fact: available_utc = the download date of each vintage, never the article date; a backtest uses only vintages that existed. Licence condition: only public-domain or openly licensed data (CC BY, ODC-PDDL, US government) goes to a public release; anything personal-use-only stays local (brain or a private repository, ADR 0048) with only code, schema, row counts and sha256 in the repo. Done = verified first date per series, the PIT stamp rule (available_utc) written and tested with a seeded future row, and the quality checks (coverage per month, gaps, price-scale sanity against known levels, spikes, weekend rows) run and reported.
