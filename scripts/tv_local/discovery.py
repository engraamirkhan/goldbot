"""What to pull: the owner's saved layouts, the studies on each, and each study's metadata (pine-facade). GET only.

Verified unauthenticated (2026-10-18): pine-facade `list?filter=standard|saved|published` and
`translate/<pineId>/<version>`; `/chart-token/` exists (needs user_id); `/my-charts/` exists (302 to login without a
session). The authenticated shapes of `/my-charts/`, `/chart-token/` and charts-storage are from the open-source
TradingView-API client and are parsed tolerantly: every attempt is reported (status + shape, never the body).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator
from urllib.parse import quote

from .auth import Session
from .http import Http

PINE = "https://pine-facade.tradingview.com/pine-facade"
WWW = "https://www.tradingview.com"
STORAGE = "https://charts-storage.tradingview.com/charts-storage"
XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}


@dataclass
class Layout:
    layout_id: str
    name: str = ""
    symbol: str = ""
    interval: str = ""


@dataclass
class StudyRef:
    pine_id: str                       # STD;RSI, PUB;<id>, USER;<id>
    pine_version: str = "last"
    inputs: dict[str, Any] = field(default_factory=dict)   # the layout's saved input values (in_N -> value)
    title: str = ""
    found_in: str = ""


@dataclass
class StudyMeta:
    pine_id: str
    pine_version: str
    description: str
    short: str
    is_strategy: bool
    is_price_study: bool
    plots: list[dict[str, Any]]        # [{id, type, title}]
    inputs: list[dict[str, Any]]       # metaInfo inputs minus the IL text
    il_template: str = field(repr=False)
    raw_inputs: list[dict[str, Any]] = field(repr=False, default_factory=list)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("il_template")
        d.pop("raw_inputs")
        return d


# ------------------------------------------------------------------ study metadata
def translate(http: Http, pine_id: str, version: str = "last", session: Session | None = None) -> StudyMeta:
    status, data = http.get_json(f"{PINE}/translate/{quote(pine_id, safe='')}/{quote(version, safe='')}",
                                 cookies=session.cookies if session else None)
    if status != 200 or not isinstance(data, dict) or not data.get("success"):
        reason = (data or {}).get("reason") if isinstance(data, dict) else None
        raise LookupError(f"translate {pine_id}@{version}: HTTP {status} {reason or ''}".strip())
    res = data["result"]
    mi = res["metaInfo"]
    styles = mi.get("styles", {}) or {}
    plots = [{"id": p["id"], "type": p.get("type", ""), "title": (styles.get(p["id"], {}) or {}).get("title", p["id"]),
              **({"target": p["target"]} if "target" in p else {})} for p in mi.get("plots", [])]
    ins = [{k: v for k, v in i.items() if k in ("id", "name", "type", "defval", "isHidden", "isFake", "options",
                                                 "min", "max")}
           for i in mi.get("inputs", []) if i.get("id") not in ("text",)]
    return StudyMeta(pine_id=mi.get("scriptIdPart") or pine_id,
                     pine_version=str((mi.get("pine") or {}).get("version") or version),
                     description=mi.get("description", ""), short=mi.get("shortDescription", ""),
                     is_strategy=bool(mi.get("isTVScriptStrategy")) or "strategy" in str(mi.get("id", "")).lower(),
                     is_price_study=bool(mi.get("is_price_study")), plots=plots, inputs=ins,
                     il_template=res["ilTemplate"], raw_inputs=mi.get("inputs", []))


def create_study_inputs(meta: StudyMeta, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """The create_study payload: IL text + pine id/version + every input as {v, f, t} (TradingView-API format)."""
    out: dict[str, Any] = {"text": meta.il_template, "pineId": meta.pine_id, "pineVersion": meta.pine_version}
    overrides = overrides or {}
    for n, i in enumerate(meta.raw_inputs):
        iid = str(i.get("id", ""))
        if not iid or iid in ("text", "pineId", "pineVersion"):
            continue
        val = overrides.get(iid, i.get("defval"))
        if isinstance(val, dict) and "v" in val:          # layout state sometimes stores {v: ...}
            val = val["v"]
        out[iid] = {"v": n if i.get("type") == "color" else val, "f": bool(i.get("isFake")), "t": i.get("type")}
    return out


# ------------------------------------------------------------------ layouts (authenticated)
def list_layouts(http: Http, session: Session, report: list[dict[str, Any]]) -> list[Layout]:
    status, data = http.get_json(f"{WWW}/my-charts/", cookies=session.cookies, headers=XHR)
    report.append({"call": "my-charts", "status": status, "shape": _shape(data)})
    items = data if isinstance(data, list) else (data or {}).get("results") if isinstance(data, dict) else None
    out: list[Layout] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        lid = str(it.get("image_url") or it.get("short_url") or it.get("id") or "")
        if lid:
            out.append(Layout(layout_id=lid, name=str(it.get("name", "")), symbol=str(it.get("symbol", "")),
                              interval=str(it.get("interval", it.get("resolution", "")))))
    return out


def layout_studies(http: Http, session: Session, layout: Layout, report: list[dict[str, Any]]) -> list[StudyRef]:
    """Studies on one layout: the chart page's embedded layout (initData.content), charts-storage as fallback."""
    found: dict[str, StudyRef] = {}
    try:
        html = http.get_text(f"{WWW}/chart/{layout.layout_id}/", cookies=session.cookies)
        report.append({"call": "chart page", "layout": layout.layout_id, "status": 200, "bytes": len(html)})
        content = _layout_content(html)
        if content is not None:
            report.append({"call": "initData.content", "layout": layout.layout_id, "shape": _shape(content)})
        for blob in ([content] if content is not None else list(_json_blobs(html))):
            for ref in _studies_in(blob, "chart page"):
                found.setdefault(ref.pine_id, ref)
            if not layout.symbol:
                m = re.search(r'"symbol"\s*:\s*"([A-Z0-9_]+:[A-Z0-9_.!]+)"', json.dumps(blob))
                layout.symbol = m.group(1) if m else ""
            if not layout.interval:
                m = re.search(r'"interval"\s*:\s*"(\d+[SHDWM]?|[DWM])"', json.dumps(blob))
                layout.interval = m.group(1) if m else ""
        for pid, ver in re.findall(r'\\?"pineId\\?"\s*:\s*\\?"((?:PUB|USER|STD|INDIC);[^"\\]+)\\?"'
                                   r'(?:[^{}]{0,200}?\\?"pineVersion\\?"\s*:\s*\\?"([^"\\]+))?', html):
            found.setdefault(pid, StudyRef(pine_id=pid, pine_version=ver or "last", found_in="chart page regex"))
    except Exception as exc:                       # noqa: BLE001 - discovery is best effort, reported
        report.append({"call": "chart page", "layout": layout.layout_id, "error": type(exc).__name__})
    if not found and session.user_id:
        # fallback (TradingView-API route). 403 "token for different user" when user_id is wrong: the home-page
        # user id extraction is not reliable, which is why the chart page is the primary source
        status, tok = http.get_json(f"{WWW}/chart-token/", cookies=session.cookies,
                                    params={"image_url": layout.layout_id, "user_id": session.user_id})
        report.append({"call": "chart-token", "layout": layout.layout_id, "status": status, "shape": _shape(tok)})
        jwt = tok.get("token") if isinstance(tok, dict) else None
        if jwt:
            st, src = http.get_json(f"{STORAGE}/get/layout/{layout.layout_id}/sources",
                                    params={"chart_id": "_shared", "jwt": jwt})
            report.append({"call": "charts-storage sources", "layout": layout.layout_id, "status": st, "shape": _shape(src)})
            for ref in _studies_in(src, "charts-storage"):
                found.setdefault(ref.pine_id, ref)
    return list(found.values())


def fetch_layout_content(http: Http, session: Session, layout: Layout) -> Any:
    return _layout_content(http.get_text(f"{WWW}/chart/{layout.layout_id}/", cookies=session.cookies))


def anonymise_content(content: Any) -> dict[str, Any]:
    """Structure-only copy of a layout for a public fixture: script ids -> PUB;<script-N>, no IL, no names/ids,
    input values kept only for their types (an invite-only script's settings and outputs stay private)."""
    out: dict[str, Any] = {"charts": []}
    n = 0
    for ch in content.get("charts", []):
        panes = []
        for pane in ch.get("panes", []):
            srcs = []
            for src in pane.get("sources", []):
                st = src.get("state", {}) or {}
                if src.get("type") == "MainSeries":
                    srcs.append({"type": "MainSeries", "state": {"symbol": st.get("symbol"),
                                                                 "interval": st.get("interval")}})
                elif src.get("type") == "Study" and isinstance(src.get("metaInfo"), str):
                    n += 1
                    mi = re.sub(r"\$[A-Z]+;[^@]+@", f"$PUB;script-{n}@", src["metaInfo"])
                    ins = {k: type(v).__name__ for k, v in (st.get("inputs") or {}).items()}
                    srcs.append({"type": "Study", "metaInfo": mi,
                                 "state": {"inputs": {**ins, "pineId": "", "pineVersion": ""},
                                           "styles": {k: {"title": (v or {}).get("title")}
                                                      for k, v in (st.get("styles") or {}).items()}}})
            panes.append({"sources": srcs})
        out["charts"].append({"panes": panes})
    return out


def _layout_content(html: str) -> Any:
    """The saved layout as the chart page embeds it: `initData.content = {...};` (JSON). Verified 2026-10-18:
    content.charts[].panes[].sources[] with type MainSeries (state.symbol/interval) and Study (metaInfo string,
    state.inputs = the owner's input values, state.styles = plot styles)."""
    m = re.search(r"initData\.content\s*=\s*", html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, m.end())
        return obj
    except ValueError:
        return None


def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    elif isinstance(obj, str) and obj[:1] in "{[" and "pineId" in obj:
        try:
            yield from _walk(json.loads(obj))
        except ValueError:
            pass


def _studies_in(obj: Any, where: str) -> list[StudyRef]:
    out: list[StudyRef] = []
    for d in _walk(obj):
        st_raw = d.get("state")
        state: dict[str, Any] = st_raw if isinstance(st_raw, dict) else d
        in_raw = state.get("inputs")
        inputs: dict[str, Any] = in_raw if isinstance(in_raw, dict) else {}
        mi_raw = d.get("metaInfo")
        mi: dict[str, Any] = mi_raw if isinstance(mi_raw, dict) else {}
        # saved layouts (initData.content) store metaInfo as a STRING: "Script$PUB;<id>@tv-scripting-101[v.2.0]"
        # and leave state.inputs.pineId/pineVersion empty
        if isinstance(mi_raw, str):
            ms = re.match(r"^(?:\w+)\$([A-Z]+;[^@]+)@[\w-]+(?:\[v\.([\d.]+)\])?", mi_raw)
            if ms:
                mi = {"scriptIdPart": ms.group(1), "pine": {"version": ms.group(2) or "last"}}
        pid = inputs.get("pineId") or mi.get("scriptIdPart") or d.get("pineId")
        if not isinstance(pid, str) or ";" not in pid:
            continue
        ver = str(inputs.get("pineVersion") or (mi.get("pine") or {}).get("version") or "last")
        vals = {k: v for k, v in inputs.items() if k not in ("text", "pineId", "pineVersion")}
        out.append(StudyRef(pine_id=pid, pine_version=ver, inputs=vals,
                            title=str(mi.get("shortDescription") or mi.get("description") or d.get("title") or ""),
                            found_in=where))
    return out


def _json_blobs(html: str) -> Iterator[Any]:
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S):
        body = m.group(1)
        if "pineId" not in body and '"panes"' not in body:
            continue
        for n, start in enumerate(i.start() for i in re.finditer(r"[{\[]", body)):
            if n > 500:
                break
            try:
                obj, _ = json.JSONDecoder().raw_decode(body, start)
            except ValueError:
                continue
            if "pineId" in json.dumps(obj) or "panes" in json.dumps(obj):
                yield obj
                break


def _shape(data: Any) -> str:
    """Describe JSON without its values (safe to report)."""
    if isinstance(data, list):
        return f"list[{len(data)}]" + (f" of keys {sorted(data[0])[:12]}" if data and isinstance(data[0], dict) else "")
    if isinstance(data, dict):
        return f"dict keys {sorted(data)[:12]}"
    return type(data).__name__
