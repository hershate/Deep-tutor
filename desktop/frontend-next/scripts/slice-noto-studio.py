#!/usr/bin/env python3
"""Cut the core face out of public/fonts/noto-studio.woff2.

The core face carries what a first screen asks for: the common simplified-Chinese
set shipped as NotoSansSC-subset.woff2, Latin, punctuation and arrows. The full
font stays as a later family in the stack, so a character the core lacks still
finds its glyph there and only then pays for the download.

Usage: slice-noto-studio.py <full.woff2> <core-out.woff2>
Needs fontTools and brotli.
"""
import sys
from fontTools import subset
from fontTools.ttLib import TTFont

COMMON = "NotoSansSC-subset.woff2"
BLOCKS = [(0x20, 0x24F), (0x2000, 0x206F), (0x2190, 0x23FF),
          (0x2500, 0x257F), (0x25A0, 0x25FF), (0x2600, 0x27BF), (0x3000, 0x303F), (0xFF00, 0xFFEF)]


def main(full_path, core_path):
    full = set(TTFont(full_path).getBestCmap())
    common = set(TTFont(full_path.replace("noto-studio.woff2", COMMON)).getBestCmap())
    core = {p for p in full if p in common or any(a <= p <= b for a, b in BLOCKS)}
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["*"]
    opts.notdef_outline = True
    font = subset.load_font(full_path, opts)
    s = subset.Subsetter(opts)
    s.populate(unicodes=sorted(core))
    s.subset(font)
    subset.save_font(font, core_path, opts)
    print(f"core: {len(core)} of {len(full)} characters")


if __name__ == "__main__":
    main(*sys.argv[1:3])
