"""A study's boxes/lines/labels are saved per closed bar (tv.db `tv_graphics`) from the frames the study already sends:
no extra request, append-only, identical consecutive sets stored once, size-capped, exported by `tv-pull graphics`.
Synthetic frames in the community-documented `ns.d.graphicsCmds` shape (TradingView gives only the final box set in
history, so the set at each bar can only be recorded live)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data import tv_pull, tv_wire
from tests.test_tv_pull import T_BACKFILL, Clock, ReplayNetwork, cfg, run


def ns(create: dict[str, Any] | None = None, erase: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"d": json.dumps({"graphicsCmds": {"create": create or {}, "erase": erase or []}})}


def box(i: int, x1: int, x2: int, top: float, bottom: float) -> dict[str, Any]:
    return {"id": i, "x1": x1, "x2": x2, "y1": top, "y2": bottom}


def with_graphics(graphics: dict[str, Any], name: str = "backfill_15m.json") -> ReplayNetwork:
    """The recorded conversation, its study's `du` carrying `graphics` (an `ns`) as well as its values."""
    net = ReplayNetwork(name)
    frames = tv_wire.decode(net.script["replies"]["create_study"][0][0])
    msg = json.loads(frames[0])
    msg["p"][1]["st1"]["ns"] = graphics
    net.script["replies"]["create_study"][0][0] = tv_wire.encode_raw(json.dumps(msg, separators=(",", ":")))
    return net


def snaps(tmp_path: Path, tf: str = "15") -> list[dict[str, Any]]:
    return tv_pull.PullLog(tmp_path / "tv.db").graphics(tf)


G = ns(create={"dwgboxes": [{"data": [box(1, 3, 6, 2403.0, 2399.0), box(2, 4, 9, 2410.5, 2404.0)]}],
               "dwglabels": [{"data": [{"id": 7, "x": 5, "y": 2405.0, "text": "R"}]}]})
GT = ns(create={"dwgboxes": [{"data": [box(1, 1792143000, 1792146600, 2403.0, 2399.0)]}]})   # x as time: stable
LIVE_C = dict(discover_every_s=10**6)


def test_a_study_that_draws_boxes_has_them_saved_at_the_newest_closed_bar(tmp_path):
    rep = run(tmp_path, with_graphics(G), Clock(T_BACKFILL))
    assert rep.ok, rep.reason
    [s] = snaps(tmp_path)
    assert s["bar_time"] == "2026-10-16T11:30:00+00:00" and s["tf"] == "15" and s["symbol"] == "XAUUSD"
    boxes = [o for o in s["objects"] if o["type"] == "box"]
    assert [(o["id"], o["top"], o["bottom"], o["left"], o["right"]) for o in boxes] == [
        (1, 2403.0, 2399.0, 3, 6), (2, 2410.5, 2404.0, 4, 9)]
    assert [o["text"] for o in s["objects"] if o["type"] == "label"] == ["R"]
    assert s["meta_hash8"] == tv_pull.PullLog(tmp_path / "tv.db").metas()[0].meta_hash8


def test_graphics_cost_no_extra_request(tmp_path):
    plain, withg = ReplayNetwork("backfill_15m.json"), with_graphics(G)
    run(tmp_path / "a", plain, Clock(T_BACKFILL))
    run(tmp_path / "b", withg, Clock(T_BACKFILL))
    assert withg.methods() == plain.methods() and len(withg.gets) == len(plain.gets)


def test_a_study_without_graphics_saves_none(tmp_path):
    run(tmp_path, ReplayNetwork("backfill_15m.json"), Clock(T_BACKFILL))
    assert snaps(tmp_path) == []


def test_an_identical_set_at_the_next_bar_is_not_stored_again_and_a_changed_one_is(tmp_path):
    run(tmp_path, with_graphics(GT), Clock(T_BACKFILL))
    live = pd.Timestamp("2026-10-16T12:01:00Z")
    run(tmp_path, with_graphics(GT, "live_15m.json"), Clock(live), settings=cfg(**LIVE_C))
    assert len(snaps(tmp_path)) == 1                         # the same objects at a later bar: one row
    changed = ns(create={"dwgboxes": [{"data": [box(3, 1792143000, 1792146000, 2420.0, 2415.0)]}]})
    run(tmp_path, with_graphics(changed, "live_15m.json"), Clock(live + pd.Timedelta(minutes=15)),
        settings=cfg(**LIVE_C))
    first, second = snaps(tmp_path)
    assert [o["id"] for o in second["objects"]] == [3] and first["bar_time"] < second["bar_time"]


def test_erase_all_then_create_leaves_only_the_new_objects():
    state: dict = {}
    tv_pull.apply_graphics(state, G)
    tv_pull.apply_graphics(state, ns(create={"dwgboxes": [{"data": [box(9, 1, 2, 5.0, 4.0)]}]},
                                     erase=[{"action": "all"}]))
    assert list(state) == [("box", 9)]
    tv_pull.apply_graphics(state, ns(erase=[{"action": "one", "type": "dwgboxes", "id": 9}]))
    assert state == {}


def test_frames_that_do_not_fit_are_skipped_not_raised():
    state: dict = {}
    for bad in (None, {}, {"d": 5}, {"d": "not json"}, {"d": "[]"}, {"d": '{"graphicsCmds": 3}'},
                {"d": '{"graphicsCmds": {"create": {"dwgboxes": 4, "dwglines": [3, {"data": 1}]}}}'}):
        tv_pull.apply_graphics(state, bad)
    assert state == {}


def test_bar_indices_resolve_to_bar_times_when_the_series_knows_them():
    state: dict = {}
    tv_pull.apply_graphics(state, ns(create={"dwgboxes": [{"data": [box(1, 3, 1792150200, 2.0, 1.0)]}]}))
    [o], cut = tv_pull.graphics_snapshot(state, {3: 1792143000}, 10)
    assert (o["left_time"], o["right_time"], o["right"], cut) == (1792143000, 1792150200, 1792150200, False)


def test_a_snapshot_is_capped_keeping_the_highest_ids_and_says_so():
    state: dict = {}
    tv_pull.apply_graphics(state, ns(create={"dwgboxes": [{"data": [box(i, 1, 2, 2.0, 1.0) for i in range(10)]}]}))
    objs, cut = tv_pull.graphics_snapshot(state, {}, 3)
    assert [o["id"] for o in objs] == [7, 8, 9] and cut


def test_rows_are_append_only_first_receipt_wins_and_capped_per_study(tmp_path):
    log = tv_pull.PullLog(tmp_path / "tv.db")
    t = pd.Timestamp("2026-10-16T12:00:00Z")

    def save(h: str, bar: pd.Timestamp, top: float) -> bool:
        return log.save_graphics(meta_hash8=h, symbol="XAUUSD", tf="60", bar_time=bar, received=bar,
                                 objects=[{"id": 1, "top": top}], truncated=False, max_snapshots=2)

    assert save("aaaa", t, 1.0)
    assert not save("aaaa", t, 2.0)                          # same bar: first receipt wins
    assert save("aaaa", t + pd.Timedelta(hours=1), 2.0)
    assert not save("aaaa", t + pd.Timedelta(hours=2), 3.0)  # cap of 2 reached
    assert save("bbbb", t, 1.0)                              # another study has its own count
    assert [r["objects"][0]["top"] for r in log.graphics("60", meta_hash8="aaaa")] == [1.0, 2.0]
    assert [r["bar_time"] for r in log.graphics("60", start=t + pd.Timedelta(hours=1))] == [
        "2026-10-16T13:00:00+00:00"]


def test_only_the_configured_timeframes_are_saved(tmp_path):
    run(tmp_path, with_graphics(G), Clock(T_BACKFILL), settings=cfg(graphics_timeframes=["60"]))
    assert snaps(tmp_path) == []


def test_the_export_prints_json_lines_for_one_timeframe(tmp_path, capsys):
    run(tmp_path, with_graphics(G), Clock(T_BACKFILL))
    assert tv_pull.main(["graphics", "--tf", "15", "--state", str(tmp_path), "--from", "2026-10-16"]) == 0
    [line] = capsys.readouterr().out.splitlines()
    assert {o["type"] for o in json.loads(line)["objects"]} == {"box", "label"}
    assert tv_pull.main(["graphics", "--state", str(tmp_path)]) == 2


def test_tv_graphics_table_is_in_the_tv_migrations(tmp_path):
    tv_pull.PullLog(tmp_path / "tv.db")
    with sqlite3.connect(tmp_path / "tv.db") as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tv_graphics)")]
    assert cols[:4] == ["meta_hash8", "symbol", "tf", "bar_time"]
