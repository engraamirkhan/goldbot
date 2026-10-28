- TradingView author ideas (owner directive 2026-10-28; ADR 0059): `goldbot/data/tv_ideas.py` follows KnightsofGold,
  Goldviewfx and ArmanShabanTrading (`tv_ideas.authors`, handles only). It reads TradingView's public ideas listing
  (`/api/v1/ideas/?by=<handle>&sort=recent_extended`) and each idea's detail. No cookie is sent; requests are
  read-only through the tv_wire guard and paced by tv_pull's Pacer. Rows go to `state/tv_ideas.db`: ideas as first
  released, later versions, the author's updates, each with `first_seen_utc` and `available_utc`. Back-catalogue
  rows are `backfilled=1` and available at TradingView's time + 900 s. The Mac research copy is
  `raw/tv_ideas/tv_ideas.db` (one-time backfill; `tv-ideas export --out raw/tv_ideas` writes Parquet). Health
  `tv_ideas` WARNs after 2 h without a successful poll on a weekday. Deploy step still owed (SRE): install and
  enable `goldbot-tv-ideas.service` on the brain (RUNBOOK 2.7f); it needs no secret. Back catalogue (2026-10-28):
  6,843 ideas (KnightsofGold 1,955, Goldviewfx 2,220, ArmanShabanTrading 2,667; 4,862 of them XAUUSD;
  2020-11-27..2026-10-27) and 2,933 updates. Direction label: Arman 81%, Goldviewfx 1%, KoG 5%. A target is
  named in about 35%; entry, stop and target together in under 0.1%. Ideas with updates: 10-36%.
