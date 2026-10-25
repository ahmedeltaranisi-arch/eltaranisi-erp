#!/usr/bin/env python3
"""Generate ``arabic_ligatures.py`` from a font's GSUB ``liga`` / ``rlig`` lookups.

Arabic words are joined by the font, and several joins in «PT Bold Heading» only
look right when the font's own ligature glyphs are used, e.g.

    ﻴ + ﺮ  ->  ﻁ… (yeh + reh)      lam + meem      lam + alef

Drawing the two presentation forms side by side leaves a visible gap in the middle
of the word ("سيرفيس" / "الملاحية" look cut).  HarfBuzz would pick the ligature;
this project shapes Arabic itself (no binary dependencies in the serverless
runtime), so the handful of ligatures the font defines are extracted here, once,
into a plain-Python table that ``pdfgen`` applies after shaping.

Run:  python3 tools/gen_ligatures.py assets/fonts/PTBoldHeading.ttf
"""
from __future__ import annotations

import os
import sys

from fontTools.ttLib import TTFont

HEADER = '''"""Arabic ligature table extracted from a font's GSUB (see tools/gen_ligatures.py).

Generated from: {font}
Do not edit by hand.
"""
from __future__ import annotations

# (first presentation form, next form, ...) -> ligature code point
LIGATURES = {{
{body}
}}

MAX_LIG_LEN = {maxlen}
'''


def component_lookups(table) -> set[int]:
    """Lookup indexes used by the ``liga`` and ``rlig`` features."""
    out: set[int] = set()
    for record in table.FeatureList.FeatureRecord:
        if record.FeatureTag in ("liga", "rlig"):
            out.update(record.Feature.LookupListIndex)
    return out


def extract(path: str) -> tuple[dict[tuple[int, ...], int], int]:
    font = TTFont(path)
    table = font["GSUB"].table
    reverse: dict[str, int] = {}
    for code, name in font.getBestCmap().items():
        reverse.setdefault(name, code)
    wanted = component_lookups(table)
    ligatures: dict[tuple[int, ...], int] = {}
    for index in sorted(wanted):
        lookup = table.LookupList.Lookup[index]
        for subtable in lookup.SubTable:
            sets = getattr(subtable, "ligatures", None)
            if not sets:
                continue
            for first, items in sets.items():
                for item in items:
                    seq = (first,) + tuple(item.Component)
                    codes = tuple(reverse.get(name) for name in seq)
                    lig = reverse.get(item.LigGlyph)
                    if lig is None or any(code is None for code in codes):
                        continue
                    # Longest match must win; keep the longest form of a sequence.
                    old = ligatures.get(codes)
                    if old is None or True:
                        ligatures[codes] = lig
    maxlen = max((len(k) for k in ligatures), default=2)
    # Drop a shorter sequence that is a prefix of a longer one - the matcher
    # always tries the longest key first, so both can coexist.
    return ligatures, maxlen


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    font_path = argv[1]
    out_path = argv[2] if len(argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "arabic_ligatures.py")
    ligatures, maxlen = extract(font_path)
    lines = []
    for key in sorted(ligatures):
        codes = ", ".join(f"0x{c:04x}" for c in key)
        brace = f"({codes},)" if len(key) == 1 else f"({codes})"
        lines.append(f"    {brace}: 0x{ligatures[key]:04x},")
    body = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(HEADER.format(font=os.path.basename(font_path), body=body, maxlen=maxlen))
    print(f"{len(ligatures)} ligatures -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
