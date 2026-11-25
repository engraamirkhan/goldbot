- **Performance by asset class (owner 2026-11-25, BACKLOG 270, R178).**
  - **Mapping.** Every trading-performance read now groups by asset class and by family x class, through one mapping,
    `goldbot.ops.class_breakdown.asset_class_of`. Arm ids use their suffix (S0-SUGAR) or, for a gold arm, the spec row.
  - **Where it shows.** The ranking rows and view, the trades page, the lab "by class" line (only when a lab closed on
    two classes or more), the ranking's Telegram family line, the performance report tables and the web ranking's
    Class column.
  - **Figures and flag.** The figures and the split flag are in `goldbot/ops/class_breakdown.py`. A family is flagged
    "split (significant)" when its best class is positive and its worst negative, each class with at least 30 fills
    that have an R. The clustered t of the difference must also reach the Bonferroni bar over class pairs.
  - **Voids.** Voided labels and fills void as evidence are kept out of every class figure (`class_voids`, PR #442).
    So are demo learning cohort fills. Their money stays in the totals.
  - **Engine ranking fix.** Cohort fills now carry evidence `learning_cohort`. Before this, the ranking skipped
    `not_evidence`, which hid every engine strategy (#440).
