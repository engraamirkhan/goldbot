"""Crop the MT5 box's full-screen capture down to the chart pane (brain side; the MT5 box is untouched).

Design rule: the dashboard shows ONLY the chart (and its price scale and time axis), never the terminal's menus,
Market Watch, Navigator, Toolbox or dialogs. Pane detection uses Pillow/numpy only: an MT5 plot area is a large dark
rectangle; the price scale is the light strip to its right and the time axis the light strip below. Candles and grid
lines are sparse, so the dark test runs on 4 px cells and the pane is the largest all-dark rectangle of cells (found
repeatedly, so tiled charts give several panes). The pane whose child-window title ("SYMBOL,TF") contains XAUUSD wins,
else the largest. Reading the title needs OCR; none is a dependency, so the title is read only by an injected reader or
the `tesseract` binary when installed. Without it the symbol is "unknown" and the largest pane is chosen. No pane found
(or an unreadable image) means the full image is served with cropped=false.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from goldbot.base import FrozenRecord

CELL = 4                # px per detection cell
DARK = 60               # a pixel this dark (0-255 luminance) is plot background
DARK_FRACTION = 0.6     # a cell is plot when this share of its pixels is dark
MIN_W, MIN_H = 160, 100  # smallest plot rectangle taken for a chart, px
MAX_PANES = 8
SCALE_MAX = 120         # price-scale strip searched to the right of the plot, px
AXIS_MAX = 40           # time-axis strip searched below the plot, px
TITLE_H = 24            # child-window title bar above the plot, px
LIGHT = 140             # a strip column/row this bright on average is scale/axis, not another window
TITLE_RE = re.compile(r"([A-Z][A-Z0-9#._]{2,11})\s*,\s*(M\d+|H\d+|D1|W1|MN)")

TitleReader = Callable[[Image.Image], str]


class Pane(FrozenRecord):
    plot: tuple[int, int, int, int]   # x0, y0, x1, y1 (exclusive) of the dark plot area
    box: tuple[int, int, int, int]    # plot + price scale + time axis
    symbol: str = "unknown"
    timeframe: str = "unknown"


class CropResult(FrozenRecord):
    png: bytes
    cropped: bool
    symbol: str = "unknown"
    timeframe: str = "unknown"
    gold: bool | None = None          # None = title not readable


def _largest_rect(grid: np.ndarray) -> tuple[int, int, int, int]:
    """Largest all-True rectangle of a boolean grid as (r0, c0, r1, c1) exclusive, area 0 when none."""
    heights = np.zeros(grid.shape[1], dtype=int)
    best, area = (0, 0, 0, 0), 0
    for r in range(grid.shape[0]):
        heights = np.where(grid[r], heights + 1, 0)
        stack: list[int] = []
        for c in range(len(heights) + 1):
            h = int(heights[c]) if c < len(heights) else 0
            while stack and int(heights[stack[-1]]) >= h:
                hi = int(heights[stack.pop()])
                left = stack[-1] + 1 if stack else 0
                if hi * (c - left) > area:
                    area = hi * (c - left)
                    best = (r + 1 - hi, left, r + 1, c)
            stack.append(c)
    return best


def _snap(lum: np.ndarray, plot: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Cells round the plot edge by up to CELL-1 px either way: trim lines that are not plot, grow over lines that are."""
    x0, y0, x1, y1 = plot
    h, w = lum.shape
    for _ in range(CELL):               # a cell that was mostly plot may overshoot by a line of window frame
        if x1 - x0 > 1 and lum[y0:y1, x1 - 1].mean() >= DARK:
            x1 -= 1
        if y1 - y0 > 1 and lum[y1 - 1, x0:x1].mean() >= DARK:
            y1 -= 1
        if x1 - x0 > 1 and lum[y0:y1, x0].mean() >= DARK:
            x0 += 1
        if y1 - y0 > 1 and lum[y0, x0:x1].mean() >= DARK:
            y0 += 1
    for _ in range(CELL):
        if x1 < w and lum[y0:y1, x1].mean() < DARK:
            x1 += 1
        if y1 < h and lum[y1, x0:x1].mean() < DARK:
            y1 += 1
        if x0 > 0 and lum[y0:y1, x0 - 1].mean() < DARK:
            x0 -= 1
        if y0 > 0 and lum[y0 - 1, x0:x1].mean() < DARK:
            y0 -= 1
    return x0, y0, x1, y1


def _extend(lum: np.ndarray, plot: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Grow the plot over the price scale (right) and time axis (below): the strip is the light band whose median
    shade is the one just outside the plot; text on it is sparse, so a neighbouring window's different shade ends it."""
    x0, y0, x1, y1 = plot
    h, w = lum.shape
    nx = x1
    if x1 < w and lum[y0:y1, x1].mean() > LIGHT:
        ref = np.median(lum[y0:y1, x1])
        while nx < min(w, x1 + SCALE_MAX) and abs(np.median(lum[y0:y1, nx]) - ref) < 4:
            nx += 1
    ny = y1
    if y1 < h and lum[y1, x0:nx].mean() > LIGHT:
        ref = np.median(lum[y1, x0:nx])
        while ny < min(h, y1 + AXIS_MAX) and abs(np.median(lum[ny, x0:nx]) - ref) < 4:
            ny += 1
    return x0, y0, nx, ny


def find_panes(img: Image.Image) -> list[Pane]:
    """Chart panes in the capture, largest first (no title read here)."""
    lum = np.asarray(img.convert("L"), dtype=np.uint8)
    h, w = lum.shape
    gh, gw = h // CELL, w // CELL
    if gh < 2 or gw < 2:
        return []
    cells = (lum[: gh * CELL, : gw * CELL] < DARK).reshape(gh, CELL, gw, CELL).mean(axis=(1, 3)) >= DARK_FRACTION
    panes: list[Pane] = []
    for _ in range(MAX_PANES):
        r0, c0, r1, c1 = _largest_rect(cells)
        if (c1 - c0) * CELL < MIN_W or (r1 - r0) * CELL < MIN_H:
            break
        cells[r0:r1, c0:c1] = False
        plot = (c0 * CELL, r0 * CELL, c1 * CELL, r1 * CELL)
        panes.append(Pane(plot=plot, box=_extend(lum, _snap(lum, plot))))
    return panes


def tesseract_reader(strip: Image.Image) -> str:
    """OCR of a title strip with the tesseract binary; "" when it is not installed or fails."""
    exe = shutil.which("tesseract")
    if not exe:
        return ""
    big = strip.convert("L").resize((strip.width * 3, strip.height * 3))
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    try:
        out = subprocess.run([exe, "stdin", "stdout", "--psm", "7"], input=buf.getvalue(),
                             capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.decode("utf-8", "replace")


def _titled(img: Image.Image, pane: Pane, reader: TitleReader) -> Pane:
    x0, y0, x1, _ = pane.box
    strip = img.crop((x0, max(0, y0 - TITLE_H), x1, y0))
    if strip.height < 8:
        return pane
    m = TITLE_RE.search(reader(strip).upper())
    return pane.model_copy(update={"symbol": m.group(1), "timeframe": m.group(2)}) if m else pane


def crop_chart(png: bytes, reader: TitleReader | None = None) -> CropResult:
    """Crop to the gold pane (else the largest); the full image with cropped=False when no pane is found."""
    try:
        raw = Image.open(io.BytesIO(png))
        raw.load()
        img = raw.convert("RGB")
    except (OSError, ValueError):
        return CropResult(png=png, cropped=False)
    panes = find_panes(img)
    if not panes:
        return CropResult(png=png, cropped=False)
    read = reader or tesseract_reader
    panes = [_titled(img, p, read) for p in panes]
    pick = next((p for p in panes if "XAU" in p.symbol), panes[0])
    out = io.BytesIO()
    img.crop(pick.box).save(out, format="PNG")
    known = pick.symbol != "unknown"
    return CropResult(png=out.getvalue(), cropped=True, symbol=pick.symbol, timeframe=pick.timeframe,
                      gold=("XAU" in pick.symbol) if known else None)


def cached_crop(state_dir: Path, reader: TitleReader | None = None) -> tuple[CropResult, float]:
    """The crop of state/chart/mt5.png and its capture time. mt5_chart.png and mt5_chart.json (atomic) are reused
    while their mtime equals the capture's, so each 60 s capture is cropped once. Raises OSError when no capture."""
    src = state_dir / "chart" / "mt5.png"
    captured = src.stat().st_mtime
    out, meta = src.with_name("mt5_chart.png"), src.with_name("mt5_chart.json")
    try:
        if out.stat().st_mtime == captured and meta.stat().st_mtime == captured:
            return CropResult(png=out.read_bytes(), **json.loads(meta.read_text())), captured
    except (OSError, ValueError, TypeError):
        pass
    res = crop_chart(src.read_bytes(), reader)
    for path, data in ((meta, json.dumps(res.model_dump(exclude={"png"})).encode()), (out, res.png)):
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.utime(tmp, (captured, captured))
        os.replace(tmp, path)
    return res, captured
