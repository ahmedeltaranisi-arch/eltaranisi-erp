"""Eltaranisi ERP — document exports.

* Tax invoice PDF (Arabic RTL by default, or English) with embedded Arabic font
* Tax invoice Excel workbook
* E-invoicing-ready JSON document (UUID + SHA-256 digest, clearly marked
  "not signed" — signing/transmission requires the Egyptian Tax Authority
  accredited integration and a registered certificate)
* Any report as PDF or Excel
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import date, datetime
from pathlib import Path

import pdfgen
import xlsxgen

BASE_DIR = Path(__file__).resolve().parent
FONT_PATH = BASE_DIR / "assets" / "fonts" / "Amiri-Regular.ttf"
LATIN_FONT_PATH = BASE_DIR / "assets" / "fonts" / "DejaVuSans.ttf"

# palette
INK = (17, 24, 39)
MUTED = (107, 114, 128)
LINE = (226, 232, 240)
BRAND = (79, 53, 216)
BRAND_SOFT = (243, 241, 255)
OK = (16, 122, 87)
DANGER = (185, 28, 28)
WARN = (180, 83, 9)
WHITE = (255, 255, 255)

STATUS_AR = {
    "draft": "مسودة", "posted": "مرحّلة", "paid": "مدفوعة",
    "partially_paid": "مدفوعة جزئياً", "cancelled": "ملغاة", "reversed": "معكوسة",
    "approved": "معتمدة", "review": "قيد المراجعة",
}
STATUS_EN = {
    "draft": "Draft", "posted": "Posted", "paid": "Paid",
    "partially_paid": "Partially paid", "cancelled": "Cancelled", "reversed": "Reversed",
    "approved": "Approved", "review": "In review",
}

# ---------------------------------------------------------------- helpers
ONES = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة",
        "عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر",
        "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
TENS = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
HUNDREDS = ["", "مائة", "مائتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة",
            "ثمانمائة", "تسعمائة"]


def _below_thousand(n: int) -> str:
    parts = []
    h, rest = divmod(n, 100)
    if h:
        parts.append(HUNDREDS[h])
    if rest:
        if rest < 20:
            parts.append(ONES[rest])
        else:
            t, o = divmod(rest, 10)
            parts.append((ONES[o] + " و" if o else "") + TENS[t])
    return " و".join(parts)


def tafqeet(amount_cents: int, currency_ar="جنيهاً مصرياً", currency_en="EGP") -> str:
    """Arabic amount-in-words (integer part)."""
    value = abs(int(amount_cents)) // 100
    piastres = abs(int(amount_cents)) % 100
    if value == 0 and piastres == 0:
        return f"صفر {currency_ar} فقط لا غير"
    groups = [(10 ** 9, "مليار", "مليارات"), (10 ** 6, "مليون", "ملايين"), (1000, "ألف", "آلاف")]
    words = []
    rest = value
    for size, singular, plural in groups:
        if rest >= size:
            count, rest = divmod(rest, size)
            if count == 1:
                words.append(singular)
            elif count == 2:
                words.append(singular + "ان" if size != 1000 else "ألفان")
            elif count <= 10:
                words.append(_below_thousand(count) + " " + plural)
            else:
                words.append(_below_thousand(count) + " " + singular)
    if rest:
        words.append(_below_thousand(rest))
    text = " و".join(w for w in words if w)
    out = f"{text} {currency_ar}"
    if piastres:
        out += f" و{_below_thousand(piastres)} قرشاً"
    return out + " فقط لا غير"


def money(cents) -> str:
    return f"{(int(cents or 0) / 100):,.2f}"


def fmt_date(value) -> str:
    if not value:
        return "—"
    return str(value)[:10]


def _rstrip_zero(qty_milli) -> str:
    q = int(qty_milli or 0) / 1000
    return f"{q:,.3f}".rstrip("0").rstrip(".") if q % 1 else f"{int(q):,}"


# ---------------------------------------------------------------- company
def company_block(conn, company_id: int) -> dict:
    row = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    settings = {r["key"]: r["value"] for r in
                conn.execute("SELECT key, value FROM settings WHERE company_id=?", (company_id,))}
    c = dict(row) if row else {}
    c["vat_number"] = c.get("tax_number") or settings.get("tax_number") or ""
    c["email"] = settings.get("company_email", "")
    c["website"] = settings.get("company_website", "")
    c["bank_details"] = settings.get("bank_details", "")
    c["invoice_footer"] = settings.get("invoice_footer", "")
    c["invoice_terms"] = settings.get("invoice_terms", "")
    return c


# ---------------------------------------------------------------- invoice data
def invoice_data(conn, company_id: int, kind: str, invoice_id: int) -> dict:
    """Load an invoice with lines + party + payments, ready for rendering."""
    if kind == "sales":
        inv = conn.execute("""SELECT i.*, c.name_ar AS party_ar, c.name_en AS party_en, c.code AS party_code,
                c.tax_number AS party_tax, c.address AS party_address, c.phone AS party_phone,
                c.email AS party_email, u.full_name_ar AS created_by_ar
            FROM sales_invoices i JOIN customers c ON c.id=i.customer_id
            LEFT JOIN users u ON u.id=i.created_by
            WHERE i.id=? AND i.company_id=?""", (invoice_id, company_id)).fetchone()
        lines = conn.execute("""SELECT l.*, p.sku, p.name_ar AS product_ar, p.name_en AS product_en, p.unit
            FROM sales_invoice_lines l LEFT JOIN products p ON p.id=l.product_id
            WHERE l.invoice_id=? ORDER BY l.line_no""", (invoice_id,)).fetchall()
        allocs = conn.execute("""SELECT a.amount_cents, p.payment_no, p.payment_date, p.method
            FROM payment_allocations a JOIN payments p ON p.id=a.payment_id
            WHERE a.invoice_kind='sales' AND a.invoice_id=? ORDER BY p.payment_date""", (invoice_id,)).fetchall()
    else:
        inv = conn.execute("""SELECT i.*, v.name_ar AS party_ar, v.name_en AS party_en, v.code AS party_code,
                v.tax_number AS party_tax, v.address AS party_address, v.phone AS party_phone,
                v.email AS party_email, u.full_name_ar AS created_by_ar
            FROM purchase_invoices i JOIN vendors v ON v.id=i.vendor_id
            LEFT JOIN users u ON u.id=i.created_by
            WHERE i.id=? AND i.company_id=?""", (invoice_id, company_id)).fetchone()
        lines = conn.execute("""SELECT l.*, p.sku, p.name_ar AS product_ar, p.name_en AS product_en, p.unit
            FROM purchase_invoice_lines l LEFT JOIN products p ON p.id=l.product_id
            WHERE l.invoice_id=? ORDER BY l.line_no""", (invoice_id,)).fetchall()
        allocs = conn.execute("""SELECT a.amount_cents, p.payment_no, p.payment_date, p.method
            FROM payment_allocations a JOIN payments p ON p.id=a.payment_id
            WHERE a.invoice_kind='purchase' AND a.invoice_id=? ORDER BY p.payment_date""", (invoice_id,)).fetchall()
    if inv is None:
        raise LookupError("invoice not found")
    return {"kind": kind, "invoice": dict(inv), "lines": [dict(r) for r in lines],
            "allocations": [dict(r) for r in allocs]}


def _einvoice_payload(company, data) -> dict:
    inv = data["invoice"]
    lines = []
    for ln in data["lines"]:
        lines.append({
            "description": ln.get("product_ar") or ln.get("description") or "",
            "itemCode": ln.get("sku") or "",
            "quantity": round((ln.get("qty_milli") or 0) / 1000, 3),
            "unitPrice": round((ln.get("unit_price_cents") or 0) / 100, 2),
            "discount": round((ln.get("discount_cents") or 0) / 100, 2),
            "taxRate": round((ln.get("tax_rate_bp") or 0) / 100, 2),
            "lineTotal": round((ln.get("line_total_cents") or 0) / 100, 2),
            "taxAmount": round((ln.get("line_tax_cents") or 0) / 100, 2),
        })
    payload = {
        "documentType": "I" if data["kind"] == "sales" else "I",
        "documentVersion": "1.0",
        "issuer": {
            "name": company.get("name_ar"),
            "nameEn": company.get("name_en"),
            "registrationNumber": company.get("tax_number") or "",
            "address": company.get("address") or "",
            "phone": company.get("phone") or "",
        },
        "receiver": {
            "name": data["invoice"].get("party_ar"),
            "nameEn": data["invoice"].get("party_en"),
            "registrationNumber": data["invoice"].get("party_tax") or "",
            "address": data["invoice"].get("party_address") or "",
        },
        "document": {
            "internalId": data["invoice"].get("invoice_no"),
            "dateTimeIssued": f'{fmt_date(data["invoice"].get("invoice_date"))}T00:00:00Z',
            "dueDate": fmt_date(data["invoice"].get("due_date")),
            "currency": company.get("base_currency") or "EGP",
            "status": data["invoice"].get("status"),
            "lineCount": len(lines),
            "totalSalesAmount": round((data["invoice"].get("subtotal_cents") or 0) / 100, 2),
            "totalDiscountAmount": round((data["invoice"].get("discount_cents") or 0) / 100, 2),
            "totalTaxAmount": round((data["invoice"].get("tax_cents") or 0) / 100, 2),
            "totalAmount": round((data["invoice"].get("total_cents") or 0) / 100, 2),
        },
        "invoiceLines": lines,
        "readiness": {
            "signed": False,
            "note": "Unsigned readiness document — transmission to the tax authority requires an "
                    "accredited ETA integration and a registered e-seal certificate.",
            "noteAr": "مستند جاهزية غير موقّع — الإرسال إلى مصلحة الضرائب يتطلب تكاملًا معتمدًا وشهادة ختم إلكتروني.",
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["documentUuid"] = hashlib.sha256(canonical).hexdigest()[:32]
    payload["digest"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return payload



def brand_mark(pdf, x, y, size=30, mono=False):
    """Vector brand mark: rounded gradient-ish square with the letter ط."""
    top = (124, 58, 237) if not mono else (255, 255, 255)
    bot = (37, 99, 235) if not mono else (235, 235, 250)
    pdf.rect(x, y, size, size, fill=top, radius=size * 0.28)
    pdf.rect(x, y, size, size * 0.45, fill=bot, radius=size * 0.28)
    pdf.rect(x, y + size * 0.35, size, size * 0.15, fill=top)
    pdf.text(x + size / 2, y + size * 0.24, "ط", size * 0.62, (255, 255, 255), align="center", rtl=True)


# ---------------------------------------------------------------- invoice PDF
def invoice_pdf(conn, company_id: int, kind: str, invoice_id: int, lang: str = "ar") -> bytes:
    data = invoice_data(conn, company_id, kind, invoice_id)
    company = company_block(conn, company_id)
    inv = data["invoice"]
    rtl = lang != "en"
    ar = rtl

    def T(key_ar, key_en):
        return key_ar if ar else key_en

    pdf = pdfgen.PDF(str(FONT_PATH), str(LATIN_FONT_PATH))
    W, H = pdf.W, pdf.H
    M = 36.0
    pdf.meta["Title"] = f'{(inv.get("invoice_no") or "invoice")} — {company.get("name_en") or ""}'
    pdf.new_page()

    # ---------- header band ----------
    pdf.rect(0, H - 118, W, 118, fill=(38, 27, 92))
    pdf.rect(0, H - 122, W, 4, fill=BRAND)
    name = (company.get("name_ar") if ar else company.get("name_en")) or "Eltaranisi ERP"
    pdf.text(W - M, H - 46, name, 17, WHITE, align="right", rtl=rtl)
    sub = (company.get("name_en") if ar else company.get("name_ar")) or ""
    pdf.text(W - M, H - 64, sub, 9.5, (205, 200, 240), align="right", rtl=not rtl)
    contact = " · ".join(x for x in [company.get("address"), company.get("phone"),
                                     f'{T("الرقم الضريبي", "Tax No.")}: {company.get("tax_number") or "—"}'] if x)
    pdf.text(W - M, H - 82, contact, 8.5, (196, 191, 235), align="right", rtl=not ar and _has_ar(contact))
    brand_mark(pdf, M + 2, H - 58, 30)
    doc_title = T("فاتورة ضريبية", "TAX INVOICE") if kind == "sales" else T("فاتورة مشتريات", "PURCHASE INVOICE")
    pdf.text(M + 40, H - 46, doc_title, 14, WHITE, align="left", rtl=ar)
    status = inv.get("status") or "draft"
    badge_text = STATUS_AR.get(status, status) if ar else STATUS_EN.get(status, status)
    bw = pdf.measure(badge_text, 9) + 22
    pdf.rect(M + 2, H - 88, bw, 20, fill=(255, 255, 255), radius=10)
    pdf.text(M + 2 + bw / 2, H - 82, badge_text, 8.6, (38, 27, 92), align="center", rtl=rtl)

    y = H - 150
    # ---------- meta boxes ----------
    box_h = 96
    pdf.rect(M, y - box_h, W - 2 * M, box_h, fill=(250, 250, 253), stroke=LINE, radius=10)
    split_x = M + (W - 2 * M) * 0.56
    pdf.line(split_x, y - 10, split_x, y - box_h + 10, color=LINE, dash=3)

    party_label = T("فاتورة إلى", "BILL TO") if kind == "sales" else T("المورّد", "SUPPLIER")
    pdf.text(W - M - 12, y - 20, party_label, 8, MUTED, align="right", rtl=rtl)
    party_name = (inv.get("party_ar") if ar else inv.get("party_en")) or inv.get("party_ar") or ""
    pdf.text(W - M - 12, y - 36, party_name, 11.5, INK, align="right", rtl=rtl)
    pmeta = " · ".join(x for x in [inv.get("party_code"),
                                   (f'{T("رقم ضريبي", "Tax No.")}: {inv["party_tax"]}' if inv.get("party_tax") else ""),
                                   inv.get("party_phone")] if x)
    pdf.text(W - M - 12, y - 51, pmeta, 8.2, MUTED, align="right", rtl=rtl)
    if inv.get("party_address"):
        pdf.text(W - M - 12, y - 65, str(inv["party_address"])[:60], 8.2, MUTED, align="right", rtl=rtl)

    # right column (in RTL: the left side of the box) - invoice facts
    fx = M + 14
    facts = [
        ((T("رقم الفاتورة", "Invoice No.")), str(inv.get("invoice_no") or "")),
        ((T("التاريخ", "Invoice date")), fmt_date(inv.get("invoice_date"))),
        ((T("تاريخ الاستحقاق", "Due date")), fmt_date(inv.get("due_date"))),
    ]
    if kind == "purchase":
        facts.append((T("مرجع المورد", "Supplier ref"), str(inv.get("supplier_ref") or "—")))
    fy = y - 22
    for label, value in facts:
        pdf.text(fx + 110, fy, label, 8, MUTED, align="left", rtl=rtl)
        pdf.text(fx, fy, value, 9.5, INK, align="left", rtl=False)
        fy -= 17

    # ---------- items table ----------
    y = y - box_h - 26
    cols_ar = [
        ("#", 26, "center"),
        (T("الصنف / الوصف", "Item / description"), 0, "right"),
        (T("الكمية", "Qty"), 52, "center"),
        (T("السعر", "Price"), 66, "right"),
        (T("الخصم", "Disc."), 54, "right"),
        (T("الضريبة", "VAT"), 60, "right"),
        (T("الإجمالي", "Total"), 78, "right"),
    ]
    fixed = sum(c[1] for c in cols_ar if c[1])
    cols_ar[1] = (cols_ar[1][0], (W - 2 * M) - fixed, "right")
    row_h = 22
    # header
    pdf.rect(M, y - row_h, W - 2 * M, row_h, fill=(38, 27, 92), radius=6)
    x = W - M if rtl else M
    header_positions = []
    for label, width, align in cols_ar:
        if rtl:
            cx = x - width / 2 if align == "center" else x - 8
            pdf.text(cx, y - 15, label, 8.6, WHITE, align="center" if align == "center" else "right", rtl=rtl)
            header_positions.append((x - width, width, align))
            x -= width
        else:
            cx = x + width / 2 if align == "center" else x + 8
            pdf.text(cx, y - 15, label, 8.6, WHITE, align="center" if align == "center" else "left", rtl=rtl)
            header_positions.append((x, width, align))
            x += width
    y -= row_h
    total_h = 0
    for idx, ln in enumerate(data["lines"]):
        desc_ar = ln.get("product_ar") or ln.get("description") or ""
        desc_en = ln.get("product_en") or ln.get("description") or ""
        desc = desc_ar if ar else desc_en
        if ln.get("sku"):
            desc = f'{desc}'
        desc_width = cols_ar[1][1] - 16
        wrapped = _wrap(pdf, desc, 8.8, desc_width, rtl)
        h = max(row_h, 12 + 11 * len(wrapped))
        if y - h < 210:      # page break
            pdf.new_page()
            y = H - 60
        if idx % 2 == 0:
            pdf.rect(M, y - h, W - 2 * M, h, fill=(249, 249, 253))
        ty = y - 14
        for wi, line_text in enumerate(wrapped):
            if rtl:
                pdf.text(W - M - 8 - cols_ar[0][1], ty - wi * 11, line_text, 8.8, INK, align="right", rtl=rtl)
            else:
                pdf.text(M + cols_ar[0][1] + 8, ty - wi * 11, line_text, 8.8, INK, align="left", rtl=rtl)
        values = [
            str(ln.get("line_no")),
            None,  # description already drawn
            _rstrip_zero(ln.get("qty_milli")),
            money(ln.get("unit_price_cents")),
            money(ln.get("discount_cents")),
            f'{money(ln.get("line_tax_cents"))}',
            money(ln.get("line_total_cents")),
        ]
        for ci, (left, width, align) in enumerate(header_positions):
            if ci == 1:
                continue
            val = values[ci]
            if not val:
                continue
            if align == "center":
                pdf.text(left + width / 2, ty, val, 8.8, INK, align="center", rtl=False)
            else:
                pdf.text(left + width - 8, ty, val, 8.8, INK, align="right", rtl=False)
        pdf.line(M, y - h, W - M, y - h, color=(238, 240, 246))
        y -= h
        total_h += h

    # ---------- totals ----------
    y -= 10
    if y < 330:                     # keep totals + words + footer on one page
        pdf.new_page()
        y = H - 70
    totals_w = 250
    tx = W - M - totals_w if rtl else M
    rows = [
        (T("الإجمالي قبل الضريبة", "Subtotal"), inv.get("subtotal_cents")),
        (T("الخصم", "Discount"), -(inv.get("discount_cents") or 0)),
        (T("ضريبة القيمة المضافة", "VAT"), inv.get("tax_cents")),
        (T("الإجمالي المستحق", "Total due"), inv.get("total_cents")),
        (T("المدفوع", "Paid"), inv.get("paid_cents")),
    ]
    remaining = (inv.get("total_cents") or 0) - (inv.get("paid_cents") or 0)
    ry = y
    for label, value in rows:
        pdf.text(tx + totals_w - 10, ry, money(value), 9.4, INK, align="right", rtl=False)
        pdf.text(tx + 10, ry, label, 8.8, MUTED, align="left", rtl=rtl)
        ry -= 17
    pdf.line(tx, ry + 6, tx + totals_w, ry + 6, color=LINE)
    pdf.rect(tx, ry - 26, totals_w, 24, fill=BRAND_SOFT, stroke=(214, 208, 250), radius=8)
    pdf.text(tx + totals_w - 10, ry - 19, money(remaining), 11, BRAND, align="right", rtl=False)
    pdf.text(tx + 12, ry - 19, T("المتبقي", "Balance due"), 9, BRAND, align="left", rtl=rtl)

    # amount in words
    wy = max(ry - 52, 232)
    if ar:
        words = tafqeet(remaining or inv.get("total_cents") or 0)
        pdf.text(W - M, wy + 14, T("المبلغ كتابةً", "Amount in words"), 7.6, MUTED, align="right", rtl=True)
        for i, line in enumerate(_wrap(pdf, words, 8.6, W - 2 * M, True)):
            pdf.text(W - M, wy - i * 12, line, 8.6, INK, align="right", rtl=True)
    else:
        pdf.text(M, wy + 14, "Amount in words", 7.6, MUTED, align="left", rtl=False)
        pdf.text(M, wy, f"{money(remaining or inv.get('total_cents') or 0)} {company.get('base_currency') or 'EGP'}", 8.6, INK, align="left", rtl=False)

    # ---------- payment notes ----------
    notes_y = 214
    if data["allocations"]:
        pdf.text(W - M, notes_y, T("الدفعات المسجلة", "Recorded payments"), 8, MUTED, align="right", rtl=rtl)
        py = notes_y - 13
        for a in data["allocations"][:3]:
            txt = f'{a["payment_no"]} · {fmt_date(a["payment_date"])} · {money(a["amount_cents"])}'
            pdf.text(W - M, py, txt, 8, INK, align="right", rtl=False)
            py -= 12
    if inv.get("notes"):
        pdf.text(M, notes_y, T("ملاحظات", "Notes"), 8, MUTED, align="left", rtl=rtl)
        ny = notes_y - 13
        for line in _wrap(pdf, str(inv["notes"]), 8, (W - 2 * M) * 0.45, rtl)[:3]:
            pdf.text(M, ny, line, 8, INK, align="left", rtl=rtl)
            ny -= 12

    # ---------- e-invoicing readiness ----------
    e = _einvoice_payload(company, data)
    by = 150
    pdf.rect(M, by, W - 2 * M, 46, fill=(250, 250, 253), stroke=LINE, radius=8)
    pdf.text(W - M - 10, by + 30, T("جاهزية الفاتورة الإلكترونية", "E-invoicing readiness"), 8.6, INK,
             align="right", rtl=rtl)
    pdf.text(W - M - 10, by + 16, f'UUID: {e["documentUuid"][:24]}…   ·   SHA-256: {e["digest"][7:23]}…', 7.6,
             MUTED, align="right", rtl=False)
    pdf.text(W - M - 10, by + 6,
             T("مستند غير موقّع — يلزم تكامل معتمد مع مصلحة الضرائب", "Unsigned document — accredited ETA integration required"),
             7.2, WARN, align="right", rtl=rtl)

    # ---------- footer (three tidy rows, nothing overlaps) ----------
    pdf.line(M, 112, W - M, 112, color=LINE)
    half = (W - 2 * M) - 14
    footer_text = company.get("invoice_footer") or T(
        "شكراً لتعاملكم معنا — هذه الفاتورة صادرة إلكترونياً من نظام الطرانيسي ERP",
        "Thank you — this invoice was generated by Eltaranisi ERP")
    foot_ar = _has_ar(footer_text)
    for i, line in enumerate(_wrap(pdf, footer_text, 7.4, half * 0.66, foot_ar)[:2]):
        pdf.text(W - M, 98 - i * 10, line, 7.4, MUTED, align="right", rtl=foot_ar)
    if company.get("invoice_terms"):
        terms = company["invoice_terms"]
        # Don't glue an English label onto Arabic terms — mixed-direction garbage.
        has_ar = any("\u0600" <= ch <= "\u06ff" for ch in terms)
        # Label follows the language of the *content* so mixed bidi never scrambles it.
        prefix = "شروط السداد: " if has_ar else "Terms: "
        for i, line in enumerate(_wrap(pdf, prefix + terms, 7.0, half * 0.66, has_ar)[:2]):
            pdf.text(W - M, 74 - i * 9, line, 7.0, MUTED, align="right", rtl=has_ar)
    if company.get("bank_details"):
        bank_ar = _has_ar(company["bank_details"])
        for i, line in enumerate(_wrap(pdf, company["bank_details"], 7.0, half * 0.32, bank_ar)[:2]):
            pdf.text(M, 98 - i * 10, line, 7.0, MUTED, align="left", rtl=bank_ar)
    # Two independent segments: the actor name keeps its own direction, the
    # timestamp+site stays LTR — no mixed-bidi scrambling on either side.
    pdf.text(W - M, 54, f'{T("أُنشئت بواسطة", "Generated by")}: {inv.get("created_by_ar") or "—"}',
             6.8, MUTED, align="right", rtl=rtl)
    pdf.text(M, 54, f'{datetime.now().strftime("%Y-%m-%d %H:%M")} · {company.get("website") or "eltaranisi.com"}',
             6.8, MUTED, align="left", rtl=False)
    return pdf.to_bytes()


def _has_ar(text) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in str(text or ""))


def _wrap(pdf, text, size, width, rtl):
    words = str(text or "").split(" ")
    lines, cur = [], ""
    for w in words:
        probe = (cur + " " + w).strip()
        if pdf.measure(probe, size, rtl) <= width or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


# ---------------------------------------------------------------- invoice XLSX
def invoice_xlsx(conn, company_id: int, kind: str, invoice_id: int, lang: str = "ar") -> bytes:
    data = invoice_data(conn, company_id, kind, invoice_id)
    company = company_block(conn, company_id)
    inv = data["invoice"]
    ar = lang != "en"
    T = (lambda a, e: a) if ar else (lambda a, e: e)

    wb = xlsxgen.Workbook(rtl=ar, title=str(inv.get("invoice_no")))
    rows = []
    rows.append([(company.get("name_ar") if ar else company.get("name_en"))])
    rows.append([(T("فاتورة ضريبية", "TAX INVOICE") if kind == "sales" else T("فاتورة مشتريات", "PURCHASE INVOICE"))])
    rows.append([f'{T("الرقم الضريبي", "Tax No.")}: {company.get("tax_number") or "—"}  ·  {company.get("address") or ""}'])
    rows.append([])
    rows.append([T("رقم الفاتورة", "Invoice No."), inv.get("invoice_no"),
                 T("التاريخ", "Date"), inv.get("invoice_date")])
    rows.append([T("الطرف", "Party"), (inv.get("party_ar") if ar else inv.get("party_en")),
                 T("الاستحقاق", "Due"), inv.get("due_date")])
    rows.append([T("الحالة", "Status"), (STATUS_AR if ar else STATUS_EN).get(inv.get("status"), inv.get("status")),
                 T("الرقم الضريبي للطرف", "Party tax No."), inv.get("party_tax") or "—"])
    rows.append([])
    header = [T("م", "#"), T("الصنف / الوصف", "Item / description"), T("الكود", "Code"),
              T("الكمية", "Qty"), T("السعر", "Unit price"), T("الخصم", "Discount"),
              T("نسبة الضريبة", "VAT %"), T("قيمة الضريبة", "VAT amount"), T("الإجمالي", "Line total")]
    rows.append(header)
    header_at = len(rows)   # 1-based index of header row
    for ln in data["lines"]:
        rows.append([
            ln.get("line_no"),
            ln.get("product_ar") if ar else (ln.get("product_en") or ln.get("product_ar")),
            ln.get("sku") or "",
            round((ln.get("qty_milli") or 0) / 1000, 3),
            round((ln.get("unit_price_cents") or 0) / 100, 2),
            round((ln.get("discount_cents") or 0) / 100, 2),
            round((ln.get("tax_rate_bp") or 0) / 100, 2),
            round((ln.get("line_tax_cents") or 0) / 100, 2),
            round((ln.get("line_total_cents") or 0) / 100, 2),
        ])
    rows.append([])
    rows.append(["", "", "", "", "", "", T("الإجمالي قبل الضريبة", "Subtotal"), "", round((inv.get("subtotal_cents") or 0) / 100, 2)])
    rows.append(["", "", "", "", "", "", T("الضريبة", "VAT"), "", round((inv.get("tax_cents") or 0) / 100, 2)])
    rows.append(["", "", "", "", "", "", T("الإجمالي", "Total"), "", round((inv.get("total_cents") or 0) / 100, 2)])
    rows.append(["", "", "", "", "", "", T("المدفوع", "Paid"), "", round((inv.get("paid_cents") or 0) / 100, 2)])
    remaining = (inv.get("total_cents") or 0) - (inv.get("paid_cents") or 0)
    rows.append(["", "", "", "", "", "", T("المتبقي", "Balance due"), "", round(remaining / 100, 2)])
    if ar:
        rows.append([tafqeet(remaining or inv.get("total_cents") or 0)])
    e = _einvoice_payload(company, data)
    rows.append([])
    rows.append([T("معرّف المستند (UUID)", "Document UUID"), e["documentUuid"]])
    rows.append([T("بصمة المستند (SHA-256)", "Document digest"), e["digest"]])
    rows.append([T("ملاحظة", "Note"), e["readiness"]["noteAr"] if ar else e["readiness"]["note"]])

    wb.add_sheet(T("الفاتورة", "Invoice"), rows,
                 widths=[6, 34, 14, 10, 12, 10, 10, 12, 14],
                 freeze=header_at, header_rows=0, money_cols=(4, 5, 7, 8))
    # restyle header manually: xlsxgen freezes N rows; use header row index
    sheet = wb.sheets[-1]
    sheet["header_rows"] = header_at
    sheet["freeze"] = header_at
    return wb.to_bytes()


# ---------------------------------------------------------------- reports
def report_pdf(title_ar: str, title_en: str, columns, rows, company=None, lang="ar",
               subtitle_ar: str = "", subtitle_en: str = "", footnotes=()) -> bytes:
    """Generic landscape-free A4 report (auto page breaks, Arabic-first)."""
    ar = lang != "en"
    T = (lambda a, e: a) if ar else (lambda a, e: e)
    pdf = pdfgen.PDF(str(FONT_PATH), str(LATIN_FONT_PATH))
    W, H = pdf.W, pdf.H
    M = 34.0
    pdf.new_page()
    pdf.rect(0, H - 92, W, 92, fill=(38, 27, 92))
    pdf.rect(0, H - 95, W, 3, fill=BRAND)
    name = (company or {}).get("name_ar" if ar else "name_en") or "Eltaranisi ERP"
    pdf.text(W - M, H - 40, name, 14, WHITE, align="right" if ar else "left", rtl=ar)
    pdf.text(W - M, H - 58, (title_ar if ar else title_en), 11.5, (226, 222, 250),
             align="right" if ar else "left", rtl=ar)
    sub = subtitle_ar if ar else subtitle_en
    if sub:
        pdf.text(W - M, H - 74, sub, 8.4, (198, 193, 238), align="right" if ar else "left", rtl=ar)
    brand_mark(pdf, M, H - 74, 26)
    pdf.text(M + 34, H - 40, T("نظام الطرانيسي ERP", "Eltaranisi ERP"), 8.6, (198, 193, 238), align="left", rtl=ar)
    pdf.text(M, H - 56, datetime.now().strftime("%Y-%m-%d %H:%M"), 8.2, (198, 193, 238), align="left", rtl=False)

    # distribute columns
    total_cfg = sum(c.get("w", 1) for c in columns)
    widths = [max(38.0, (W - 2 * M) * c.get("w", 1) / total_cfg) for c in columns]
    rows_h = []
    y = H - 120

    def draw_header(yy):
        pdf.rect(M, yy - 22, W - 2 * M, 22, fill=(38, 27, 92), radius=4)
        x = W - M if ar else M
        for c, w in zip(columns, widths):
            label = c.get("ar") if ar else c.get("en")
            if ar:
                cx = x - w / 2 if c.get("align") == "center" else x - 6
                pdf.text(cx, yy - 15, label, 8.4, WHITE,
                         align="center" if c.get("align") == "center" else "right", rtl=True)
                x -= w
            else:
                cx = x + w / 2 if c.get("align") == "center" else x + 6
                pdf.text(cx, yy - 15, label, 8.4, WHITE,
                         align="center" if c.get("align") == "center" else "left", rtl=False)
                x += w

    draw_header(y)
    y -= 22
    page_no = 1
    for i, row in enumerate(rows):
        h = 19
        if y - h < 90:
            pdf.new_page()
            page_no += 1
            y = H - 60
            draw_header(y)
            y -= 22
        if i % 2 == 0:
            pdf.rect(M, y - h, W - 2 * M, h, fill=(249, 249, 253))
        x = W - M if ar else M
        for c, w, value in zip(columns, widths, row):
            text = "" if value is None else str(value)
            bold = False
            if ar:
                cx = x - w / 2 if c.get("align") == "center" else x - 6
                pdf.text(cx, y - 13, text, 8.4, INK,
                         align="center" if c.get("align") == "center" else "right", rtl=c.get("rtl", True))
                x -= w
            else:
                cx = x + w / 2 if c.get("align") == "center" else x + 6
                pdf.text(cx, y - 13, text, 8.4, INK,
                         align="center" if c.get("align") == "center" else "left", rtl=c.get("rtl", False))
                x += w
        y -= h
        rows_h.append(h)

    # footnotes
    yy = max(y - 16, 80)
    for note in footnotes:
        pdf.text(W - M, yy, note, 8, MUTED, align="right" if ar else "left", rtl=ar)
        yy -= 13
    pdf.line(M, 58, W - M, 58, color=LINE)
    pdf.text(W - M, 44, T("تم التوليد آلياً من نظام الطرانيسي ERP — بيانات من القيود المرحّلة فقط",
                          "Generated by Eltaranisi ERP — posted ledger data only"), 7.6, MUTED,
             align="right" if ar else "left", rtl=ar)
    pdf.text(M, 44, f"{T('صفحة', 'Page')} {page_no}", 7.6, MUTED, align="left", rtl=ar)
    return pdf.to_bytes()


def report_xlsx(title: str, columns, rows, rtl=True, money_cols=()) -> bytes:
    wb = xlsxgen.Workbook(rtl=rtl, title=title)
    data = [list(columns)] + [list(r) for r in rows]
    wb.add_sheet(title[:31] or "Report", data,
                 widths=[max(12, min(38, len(str(c)) + 6)) for c in columns],
                 money_cols=money_cols)
    return wb.to_bytes()


def einvoice_json(conn, company_id: int, kind: str, invoice_id: int) -> dict:
    company = company_block(conn, company_id)
    data = invoice_data(conn, company_id, kind, invoice_id)
    return _einvoice_payload(company, data)
