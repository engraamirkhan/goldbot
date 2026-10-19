"""MT5 capture cropping: only the chart pane (plus price scale and time axis) is served, never the terminal chrome."""
import io
import os

import numpy as np
from PIL import Image, ImageDraw

from goldbot.api import mt5_crop

W, H = 1280, 800
TILES = {  # name: plot rectangle (x0, y0, x1, y1); each has a 20 px title bar above, a 60 px scale right, 20 px axis below
    "a": (230, 80, 660, 360), "b": (730, 80, 1160, 360),
    "c": (230, 440, 560, 720), "d": (730, 440, 1160, 720),
}
GOLD_MARK = (255, 0, 255)


def screen(gold: str | None = "b", dialog: bool = True, tiles=TILES) -> bytes:
    """A real-like terminal: menu, toolbar, light Market Watch, Toolbox, 4 tiled charts, a dialog over tile d."""
    im = Image.new("RGB", (W, H), (236, 233, 216))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, 50), fill=(240, 240, 240))           # menu + toolbar
    d.rectangle((0, 50, 215, 760), fill=(255, 255, 255))      # Market Watch + Navigator
    d.rectangle((0, 760, W, H), fill=(250, 250, 250))         # Toolbox / mailbox
    for name, (x0, y0, x1, y1) in tiles.items():
        d.rectangle((x0, y0 - 20, x1 + 60, y0), fill=GOLD_MARK if name == gold else (60, 90, 160))
        d.rectangle((x0, y0, x1, y1), fill=(0, 0, 0))
        d.rectangle((x1, y0, x1 + 60, y1), fill=(255, 255, 255))
        d.rectangle((x0, y1, x1 + 60, y1 + 20), fill=(255, 255, 255))
        for i in range(x0 + 10, x1 - 10, 14):                  # sparse candles and a grid line
            d.line((i, y0 + 60, i, y0 + 120), fill=(0, 200, 0))
        d.line((x0, (y0 + y1) // 2, x1, (y0 + y1) // 2), fill=(70, 70, 70))
    if dialog:
        d.rectangle((900, 520, 1120, 640), fill=(236, 233, 216), outline=(0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def reader(strip: Image.Image) -> str:
    """Stands in for OCR: the gold tile's title bar carries the marker colour."""
    return "XAUUSD,M15" if GOLD_MARK in {strip.getpixel((2, y)) for y in range(strip.height)} else "EURUSD,H1"


def near(png: bytes, w: int, h: int) -> bool:
    """Size within one detection cell either way (the synthetic rectangles are drawn inclusive of both edges)."""
    iw, ih = Image.open(io.BytesIO(png)).size
    return abs(iw - w) <= mt5_crop.CELL and abs(ih - h) <= mt5_crop.CELL


def test_four_tiles_and_a_dialog_are_cropped_to_the_gold_pane_with_scale_and_axis():
    r = mt5_crop.crop_chart(screen("b"), reader)
    assert r.cropped and r.symbol == "XAUUSD" and r.timeframe == "M15" and r.gold is True
    assert near(r.png, 430 + 60, 280 + 20)


def test_gold_pane_is_preferred_over_a_larger_one():
    r = mt5_crop.crop_chart(screen("c", dialog=False), reader)   # tile c is the smallest
    assert r.gold is True and near(r.png, 330 + 60, 280 + 20)


def test_largest_pane_is_taken_when_the_symbol_cannot_be_read():
    r = mt5_crop.crop_chart(screen("b"), lambda strip: "")
    assert r.cropped and r.symbol == "unknown" and r.gold is None
    assert near(r.png, 430 + 60, 280 + 20)


def test_a_non_gold_symbol_is_reported_not_gold():
    r = mt5_crop.crop_chart(screen(gold=None, dialog=False), reader)
    assert r.cropped and r.symbol == "EURUSD" and r.timeframe == "H1" and r.gold is False


def test_cropped_image_has_no_terminal_chrome():
    r = mt5_crop.crop_chart(screen("a", dialog=False), reader)
    px = np.asarray(Image.open(io.BytesIO(r.png)).convert("RGB")).reshape(-1, 3)
    for chrome in ((236, 233, 216), (240, 240, 240)):
        assert not (px == chrome).all(axis=1).any()


def test_no_pane_serves_the_full_image_uncropped():
    blank = io.BytesIO()
    Image.new("RGB", (W, H), (255, 255, 255)).save(blank, format="PNG")
    r = mt5_crop.crop_chart(blank.getvalue(), reader)
    assert not r.cropped and r.png == blank.getvalue() and r.gold is None


def test_unreadable_image_is_served_as_is():
    r = mt5_crop.crop_chart(b"\x89PNG\r\n\x1a\nfake", reader)
    assert not r.cropped and r.png == b"\x89PNG\r\n\x1a\nfake"


def test_crop_is_cached_per_capture_and_written_atomically(tmp_path):
    (tmp_path / "chart").mkdir()
    src = tmp_path / "chart" / "mt5.png"
    src.write_bytes(screen("b"))
    r, captured = mt5_crop.cached_crop(tmp_path, reader)
    assert r.gold is True and (tmp_path / "chart" / "mt5_chart.png").read_bytes() == r.png
    assert not [p for p in (tmp_path / "chart").iterdir() if ".png." in p.name or ".json." in p.name]
    again, _ = mt5_crop.cached_crop(tmp_path, lambda s: "EURUSD,H1")   # same capture: no recompute
    assert again == r
    os.utime(src, (captured + 60, captured + 60))
    fresh, _ = mt5_crop.cached_crop(tmp_path, lambda s: "EURUSD,H1")    # new capture: recomputed
    assert fresh.symbol == "EURUSD"


def single_window_screen() -> bytes:
    """Synthetic 1280x800 layout of the real terminal: one child window "XAUUSD,M15" (title bar y 80-98, plot
    x 367-1228 / y 105-430, price scale to x 1278, time axis y 430-446) among toolbar, Market Watch, Navigator, Toolbox."""
    im = Image.new("RGB", (W, H), (236, 233, 216))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, 78), fill=(240, 240, 240))              # menu + toolbars
    d.rectangle((0, 80, 360, 400), fill=(255, 255, 255))          # Market Watch
    d.rectangle((0, 405, 360, 600), fill=(250, 250, 250))         # Navigator
    d.rectangle((0, 610, W, H), fill=(252, 252, 252))             # Toolbox
    d.rectangle((367, 80, 1278, 98), fill=GOLD_MARK)              # title bar
    d.rectangle((367, 105, 1227, 429), fill=(0, 0, 0))            # plot
    for i in range(380, 1220, 12):
        d.line((i, 200, i, 260), fill=(200, 0, 0))
    d.rectangle((1228, 105, 1277, 429), fill=(255, 255, 255))     # price scale
    d.rectangle((367, 430, 1277, 445), fill=(255, 255, 255))      # time axis
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_single_chart_window_among_terminal_panels_is_cropped_to_plot_scale_and_axis():
    r = mt5_crop.crop_chart(single_window_screen(), reader)
    assert r.cropped and r.symbol == "XAUUSD" and r.gold is True
    assert near(r.png, 1278 - 367, 446 - 105)
    px = np.asarray(Image.open(io.BytesIO(r.png)).convert("RGB")).reshape(-1, 3)
    for chrome in ((236, 233, 216), (240, 240, 240), (250, 250, 250), GOLD_MARK):
        assert not (px == chrome).all(axis=1).any()
