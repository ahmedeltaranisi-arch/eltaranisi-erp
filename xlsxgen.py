"""ELTARANISI ERP — minimal XLSX writer (pure standard library).

Produces a real Office Open XML workbook (`.xlsx`) that opens in Excel,
Numbers, LibreOffice and Google Sheets, with:

* right-to-left sheet view for Arabic workbooks
* styled header row, banded rows, frozen panes, auto filter
* number formats (money / date / percent) applied as real cell types
* column widths, merged title rows, and a repeating print header

No third-party dependencies.
"""
from __future__ import annotations

import zipfile
from datetime import date, datetime
from xml.sax.saxutils import escape

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_REL = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

MONEY_FMT = 4      # built-in #,##0.00
DATE_FMT = 14      # built-in short date
PCT_FMT = 10       # 0.00%


def _col(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA"""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell(ref: str, value, style: int = 0, fmt=None) -> str:
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}"{s} t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN
            value = 0
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    if isinstance(value, (datetime, date)):
        serial = (value - date(1899, 12, 30)).days
        return f'<c r="{ref}"{s}><v>{serial}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


class Workbook:
    def __init__(self, rtl: bool = True, title: str = "", creator: str = "ELTARANISI ERP",
                 font_size: float = 11.0, font_name: str = "Calibri"):
        self.sheets = []          # (name, rows, options)
        self.rtl = rtl
        self.title = title
        self.creator = creator
        self.font_size = float(font_size or 11.0)
        self.font_name = font_name or "Calibri"

    def add_sheet(self, name: str, rows, widths=None, freeze: int = 1,
                  header_rows: int = 1, money_cols=(), date_cols=(), pct_cols=(),
                  merged_title: str | None = None, wrap_cols=(), row_height: float | None = None):
        self.sheets.append({
            "name": (name or "Sheet")[:31],
            "rows": rows,
            "widths": widths,
            "freeze": freeze,
            "header_rows": header_rows,
            "money": set(money_cols),
            "date": set(date_cols),
            "pct": set(pct_cols),
            "merged_title": merged_title,
            "wrap": set(wrap_cols),
            "row_height": row_height,
        })

    # ------------------------------------------------------------------
    def to_bytes(self) -> bytes:
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", self._content_types())
            z.writestr("_rels/.rels", self._root_rels())
            z.writestr("docProps/core.xml", self._core())
            z.writestr("docProps/app.xml", self._app())
            z.writestr("xl/workbook.xml", self._workbook())
            z.writestr("xl/_rels/workbook.xml.rels", self._workbook_rels())
            z.writestr("xl/styles.xml", self._styles())
            z.writestr("xl/sharedStrings.xml", self._empty_strings())
            for i, sheet in enumerate(self.sheets, start=1):
                z.writestr(f"xl/worksheets/sheet{i}.xml", self._sheet_xml(sheet))
        return buf.getvalue()

    def save(self, path: str) -> str:
        with open(path, "wb") as fh:
            fh.write(self.to_bytes())
        return path

    # ---- package parts -------------------------------------------------
    def _content_types(self):
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(self.sheets) + 1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                + overrides + '</Types>')

    def _root_rels(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                '</Relationships>')

    def _core(self):
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f'<dc:title>{escape(self.title)}</dc:title><dc:creator>{escape(self.creator)}</dc:creator>'
                f'<cp:lastModifiedBy>{escape(self.creator)}</cp:lastModifiedBy>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
                '</cp:coreProperties>')

    def _app(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                '<Application>ELTARANISI ERP</Application></Properties>')

    def _workbook(self):
        sheets = "".join(
            f'<sheet name="{escape(s["name"])}" sheetId="{i}" r:id="rId{i}"/>'
            for i, s in enumerate(self.sheets, start=1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<workbook {_NS} {_REL}>'
                f'<workbookPr/>'
                f'<bookViews><workbookView rightToLeft="{1 if self.rtl else 0}"/></bookViews>'
                f'<sheets>{sheets}</sheets></workbook>')

    def _workbook_rels(self):
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(self.sheets) + 1))
        n = len(self.sheets) + 1
        rels += (f'<Relationship Id="rId{n}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                 f'<Relationship Id="rId{n+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>')
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')

    def _empty_strings(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<sst {_NS} count="0" uniqueCount="0"/>')

    def _styles(self):
        fs = self.font_size
        name = (self.font_name or "Calibri").replace('"', "")
        fonts = ('<fonts count="5">'
                 f'<font><sz val="{fs:g}"/><name val="{name}"/></font>'
                 f'<font><b/><sz val="{fs:g}"/><color rgb="FFFFFFFF"/><name val="{name}"/></font>'
                 f'<font><b/><sz val="{max(12, fs + 4):g}"/><color rgb="FF1E1B4B"/><name val="{name}"/></font>'
                 f'<font><b/><sz val="{fs:g}"/><color rgb="FF111827"/><name val="{name}"/></font>'
                 f'<font><i/><sz val="{max(7, fs - 1):g}"/><color rgb="FF6B7280"/><name val="{name}"/></font>'
                 '</fonts>')
        fills = ('<fills count="4">'
                 '<fill><patternFill patternType="none"/></fill>'
                 '<fill><patternFill patternType="gray125"/></fill>'
                 '<fill><patternFill patternType="solid"><fgColor rgb="FF4F35D8"/><bgColor indexed="64"/></patternFill></fill>'
                 '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F1FE"/><bgColor indexed="64"/></patternFill></fill>'
                 '</fills>')
        borders = ('<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
                   '<border><left/><right/><top/><bottom style="thin"><color rgb="FFE5E7EB"/></bottom><diagonal/></border>'
                   '</borders>')
        numfmts = ('<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00;[Red]-#,##0.00"/></numFmts>')
        cellxfs = ('<cellXfs count="7">'
                   '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
                   '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>'
                   '<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1"/>'
                   '<xf numFmtId="164" fontId="0" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1"/>'
                   '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
                   '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" '
                   'applyAlignment="1"><alignment horizontal="right" vertical="center" wrapText="1"/>'
                   '</xf>'
                   '</cellXfs>')
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<styleSheet {_NS}>' + numfmts + fonts + fills + borders +
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                + cellxfs + '</styleSheet>')

    def _sheet_xml(self, sheet) -> str:
        rows = sheet["rows"]
        cols = max((len(r) for r in rows), default=1)
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               f'<worksheet {_NS} {_REL}>',
               f'<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>',
               f'<sheetViews><sheetView rightToLeft="{1 if self.rtl else 0}" '
               f'workbookViewId="0" showGridLines="0">'
               ]
        if sheet["freeze"]:
            out.append('<pane ySplit="%d" topLeftCell="A%d" activePane="bottomLeft" state="frozen"/>'
                       % (sheet["freeze"], sheet["freeze"] + 1))
        out.append('</sheetView></sheetViews>')
        out.append('<sheetFormatPr defaultRowHeight="18"/>')
        widths = sheet["widths"] or [18] * cols
        out.append("<cols>" + "".join(
            f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)) + "</cols>")
        out.append("<sheetData>")
        base_h = sheet.get("row_height") or max(15.0, self.font_size + 9)
        for r_idx, row in enumerate(rows, start=1):
            style_row = 1 if r_idx <= sheet["header_rows"] else 0
            height = max(base_h + 8, 22) if style_row else base_h
            out.append(f'<row r="{r_idx}" ht="{height}" customHeight="1">')
            for c_idx, value in enumerate(row):
                ref = f"{_col(c_idx)}{r_idx}"
                if style_row:
                    out.append(_cell(ref, value, style=1))
                    continue
                style = 0
                if c_idx in sheet["wrap"]:
                    style = 6
                elif c_idx in sheet["money"]:
                    style = 2
                elif c_idx in sheet["date"]:
                    style = 2
                elif c_idx in sheet["pct"]:
                    style = 2
                    if isinstance(value, (int, float)):
                        value = value / 100.0
                out.append(_cell(ref, value, style=style))
            out.append("</row>")
        out.append("</sheetData>")
        if sheet["header_rows"] and len(rows) > sheet["header_rows"]:
            out.append(f'<autoFilter ref="A{sheet["header_rows"]}:{_col(cols-1)}{len(rows)}"/>')
        out.append(f'<printOptions horizontalCentered="1"/>')
        out.append(f'<pageMargins left="0.4" right="0.4" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>')
        out.append(f'<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>')
        out.append('</worksheet>')
        return "".join(out)
