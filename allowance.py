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

DEFAULT_DEPARTMENTS = ["الإدارة والمخازن", "ورشة الصيانة"]

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


def compute_line(line: dict, days: int) -> dict:
    """Return a line with every count and amount recomputed (never trusts input)."""
    codes = normalize_codes(line.get("day_codes"), days)
    counts = {field: 0 for field in DERIVED_FIELDS}
    if codes:
        for code in codes.values():
            counts[DAY_CODES[code]] += 1
        # the grid is the source of truth once it has any code in it
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
    line["days_in_period"] = days

    away = (line["leave_annual"] + line["leave_sick"] + line["leave_unpaid"]
            + line["absence_days"] + line["mission_days"])
    if away > days:
        raise AllowanceError(
            "DAYS_OVERFLOW: مجموع أيام الغياب والاجازات أكبر من أيام الفترة "
            f"({away} > {days}) / leave days exceed the period length")
    present = days - away
    net = present - line["ded_allowance_days"] - line["ded_salary_days"]
    if net < 0:
        raise AllowanceError("DAYS_OVERFLOW: صافي الأيام بالسالب / Net days below zero")

    line["present_days"] = present
    line["net_days"] = net
    line["total_cents"] = net * line["daily_rate_cents"] + line["adjustments_cents"]
    line["day_codes"] = json.dumps(codes, ensure_ascii=False, sort_keys=True) if codes else None
    return line


def summarize(lines) -> dict:
    return {
        "people_count": len(lines),
        "total_days": sum(int(x.get("net_days") or 0) for x in lines),
        "total_cents": sum(int(x.get("total_cents") or 0) for x in lines),
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


def create_period(conn, user, company_id: int, department: str, start: str, end: str,
                  branch_id=None, copy_roster: bool = True) -> dict:
    days = period_days(start, end)
    if not department:
        raise AllowanceError("MISSING_FIELD: القسم مطلوب / Department required")
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
        people = list_people(conn, company_id, department)
        for i, p in enumerate(people):
            line = compute_line({"name_ar": p["name_ar"], "person_id": p["id"],
                                 "daily_rate_cents": p["daily_rate_cents"], "day_codes": {}}, days)
            conn.execute(
                """INSERT INTO allowance_lines (period_id, person_id, name_ar, daily_rate_cents,
                      days_in_period, leave_annual, leave_sick, leave_unpaid, absence_days, mission_days,
                      ded_allowance_days, ded_salary_days, present_days, net_days, adjustments_cents,
                      total_cents, day_codes, sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (period_id, p["id"], line["name_ar"], line["daily_rate_cents"], days,
                 line["leave_annual"], line["leave_sick"], line["leave_unpaid"], line["absence_days"],
                 line["mission_days"], line["ded_allowance_days"], line["ded_salary_days"],
                 line["present_days"], line["net_days"], line["adjustments_cents"],
                 line["total_cents"], None, i))
    recalc_period(conn, period_id)
    return {"id": period_id}


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
        merged = {k: base.get(k) for k in ("person_id", "name_ar", "daily_rate_cents", "day_codes")}
        merged.update({k: raw[k] for k in raw if k not in ("id",)})
        name = (merged.get("name_ar") or "").strip()
        if not name:
            continue
        merged["name_ar"] = name
        line = compute_line(merged, days)
        if line_id and line_id in existing:
            seen.add(line_id)
            conn.execute(
                """UPDATE allowance_lines SET name_ar=?, daily_rate_cents=?, days_in_period=?,
                     leave_annual=?, leave_sick=?, leave_unpaid=?, absence_days=?, mission_days=?,
                     ded_allowance_days=?, ded_salary_days=?, present_days=?, net_days=?,
                     adjustments_cents=?, total_cents=?, day_codes=?, notes=?, sort_order=?
                   WHERE id=? AND period_id=?""",
                (line["name_ar"], line["daily_rate_cents"], days, line["leave_annual"],
                 line["leave_sick"], line["leave_unpaid"], line["absence_days"], line["mission_days"],
                 line["ded_allowance_days"], line["ded_salary_days"], line["present_days"],
                 line["net_days"], line["adjustments_cents"], line["total_cents"], line["day_codes"],
                 merged.get("notes"), i, line_id, period_id))
        else:
            cur = conn.execute(
                """INSERT INTO allowance_lines (period_id, person_id, name_ar, daily_rate_cents,
                     days_in_period, leave_annual, leave_sick, leave_unpaid, absence_days, mission_days,
                     ded_allowance_days, ded_salary_days, present_days, net_days, adjustments_cents,
                     total_cents, day_codes, notes, sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (period_id, merged.get("person_id"), line["name_ar"], line["daily_rate_cents"], days,
                 line["leave_annual"], line["leave_sick"], line["leave_unpaid"], line["absence_days"],
                 line["mission_days"], line["ded_allowance_days"], line["ded_salary_days"],
                 line["present_days"], line["net_days"], line["adjustments_cents"],
                 line["total_cents"], line["day_codes"], merged.get("notes"), i))
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


def export_matrix(period: dict, with_grid: bool = True) -> dict:
    """Rows for Excel/PDF export, mirroring the paper sheet."""
    days = period["days"]
    start_day = period["start_day"]
    head = ["م", "الفئة", "الاسم", "الحضور", "اعتيادى", "مرضي", "بدون مرتب", "غياب", "ماموريات",
            "خصم إعاشة", "خصم مرتب", "الصافى", "تطبيقات", "إجمالى المستحق"]
    if with_grid:
        head += [str(d) for d in range(start_day, start_day + days)]
    rows = []
    for i, line in enumerate(period["lines"], start=1):
        codes = line.get("day_codes") or {}
        row = [str(i), f"{line['daily_rate_cents'] / 100:.0f}", line["name_ar"],
               str(line["present_days"]), str(line["leave_annual"]), str(line["leave_sick"]),
               str(line["leave_unpaid"]), str(line["absence_days"]), str(line["mission_days"]),
               str(line["ded_allowance_days"]), str(line["ded_salary_days"]), str(line["net_days"]),
               f"{line['adjustments_cents'] / 100:.2f}" if line["adjustments_cents"] else "",
               f"{line['total_cents'] / 100:.2f}"]
        if with_grid:
            row += [codes.get(str(d), "") for d in range(1, days + 1)]
        rows.append(row)
    return {"head": head, "rows": rows}
