- **Owner history: security and quant review fixes to bd5324e (2026-10-11, M43).** The `no-mt5-report` hook now
  catches the following, whatever the file is called:
  - a CSV with a UTF-8 BOM (Excel "CSV UTF-8");
  - the analyser's private markdown, by its title line, and its JSON summary, by both of its source keys;
  - a gzip, decompressed up to 20 MB;
  - a zip, by each member's name and by each member's content within the byte budget. Archives are opened one
    level deep.

  Refusal messages show only `~/Personal project/trade-history`, never a path. `main` turns both refusals into a
  plain exit. `.share.md` now states how the USD conversion was found ("implied by N trades" / "assumed 1.0"),
  never the rate. The module docstring records the rule: nothing that multiplies with USD/oz into an amount (lots,
  ounces, the rate) may enter `.share.md`.
