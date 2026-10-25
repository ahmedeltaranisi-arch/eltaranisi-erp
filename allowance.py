"""إعاشة العاملين — staff subsistence (meals) allowance.

The paper sheet this module replaces works like this (see
``seed_data/allowance_roster.csv`` and the customer's Excel file):

    م | الفئة | الاسم | الحضور | اعتيادى | مرضى | بدون مرتب | غياب | ماموريات
      | خصم إعاشة | خصم مرتب | الصافى | تطبيقات | إجمالى المستحق

* **الفئة** is the daily meal rate (30 / 35 / 40 EGP in the customer's sheet).
* The day grid (1..31) holds one letter code per day; the counters above are
  derived from it with ``COUNTIF`` in Excel — here they are derived in Python so
  the numbers can never disagree with the grid.
* ``الحضور  = أيام الفترة − (اعتيادى + مرضى + بدون مرتب + غياب + ماموريات)``
* ``الصافى  = أيام الفترة − (كل ما سبق + خصم الإعاشة + خصم المرتب)``
* ``الإجمالى = الصافى × الفئة + التطبيقات``

Everything is computed on the server: the browser may send day codes or day
counts, but the stored totals are always recomputed from them.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

# Day-grid codes exactly as used on the paper sheet.
DAY_CODES = {
    "ام": "leave_sick",           # اجازة مرضية
    "بم": "leave_unpaid",         # بدون مرتب
    "غ": "absence_days",          # غياب
    "م": "mission_days",          # مامورية
    "خ": "ded_allowance_days",    # خصم إعاشة
}
CODE_OF = {v: k for k, v in DAY_CODES.items()}

# Fields a user may type by hand when the day grid is not used.
DERIVED_FIELDS = ["leave_sick", "leave_unpaid", "absence_days", "mission_days", "ded_allowance_days"]
MANUAL_FIELDS = ["leave_annual", "ded_salary_days", "adjustments_cents"]

ACCOUNTS = {"expense": "5215", "payable": "2140"}

DEFAULT_DEPARTMENTS = ["الإدارة والإشراف", "ورشة الصيانة"]
# The customer's sheet always covers the two sections together (two blocks in the
# Excel file), so a period created without a department holds both.
ALL_DEPARTMENTS = " + ".join(DEFAULT_DEPARTMENTS)

CODE_LABELS = {
    "ام": ("اجازة مرضية", "Sick leave"),
    "بم": ("بدون مرتب", "Unpaid leave"),
    "غ": ("غياب", "Absence"),
    "م": ("مامورية", "Mission"),
    "خ": ("خصم إعاشة", "Allowance deduction"),
}


class AllowanceError(Exception):
    """Validation problem — surfaced to the API layer as 422."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _cents(value, default=0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return default


def period_days(start: str, end: str) -> int:
    try:
        d1 = date.fromisoformat(str(start)[:10])
        d2 = date.fromisoformat(str(end)[:10])
    except ValueError:
        raise AllowanceError("INVALID_DATES: صيغة التاريخ غير صحيحة / Invalid dates")
    if d2 < d1:
        raise AllowanceError("INVALID_DATES: تاريخ النهاية قبل البداية / End date is before start date")
    return (d2 - d1).days + 1


def normalize_codes(raw, days: int) -> dict:
    """Keep only day numbers 1..days carrying a known code."""
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, code in raw.items():
        try:
            day = int(key)
        except (TypeError, ValueError):
            continue
        code = str(code or "").strip()
        if 1 <= day <= days and code in DAY_CODES:
            out[str(day)] = code
    return out


def _iso(value):
    """Accept ISO (2026-10-01) or the UI day/month/year form (01/10/2026)."""
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None
    parts = text.replace(".", "/").replace("-", "/").split("/")
    if len(parts) == 3:
        try:
            d, m, y = (int(p) for p in parts)
            return date(y, m, d).isoformat()
        except ValueError:
            return None
    return None


def _iso_checked(value, field="att_from"):
    """Same as ``_iso`` but rejects a non-empty value it cannot parse.

    Without this an obvious typo (``15/11`` or ``2026-13-01``) was silently read
    as "no range" and wiped a configured attendance range on save.
    """
    text = str(value or "").strip()
    iso = _iso(text)
    if text and not iso:
        raise AllowanceError(
            "INVALID_RANGE: صيغة التاريخ غير صحيحة ({0}: {1}) — استخدم يوم/شهر/سنة / "
            "Invalid date ({0}: {1}) — use dd/mm/yyyy".format(field, text))
    return iso


def attendance_window(line: dict, start_date: str, days: int) -> tuple:
    """Day numbers (1-based inside the period) covered by the line's range.

    ``att_from``/``att_to`` let one worker be paid for part of the period
    (someone who joined or left mid-month) without splitting the sheet:
         01/10/2026 → 15/10/2026  ⇒  15 counted days instead of 30.
    Empty range = the whole sheet period.
    """
    period_start = date.fromisoformat(str(start_date)[:10])
    period_end = period_start + timedelta(days=days - 1)
    start = _iso_checked(line.get("att_from"), "att_from") or period_start.isoformat()
    end = _iso_checked(line.get("att_to"), "att_to") or period_end.isoformat()
    start = max(date.fromisoformat(start), period_start)
    end = min(date.fromisoformat(end), period_end)
    if end < start:
        raise AllowanceError(
            "INVALID_RANGE: نطاق الحضور خارج فترة الكشف أو نهايته قبل بدايته / "
            "Attendance range is outside the period or ends before it starts")
    return (start - period_start).days + 1, (end - period_start).days + 1, start.isoformat(), end.isoformat()


def compute_line(line: dict, days: int, start_date: str = None) -> dict:
    """Return a line with every count and amount recomputed (never trusts input)."""
    first_day, last_day, win_from, win_to = (1, days, None, None)
    # a range the user actually asked for; empty means "the whole period"
    ranged = bool(_iso_checked(line.get("att_from"), "att_from")
                  or _iso_checked(line.get("att_to"), "att_to"))
    if start_date:
        first_day, last_day, win_from, win_to = attendance_window(line, start_date, days)
    window = last_day - first_day + 1
    codes = {k: v for k, v in normalize_codes(line.get("day_codes"), days).items()
             if first_day <= int(k) <= last_day}
    raw_codes = line.get("day_codes")
    # A grid that exists (even when the user cleared every cell) is the source of
    # truth; manual counters only apply to rows that never had a grid. Without
    # this, clearing the last «خ» cell left a phantom "خصم إعاشة" day behind.
    has_grid = isinstance(raw_codes, dict) or (isinstance(raw_codes, str) and raw_codes.strip() not in ("", "null"))
    counts = {field: 0 for field in DERIVED_FIELDS}
    if has_grid:
        for code in codes.values():
            counts[DAY_CODES[code]] += 1
        for field in DERIVED_FIELDS:
            line[field] = counts[field]
    else:
        for field in DERIVED_FIELDS:
            line[field] = max(0, _int(line.get(field)))

    line["leave_annual"] = max(0, _int(line.get("leave_annual")))
    line["ded_salary_days"] = max(0, _int(line.get("ded_salary_days")))
    # money may arrive as EGP ("30", "12.50") or already as cents (from the DB)
    line["adjustments_cents"] = (_cents(line["adjustments"]) if "adjustments" in line
                                 else _int(line.get("adjustments_cents")))
    line["daily_rate_cents"] = (_cents(line["daily_rate"]) if "daily_rate" in line
                                else _int(line.get("daily_rate_cents")))
    line["days_in_period"] = window
    # keep the range NULL when the whole period is paid, otherwise every row would
    # look "ranged" after a clear and the exports would grow two useless columns
    line["att_from"] = win_from if ranged else None
    line["att_to"] = win_to if ranged else None

    away = (line["leave_annual"] + line["leave_sick"] + line["leave_unpaid"]
            + line["absence_days"] + line["mission_days"])
    if away > window:
        raise AllowanceError(
            "DAYS_OVERFLOW: مجموع أيام الغياب والاجازات أكبر من أيام الحضور "
            f"({away} > {window}) / leave days exceed the attendance window")
    present = window - away
    net = present - line["ded_allowance_days"] - line["ded_salary_days"]
    if net < 0:
        raise AllowanceError("DAYS_OVERFLOW: صافي الأيام بالسالب / Net days below zero")

    line["present_days"] = present
    line["net_days"] = net
    line["total_cents"] = net * line["daily_rate_cents"] + line["adjustments_cents"]
    line["day_codes"] = json.dumps(codes, ensure_ascii=False, sort_keys=True) if codes else None
    return line


def summarize(lines) -> dict:
    buckets = {}
    for line in lines:
        dept = line.get("department") or ""
        b = buckets.setdefault(dept, {"department": dept, "people_count": 0,
                                      "total_days": 0, "total_cents": 0})
        b["people_count"] += 1
        b["total_days"] += int(line.get("net_days") or 0)
        b["total_cents"] += int(line.get("total_cents") or 0)
    order = {name: i for i, name in enumerate(DEFAULT_DEPARTMENTS)}
    sections = sorted(buckets.values(), key=lambda b: (order.get(b["department"], 99), b["department"]))
    return {
        "people_count": len(lines),
        "total_days": sum(int(x.get("net_days") or 0) for x in lines),
        "total_cents": sum(int(x.get("total_cents") or 0) for x in lines),
        "sections": sections,
    }


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------
def _val(user, key, default=None):
    """Users arrive as sqlite3.Row (no .get) or as dicts."""
    try:
        return user[key]
    except (KeyError, IndexError, TypeError):
        return default


def _q1(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def ensure_accounts(conn, company_id: int) -> None:
    """Make sure the two accounts this module posts to exist for the company."""
    row = _q1(conn, "SELECT id FROM accounts WHERE company_id=? AND code=?", (company_id, ACCOUNTS["expense"]))
    if row:
        return
    parent = _q1(conn, "SELECT id, level FROM accounts WHERE company_id=? AND code='52'", (company_id,))
    conn.execute(
        """INSERT INTO accounts (company_id, code, name_ar, name_en, type, parent_id, level,
              is_postable, is_active, currency) VALUES (?,?,?,?,?,?,?,1,1,'EGP')""",
        (company_id, ACCOUNTS["expense"], "إعاشة العاملين", "Staff Subsistence Allowance", "expense",
         parent["id"] if parent else None, (parent["level"] + 1) if parent else 2))


def list_people(conn, company_id: int, department: str | None = None) -> list:
    sql = "SELECT * FROM allowance_people WHERE company_id=?"
    params = [company_id]
    if department:
        sql += " AND department=?"
        params.append(department)
    sql += " ORDER BY department, sort_order, name_ar"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def departments(conn, company_id: int) -> list:
    rows = conn.execute(
        "SELECT department, COUNT(*) n FROM allowance_people WHERE company_id=? AND is_active=1 "
        "GROUP BY department ORDER BY MIN(sort_order), department", (company_id,)).fetchall()
    names = [r["department"] for r in rows]
    for name in DEFAULT_DEPARTMENTS:
        if name not in names:
            names.append(name)
    return names


def create_period(conn, user, company_id: int, department: str | None, start: str, end: str,
                  branch_id=None, copy_roster: bool = True, att_from=None, att_to=None) -> dict:
    """Create a sheet. No department (or the combined label) ⇒ both sections.

    The customer's sheet is one statement that always contains «الإدارة والإشراف»
    and «ورشة الصيانة», so the department is only a filter for exceptions.
    """
    days = period_days(start, end)
    selected = (department or "").strip()
    combined = selected in ("", ALL_DEPARTMENTS)
    if combined:
        department = ALL_DEPARTMENTS
        target_depts = list(DEFAULT_DEPARTMENTS)
    else:
        target_depts = [selected]
    exists = _q1(conn, "SELECT id FROM allowance_periods WHERE company_id=? AND department=? "
                       "AND start_date=? AND end_date=?", (company_id, department, start, end))
    if exists:
        raise AllowanceError("DUPLICATE: يوجد كشف لهذا القسم عن نفس الفترة / "
                             "A sheet already exists for this department and period", 409)
    cur = conn.execute(
        """INSERT INTO allowance_periods (company_id, branch_id, department, start_date, end_date,
              status, created_by, created_at) VALUES (?,?,?,?,?,'draft',?,?)""",
        (company_id, branch_id or _val(user, "branch_id"), department, start, end,
         _val(user, "id"), datetime.utcnow().isoformat(timespec="seconds")))
    period_id = cur.lastrowid
    if copy_roster:
        people = [p for dept in target_depts for p in list_people(conn, company_id, dept)]
        for i, p in enumerate(people):
            line = compute_line({"name_ar": p["name_ar"], "person_id": p["id"],
                                 "daily_rate_cents": p["daily_rate_cents"], "day_codes": {},
                                 "att_from": att_from, "att_to": att_to},
                                days, start)
            conn.execute(
                """INSERT INTO allowance_lines (period_id, person_id, name_ar, daily_rate_cents,
                      days_in_period, leave_annual, leave_sick, leave_unpaid, absence_days, mission_days,
                      ded_allowance_days, ded_salary_days, present_days, net_days, adjustments_cents,
                      total_cents, day_codes, att_from, att_to, department, notes, sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (period_id, p["id"], line["name_ar"], line["daily_rate_cents"], line["days_in_period"],
                 line["leave_annual"], line["leave_sick"], line["leave_unpaid"], line["absence_days"],
                 line["mission_days"], line["ded_allowance_days"], line["ded_salary_days"],
                 line["present_days"], line["net_days"], line["adjustments_cents"],
                 line["total_cents"], None, line["att_from"], line["att_to"], p["department"], None, i))
    stats = recalc_period(conn, period_id)
    return {"id": period_id, "department": department, **stats}


def recalc_period(conn, period_id: int) -> dict:
    rows = conn.execute("SELECT * FROM allowance_lines WHERE period_id=?", (period_id,)).fetchall()
    stats = summarize([dict(r) for r in rows])
    conn.execute("UPDATE allowance_periods SET people_count=?, total_days=?, total_cents=? WHERE id=?",
                 (stats["people_count"], stats["total_days"], stats["total_cents"], period_id))
    return stats


def get_period(conn, company_id: int, period_id: int) -> dict:
    p = _q1(conn, "SELECT * FROM allowance_periods WHERE id=? AND company_id=?", (period_id, company_id))
    if not p:
        raise AllowanceError("NOT_FOUND: الكشف غير موجود / Sheet not found", 404)
    period = dict(p)
    lines = [dict(r) for r in conn.execute(
        "SELECT * FROM allowance_lines WHERE period_id=? ORDER BY sort_order, id", (period_id,)).fetchall()]
    days = period_days(period["start_date"], period["end_date"])
    for line in lines:
        line["day_codes"] = normalize_codes(line.get("day_codes"), days)
    period["lines"] = lines
    period["days"] = days
    period["start_day"] = date.fromisoformat(period["start_date"][:10]).day
    period["totals"] = summarize(lines)
    return period


def save_period(conn, user, company_id: int, period_id: int, payload: dict) -> dict:
    period = get_period(conn, company_id, period_id)
    if period["status"] == "posted":
        raise AllowanceError("INVALID_STATE: الكشف مُرحَّل ولا يمكن تعديله — اعكس القيد أولاً / "
                             "Sheet is posted; reverse the entry first", 409)
    days = period["days"]
    incoming = payload.get("lines")
    if not isinstance(incoming, list):
        raise AllowanceError("MISSING_FIELD: لا توجد بنود للحفظ / No lines supplied")

    existing = {r["id"]: r for r in conn.execute(
        "SELECT * FROM allowance_lines WHERE period_id=?", (period_id,)).fetchall()}
    seen = set()
    for i, raw in enumerate(incoming):
        line_id = raw.get("id")
        base = dict(existing.get(line_id) or {})
        merged = {k: base.get(k) for k in ("person_id", "name_ar", "daily_rate_cents", "day_codes",
                                           "department")}
        merged.update({k: raw[k] for k in raw if k not in ("id",)})
        name = (merged.get("name_ar") or "").strip()
        if not name:
            continue
        merged["name_ar"] = name
        line = compute_line(merged, days, period["start_date"])
        if line_id and line_id in existing:
            seen.add(line_id)
            conn.execute(
                """UPDATE allowance_lines SET name_ar=?, daily_rate_cents=?, days_in_period=?,
                     leave_annual=?, leave_sick=?, leave_unpaid=?, absence_days=?, mission_days=?,
                     ded_allowance_days=?, ded_salary_days=?, present_days=?, net_days=?,
                     adjustments_cents=?, total_cents=?, day_codes=?, att_from=?, att_to=?,
                     notes=?, sort_order=? WHERE id=? AND period_id=?""",
                (line["name_ar"], line["daily_rate_cents"], line["days_in_period"], line["leave_annual"],
                 line["leave_sick"], line["leave_unpaid"], line["absence_days"], line["mission_days"],
                 line["ded_allowance_days"], line["ded_salary_days"], line["present_days"],
                 line["net_days"], line["adjustments_cents"], line["total_cents"], line["day_codes"],
                 line["att_from"], line["att_to"], merged.get("notes"), i, line_id, period_id))
        else:
            cur = conn.execute(
                """INSERT INTO allowance_lines (period_id, person_id, name_ar, daily_rate_cents,
                     days_in_period, leave_annual, leave_sick, leave_unpaid, absence_days, mission_days,
                     ded_allowance_days, ded_salary_days, present_days, net_days, adjustments_cents,
                     total_cents, day_codes, att_from, att_to, department, notes, sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (period_id, merged.get("person_id"), line["name_ar"], line["daily_rate_cents"],
                 line["days_in_period"],
                 line["leave_annual"], line["leave_sick"], line["leave_unpaid"], line["absence_days"],
                 line["mission_days"], line["ded_allowance_days"], line["ded_salary_days"],
                 line["present_days"], line["net_days"], line["adjustments_cents"],
                 line["total_cents"], line["day_codes"], line["att_from"], line["att_to"],
                 merged.get("department"), merged.get("notes"), i))
            seen.add(cur.lastrowid)
    for line_id in existing:
        if line_id not in seen:
            conn.execute("DELETE FROM allowance_lines WHERE id=? AND period_id=?", (line_id, period_id))

    remove = payload.get("remove_line_ids") or []
    for line_id in remove:
        conn.execute("DELETE FROM allowance_lines WHERE id=? AND period_id=?", (int(line_id), period_id))

    if "notes" in payload:
        conn.execute("UPDATE allowance_periods SET notes=? WHERE id=?", (payload.get("notes"), period_id))
    conn.execute("UPDATE allowance_periods SET updated_at=? WHERE id=?",
                 (datetime.utcnow().isoformat(timespec="seconds"), period_id))
    return recalc_period(conn, period_id)


def set_window(conn, user, company_id: int, period_id: int, att_from, att_to, line_ids=None) -> dict:
    """Apply an attendance range (from → to) to some or all rows of a sheet.

    The paid days of every affected row become the number of days inside that
    range — e.g. 01/10/2026 → 15/10/2026 counts 15 days, not the whole month.
    """
    period = get_period(conn, company_id, period_id)
    if period["status"] == "posted":
        raise AllowanceError("INVALID_STATE: الكشف مُرحَّل ولا يمكن تعديله / Sheet is posted", 409)
    start, end = _iso_checked(att_from, "att_from"), _iso_checked(att_to, "att_to")
    if start and end and end < start:
        raise AllowanceError("INVALID_RANGE: تاريخ النهاية قبل البداية / End date is before start date")
    targets = [line for line in period["lines"]
               if not line_ids or line["id"] in {int(x) for x in line_ids}]
    if not targets:
        raise AllowanceError("NOT_FOUND: لا توجد صفوف محددة / No matching rows")
    updated = 0
    for line in targets:
        payload = dict(line)
        payload["att_from"] = start
        payload["att_to"] = end
        payload["day_codes"] = line.get("day_codes") or {}
        computed = compute_line(payload, period["days"], period["start_date"])
        conn.execute(
            """UPDATE allowance_lines SET att_from=?, att_to=?, days_in_period=?, present_days=?,
                 net_days=?, total_cents=? WHERE id=? AND period_id=?""",
            (computed["att_from"], computed["att_to"], computed["days_in_period"],
             computed["present_days"], computed["net_days"], computed["total_cents"],
             line["id"], period_id))
        updated += 1
    conn.execute("UPDATE allowance_periods SET updated_at=? WHERE id=?",
                 (datetime.utcnow().isoformat(timespec="seconds"), period_id))
    stats = recalc_period(conn, period_id)
    return {"updated": updated, **stats}


def delete_period(conn, company_id: int, period_id: int) -> dict:
    p = _q1(conn, "SELECT * FROM allowance_periods WHERE id=? AND company_id=?", (period_id, company_id))
    if not p:
        raise AllowanceError("NOT_FOUND: الكشف غير موجود / Sheet not found", 404)
    if p["status"] == "posted":
        raise AllowanceError("INVALID_STATE: لا يمكن حذف كشف مُرحَّل / Posted sheets cannot be deleted", 409)
    conn.execute("DELETE FROM allowance_lines WHERE period_id=?", (period_id,))
    conn.execute("DELETE FROM allowance_periods WHERE id=?", (period_id,))
    return {"deleted": period_id}


def upsert_person(conn, company_id: int, data: dict) -> dict:
    name = (data.get("name_ar") or "").strip()
    department = (data.get("department") or "").strip()
    if not name or not department:
        raise AllowanceError("MISSING_FIELD: الاسم والقسم مطلوبان / Name and department are required")
    rate = max(0, _cents(data.get("daily_rate"), 3000))
    row = _q1(conn, "SELECT id FROM allowance_people WHERE company_id=? AND department=? AND name_ar=?",
              (company_id, department, name))
    if row:
        conn.execute("UPDATE allowance_people SET daily_rate_cents=?, is_active=? WHERE id=?",
                     (rate, 1 if data.get("is_active", 1) else 0, row["id"]))
        return {"id": row["id"], "updated": True}
    cur = conn.execute(
        """INSERT INTO allowance_people (company_id, department, name_ar, name_en, daily_rate_cents,
              employee_id, is_active, sort_order) VALUES (?,?,?,?,?,?,?,?)""",
        (company_id, department, name, data.get("name_en"), rate, data.get("employee_id"),
         1 if data.get("is_active", 1) else 0, _int(data.get("sort_order"), 0)))
    return {"id": cur.lastrowid, "updated": False}


def set_people_active(conn, company_id: int, person_id: int, active: bool) -> dict:
    conn.execute("UPDATE allowance_people SET is_active=? WHERE id=? AND company_id=?",
                 (1 if active else 0, person_id, company_id))
    return {"id": person_id, "is_active": bool(active)}


def import_roster(conn, company_id: int, rows) -> dict:
    """Import ``[{department, name_ar, daily_rate}]`` — idempotent by name."""
    created = updated = 0
    for i, row in enumerate(rows):
        name = (row.get("name_ar") or "").strip()
        department = (row.get("department") or "").strip()
        if not name or not department:
            continue
        rate = max(0, _cents(row.get("daily_rate"), 3000))
        existing = _q1(conn, "SELECT id, daily_rate_cents FROM allowance_people "
                             "WHERE company_id=? AND department=? AND name_ar=?",
                       (company_id, department, name))
        if existing:
            if existing["daily_rate_cents"] != rate:
                conn.execute("UPDATE allowance_people SET daily_rate_cents=? WHERE id=?",
                             (rate, existing["id"]))
                updated += 1
            continue
        conn.execute(
            """INSERT INTO allowance_people (company_id, department, name_ar, daily_rate_cents,
                  is_active, sort_order) VALUES (?,?,?,?,1,?)""",
            (company_id, department, name, rate, i))
        created += 1
    return {"created": created, "updated": updated}


# --------------------------------------------------------------------------
# posting to the ledger
# --------------------------------------------------------------------------
def post_period(conn, user, company_id: int, period_id: int, entry_date: str | None = None) -> dict:
    import accounting

    period = get_period(conn, company_id, period_id)
    if period["status"] == "posted":
        raise AllowanceError("ALREADY_POSTED: الكشف مُرحَّل بالفعل / Sheet already posted", 409)
    if not period["lines"]:
        raise AllowanceError("EMPTY: لا يوجد عمال في الكشف / The sheet has no lines")
    total = period["totals"]["total_cents"]
    if total <= 0:
        raise AllowanceError("ZERO_TOTAL: إجمالي الإعاشة صفر — لا يوجد ما يُرحَّل / Nothing to post")

    ensure_accounts(conn, company_id)
    on_date = entry_date or period["end_date"]
    memo = f"إعاشة {period['department']} — {period['start_date']} إلى {period['end_date']}"
    lines = [
        {"account_id": accounting.account_id_by_code(conn, company_id, ACCOUNTS["expense"]),
         "debit_cents": total, "credit_cents": 0, "description": memo},
        {"account_id": accounting.account_id_by_code(conn, company_id, ACCOUNTS["payable"]),
         "debit_cents": 0, "credit_cents": total, "description": memo},
    ]
    entry_id = accounting.auto_post_entry(
        conn, user, company_id=company_id, entry_date=on_date, memo=memo, lines=lines,
        entry_type="expense", source_type="allowance_period", source_id=period_id,
        idempotency_key=f"allowance:{period_id}")
    conn.execute("UPDATE allowance_periods SET status='posted', journal_entry_id=?, posted_by=?, posted_at=? "
                 "WHERE id=?",
                 (entry_id, user["id"], datetime.utcnow().isoformat(timespec="seconds"), period_id))
    return {"journal_entry_id": entry_id, "total_cents": total}


def fmt_iso(value) -> str:
    """ISO date → day/month/year for the printed sheet."""
    if not value:
        return ""
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return d.strftime("%d/%m/%Y")


# Column geometry for the printed/exported sheet: the name gets room to breathe,
# every other column is tight, and the day grid only needs a digit or a letter.
COLUMN_WIDTHS = {
    "م": 4.5, "القسم": 19, "الفئة": 6.5, "الاسم": 40, "الحضور": 7.5,
    "اعتيادى": 8, "مرضي": 7, "بدون مرتب": 8.5, "غياب": 6.5, "ماموريات": 8,
    "خصم إعاشة": 8.5, "خصم مرتب": 8, "الصافى": 7, "تطبيقات": 8.5, "إجمالى المستحق": 11,
    "من تاريخ": 10.5, "إلى تاريخ": 10.5,
}


def column_widths(head) -> list:
    """Excel column widths (character units) for a given header row."""
    return [COLUMN_WIDTHS.get(str(h), 4.2 if str(h).isdigit() else 9) for h in head]


# The printed sheet is landscape A4: named columns take a fixed slice and the day
# grid shares whatever is left, so a two-digit day still fits in its cell.
COLUMN_POINTS = {
    "م": 15, "القسم": 44, "الفئة": 22, "الاسم": 126, "الحضور": 26, "اعتيادى": 26,
    "مرضي": 23, "بدون مرتب": 28, "غياب": 23, "ماموريات": 26, "خصم إعاشة": 26,
    "خصم مرتب": 26, "الصافى": 23, "تطبيقات": 26, "إجمالى المستحق": 46,
    "من تاريخ": 32, "إلى تاريخ": 32,
}


def column_points(head, total: float) -> list:
    """Widths in PDF points that always add up to the printable width."""
    fixed = [COLUMN_POINTS.get(str(h)) for h in head]
    day_idx = [i for i, h in enumerate(head) if str(h).isdigit()]
    fixed_sum = sum(w for w in fixed if w is not None)
    remaining = total - fixed_sum
    day_w = (remaining / len(day_idx)) if day_idx and remaining > 0 else 0
    if not day_idx or day_w <= 0:                  # no grid / too many columns: share
        scale = total / float(fixed_sum or 1)
        return [((w or 6) * scale) for w in fixed]
    return [(w if w is not None else day_w) for w in fixed]


def export_matrix(period: dict, with_grid: bool = True) -> dict:
    """Rows for Excel/PDF export, mirroring the paper sheet.

    A sheet that holds both departments is exported as two blocks — a section
    header, the workers (numbering restarts), then the section subtotal — exactly
    like the customer's Excel file. Every row keeps the same length as ``head``
    so the printed table can never shift by a column.
    """
    days = period["days"]
    start_day = period["start_day"]
    head = ["م", "القسم", "الفئة", "الاسم", "الحضور", "اعتيادى", "مرضي", "بدون مرتب", "غياب",
            "ماموريات", "خصم إعاشة", "خصم مرتب", "الصافى", "تطبيقات", "إجمالى المستحق"]
    has_range = any(line.get("att_from") for line in period["lines"])
    if has_range:
        head += ["من تاريخ", "إلى تاريخ"]
    if with_grid:
        head += [str(d) for d in range(start_day, start_day + days)]
    def blank_row(filled=None):
        """Place values by column name so an added column can never shift a row."""
        out = [""] * (15 + (2 if has_range else 0))
        for name, value in (filled or {}).items():
            if name in head[:15 + (2 if has_range else 0)]:
                out[head.index(name)] = value
        if with_grid:
            out += [""] * days
        return out

    sections = period["totals"].get("sections") or [
        {"department": "", "people_count": len(period["lines"]),
         "total_days": period["totals"]["total_days"], "total_cents": period["totals"]["total_cents"]}]
    rows = []
    for section in sections:
        dept = section.get("department") or ""
        if len(sections) > 1 or dept:
            rows.append(blank_row({"القسم": (f"القسم: {dept}" if dept else "القسم: بدون")}))
        group = [ln for ln in period["lines"] if (ln.get("department") or "") == dept]
        for i, line in enumerate(group, start=1):
            codes = line.get("day_codes") or {}
            out = [""] * (15 + (2 if has_range else 0))
            values = {
                "م": str(i), "القسم": dept, "الفئة": f"{line['daily_rate_cents'] / 100:.0f}",
                "الاسم": line["name_ar"], "الحضور": str(line["present_days"]),
                "اعتيادى": str(line["leave_annual"]), "مرضي": str(line["leave_sick"]),
                "بدون مرتب": str(line["leave_unpaid"]), "غياب": str(line["absence_days"]),
                "ماموريات": str(line["mission_days"]), "خصم إعاشة": str(line["ded_allowance_days"]),
                "خصم مرتب": str(line["ded_salary_days"]), "الصافى": str(line["net_days"]),
                "تطبيقات": f"{line['adjustments_cents'] / 100:.2f}" if line["adjustments_cents"] else "",
                "إجمالى المستحق": f"{line['total_cents'] / 100:.2f}",
            }
            if has_range:
                values["من تاريخ"] = fmt_iso(line.get("att_from"))
                values["إلى تاريخ"] = fmt_iso(line.get("att_to"))
            for name, value in values.items():
                if name in head:
                    out[head.index(name)] = value
            if with_grid:
                out += [codes.get(str(d), "") for d in range(1, days + 1)]
            rows.append(out)
        rows.append(blank_row({"القسم": "إجمالي القسم" if dept else "الإجمالي",
                               "الصافى": str(section["total_days"]),
                               "إجمالى المستحق": f"{section['total_cents'] / 100:.2f}"}))
    total_row = blank_row({"القسم": "الإجمالي العام",
                           "الصافى": str(period["totals"]["total_days"]),
                           "إجمالى المستحق": f"{period['totals']['total_cents'] / 100:.2f}"})
    return {"head": head, "rows": rows, "total_row": total_row}
