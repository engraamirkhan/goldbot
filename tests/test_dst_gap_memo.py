"""arm_rules_growth `_in_dst_gap`: a `session_local` clock that some day skips is refused at load (trading-safety
review of #310, L4). The answer depends only on (zone, minute of day), yet every arm load validated it again over 41
years of days: ~0.8 s of the health pass on the Mac, once per exploring account (health pass cost, 2026-11-10). It is
worked out once per (zone, minute) and the refusal is unchanged."""
from __future__ import annotations

from goldbot.research import arm_rules_growth as g


def test_a_skipped_clock_is_still_refused_and_a_normal_one_is_not():
    assert g._in_dst_gap("America/New_York", 2 * 60 + 30) is True     # 02:30 on the spring Sunday does not exist
    assert g._in_dst_gap("America/New_York", 10 * 60) is False
    assert g._in_dst_gap("Europe/London", 1 * 60 + 15) is True
    assert g._in_dst_gap("UTC", 2 * 60 + 30) is False


def test_each_zone_and_minute_is_worked_out_once_across_arm_loads():
    g._in_dst_gap.cache_clear()
    for _ in range(100):
        g._in_dst_gap("Europe/London", 7 * 60)
        g._in_dst_gap("America/New_York", 9 * 60 + 30)
    info = g._in_dst_gap.cache_info()
    assert (info.misses, info.hits) == (2, 198)
