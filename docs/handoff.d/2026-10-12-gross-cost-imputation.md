- Gross closed trades are charged a conservative imputed cost (quant review 2026-10-12, findings A-D; TRACEABILITY X20;
  ADR 0017).
  - goldbot/ops/cost_imputation.py is the one shared helper. It charges only the costs a row's return lacks: the
    commission from the cost table (else the settings value), plus paid swap for every rollover held through, with the
    triple day counted (a swap credit is never counted). Gross rows are kept, never dropped.
  - Phase gates: paper and live expectancy, live drawdown, broker agreement and the stop-rule bound use the charged
    returns. Each item shows the gross count and the total charged. A gross row that cannot be priced fails the item.
  - New optional items `paper_gross_share` and `live_gross_share` under `gates.max_gross_share`. It is null (context
    only) until the owner decides; the recommendation is 0.05.
  - Ruin Monte Carlo: gross rows are charged in R, and the cost basis reads "net+imputed (N)", or "unknown" when a
    row's initial risk is missing.
  - Engine record: a USD account without the broker's conversion now records ret = pnl / notional (it used to fall
    back to the price return while pnl_basis said "net"). The new `ClosedTrade.ret_basis` ("pnl" or "price"; None on
    older records) marks a return that came from prices. Review R1: an older or rolled-back record with no
    `ret_basis`, broker money, an initial stop and no R is charged as a price return too.
  - The stop rule's incident check adds the imputed cost to a gross loss. A row with no side pays the larger swap, and
    a row with no entry price is priced from its exit price. This needs trading-safety review (record field) and quant
    review (gate inputs).
