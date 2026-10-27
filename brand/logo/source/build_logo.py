"""Builds the TalentRadar logo masters: the Sweep symbol, its small-size cut, the wordmark and the lockups.

The symbol is pure geometry. The wordmark is Inter (SIL Open Font License) at weight 600, optical size 32,
outlined into paths, so no file depends on an installed font.

    python brand/logo/source/build_logo.py --inter path/to/Inter[opsz,wght].ttf

Needs fontTools (pip install fonttools). Writes brand/logo/master/.
"""
from __future__ import annotations

import argparse
import math
import os

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

BLUE = "#0A84FF"     # system blue, the app's --accent
INK = "#1D1D1F"      # wordmark on light backgrounds
PAPER = "#F5F5F7"    # wordmark on dark backgrounds, the app's --ink
WORD = "TalentRadar"
TRACKING = -0.012    # em; Inter's display cut, tightened a touch for logo size

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "master"))


def num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


# ----------------------------------------------------------------------------- symbol

def pol(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    """Point at radius r, deg degrees clockwise from straight up."""
    a = math.radians(deg)
    return cx + r * math.sin(a), cy - r * math.cos(a)


def circle(cx: float, cy: float, r: float) -> str:
    return (f"M{num(cx - r)} {num(cy)} A{num(r)} {num(r)} 0 1 1 {num(cx + r)} {num(cy)} "
            f"A{num(r)} {num(r)} 0 1 1 {num(cx - r)} {num(cy)} Z")


def sweep(px: float, py: float, radius: float, beam: tuple[float, float, float],
          blip: tuple[float, float, float]) -> str:
    """A quarter disc pivoting at its lower-left corner, the radar beam cut in as one radial slot
    (angle, width, inner radius; round inner end) and the blip as a hole (angle, distance, radius)."""
    g, w, r0 = beam
    a = math.radians(g)
    dx, dy = math.sin(a), -math.cos(a)
    nx, ny = math.cos(a), math.sin(a)
    t = math.sqrt(radius * radius - (w / 2) ** 2)

    def side(s: int, d: float) -> tuple[float, float]:
        return px + s * nx * w / 2 + d * dx, py + s * ny * w / 2 + d * dy

    (ax, ay), (bx, by) = side(-1, t), side(-1, r0)
    (cx, cy), (ex, ey) = side(1, r0), side(1, t)
    r = num(radius)
    d = (f"M{num(px)} {num(py)} V{num(py - radius)} A{r} {r} 0 0 1 {num(ax)} {num(ay)} "
         f"L{num(bx)} {num(by)} A{num(w / 2)} {num(w / 2)} 0 0 0 {num(cx)} {num(cy)} "
         f"L{num(ex)} {num(ey)} A{r} {r} 0 0 1 {num(px + radius)} {num(py)} Z")
    ang, dist, br = blip
    return d + " " + circle(*pol(px, py, dist, ang), br)


# Master on a 256 grid: the quadrant spans 40..216 both ways, beam at 30 degrees, blip at 62 degrees.
SYMBOL = sweep(40, 216, 176, beam=(30, 20, 64), blip=(62, 116, 26))
# Small-size cut (16-24 px): a wider, shorter beam and a bigger blip, kept a pixel apart at 16 px so they
# don't merge into a "6".
SYMBOL_SMALL = sweep(40, 216, 176, beam=(30, 28, 104), blip=(62, 108, 30))
SYMBOL_BOX = (40.0, 40.0, 216.0, 216.0)


# ----------------------------------------------------------------------------- wordmark

def pair_kern(font: TTFont, left: str, right: str) -> int:
    """Horizontal kerning for one glyph pair from the GPOS pair-adjustment lookups."""
    total = 0
    for lookup in font["GPOS"].table.LookupList.Lookup:
        for sub in lookup.SubTable:
            if lookup.LookupType == 9:
                if sub.ExtensionLookupType != 2:
                    continue
                sub = sub.ExtSubTable
            elif lookup.LookupType != 2:
                continue
            if left not in sub.Coverage.glyphs:
                continue
            if sub.Format == 1:
                pset = sub.PairSet[sub.Coverage.glyphs.index(left)]
                for rec in pset.PairValueRecord:
                    if rec.SecondGlyph == right and rec.Value1 is not None:
                        total += int(getattr(rec.Value1, "XAdvance", 0) or 0)
                        break
            else:
                c1 = sub.ClassDef1.classDefs.get(left, 0)
                c2 = sub.ClassDef2.classDefs.get(right, 0)
                rec = sub.Class1Record[c1].Class2Record[c2]
                if rec.Value1 is not None:
                    total += int(getattr(rec.Value1, "XAdvance", 0) or 0)
    return total


def wordmark(font: TTFont, cap: float, x0: float, baseline: float) -> tuple[str, float]:
    """Outline WORD with its cap height scaled to `cap`; returns (path data, advance width)."""
    upm = int(font["head"].unitsPerEm)
    scale = cap / float(font["OS/2"].sCapHeight)
    glyphs = font.getGlyphSet()
    names = [font.getBestCmap()[ord(ch)] for ch in WORD]
    hmtx = font["hmtx"]
    pen = SVGPathPen(glyphs, ntos=num)
    x = 0.0
    for i, name in enumerate(names):
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x0 + x * scale, baseline)))
        x += hmtx[name][0] + TRACKING * upm
        if i + 1 < len(names):
            x += pair_kern(font, name, names[i + 1])
    return str(pen.getCommands()), (x - TRACKING * upm) * scale


def side_bearings(font: TTFont, cap: float) -> tuple[float, float]:
    """Left side bearing of the first letter and right side bearing of the last, scaled to `cap`."""
    scale = cap / float(font["OS/2"].sCapHeight)
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()

    def x_bounds(name: str) -> tuple[float, float]:
        bp = BoundsPen(glyphs)
        glyphs[name].draw(bp)
        x_min, _, x_max, _ = bp.bounds
        return float(x_min), float(x_max)

    first, last = cmap[ord(WORD[0])], cmap[ord(WORD[-1])]
    return x_bounds(first)[0] * scale, (float(font["hmtx"][last][0]) - x_bounds(last)[1]) * scale


# ----------------------------------------------------------------------------- files

def svg(name: str, w: float, h: float, body: str) -> None:
    w, h = math.ceil(w), math.ceil(h)  # whole-unit canvases; the padding absorbs the fraction
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {num(w)} {num(h)}" width="{num(w)}" '
                 f'height="{num(h)}" role="img" aria-labelledby="title"><title id="title">TalentRadar</title>'
                 f"{body}</svg>\n")


def symbol_at(x: float, y: float, size: float, fill: str) -> str:
    """The symbol placed so its quadrant spans `size` units with its top-left corner at (x, y)."""
    s = size / (SYMBOL_BOX[2] - SYMBOL_BOX[0])
    return (f'<g id="symbol" transform="translate({num(x - SYMBOL_BOX[0] * s)} {num(y - SYMBOL_BOX[1] * s)}) '
            f'scale({num(s)})"><path fill="{fill}" fill-rule="evenodd" d="{SYMBOL}"/></g>')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inter", required=True, help="Inter variable TTF (opsz, wght axes)")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    font = instancer.instantiateVariableFont(TTFont(args.inter), {"wght": 600, "opsz": 32})
    tones = (("light", INK), ("dark", PAPER))
    pad = 48

    for d, base in ((SYMBOL, "talentradar-symbol"), (SYMBOL_SMALL, "talentradar-symbol-small")):
        svg(f"{base}.svg", 256, 256, f'<path fill="{BLUE}" fill-rule="evenodd" d="{d}"/>')

    # Horizontal: symbol 1.6 cap heights tall, centred on the cap band; gap 0.56 cap heights.
    cap, sym, gap, h = 100, 160, 56, 256
    lsb, rsb = side_bearings(font, cap)
    baseline = h / 2 + cap / 2
    word_x = pad + sym + gap - lsb
    for tone, fill in tones:
        word, adv = wordmark(font, cap, word_x, baseline)
        svg(f"talentradar-horizontal-{tone}.svg", word_x + adv - rsb + pad, h,
            symbol_at(pad, (h - sym) / 2, sym, BLUE) + f'<path id="wordmark" fill="{fill}" d="{word}"/>')

    # Stacked: symbol 2.4 cap heights tall, centred over the wordmark; gap 0.8 cap heights.
    cap_s, sym_s, gap_s = 64, 154, 51
    lsb_s, rsb_s = side_bearings(font, cap_s)
    _, adv_s = wordmark(font, cap_s, 0, 0)
    w_s = adv_s - lsb_s - rsb_s + 2 * pad
    h_s = pad + sym_s + gap_s + cap_s + pad
    for tone, fill in tones:
        word, _ = wordmark(font, cap_s, pad - lsb_s, pad + sym_s + gap_s + cap_s)
        svg(f"talentradar-stacked-{tone}.svg", w_s, h_s,
            symbol_at((w_s - sym_s) / 2, pad, sym_s, BLUE) + f'<path id="wordmark" fill="{fill}" d="{word}"/>')

    # Wordmark only: cap height 100 on a 256-high canvas.
    for tone, fill in tones:
        word, adv = wordmark(font, cap, pad - lsb, baseline)
        svg(f"talentradar-wordmark-{tone}.svg", adv - lsb - rsb + 2 * pad, h,
            f'<path id="wordmark" fill="{fill}" d="{word}"/>')
    print("wrote", ", ".join(sorted(os.listdir(OUT))))


if __name__ == "__main__":
    main()
