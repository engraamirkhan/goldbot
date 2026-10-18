"""Local TradingView probe CLI (read-only). Run from the repo root:

  uv run --with websocket-client --with keyring python -m scripts.tv_local.run unauth
  uv run --with websocket-client --with keyring python -m scripts.tv_local.run auth [--layout ID ...]
  uv run --with websocket-client --with keyring python -m scripts.tv_local.run record        # unauth fixture
  uv run --with websocket-client --with keyring python -m scripts.tv_local.run fixture-auth  # auth fixture (public study)

Outputs go to ~/tmp/tv. Prints counts and metadata only, never cookie/token/user id values.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import auth, discovery, storage
from .client import PRO_URL, URL, Recorder, TvSocket
from .http import Http
from .pull import pull, resolve_only

TFS = ["15", "60", "240", "1D"]
CANDIDATES = ["OANDA:XAUUSD", "ICMARKETS:XAUUSD", "PEPPERSTONE:XAUUSD", "FOREXCOM:XAUUSD", "FXCM:XAUUSD",
              "TVC:GOLD"]
BUILTIN = [("STD;RSI", "last"), ("STD;EMA", "last")]
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tv"


def _print(obj: Any) -> None:
    print(json.dumps(obj, default=str), flush=True)


def _interval(iv: str) -> str:
    iv = iv.strip().upper()
    return {"D": "1D", "1D": "1D", "W": "1W", "1W": "1W"}.get(iv, iv)


def cmd_unauth(args: argparse.Namespace) -> int:
    http = Http()
    metas = [discovery.translate(http, pid, ver) for pid, ver in BUILTIN]
    for m in metas:
        _print({"study": m.pine_id, "version": m.pine_version, "short": m.short,
                "plots": [(p["id"], p["type"], p["title"]) for p in m.plots]})
    with TvSocket(URL) as sock:
        sock.send("set_auth_token", ["unauthorized_user_token"])
        ok_syms = []
        for k, sym in enumerate(CANDIDATES):
            ok, info = resolve_only(sock, f"cs_res{k:02d}", sym)
            _print({"resolve": sym, "ok": ok, "info": info})
            if ok:
                ok_syms.append(sym)
        n = 0
        for sym in ok_syms[: args.max_symbols]:
            for tf in TFS:
                n += 1
                res = pull(sock, f"cs_pull{n:02d}", sym, tf, [(m, None) for m in metas] if sym == ok_syms[0] else [],
                           cap=args.cap)
                _print(storage.write(res, tag="unauth"))
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    present = auth.keys_present()
    _print({"keys_present": present})
    if not present[auth.KEY_SESSIONID]:
        return 2
    http = Http()
    sess = auth.login(http)
    _print({"signed_in": True, "plan": sess.plan or "free", "user_id_found": bool(sess.user_id)})
    report: list[dict[str, Any]] = []
    layouts = [discovery.Layout(layout_id=i) for i in args.layout] or discovery.list_layouts(http, sess, report)
    _print({"layouts": [(lo.name, lo.symbol, lo.interval) for lo in layouts]})
    plan_studies: list[tuple[discovery.Layout, list[discovery.StudyRef]]] = []
    for lo in layouts:
        refs = discovery.layout_studies(http, sess, lo, report)
        plan_studies.append((lo, refs))
        _print({"layout": lo.name or "?", "symbol": lo.symbol, "interval": lo.interval,
                "studies": [(r.pine_id, r.pine_version, r.title, r.found_in, len(r.inputs)) for r in refs]})
    _print({"discovery_calls": report})
    out = storage.OUT
    out.mkdir(parents=True, exist_ok=True)
    rec = Recorder(secrets=sess.secrets()) if args.record else None
    url = PRO_URL if sess.plan else URL
    summaries = []
    with TvSocket(url, recorder=rec) as sock:
        sock.send("set_auth_token", [sess.auth_token])
        n = 0
        meta_cache: dict[tuple[str, str], discovery.StudyMeta | str] = {}
        for lo, refs in plan_studies:
            studies: list[tuple[discovery.StudyMeta, dict[str, Any] | None]] = []
            for r in refs:
                key = (r.pine_id, r.pine_version)
                if key not in meta_cache:
                    try:
                        meta_cache[key] = discovery.translate(http, r.pine_id, r.pine_version, sess)
                    except LookupError as exc:
                        meta_cache[key] = str(exc)
                m = meta_cache[key]
                if isinstance(m, str):
                    _print({"study_unavailable": r.pine_id, "why": m})
                    continue
                studies.append((m, r.inputs or None))
                _print({"study": m.pine_id, "version": m.pine_version, "short": m.short, "desc": m.description,
                        "plots": [(p["id"], p["type"], p["title"]) for p in m.plots]})
            tfs = list(dict.fromkeys(([_interval(lo.interval)] if lo.interval else []) + TFS))
            sym = args.symbol or lo.symbol or "OANDA:XAUUSD"
            for tf in tfs:
                n += 1
                res = pull(sock, f"cs_auth{n:02d}", sym, tf, studies, cap=args.cap)
                s = storage.write(res, tag=f"auth{n:02d}")
                s["layout"] = lo.name
                summaries.append(s)
                _print(s)
    (out / "auth_summary.json").write_text(json.dumps({"layouts": [lo.name for lo in layouts], "pulls": summaries},
                                                      indent=1, default=str))
    if rec is not None:
        rec.dump(out / "auth_frames_sanitised.jsonl")       # local only; review before copying to fixtures
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Small unauthenticated session for offline tests: 300 x 60m bars, one more-data page, RSI, a bad symbol."""
    http = Http()
    rsi = discovery.translate(http, "STD;RSI", "last")
    rec = Recorder()
    with TvSocket(URL, recorder=rec) as sock:
        sock.send("set_auth_token", ["unauthorized_user_token"])
        resolve_only(sock, "cs_fixbad", "NOSUCHEXCHANGE:XAUUSD")
        res = pull(sock, "cs_fix01", "OANDA:XAUUSD", "60", [(rsi, None)], first=300, more=100, cap=400)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    rec.dump(FIXTURES / "ws_unauth_oanda_xauusd_60_rsi.jsonl")
    (FIXTURES / "study_meta_std_rsi.json").write_text(json.dumps(rsi.public(), indent=1) + "\n")
    _print({"frames": len(rec.frames), "bars": len(res.bars), "rsi_points": len(res.studies[0].values) if res.studies else 0})
    return 0


def cmd_fixture_auth(args: argparse.Namespace) -> int:
    """Public fixtures from an authenticated session: the frames of a small pull with a PUBLIC built-in (STD;RSI;
    invite-only scripts' IL and outputs never go in the repo) and the anonymised structure of the first layout."""
    http = Http()
    sess = auth.login(http)
    rsi = discovery.translate(http, "STD;RSI", "last", sess)
    rec = Recorder(secrets=sess.secrets())
    with TvSocket(PRO_URL if sess.plan else URL, recorder=rec) as sock:
        sock.send("set_auth_token", [sess.auth_token])
        res = pull(sock, "cs_fixauth01", "OANDA:XAUUSD", "60", [(rsi, None)], first=300, more=100, cap=400)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    rec.dump(FIXTURES / "ws_auth_oanda_xauusd_60_rsi.jsonl")
    layouts = discovery.list_layouts(http, sess, [])
    if layouts:
        content = discovery.fetch_layout_content(http, sess, layouts[0])
        if content is not None:
            (FIXTURES / "layout_content_anonymised.json").write_text(
                json.dumps(discovery.anonymise_content(content), indent=1) + "\n")
    _print({"frames": len(rec.frames), "bars": len(res.bars),
            "rsi_points": len(res.studies[0].values) if res.studies else 0,
            "rsi_error": res.studies[0].error if res.studies else ""})
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="tv_local")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("unauth")
    u.add_argument("--cap", type=int, default=100_000)
    u.add_argument("--max-symbols", type=int, default=2)
    a = sub.add_parser("auth")
    a.add_argument("--layout", action="append", default=[], help="layout id (from the chart URL) if listing fails")
    a.add_argument("--symbol", default="", help="override the layout's symbol, e.g. OANDA:XAUUSD")
    a.add_argument("--cap", type=int, default=100_000)
    a.add_argument("--record", action="store_true", help="save sanitised frames to ~/tmp/tv")
    sub.add_parser("record")
    sub.add_parser("fixture-auth")
    args = ap.parse_args(argv)
    return {"unauth": cmd_unauth, "auth": cmd_auth, "record": cmd_record,
            "fixture-auth": cmd_fixture_auth}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
