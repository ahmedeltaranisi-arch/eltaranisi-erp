"""ELTARANISI ERP — compact PDF generator (pure standard library).

Why hand-rolled: the deployment target runs on Python stdlib only (no pip), so
invoices must be produced server-side without reportlab/weasyprint.

Features
--------
* TrueType embedding (Type0 / CIDFontType2 / Identity-H, Flate compressed)
* Arabic shaping: contextual joining forms + lam-alef ligatures
* Light bidi: Arabic runs are laid out right-to-left, Latin/digit runs keep
  their internal order (so "1,250.00 EGP" stays readable inside Arabic text)
* ToUnicode CMap -> the PDF text is selectable / copyable / searchable
* Drawing primitives: text, rect, line, rounded box, table helper

The public API lives at the bottom of the file (`PDF`, `Doc`).
"""
from __future__ import annotations

import os
import zlib
from datetime import datetime

# --------------------------------------------------------------------------
# Arabic shaping data
# --------------------------------------------------------------------------
# joining classes: D = dual joining, R = right joining (connects to previous
# letter only), U = non joining.
JOIN = {}
for ch in "بتثجحخسشصضطظعغفقكلمنهيئ":
    JOIN[ch] = "D"
for ch in "اأإآؤدذرزو ةىء":
    JOIN[ch] = "R"
JOIN["ء"] = "U"
JOIN.update({"\u0640": "D", "\u0671": "R", "\u0622": "R"})

# base letter -> (isolated, final, initial, medial) presentation forms
FORMS = {
    "\u0621": (0xFE80, None, None, None),          # ء
    "\u0622": (0xFE81, 0xFE82, None, None),        # آ
    "\u0623": (0xFE83, 0xFE84, None, None),        # أ
    "\u0624": (0xFE85, 0xFE86, None, None),        # ؤ
    "\u0625": (0xFE87, 0xFE88, None, None),        # إ
    "\u0626": (0xFE89, 0xFE8A, 0xFE8B, 0xFE8C),    # ئ
    "\u0627": (0xFE8D, 0xFE8E, None, None),        # ا
    "\u0628": (0xFE8F, 0xFE90, 0xFE91, 0xFE92),    # ب
    "\u0629": (0xFE93, 0xFE94, None, None),        # ة
    "\u062A": (0xFE95, 0xFE96, 0xFE97, 0xFE98),    # ت
    "\u062B": (0xFE99, 0xFE9A, 0xFE9B, 0xFE9C),    # ث
    "\u062C": (0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0),    # ج
    "\u062D": (0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4),    # ح
    "\u062E": (0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8),    # خ
    "\u062F": (0xFEA9, 0xFEAA, None, None),        # د
    "\u0630": (0xFEAB, 0xFEAC, None, None),        # ذ
    "\u0631": (0xFEAD, 0xFEAE, None, None),        # ر
    "\u0632": (0xFEAF, 0xFEB0, None, None),        # ز
    "\u0633": (0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4),    # س
    "\u0634": (0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8),    # ش
    "\u0635": (0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC),    # ص
    "\u0636": (0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0),    # ض
    "\u0637": (0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4),    # ط
    "\u0638": (0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8),    # ظ
    "\u0639": (0xFEC9, 0xFECA, 0xFECB, 0xFECC),    # ع
    "\u063A": (0xFECD, 0xFECE, 0xFECF, 0xFED0),    # غ
    "\u0641": (0xFED1, 0xFED2, 0xFED3, 0xFED4),    # ف
    "\u0642": (0xFED5, 0xFED6, 0xFED7, 0xFED8),    # ق
    "\u0643": (0xFED9, 0xFEDA, 0xFEDB, 0xFEDC),    # ك
    "\u0644": (0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0),    # ل
    "\u0645": (0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4),    # م
    "\u0646": (0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8),    # ن
    "\u0647": (0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC),    # ه
    "\u0648": (0xFEED, 0xFEEE, None, None),        # و
    "\u0649": (0xFEEF, 0xFEF0, None, None),        # ى
    "\u064A": (0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4),    # ي
}
# lam + alef ligatures: (isolated, final)
LAM_ALEF = {
    "\u0622": (0xFEF5, 0xFEF6),
    "\u0623": (0xFEF7, 0xFEF8),
    "\u0625": (0xFEF9, 0xFEFA),
    "\u0627": (0xFEFB, 0xFEFC),
}
LAM = "\u0644"
TATWEEL = "\u0640"
# marks that transparently pass the joining state through
TRANSPARENT = set("\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0653\u0654\u0655\u0670\u0656\u0657\u0658\u06D6\u06D7\u06D8\u06D9\u06DA\u06DB\u06DC\u06DF\u06E0\u06E1\u06E2\u06E3\u06E4\u06E7\u06E8\u06EA\u06EB\u06EC\u06ED")

ARABIC_RANGES = (
    (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
)


def is_arabic(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in ARABIC_RANGES)


def _is_ltr(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in ".,:;%+-/#()[]&'\"_@")


def shape_arabic(text: str) -> str:
    """Return text converted to Arabic presentation forms (still logical order)."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch not in JOIN:
            out.append(ch)
            i += 1
            continue
        # lam + alef ligature
        if ch == LAM and i + 1 < n and text[i + 1] in LAM_ALEF:
            prev = _prev_joiner(text, i)
            iso, fin = LAM_ALEF[text[i + 1]]
            out.append(chr(fin if prev else iso))
            i += 2
            continue
        prev = _prev_joiner(text, i)
        nxt = _next_joiner(text, i)
        cls = JOIN.get(ch, "U")
        can_prev = prev is not None and JOIN.get(prev, "U") in ("D",)
        can_next = nxt is not None and cls == "D"
        forms = FORMS.get(ch)
        if forms is None:
            out.append(ch)
        else:
            iso, fin, init, med = forms
            if can_prev and can_next and med:
                out.append(chr(med))
            elif can_prev and fin:
                out.append(chr(fin))
            elif can_next and init:
                out.append(chr(init))
            else:
                out.append(chr(iso))
        i += 1
    return "".join(out)


def _prev_joiner(text, i):
    j = i - 1
    while j >= 0 and text[j] in TRANSPARENT:
        j -= 1
    if j < 0:
        return None
    ch = text[j]
    return ch if (ch in JOIN or ch == TATWEEL) else None


def _next_joiner(text, i):
    j = i + 1
    while j < len(text) and text[j] in TRANSPARENT:
        j += 1
    if j >= len(text):
        return None
    ch = text[j]
    return ch if (ch in JOIN or ch == TATWEEL) else None


# --- lightweight bidi (UAX #9 subset) --------------------------------------
# The generator draws left-to-right, so RTL text must be re-ordered by hand.
# A character is classified R (Arabic), L (Latin), N (number, internally LTR)
# or W (neutral: spaces, punctuation, separators).  Neutrals take the
# direction of their neighbours, which is what makes "كشف حساب — ح / إبراهيم"
# and "دفعة 350,000.00 ج.م" come out right instead of being chopped into
# isolated runs.

# Characters that no shipped face can draw are replaced (never shown as tofu).
GLYPH_FALLBACK = {
    "\u2705": "", "\u2714": "", "\u2713": "", "\u2611": "",      # ✅ ✔ ✓ ☑
    "\u26A0": "!", "\u2757": "!", "\u2755": "!",                   # ⚠ ❗ ❕
    "\u274C": "\u00D7", "\u2717": "\u00D7", "\u2718": "\u00D7",  # ❌ ✗ ✘
    "\u2B06": "^", "\u2B07": "v", "\u2191": "^", "\u2193": "v",
    "\u2022": "-", "\u25CF": "-", "\u25AA": "-",
}

RTL_NEUTRALS = set(" !\"'()[]{}<>|/\\.,:;-_—–ـ*+=%&#@~^$\u00A0\u060C\u061B\u061F\u066B\u066C\u2010\u2011\u2012\u2013\u2014\u2015\u2022\u2026\u00AB\u00BB")
MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{",
          "<": ">", ">": "<", "\u00AB": "\u00BB", "\u00BB": "\u00AB"}


def _bidi_class(ch: str) -> str:
    cp = ord(ch)
    if is_arabic(ch):
        return "R"
    if ch.isdigit() or 0x0660 <= cp <= 0x0669 or 0x06F0 <= cp <= 0x06F9:
        return "L"          # numbers are LTR and never joined to their neighbours
    if ch.isalpha():
        return "L"
    return "W"


def bidi_runs(text: str, base_rtl: bool = True):
    """Split *text* into directional runs, in LOGICAL order.

    Returns [(chunk, 'R' | 'L')].  Numbers stay 'L' (they are never reshaped or
    reversed); everything Arabic - including the punctuation sitting between
    two Arabic words - is 'R'.
    """
    if not text:
        return []
    classes = [_bidi_class(c) for c in text]
    base = "R" if base_rtl else "L"

    def strong(idx, step):
        j = idx + step
        while 0 <= j < len(classes):
            if classes[j] != "W":
                return classes[j]
            j += step
        return base

    resolved = []
    for i, cls in enumerate(classes):
        if cls != "W":
            resolved.append(cls)
            continue
        before, after = strong(i, -1), strong(i, +1)
        if before == after:                  # "a b", "برنامج كشف", "1,250.00"
            resolved.append(before)
        elif "R" in (before, after):         # Arabic wins a mixed sandwich
            resolved.append("R")
        else:
            resolved.append(base)

    runs, cur, cur_cls = [], [], None
    for ch, cls in zip(text, resolved):
        # blanks always form their own run: that keeps the gap *between* an
        # Arabic word and a number instead of pushing it to the outer edge
        this = "W" if ch == " " else cls
        if cur and this != cur_cls:
            runs.append(("".join(cur), cur_cls))
            cur = []
        cur.append(ch)
        cur_cls = this
    if cur:
        runs.append(("".join(cur), cur_cls))
    return runs


def visual_runs(text: str):
    """Directional runs in VISUAL order (RTL base): (text, is_rtl)."""
    out = []
    for chunk, cls in reversed(bidi_runs(text, True)):
        if cls == "R":
            out.append(("".join(MIRROR.get(c, c) for c in reversed(chunk)), True))
        else:
            out.append((chunk, False))
    return out


def visual_order(text: str, base_rtl: bool = True) -> str:
    """Visual (left-to-right drawing) order of *text* in a paragraph.

    base_rtl=True for Arabic-first paragraphs (the default in this app),
    False for an English/LTR paragraph or a bare Latin string.
    """
    if not base_rtl:
        return "".join(chunk for chunk, _ in bidi_runs(text, False))
    return "".join(chunk for chunk, _ in visual_runs(text))


# --------------------------------------------------------------------------
# TrueType loading
# --------------------------------------------------------------------------
class TrueType:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self.data = fh.read()
        d = self.data
        num = _u16(d, 4)
        self.tables = {}
        for i in range(num):
            o = 12 + 16 * i
            tag = d[o:o + 4].decode("latin1")
            self.tables[tag] = (_u32(d, o + 8), _u32(d, o + 12))
        head = self.tables["head"][0]
        self.units_per_em = _u16(d, head + 18)
        hhea = self.tables["hhea"][0]
        self.num_hmetrics = _u16(d, hhea + 34)
        self.ascender = _s16(d, hhea + 4)
        self.descender = _s16(d, hhea + 6)
        self.num_glyphs = _u16(d, self.tables["maxp"][0] + 4)
        self._cache = {}
        self._load_cmap()
        self._load_metrics()
        os2 = self.tables.get("OS/2")
        self.cap_height = _s16(d, os2[0] + 88) if os2 and os2[1] >= 90 else self.ascender

    # ---- cmap -----------------------------------------------------------
    def _load_cmap(self):
        d = self.data
        base = self.tables["cmap"][0]
        count = _u16(d, base + 2)
        subtables = []
        for i in range(count):
            pid, eid, off = _u16(d, base + 4 + 8 * i), _u16(d, base + 6 + 8 * i), _u32(d, base + 8 + 8 * i)
            subtables.append((pid, eid, base + off))
        best = None
        for pid, eid, off in subtables:
            if pid in (0, 3) and eid in (1, 4, 10):
                best = off
        if best is None:
            raise ValueError("no usable cmap in " + self.path)
        self._cmap_off = best
        self._cmap_fmt = _u16(d, best)

    def gid(self, ch: str) -> int:
        cp = ord(ch)
        if cp in self._cache:
            return self._cache[cp]
        d = self.data
        off = self._cmap_off
        fmt = self._cmap_fmt
        g = 0
        if fmt == 4:
            seg_x2 = _u16(d, off + 6)
            seg = seg_x2 // 2
            ends = off + 14
            starts = ends + seg_x2 + 2
            deltas = starts + seg_x2
            ranges = deltas + seg_x2
            for i in range(seg):
                end = _u16(d, ends + 2 * i)
                if cp > end:
                    continue
                start = _u16(d, starts + 2 * i)
                if cp < start:
                    break
                delta = _s16(d, deltas + 2 * i)
                ro = _u16(d, ranges + 2 * i)
                if ro == 0:
                    g = (cp + delta) & 0xFFFF
                else:
                    addr = ranges + 2 * i + ro + 2 * (cp - start)
                    g = _u16(d, addr)
                    if g:
                        g = (g + delta) & 0xFFFF
                break
        elif fmt == 12:
            groups = _u32(d, off + 12)
            lo, hi = 0, groups - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                go = off + 16 + 12 * mid
                s, e, sg = _u32(d, go), _u32(d, go + 4), _u32(d, go + 8)
                if cp < s:
                    hi = mid - 1
                elif cp > e:
                    lo = mid + 1
                else:
                    g = sg + (cp - s)
                    break
        self._cache[cp] = g
        return g

    # ---- metrics --------------------------------------------------------
    def _load_metrics(self):
        d = self.data
        hmtx = self.tables["hmtx"][0]
        self._adv = []
        for i in range(self.num_hmetrics):
            self._adv.append(_u16(d, hmtx + 4 * i))

    def width(self, g: int) -> int:
        if g < self.num_hmetrics:
            return self._adv[g]
        return self._adv[-1] if self._adv else 0


def _u16(d, o):
    return int.from_bytes(d[o:o + 2], "big")


def _s16(d, o):
    return int.from_bytes(d[o:o + 2], "big", signed=True)


def _u32(d, o):
    return int.from_bytes(d[o:o + 4], "big")


# --------------------------------------------------------------------------
# PDF writer
# --------------------------------------------------------------------------
# Per-typeface optical scale (measured against Arabic x-height/cap-height).
# Amiri is a naskh whose glyphs are small on the em square, so it needs +16%.
OPTICAL_SCALE = {
    "amiri-regular": 1.16,
    "amiri": 1.16,
}


def _optical_scale(font_path: str) -> float:
    stem = os.path.splitext(os.path.basename(str(font_path)))[0].lower()
    return OPTICAL_SCALE.get(stem, 1.0)


def _esc(text: str) -> bytes:
    return text.encode("latin-1")


class PDF:
    """Multi-font PDF writer: Amiri for Arabic, DejaVu Sans for Latin/digits."""

    def __init__(self, font_path: str, latin_font_path: str | None = None,
                 width=595.28, height=841.89, compress=True, ar_scale: float | None = None,
                 heading_font_path: str | None = None):
        # index 0 = Arabic body face, 1 = Latin/digits, 2 = optional Arabic
        # heading (display) face: a heavy display typeface is right for the title
        # band but wrong for a 45-column table, so the two can differ.
        self.fonts = [TrueType(font_path)]
        if latin_font_path and os.path.exists(latin_font_path):
            self.fonts.append(TrueType(latin_font_path))
        self.ar_font = 0
        self.la_font = 1 if len(self.fonts) > 1 else 0
        self.heading_font = None
        if heading_font_path and os.path.exists(str(heading_font_path)):
            self.fonts.append(TrueType(str(heading_font_path)))
            self.heading_font = len(self.fonts) - 1
        # optical size correction: the typeface's glyphs sit high/low on the em
        # square, so a nominal 10pt would not *look* like 10pt.
        self.AR_SCALE = ar_scale if ar_scale else _optical_scale(font_path)
        self.HEAD_SCALE = _optical_scale(heading_font_path) if heading_font_path else 1.0
        self.ar_scale = self.AR_SCALE
        self.W = width
        self.H = height
        self.compress = compress
        self.pages = []
        self._ops = []
        self.used = [{} for _ in self.fonts]     # per font: gid -> char
        self.meta = {"Title": "", "Author": "ELTARANISI ERP", "Creator": "ELTARANISI ERP"}

    # ---- primitives -----------------------------------------------------
    def _emit(self, op: str):
        self._ops.append(op)

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.8, radius=0.0):
        if fill:
            r, g, b = [c / 255 for c in fill]
            self._emit(f"{r:.4f} {g:.4f} {b:.4f} rg")
        if stroke:
            r, g, b = [c / 255 for c in stroke]
            self._emit(f"{r:.4f} {g:.4f} {b:.4f} RG {lw:.2f} w")
        if radius:
            k = radius * 0.5523
            p = [f"{x + radius:.2f} {y:.2f} m",
                 f"{x + w - radius:.2f} {y:.2f} l",
                 f"{x + w - radius + k:.2f} {y:.2f} {x + w:.2f} {y + radius - k:.2f} {x + w:.2f} {y + radius:.2f} c",
                 f"{x + w:.2f} {y + h - radius:.2f} l",
                 f"{x + w:.2f} {y + h - radius + k:.2f} {x + w - radius + k:.2f} {y + h:.2f} {x + w - radius:.2f} {y + h:.2f} c",
                 f"{x + radius:.2f} {y + h:.2f} l",
                 f"{x + radius - k:.2f} {y + h:.2f} {x:.2f} {y + h - radius + k:.2f} {x:.2f} {y + h - radius:.2f} c",
                 f"{x:.2f} {y + radius:.2f} l",
                 f"{x:.2f} {y + radius - k:.2f} {x + radius - k:.2f} {y:.2f} {x + radius:.2f} {y:.2f} c",
                 "h"]
            self._emit(" ".join(p))
            self._emit("B" if (fill and stroke) else ("f" if fill else "S"))
        else:
            self._emit(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re")
            self._emit("B" if (fill and stroke) else ("f" if fill else "S"))

    def line(self, x1, y1, x2, y2, color=(210, 216, 228), lw=0.8, dash=None):
        r, g, b = [c / 255 for c in color]
        d = f"[{dash} {dash}] 0 d " if dash else ""
        self._emit(f"{d}{r:.4f} {g:.4f} {b:.4f} RG {lw:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")
        if dash:
            self._emit("[] 0 d")

    # ---- text layout ----------------------------------------------------
    @staticmethod
    def _is_arabic_char(ch: str) -> bool:
        return is_arabic(ch) or ch in TRANSPARENT

    def _split_font(self, chunk: str, ar_index: int | None = None):
        """[(substring, font_index)] - Arabic picks the Arabic face.

        A character the chosen face cannot draw (an emoji, a tick, a rare
        symbol) is re-routed to the other face and finally replaced by a safe
        equivalent - never drawn as a blank "tofu" box.
        """
        out = []
        ar_index = self.ar_font if ar_index is None else ar_index
        for ch in chunk:
            fi = ar_index if self._is_arabic_char(ch) else self.la_font
            if self.fonts[fi].gid(ch) == 0:
                other = self.la_font if fi == self.ar_font else self.ar_font
                if other != fi and self.fonts[other].gid(ch) != 0:
                    fi = other
                else:
                    ch = GLYPH_FALLBACK.get(ch, "")
                    if ch == "":
                        continue
            if out and out[-1][1] == fi:
                out[-1][0] += ch
            else:
                out.append([ch, fi])
        return [(c, f) for c, f in out]

    def _runs(self, text: str, rtl: bool, heading: bool = False):
        """[(chunk, font_index)] in VISUAL (drawing) order."""
        text = text.strip()                  # outer padding is layout, not content
        shaped = shape_arabic(text)
        chunks = bidi_runs(shaped, rtl)
        if rtl:
            chunks = list(reversed(chunks))      # RTL base: last run is drawn first
        runs = []
        for chunk, cls in chunks:
            if cls == "R":
                # Arabic inside an English line keeps its own right-to-left
                # order; only the runs themselves follow the paragraph base
                chunk = "".join(MIRROR.get(c, c) for c in reversed(chunk))
            target = self.heading_font if (heading and self.heading_font is not None) else None
            runs.extend(self._split_font(chunk, target))
        return runs

    AR_SCALE = 1.0       # body face optical scale (see OPTICAL_SCALE)
    HEAD_SCALE = 1.0     # heading face optical scale

    def _run_size(self, fi: int, size: float) -> float:
        if fi == self.ar_font:
            return size * self.AR_SCALE
        if self.heading_font is not None and fi == self.heading_font:
            return size * self.HEAD_SCALE
        return size

    def _run_width(self, chunk: str, fi: int, size: float) -> float:
        font = self.fonts[fi]
        total = 0
        for ch in chunk:
            w = font.width(font.gid(ch))
            if ch == " ":
                w += 120                      # a little extra air between words
            total += w
        return total * self._run_size(fi, size) / font.units_per_em

    def measure(self, text: str, size: float, rtl: bool | None = None, heading: bool = False) -> float:
        if text is None:
            return 0.0
        text = str(text)
        if rtl is None:
            rtl = any(is_arabic(c) for c in text)
        return sum(self._run_width(chunk, fi, size) for chunk, fi in self._runs(text, rtl, heading))

    def text(self, x, y, text, size=10, color=(17, 24, 39), align="left", rtl=None, heading=False):
        if text is None or text == "":
            return 0.0
        text = str(text)
        if rtl is None:
            rtl = any(is_arabic(c) for c in text)
        runs = self._runs(text, rtl, heading)
        total = sum(self._run_width(chunk, fi, size) for chunk, fi in runs)
        if align == "right":
            x = x - total
        elif align == "center":
            x = x - total / 2
        r, g, b = [c / 255 for c in color]
        cx = x
        for chunk, fi in runs:
            hexes = []
            font = self.fonts[fi]
            for ch in chunk:
                gid = font.gid(ch)
                if gid:
                    self.used[fi].setdefault(gid, ch)
                hexes.append(f"{gid:04X}")
            if hexes:
                self._emit("BT")
                self._emit(f"/F{fi + 1} {self._run_size(fi, size):.2f} Tf")
                self._emit(f"{r:.4f} {g:.4f} {b:.4f} rg")
                self._emit(f"1 0 0 1 {cx:.2f} {y:.2f} Tm")
                self._emit(f"<{''.join(hexes)}> Tj")
                self._emit("ET")
            cx += self._run_width(chunk, fi, size)
        return total

    def paragraph(self, x, y, w, text, size=10, color=(17, 24, 39), rtl=True, leading=1.45):
        words = str(text).split(" ")
        line, lines = "", []
        for word in words:
            probe = (line + " " + word).strip()
            if self.measure(probe, size, rtl) <= w or not line:
                line = probe
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        for ln in lines:
            self.text(x + w, y, ln, size, color, align="right" if rtl else "left", rtl=rtl)
            y -= size * leading
        return y

    # ---- pages ----------------------------------------------------------
    def new_page(self):
        if self._ops:
            self.pages.append("\n".join(self._ops).encode("latin-1"))
        self._ops = []

    def to_bytes(self):
        if self._ops:
            self.pages.append("\n".join(self._ops).encode("latin-1"))
        self._ops = []
        return self._build()

    def save(self, path: str):
        with open(path, "wb") as fh:
            fh.write(self.to_bytes())
        return path

    def _build(self) -> bytes:
        objs = {}

        def add(body: bytes) -> int:
            n = len(objs) + 1
            objs[n] = body
            return n

        catalog = add(b"")
        pages_obj = add(b"")
        # reserve font objects: per font -> (Type0, CIDFont, FontFile2, ToUnicode)
        font_objs = []
        for f in self.fonts:
            font_objs.append({
                "type0": add(b""), "cid": add(b""), "file": add(b""), "tounicode": add(b""),
                "descriptor": add(b""),
            })

        page_ids = []
        for stream in self.pages:
            raw = zlib.compress(stream) if self.compress else stream
            body = (b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(raw)) + raw + b"\nendstream"
            content = add(body)
            fonts_res = " ".join(f"/F{i + 1} {fo['type0']} 0 R" for i, fo in enumerate(font_objs))
            page = add((
                f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {self.W:.2f} {self.H:.2f}] "
                f"/Resources << /Font << {fonts_res} >> >> /Contents {content} 0 R >>"
            ).encode("latin-1"))
            page_ids.append(page)

        objs[pages_obj] = (
            f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>"
        ).encode("latin-1")
        objs[catalog] = (f"<< /Type /Catalog /Pages {pages_obj} 0 R "
                         f"/ViewerPreferences << /Direction /R2L >> >>").encode("latin-1")

        for i, font in enumerate(self.fonts):
            fo = font_objs[i]
            stem = os.path.splitext(os.path.basename(font.path))[0]
            base = "".join(ch for ch in stem if ch.isalnum()) or f"Font{i}"
            scale = 1000 / font.units_per_em
            widths = " ".join(f"{gid} [{font.width(gid) * scale:.0f}]"
                              for gid, _ in sorted(self.used[i].items()))
            objs[fo["type0"]] = (
                f"<< /Type /Font /Subtype /Type0 /BaseFont /{base} /Encoding /Identity-H "
                f"/DescendantFonts [{fo['cid']} 0 R] /ToUnicode {fo['tounicode']} 0 R >>"
            ).encode("latin-1")
            objs[fo["descriptor"]] = (
                f"<< /Type /FontDescriptor /FontName /{base} /Flags 4 "
                f"/FontBBox [-700 -400 1500 1200] /ItalicAngle 0 /Ascent {font.ascender} "
                f"/Descent {font.descender} /CapHeight {font.cap_height} /StemV 80 "
                f"/FontFile2 {fo['file']} 0 R >>"
            ).encode("latin-1")
            objs[fo["cid"]] = (
                f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{base} /CIDSystemInfo "
                f"<< /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
                f"/FontDescriptor {fo['descriptor']} 0 R /DW 600 /W [{widths}] /CIDToGIDMap /Identity >>"
            ).encode("latin-1")
            fontdata = zlib.compress(font.data)
            objs[fo["file"]] = (
                (f"<< /Length {len(fontdata)} /Length1 {len(font.data)} /Filter /FlateDecode >>\nstream\n"
                 ).encode("latin-1") + fontdata + b"\nendstream"
            )
            objs[fo["tounicode"]] = self._tounicode_cmap(i)

        def pdftext(v):
            v = str(v or "")
            return "".join(ch if 32 <= ord(ch) < 127 else " " for ch in v).replace("(", " ").replace(")", " ")

        info = (f"<< /Title ({pdftext(self.meta['Title'])}) /Author ({pdftext(self.meta['Author'])}) "
                f"/Creator ({pdftext(self.meta['Creator'])}) /Producer (ELTARANISI ERP) "
                f"/CreationDate (D:{datetime.now().strftime('%Y%m%d%H%M%S')}) >>")
        info_obj = add(info.encode("latin-1"))

        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for n in sorted(objs):
            offsets[n] = len(out)
            out += f"{n} 0 obj\n".encode("latin-1") + objs[n] + b"\nendobj\n"
        xref_at = len(out)
        size = max(objs) + 1
        out += f"xref\n0 {size}\n".encode("latin-1")
        out += b"0000000000 65535 f \n"
        for n in range(1, size):
            out += f"{offsets.get(n, 0):010d} 00000 n \n".encode("latin-1")
        out += (f"trailer\n<< /Size {size} /Root {catalog} 0 R /Info {info_obj} 0 R >>\n"
                f"startxref\n{xref_at}\n%%EOF\n").encode("latin-1")
        return bytes(out)

    def _tounicode_cmap(self, index: int) -> bytes:
        entries = []
        for gid, ch in sorted(self.used[index].items()):
            try:
                u = ch.encode("utf-16-be").hex().upper()
            except Exception:
                u = "003F"
            entries.append(f"<{gid:04X}> <{u}>")
        body = "\n".join(entries)
        cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
                "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
                f"{len(entries)} beginbfchar\n{body}\nendbfchar\n"
                "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
        raw = cmap.encode("latin-1")
        return (b"<< /Length %d >>\nstream\n" % len(raw)) + raw + b"\nendstream"
