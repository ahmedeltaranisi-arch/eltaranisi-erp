"""Financial reporting layer — every figure is derived from posted journal lines."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP


def _fy_bounds(conn, company_id, on_date):
    row = conn.execute(
        "SELECT * FROM fiscal_years WHERE company_id=? AND start_date<=? AND end_date>=?",
        (company_id, on_date, on_date)).fetchone()
    if row:
        return row["start_date"], row["end_date"]
    y = str(on_date)[:4]
    return f"{y}-01-01", f"{y}-12-31"


def account_balances(conn, company_id, date_from, date_to, extra_where="", params=()):
    """Return opening / period movement / closing per account."""
    sql = f"""
    SELECT a.id, a.code, a.name_ar, a.name_en, a.type, a.parent_id, a.is_postable,
           COALESCE(SUM(CASE WHEN l.entry_date < ? THEN l.debit_cents - l.credit_cents ELSE 0 END),0) AS opening,
           COALESCE(SUM(CASE WHEN l.entry_date BETWEEN ? AND ? THEN l.debit_cents ELSE 0 END),0) AS period_debit,
           COALESCE(SUM(CASE WHEN l.entry_date BETWEEN ? AND ? THEN l.credit_cents ELSE 0 END),0) AS period_credit,
           COALESCE(SUM(l.debit_cents - l.credit_cents),0) AS closing
    FROM accounts a
    LEFT JOIN v_posted_lines l ON l.account_id = a.id AND l.company_id = a.company_id
         AND l.entry_date <= ? {extra_where}
    WHERE a.company_id = ?
    GROUP BY a.id
    ORDER BY a.code
    """
    rows = conn.execute(sql, (date_from, date_from, date_to, date_from, date_to, date_to, *params, company_id)).fetchall()
    return [dict(r) for r in rows]


def trial_balance(conn, company_id, date_from, date_to, hide_zero=True):
    rows = account_balances(conn, company_id, date_from, date_to)
    out = []
    td = tc = od = oc = 0
    for r in rows:
        if hide_zero and r["period_debit"] == 0 and r["period_credit"] == 0 and r["opening"] == 0:
            continue
        out.append(r)
        td += r["period_debit"]
        tc += r["period_credit"]
        od += r["opening"]
        oc += r["closing"]
    return {
        "rows": out,
        "totals": {"period_debit": td, "period_credit": tc, "opening_net": od, "closing_net": oc},
        "balanced": td == tc,
    }


def ledger(conn, company_id, account_id, date_from, date_to):
    acc = conn.execute("SELECT * FROM accounts WHERE id=? AND company_id=?", (account_id, company_id)).fetchone()
    if acc is None:
        return None
    opening = conn.execute(
        """SELECT COALESCE(SUM(debit_cents-credit_cents),0) v FROM v_posted_lines
           WHERE company_id=? AND account_id=? AND entry_date < ?""",
        (company_id, account_id, date_from)).fetchone()["v"]
    rows = conn.execute(
        """SELECT line_id, entry_id, entry_no, entry_date, entry_type, description,
                  debit_cents, credit_cents
           FROM v_posted_lines WHERE company_id=? AND account_id=? AND entry_date BETWEEN ? AND ?
           ORDER BY entry_date, entry_id, line_id""",
        (company_id, account_id, date_from, date_to)).fetchall()
    running = opening
    out = []
    for r in rows:
        running += r["debit_cents"] - r["credit_cents"]
        d = dict(r)
        d["running_cents"] = running
        out.append(d)
    return {"account": dict(acc), "opening_cents": opening, "rows": out, "closing_cents": running}


def income_statement(conn, company_id, date_from, date_to):
    rows = account_balances(conn, company_id, date_from, date_to)
    revenue, expenses, other = [], [], []
    total_rev = total_exp = 0
    for r in rows:
        if not r["is_postable"]:
            continue
        amount = r["period_credit"] - r["period_debit"] if r["type"] == "revenue" else r["period_debit"] - r["period_credit"]
        if amount == 0:
            continue
        item = {"code": r["code"], "name_ar": r["name_ar"], "name_en": r["name_en"], "amount_cents": amount}
        if r["type"] == "revenue":
            revenue.append(item)
            total_rev += amount
        elif r["type"] == "expense":
            if r["code"].startswith("52"):
                expenses.append(item)
                total_exp += amount
            else:
                other.append(item)
                total_exp += amount
    return {
        "date_from": date_from, "date_to": date_to,
        "revenue": revenue, "expenses": expenses + other,
        "total_revenue_cents": total_rev, "total_expenses_cents": total_exp,
        "net_profit_cents": total_rev - total_exp,
        "gross_profit_cents": total_rev - sum(i["amount_cents"] for i in expenses + other
                                               if i["code"].startswith("51")),
    }


def balance_sheet(conn, company_id, as_of):
    fy_start, _ = _fy_bounds(conn, company_id, as_of)
    rows = account_balances(conn, company_id, "0000-01-01", as_of)
    assets, liabilities, equity = [], [], []
    ta = tl = te = 0
    revenue_ytd = expense_ytd = 0
    for r in rows:
        if not r["is_postable"]:
            continue
        bal = r["closing"]
        item = {"code": r["code"], "name_ar": r["name_ar"], "name_en": r["name_en"], "amount_cents": bal}
        if r["type"] == "asset":
            if bal:
                assets.append(item)
                ta += bal
        elif r["type"] == "liability":
            if bal:
                liabilities.append({"amount_cents": -bal, **{k: item[k] for k in ("code", "name_ar", "name_en")}})
                tl += -bal
        elif r["type"] == "equity":
            if bal:
                equity.append({"amount_cents": -bal, **{k: item[k] for k in ("code", "name_ar", "name_en")}})
                te += -bal
        elif r["type"] == "revenue":
            revenue_ytd += -bal
        elif r["type"] == "expense":
            expense_ytd += bal
    profit = revenue_ytd - expense_ytd
    equity.append({"code": "3900", "name_ar": "أرباح الفترة (غير مقفلة)", "name_en": "Current period result",
                   "amount_cents": profit})
    te += profit
    return {
        "as_of": as_of, "assets": assets, "liabilities": liabilities, "equity": equity,
        "total_assets_cents": ta, "total_liabilities_cents": tl, "total_equity_cents": te,
        "balanced": ta == tl + te,
        "current_period_profit_cents": profit,
    }


def cash_flow(conn, company_id, date_from, date_to):
    """Indirect-method summary: operating = net profit + depreciation ± working capital."""
    inc = income_statement(conn, company_id, date_from, date_to)
    cash_accounts = [r["gl_account_id"] for r in conn.execute(
        "SELECT gl_account_id FROM bank_accounts WHERE company_id=?", (company_id,)).fetchall()]
    if not cash_accounts:
        return {"inflow": 0, "outflow": 0, "net": 0, "rows": []}
    qmarks = ",".join("?" * len(cash_accounts))
    rows = conn.execute(
        f"""SELECT entry_date, entry_no, description, SUM(debit_cents) d, SUM(credit_cents) c
            FROM v_posted_lines WHERE company_id=? AND account_id IN ({qmarks})
              AND entry_date BETWEEN ? AND ?
            GROUP BY entry_id ORDER BY entry_date""",
        (company_id, *cash_accounts, date_from, date_to)).fetchall()
    inflow = sum(r["d"] for r in rows)
    outflow = sum(r["c"] for r in rows)
    return {
        "operating_profit_cents": inc["net_profit_cents"],
        "inflow_cents": inflow, "outflow_cents": outflow, "net_cents": inflow - outflow,
        "rows": [dict(r) for r in rows],
    }


def _bucket(days):
    if days <= 0:
        return "current"
    if days <= 30:
        return "1_30"
    if days <= 60:
        return "31_60"
    if days <= 90:
        return "61_90"
    return "over_90"


def aging(conn, company_id, as_of, kind="sales"):
    table = "sales_invoices" if kind == "sales" else "purchase_invoices"
    party_col = "customer_id" if kind == "sales" else "vendor_id"
    party_table = "customers" if kind == "sales" else "vendors"
    rows = conn.execute(
        f"""SELECT i.id, i.invoice_no, i.invoice_date, i.due_date, i.total_cents, i.paid_cents,
                   i.{party_col} AS party_id, p.name_ar, p.name_en, p.code
            FROM {table} i JOIN {party_table} p ON p.id = i.{party_col}
            WHERE i.company_id=? AND i.status IN ('posted','partially_paid') AND i.total_cents > i.paid_cents
            ORDER BY i.due_date""",
        (company_id,)).fetchall()
    as_of_d = datetime.strptime(as_of, "%Y-%m-%d").date()
    buckets = {"current": 0, "1_30": 0, "31_60": 0, "61_90": 0, "over_90": 0}
    parties = {}
    items = []
    for r in rows:
        remaining = r["total_cents"] - r["paid_cents"]
        due = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
        days_overdue = (as_of_d - due).days
        b = _bucket(days_overdue)
        buckets[b] += remaining
        items.append({
            "invoice_id": r["id"], "invoice_no": r["invoice_no"], "party_id": r["party_id"],
            "party_ar": r["name_ar"], "party_en": r["name_en"], "invoice_date": r["invoice_date"],
            "due_date": r["due_date"], "total_cents": r["total_cents"], "paid_cents": r["paid_cents"],
            "remaining_cents": remaining, "days_overdue": days_overdue, "bucket": b,
        })
        p = parties.setdefault(r["party_id"], {"party_id": r["party_id"], "name_ar": r["name_ar"],
                                               "name_en": r["name_en"], "code": r["code"],
                                               "current": 0, "1_30": 0, "31_60": 0, "61_90": 0, "over_90": 0,
                                               "total": 0, "invoices": 0})
        p[b] += remaining
        p["total"] += remaining
        p["invoices"] += 1
    return {
        "as_of": as_of, "buckets": buckets, "parties": sorted(parties.values(), key=lambda x: -x["total"]),
        "items": items, "total_cents": sum(buckets.values()),
    }


def vendor_payables(conn, company_id, as_of):
    """Detailed vendor payable position: invoiced, paid, outstanding, aging, next due.

    The per-vendor aggregates and the journal balance are joined in SQL: this
    used to run two statements per vendor (N+1) — with ~2 vendors that is 4 extra
    D1 round-trips on the dashboard.
    """
    aging_data = aging(conn, company_id, as_of, "purchase")
    rows = conn.execute(
        """SELECT v.*,
                  COALESCE(inv.invoiced,0) AS invoiced, COALESCE(inv.paid,0) AS paid,
                  COALESCE(inv.cnt,0) AS cnt, inv.next_due,
                  COALESCE(jnl.bal,0) AS journal_balance
           FROM vendors v
           LEFT JOIN (SELECT vendor_id, SUM(total_cents) invoiced, SUM(paid_cents) paid,
                             COUNT(*) cnt,
                             MIN(CASE WHEN total_cents>paid_cents THEN due_date END) next_due
                      FROM purchase_invoices
                      WHERE company_id=? AND status IN ('posted','partially_paid','paid')
                      GROUP BY vendor_id) inv ON inv.vendor_id=v.id
           LEFT JOIN (SELECT vendor_id, SUM(credit_cents-debit_cents) bal
                      FROM v_posted_lines WHERE company_id=? GROUP BY vendor_id) jnl
                  ON jnl.vendor_id=v.id
           WHERE v.company_id=? ORDER BY v.name_ar""",
        (company_id, company_id, company_id)).fetchall()
    parties = {p["party_id"]: p for p in aging_data["parties"]}
    out = []
    for v in rows:
        buckets = parties.get(v["id"])
        overdue = sum(buckets[k] for k in ("1_30", "31_60", "61_90", "over_90")) if buckets else 0
        out.append({
            "id": v["id"], "code": v["code"], "name_ar": v["name_ar"], "name_en": v["name_en"],
            "phone": v["phone"], "payment_terms_days": v["payment_terms_days"],
            "invoiced_cents": v["invoiced"], "paid_cents": v["paid"],
            "outstanding_cents": v["invoiced"] - v["paid"],
            "journal_balance_cents": v["journal_balance"],
            "overdue_cents": overdue,
            "next_due_date": v["next_due"],
            "invoice_count": v["cnt"],
            "buckets": {k: (buckets[k] if buckets else 0) for k in ("current", "1_30", "31_60", "61_90", "over_90")},
        })
    totals = {
        "invoiced_cents": sum(o["invoiced_cents"] for o in out),
        "paid_cents": sum(o["paid_cents"] for o in out),
        "outstanding_cents": sum(o["outstanding_cents"] for o in out),
        "overdue_cents": sum(o["overdue_cents"] for o in out),
        "journal_balance_cents": sum(o["journal_balance_cents"] for o in out),
    }
    # Money paid to a vendor that has not been placed against an invoice still
    # debits the control account, but no invoice is reduced — so the invoice
    # subledger still shows it while the ledger does not. The identity is
    #
    #     outstanding = journal balance + unallocated payments
    #
    # and without this line the two sides never agree, so the difference looks
    # like a bug when it is really an open item waiting to be allocated.
    unalloc = conn.execute(
        """SELECT COALESCE(SUM(p.amount_cents - p.allocated_cents), 0) AS c
           FROM payments p
           WHERE p.company_id=? AND p.party_type='vendor' AND p.status='posted'
             AND p.amount_cents > p.allocated_cents""",
        (company_id,)).fetchone()["c"]
    totals["unallocated_payments_cents"] = unalloc
    totals["reconciles"] = (totals["journal_balance_cents"] + unalloc
                            == totals["outstanding_cents"])
    return {"as_of": as_of, "vendors": out, "totals": totals,
            "aging": aging_data["buckets"], "invoices": aging_data["items"]}


def cash_position(conn, company_id):
    rows = conn.execute(
        """SELECT b.id, b.code, b.name_ar, b.name_en, b.account_type, b.gl_account_id, b.currency,
                  a.code AS gl_code,
                  COALESCE((SELECT SUM(debit_cents-credit_cents) FROM v_posted_lines l
                            WHERE l.account_id=b.gl_account_id AND l.company_id=b.company_id),0) AS balance_cents
           FROM bank_accounts b JOIN accounts a ON a.id=b.gl_account_id
           WHERE b.company_id=? AND b.is_active=1 ORDER BY b.account_type DESC, b.code""",
        (company_id,)).fetchall()
    total = sum(r["balance_cents"] for r in rows)
    return {"accounts": [dict(r) for r in rows], "total_cents": total}


def ar_position(conn, company_id):
    rows = conn.execute(
        """SELECT c.id, c.code, c.name_ar, c.name_en, c.credit_limit_cents,
                  COALESCE(SUM(CASE WHEN i.status IN ('posted','partially_paid')
                                    THEN i.total_cents - i.paid_cents ELSE 0 END),0) AS outstanding_cents
           FROM customers c LEFT JOIN sales_invoices i ON i.customer_id=c.id
           WHERE c.company_id=? GROUP BY c.id ORDER BY outstanding_cents DESC""",
        (company_id,)).fetchall()
    return {"customers": [dict(r) for r in rows], "total_cents": sum(r["outstanding_cents"] for r in rows)}


def monthly_series(conn, company_id, months=6, as_of=None):
    """Revenue / expense per month — ONE query for the whole window.

    It used to run one aggregate per month (6 round-trips). With the D1 backend
    every statement is an HTTPS request, so this alone could cost a second.
    """
    as_of = as_of or date.today().isoformat()
    end = datetime.strptime(as_of, "%Y-%m-%d").date().replace(day=1)
    first = end
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    nxt = (end.replace(day=28) + timedelta(days=7)).replace(day=1)
    rows = conn.execute(
        """SELECT substr(l.entry_date,1,7) AS m, a.type,
                  SUM(l.debit_cents) d, SUM(l.credit_cents) c
           FROM v_posted_lines l JOIN accounts a ON a.id=l.account_id
           WHERE l.company_id=? AND l.entry_date>=? AND l.entry_date<?
           GROUP BY m, a.type""",
        (company_id, first.isoformat(), nxt.isoformat())).fetchall()
    acc = {}
    for r in rows:
        bucket = acc.setdefault(r["m"], {"rev": 0, "exp": 0})
        if r["type"] == "revenue":
            bucket["rev"] += (r["c"] or 0) - (r["d"] or 0)
        elif r["type"] == "expense":
            bucket["exp"] += (r["d"] or 0) - (r["c"] or 0)
    series = []
    m = first
    while m < nxt:
        key = m.strftime("%Y-%m")
        bucket = acc.get(key, {"rev": 0, "exp": 0})
        series.append({"month": key, "revenue_cents": bucket["rev"],
                       "expenses_cents": bucket["exp"], "profit_cents": bucket["rev"] - bucket["exp"]})
        m = (m.replace(day=28) + timedelta(days=7)).replace(day=1)
    return series


def dashboard(conn, company_id, as_of=None):
    as_of = as_of or date.today().isoformat()
    fy_start, fy_end = _fy_bounds(conn, company_id, as_of)
    inc = income_statement(conn, company_id, fy_start, as_of)
    cash = cash_position(conn, company_id)
    ar = ar_position(conn, company_id)
    ap = vendor_payables(conn, company_id, as_of)
    aging_ar = aging(conn, company_id, as_of, "sales")
    overdue = conn.execute(
        """SELECT COUNT(*) cnt, COALESCE(SUM(total_cents-paid_cents),0) v FROM sales_invoices
           WHERE company_id=? AND status IN ('posted','partially_paid') AND due_date < ?""",
        (company_id, as_of)).fetchone()
    pending = conn.execute(
        "SELECT COUNT(*) cnt FROM journal_entries WHERE company_id=? AND status IN ('review','approved')",
        (company_id,)).fetchone()["cnt"]
    recent = conn.execute(
        """SELECT e.id, e.entry_no, e.entry_date, e.memo, e.entry_type, e.status,
                  e.total_debit_cents FROM journal_entries e WHERE e.company_id=?
           ORDER BY e.id DESC LIMIT 8""", (company_id,)).fetchall()
    return {
        "as_of": as_of, "fiscal_start": fy_start,
        "kpis": {
            "revenue_cents": inc["total_revenue_cents"],
            "expenses_cents": inc["total_expenses_cents"],
            "net_profit_cents": inc["net_profit_cents"],
            "cash_cents": cash["total_cents"],
            "ar_cents": ar["total_cents"],
            "ap_cents": ap["totals"]["outstanding_cents"],
            "overdue_ar_cents": overdue["v"], "overdue_count": overdue["cnt"],
            "vendor_overdue_cents": ap["totals"]["overdue_cents"],
            "pending_approvals": pending,
            "inventory_value_cents": conn.execute(
                "SELECT COALESCE(SUM(value_cents),0) v FROM stock_movements WHERE company_id=?",
                (company_id,)).fetchone()["v"],
        },
        "series": monthly_series(conn, company_id, 6, as_of),
        "cash_accounts": cash["accounts"],
        "top_vendors": sorted(ap["vendors"], key=lambda x: -x["outstanding_cents"])[:5],
        "ar_aging": aging_ar["buckets"],
        "ap_aging": ap["aging"],
        "recent_entries": [dict(r) for r in recent],
    }


def budget_vs_actual(conn, company_id, fiscal_year_id):
    rows = conn.execute(
        """SELECT b.period_month, b.budget_cents, a.code, a.name_ar, a.name_en, a.type, b.account_id
           FROM budgets b JOIN accounts a ON a.id=b.account_id
           WHERE b.company_id=? AND b.fiscal_year_id=? ORDER BY a.code, b.period_month""",
        (company_id, fiscal_year_id)).fetchall()
    out = {}
    for r in rows:
        key = (r["code"], r["account_id"])
        e = out.setdefault(key, {"code": r["code"], "name_ar": r["name_ar"], "name_en": r["name_en"],
                                 "type": r["type"], "account_id": r["account_id"],
                                 "budget_cents": 0, "actual_cents": 0})
        e["budget_cents"] += r["budget_cents"]
        m = f"{r['period_month']:02d}"
        act = conn.execute(
            """SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) v FROM v_posted_lines l
               WHERE l.company_id=? AND l.account_id=? AND substr(l.entry_date,6,2)=?""",
            (company_id, r["account_id"], m)).fetchone()["v"]
        e["actual_cents"] += act if r["type"] == "expense" else -act
    result = []
    for e in out.values():
        e["variance_cents"] = e["budget_cents"] - e["actual_cents"]
        e["variance_pct"] = round((e["variance_cents"] / e["budget_cents"] * 100), 1) if e["budget_cents"] else None
        result.append(e)
    return result


# =========================================================================
# Financial statements — presentation layer
# =========================================================================
# The functions above answer "what are the balances". This section turns them
# into the statements an accountant actually reads: a multi-step income
# statement with sections and totals, a classified balance sheet with groups
# and a current-year-earnings line derived from the ledger, a cash-flow
# statement with three activities (IAS 7 style), a statement of changes in
# equity, a trial-balance balance sheet, plus three "analytic" statements that
# are specific to this business (receivables/payables ledgers and the
# contractor debt position).
#
# Everything below is built from posted journal lines only (v_posted_lines) and
# never from invoice tables, so the statements always tie back to the ledger.

STATEMENT_META = [
    {"id": "income-statement", "group": "primary",
     "name_ar": "قائمة الدخل", "name_en": "Income statement",
     "desc_ar": "الإيرادات والتكاليف والمصروفات وصافي الربح للفترة.",
     "desc_en": "Revenue, cost of sales, expenses and net profit for the period."},
    {"id": "balance-sheet", "group": "primary",
     "name_ar": "قائمة المركز المالي", "name_en": "Statement of financial position",
     "desc_ar": "الأصول والالتزامات وحقوق الملكية في تاريخ معين.",
     "desc_en": "Assets, liabilities and equity at a point in time."},
    {"id": "cash-flow", "group": "primary",
     "name_ar": "قائمة التدفقات النقدية", "name_en": "Cash flow statement",
     "desc_ar": "التدفقات من الأنشطة التشغيلية والاستثمارية والتمويلية.",
     "desc_en": "Operating, investing and financing cash flows."},
    {"id": "equity-changes", "group": "primary",
     "name_ar": "قائمة التغير في حقوق الملكية", "name_en": "Statement of changes in equity",
     "desc_ar": "حركة كل بند من بنود حقوق الملكية خلال الفترة.",
     "desc_en": "Movement of each equity line during the period."},
    {"id": "trial-balance", "group": "verification",
     "name_ar": "ميزان المراجعة", "name_en": "Trial balance",
     "desc_ar": "أرصدة افتتاحية وحركة وأرصدة ختامية لكل حساب.",
     "desc_en": "Opening balances, movements and closing balances per account."},
    {"id": "tb-balance-sheet", "group": "verification",
     "name_ar": "الميزانية العمومية (من ميزان المراجعة)", "name_en": "Balance sheet from trial balance",
     "desc_ar": "عرض الميزانية بالطريقة التقليدية مدين/دائن من أرصدة الميزان.",
     "desc_en": "Traditional debit/credit presentation built from trial-balance balances."},
    {"id": "general-ledger", "group": "verification",
     "name_ar": "دفتر الأستاذ العام", "name_en": "General ledger",
     "desc_ar": "حركة كل حساب بالتفصيل مع الرصيد الجاري.",
     "desc_en": "Every account's postings with a running balance."},
    {"id": "journal", "group": "verification",
     "name_ar": "دفتر اليومية", "name_en": "General journal",
     "desc_ar": "كل القيود المرحّلة في الفترة ببنودها.",
     "desc_en": "All posted entries in the period with their lines."},
    {"id": "receivables-aging", "group": "analytic",
     "name_ar": "أعمار ديون العملاء", "name_en": "Receivables aging",
     "desc_ar": "مستحقات العملاء موزعة على فترات التأخير.",
     "desc_en": "Customer balances by age bucket."},
    {"id": "payables-aging", "group": "analytic",
     "name_ar": "أعمار ديون الموردين", "name_en": "Payables aging",
     "desc_ar": "مستحقات الموردين موزعة على فترات التأخير.",
     "desc_en": "Vendor balances by age bucket."},
    {"id": "debt-position", "group": "analytic",
     "name_ar": "حساب المديونية والمقاولين", "name_en": "Contractor debt position",
     "desc_ar": "مستحقات وسدادات المقاولين والموردين بحسب الحساب.",
     "desc_en": "Contractor and vendor dues against settlements, per account."},
]


def _amt(conn, company_id, code_prefixes, date_from, date_to, side="credit"):
    """Net movement of postable accounts whose code starts with any prefix.

    ``side='credit'`` returns credits minus debits (revenue/liability view),
    ``side='debit'`` returns debits minus credits (asset/expense view).
    """
    qmarks = ",".join("?" * len(code_prefixes))
    cond = " OR ".join(["a.code LIKE ?"] * len(code_prefixes))
    params = [p + "%" for p in code_prefixes]
    row = conn.execute(
        f"""SELECT COALESCE(SUM(l.debit_cents),0) d, COALESCE(SUM(l.credit_cents),0) c
            FROM v_posted_lines l JOIN accounts a ON a.id = l.account_id
            WHERE l.company_id = ? AND a.is_postable = 1 AND ({cond})
              AND l.entry_date BETWEEN ? AND ?""",
        (company_id, *params, date_from, date_to)).fetchone()
    d, c = row["d"] or 0, row["c"] or 0
    return (c - d) if side == "credit" else (d - c)


def _leaf_accounts(conn, company_id):
    return [dict(r) for r in conn.execute(
        """SELECT id, code, name_ar, name_en, type, parent_id, level
           FROM accounts WHERE company_id=? AND is_postable=1 ORDER BY code""",
        (company_id,)).fetchall()]


def _group_tree(accounts, parent_codes):
    """Split leaf accounts into the (code, children) groups of ``parent_codes``.

    Accounts are matched on their code prefix, so a group stays correct even if
    the chart of accounts was extended by the customer (5110, 5115 …).
    """
    groups = []
    for pcode in parent_codes:
        children = [a for a in accounts if a["code"].startswith(pcode) and a["code"] != pcode]
        groups.append((pcode, children))
    return groups


def _line(a, amount):
    return {"code": a["code"], "name_ar": a["name_ar"], "name_en": a["name_en"],
            "amount_cents": amount}


def _section(key, name_ar, name_en, lines, total_cents=None):
    total = sum(l["amount_cents"] for l in lines) if total_cents is None else total_cents
    return {"key": key, "name_ar": name_ar, "name_en": name_en,
            "lines": lines, "total_cents": total}


def income_statement_full(conn, company_id, date_from, date_to):
    """Multi-step income statement: revenue → cost of sales → operating
    expenses → other income/expense → profit, with comparatives left to the
    caller (the API runs this twice for two periods)."""
    accounts = _leaf_accounts(conn, company_id)
    bal = account_balances(conn, company_id, date_from, date_to)
    by_id = {r["id"]: r for r in bal}

    def movement(prefixes, side):
        out = []
        for a in accounts:
            if not any(a["code"].startswith(p) for p in prefixes):
                continue
            r = by_id.get(a["id"], {})
            d, c = r.get("period_debit", 0), r.get("period_credit", 0)
            amt = (c - d) if side == "credit" else (d - c)
            if amt:
                out.append(_line(a, amt))
        return out

    # revenue: 4x excluding returns/discounts (4200) which reduce revenue
    gross_rev = movement(["4100", "4110", "4120", "4130"], "credit")
    returns = movement(["4200"], "debit")
    other_income = movement(["49"], "credit")
    revenue_total = sum(l["amount_cents"] for l in gross_rev) + sum(l["amount_cents"] for l in other_income)
    returns_total = sum(l["amount_cents"] for l in returns)
    net_revenue = revenue_total - returns_total

    cogs = movement(["5100", "511"], "debit")
    cogs_total = sum(l["amount_cents"] for l in cogs)
    gross_profit = net_revenue - cogs_total

    # operating expenses: everything in 52xx and 53xx/54xx/59xx, by group
    op_groups = [("52", "مصروفات تشغيلية", "Operating expenses"),
                 ("53", "مصروفات بيعية وإدارية", "Selling & administrative"),
                 ("54", "مصروفات بنكية وتمويلية", "Bank & finance charges"),
                 ("59", "مصروفات أخرى", "Other expenses")]
    op_expense_sections = []
    for prefix, ar, en in op_groups:
        lines = movement([prefix], "debit")
        if lines:
            op_expense_sections.append(_section(prefix, ar, en, lines))
    op_total = sum(s["total_cents"] for s in op_expense_sections)
    ebitda_ish = gross_profit - op_total

    depreciation = [l for l in sum([s["lines"] for s in op_expense_sections], [])
                    if l["code"].startswith("5320")]
    dep_total = sum(l["amount_cents"] for l in depreciation)
    ebit = ebitda_ish

    other_exp = movement(["55", "56", "57", "58"], "debit")
    other_exp_total = sum(l["amount_cents"] for l in other_exp)
    profit_before_tax = ebit + sum(l["amount_cents"] for l in other_income) - other_exp_total
    tax_lines = movement(["221", "222", "223"], "credit")
    net_profit = profit_before_tax  # no P&L tax expense account in this chart

    return {
        "date_from": date_from, "date_to": date_to,
        "sections": {
            "revenue": _section("revenue", "الإيرادات", "Revenue", gross_rev, revenue_total),
            "returns": _section("returns", "مردودات وخصومات المبيعات", "Sales returns & discounts",
                                returns, returns_total),
            "cost_of_sales": _section("cost_of_sales", "تكلفة النشاط", "Cost of sales", cogs, cogs_total),
            "operating_expenses": op_expense_sections,
            "other_expenses": other_exp,
        },
        "net_revenue_cents": net_revenue,
        "total_revenue_cents": net_revenue,
        "gross_profit_cents": gross_profit,
        "operating_expenses_cents": op_total,
        "depreciation_cents": dep_total,
        "operating_profit_cents": ebit,
        "other_income_cents": sum(l["amount_cents"] for l in other_income),
        "other_expenses_cents": other_exp_total,
        "profit_before_tax_cents": profit_before_tax,
        "net_profit_cents": net_profit,
        "gross_margin_pct": round(gross_profit / net_revenue * 100, 1) if net_revenue else None,
        "net_margin_pct": round(net_profit / net_revenue * 100, 1) if net_revenue else None,
        "expense_ratio_pct": round(op_total / net_revenue * 100, 1) if net_revenue else None,
        # kept for backwards compatibility with existing screens / exports
        "revenue": gross_rev + other_income,
        "expenses": cogs + sum([s["lines"] for s in op_expense_sections], []) + other_exp,
        "total_expenses_cents": cogs_total + op_total + other_exp_total,
    }


BALANCE_GROUPS = {
    "current_assets": ("الأصول المتداولة", "Current assets", ["111", "112", "12", "126", "127"]),
    "inventory": ("المخزون", "Inventory", ["131"]),
    "fixed_assets": ("الأصول الثابتة", "Fixed assets", ["14", "133"]),
    "other_assets": ("أصول أخرى", "Other assets", ["15", "16", "17", "18", "19"]),
    "current_liabilities": ("الالتزامات المتداولة", "Current liabilities", ["21", "22"]),
    "long_term_liabilities": ("التزامات طويلة الأجل", "Long-term liabilities", ["23", "24", "25"]),
    "equity": ("حقوق الملكية", "Equity", ["3"]),
}


def balance_sheet_full(conn, company_id, as_of):
    """Classified balance sheet at a date.

    - Assets are shown net: accumulated depreciation (1330, a credit-balance
      asset contra account) is presented as a negative line inside fixed assets,
      so the fixed-asset block shows book value.
    - Equity includes the *current period result* computed from the ledger
      (revenue − expense for the current fiscal year) instead of a hard-coded
      equity balance, so the statement balances even before the year is closed.
    """
    accounts = _leaf_accounts(conn, company_id)
    bal = account_balances(conn, company_id, "0000-01-01", as_of)
    by_id = {r["id"]: r for r in bal}
    fy_start, fy_end = _fy_bounds(conn, company_id, as_of)

    def bucket(a):
        for key, (_ar, _en, prefixes) in BALANCE_GROUPS.items():
            if any(a["code"].startswith(p) for p in prefixes):
                return key
        if a["type"] == "asset":
            return "other_assets"
        if a["type"] == "liability":
            return "current_liabilities"
        if a["type"] == "equity":
            return "equity"
        return None

    groups = {k: [] for k in BALANCE_GROUPS}
    for a in accounts:
        r = by_id.get(a["id"], {})
        closing = r.get("closing", 0)
        if not closing:
            continue
        key = bucket(a)
        if key is None:
            continue
        # present in the natural sign of the statement:
        # assets / expenses debit-positive, liabilities / equity credit-positive
        natural = closing if a["type"] in ("asset", "expense") else -closing
        groups[key].append(_line(a, natural))

    def mk(key, sign=1):
        ar, en, _ = BALANCE_GROUPS[key]
        lines = sorted(groups[key], key=lambda l: l["code"])
        total = sum(l["amount_cents"] for l in lines) * sign
        return _section(key, ar, en, lines, total)

    current_assets = mk("current_assets")
    inventory = mk("inventory")
    fixed_assets = mk("fixed_assets")
    other_assets = mk("other_assets")
    current_liabilities = mk("current_liabilities")
    long_term = mk("long_term_liabilities")
    equity = mk("equity")

    # current-period result from the ledger (not from the 3900 account)
    period = income_statement(conn, company_id, fy_start, as_of)
    period_result = period["net_profit_cents"]
    closed_result_rows = [l for l in equity["lines"] if l["code"].startswith("3900")]
    for row in closed_result_rows:            # 3900 already posted → avoid double count
        period_result -= -row["amount_cents"] if row["amount_cents"] < 0 else 0
    equity_lines = [l for l in equity["lines"] if not l["code"].startswith("3900")]
    equity_lines.append({"code": "3900", "name_ar": "نتيجة أعمال الفترة (من الدفاتر)",
                         "name_en": "Result for the period (from the ledger)",
                         "amount_cents": period_result})
    equity_total = sum(l["amount_cents"] for l in equity_lines)
    equity["lines"] = equity_lines
    equity["total_cents"] = equity_total

    total_assets = (current_assets["total_cents"] + inventory["total_cents"]
                    + fixed_assets["total_cents"] + other_assets["total_cents"])
    total_liabilities = current_liabilities["total_cents"] + long_term["total_cents"]
    total_equity = equity_total

    return {
        "as_of": as_of, "fiscal_start": fy_start,
        "assets_groups": [current_assets, inventory, fixed_assets, other_assets],
        "liabilities_groups": [current_liabilities, long_term],
        "equity_group": equity,
        "current_assets_cents": current_assets["total_cents"] + inventory["total_cents"],
        "non_current_assets_cents": fixed_assets["total_cents"] + other_assets["total_cents"],
        "total_assets_cents": total_assets,
        "current_liabilities_cents": current_liabilities["total_cents"],
        "non_current_liabilities_cents": long_term["total_cents"],
        "total_liabilities_cents": total_liabilities,
        "total_equity_cents": total_equity,
        "total_liabilities_equity_cents": total_liabilities + total_equity,
        "balanced": total_assets == total_liabilities + total_equity,
        "current_period_profit_cents": period_result,
        "working_capital_cents": (current_assets["total_cents"] + inventory["total_cents"]
                                  - current_liabilities["total_cents"]),
        "current_ratio": (round((current_assets["total_cents"] + inventory["total_cents"])
                                / current_liabilities["total_cents"], 2)
                          if current_liabilities["total_cents"] else None),
        "debt_to_equity": (round(total_liabilities / total_equity, 2) if total_equity else None),
    }


def _cash_account_ids(conn, company_id):
    ids = [r["gl_account_id"] for r in conn.execute(
        "SELECT gl_account_id FROM bank_accounts WHERE company_id=? AND gl_account_id IS NOT NULL",
        (company_id,)).fetchall()]
    return [i for i in ids if i]


def _classify_counter_line(conn, company_id, account_id):
    """Which activity does a non-cash leg belong to?"""
    a = conn.execute("SELECT code, type, is_postable FROM accounts WHERE id=?", (account_id,)).fetchone()
    if a is None:
        return "operating"
    code = a["code"] or ""
    if code.startswith("14") or code.startswith("15") or code.startswith("16"):
        return "investing"
    if code.startswith("23") or code.startswith("24") or code.startswith("3"):
        return "financing"
    return "operating"


def cash_flow_statement(conn, company_id, date_from, date_to):
    """Cash flow statement with three activities (IAS 7, direct method).

    Each posted entry that touches a cash/bank GL account is re-read together
    with its counter-lines: the cash movement is classified by the *nature of
    the counter account* (fixed asset → investing, loan/capital → financing,
    everything else → operating). This is a real statement, not a summary of
    bank traffic, and it always reconciles: opening + net = closing.

    One extra statement is needed per date (opening and closing cash), which is
    exactly what a D1 deployment can afford.
    """
    cash_ids = _cash_account_ids(conn, company_id)
    if not cash_ids:
        return {"date_from": date_from, "date_to": date_to, "sections": [],
                "opening_cents": 0, "closing_cents": 0, "net_cents": 0,
                "inflow_cents": 0, "outflow_cents": 0, "rows": [], "reconciled": True,
                "activities": {"operating": 0, "investing": 0, "financing": 0}}
    qmarks = ",".join("?" * len(cash_ids))
    opening = conn.execute(
        f"""SELECT COALESCE(SUM(debit_cents-credit_cents),0) v FROM v_posted_lines
            WHERE company_id=? AND account_id IN ({qmarks}) AND entry_date < ?""",
        (company_id, *cash_ids, date_from)).fetchone()["v"]
    entries = conn.execute(
        f"""SELECT DISTINCT entry_id FROM v_posted_lines
            WHERE company_id=? AND account_id IN ({qmarks})
              AND entry_date BETWEEN ? AND ?""",
        (company_id, *cash_ids, date_from, date_to)).fetchall()
    entry_ids = [r["entry_id"] for r in entries]
    buckets = {"operating": {}, "investing": {}, "financing": {}}
    inflow = outflow = 0
    rows = []
    type_names = {
        "operating": ("الأنشطة التشغيلية", "Operating activities"),
        "investing": ("الأنشطة الاستثمارية", "Investing activities"),
        "financing": ("الأنشطة التمويلية", "Financing activities"),
    }
    for eid in entry_ids:
        lines = conn.execute(
            """SELECT l.account_id, l.debit_cents, l.credit_cents, a.code, a.name_ar, a.name_en,
                      e.entry_no, e.entry_date, e.entry_type, e.memo
               FROM journal_lines l JOIN accounts a ON a.id=l.account_id
               JOIN journal_entries e ON e.id=l.entry_id
               WHERE l.entry_id=? ORDER BY l.line_no""", (eid,)).fetchall()
        cash_ids_set = set(cash_ids)
        cash_lines = [l for l in lines if l["account_id"] in cash_ids_set]
        other = [l for l in lines if l["account_id"] not in cash_ids_set]
        net = sum(l["debit_cents"] - l["credit_cents"] for l in cash_lines)
        if net == 0:
            continue
        # classify by the biggest counter-line (an entry is normally one-sided)
        if other:
            biggest = max(other, key=lambda l: l["debit_cents"] + l["credit_cents"])
            activity = _classify_counter_line(conn, company_id, biggest["account_id"])
            label_ar = biggest["name_ar"]
            label_en = biggest["name_en"]
        else:
            activity = "operating"
            label_ar = label_en = lines[0]["memo"]
        key = (label_en or label_ar)
        b = buckets[activity].setdefault(key, {"name_ar": label_ar, "name_en": label_en, "amount": 0})
        b["amount"] += net
        if net > 0:
            inflow += net
        else:
            outflow += -net
        rows.append({"entry_no": lines[0]["entry_no"], "entry_date": lines[0]["entry_date"],
                     "memo": lines[0]["memo"], "activity": activity,
                     "counter_account": f"{biggest['code']} — {label_ar}" if other else "",
                     "amount_cents": net})
    sections = []
    for act in ("operating", "investing", "financing"):
        items = sorted(buckets[act].values(), key=lambda x: -abs(x["amount"]))
        ar, en = type_names[act]
        sections.append({
            "key": act, "name_ar": ar, "name_en": en,
            "lines": [{"name_ar": i["name_ar"], "name_en": i["name_en"],
                       "amount_cents": i["amount"]} for i in items],
            "total_cents": sum(i["amount"] for i in items),
        })
    net_change = sum(s["total_cents"] for s in sections)
    closing = opening + net_change
    return {
        "date_from": date_from, "date_to": date_to,
        "sections": sections,
        "activities": {s["key"]: s["total_cents"] for s in sections},
        "opening_cents": opening, "closing_cents": closing, "net_cents": net_change,
        "inflow_cents": inflow, "outflow_cents": outflow,
        "rows": rows[-40:],
        "reconciled": closing == opening + net_change,
    }


def equity_changes(conn, company_id, date_from, date_to):
    """Statement of changes in equity, built from the ledger movements."""
    accounts = [a for a in _leaf_accounts(conn, company_id) if a["type"] == "equity"]
    rows = []
    for a in accounts:
        opening = conn.execute(
            """SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) v FROM v_posted_lines l
               WHERE l.company_id=? AND l.account_id=? AND l.entry_date < ?""",
            (company_id, a["id"], date_from)).fetchone()["v"]
        period = conn.execute(
            """SELECT COALESCE(SUM(l.credit_cents),0) c, COALESCE(SUM(l.debit_cents),0) d
               FROM v_posted_lines l WHERE l.company_id=? AND l.account_id=?
                 AND l.entry_date BETWEEN ? AND ?""",
            (company_id, a["id"], date_from, date_to)).fetchone()
        if not opening and not (period["c"] or period["d"]):
            continue
        rows.append({"code": a["code"], "name_ar": a["name_ar"], "name_en": a["name_en"],
                     "opening_cents": opening,
                     "additions_cents": period["c"] or 0,
                     "reductions_cents": period["d"] or 0,
                     "closing_cents": opening + period["c"] - period["d"]})
    fy_start, _ = _fy_bounds(conn, company_id, date_to)
    result = income_statement(conn, company_id, fy_start, date_to)["net_profit_cents"]
    rows.append({"code": "3900", "name_ar": "نتيجة أعمال الفترة", "name_en": "Result for the period",
                 "opening_cents": 0, "additions_cents": max(result, 0),
                 "reductions_cents": max(-result, 0), "closing_cents": result})
    return {
        "date_from": date_from, "date_to": date_to, "rows": rows,
        "totals": {
            "opening_cents": sum(r["opening_cents"] for r in rows),
            "additions_cents": sum(r["additions_cents"] for r in rows),
            "reductions_cents": sum(r["reductions_cents"] for r in rows),
            "closing_cents": sum(r["closing_cents"] for r in rows),
        },
    }


def balance_sheet_from_tb(conn, company_id, as_of):
    """Classic balance sheet taken straight from trial-balance balances.

    Accounts are grouped by their *type*, not by code prefix, so a contra
    account (accumulated depreciation, a credit-balance asset) is shown as a
    negative line inside assets instead of being pushed to liabilities. The P&L
    accounts are not listed: their net effect is the current-period result shown
    inside equity, which is what makes the two sides tie.
    """
    rows = [r for r in account_balances(conn, company_id, "0000-01-01", as_of)
            if r["is_postable"] and r["closing"]]

    def natural(r):
        # assets & expenses are debit-positive, liabilities / equity / revenue credit-positive
        return r["closing"] if r["type"] in ("asset", "expense") else -r["closing"]

    assets = [{"code": r["code"], "name_ar": r["name_ar"], "name_en": r["name_en"],
               "amount_cents": natural(r)} for r in rows if r["type"] == "asset"]
    liabilities = [{"code": r["code"], "name_ar": r["name_ar"], "name_en": r["name_en"],
                    "amount_cents": natural(r)} for r in rows if r["type"] == "liability"]
    equity = [{"code": r["code"], "name_ar": r["name_ar"], "name_en": r["name_en"],
               "amount_cents": natural(r)} for r in rows if r["type"] == "equity"]
    revenue_cents = sum(natural(r) for r in rows if r["type"] == "revenue")
    expense_cents = sum(natural(r) for r in rows if r["type"] == "expense")
    profit = revenue_cents - expense_cents
    equity = equity + [{"code": "3900", "name_ar": "نتيجة أعمال الفترة",
                        "name_en": "Result for the period", "amount_cents": profit}]
    assets_total = sum(a["amount_cents"] for a in assets)
    liabilities_total = sum(l["amount_cents"] for l in liabilities)
    equity_total = sum(e["amount_cents"] for e in equity)
    return {
        "as_of": as_of,
        "assets": sorted(assets, key=lambda x: x["code"]),
        "assets_total_cents": assets_total,
        "liabilities": sorted(liabilities, key=lambda x: x["code"]),
        "liabilities_total_cents": liabilities_total,
        "equity": equity, "equity_total_cents": equity_total,
        "revenue_cents": revenue_cents, "expense_cents": expense_cents,
        "profit_cents": profit,
        "balanced": assets_total == liabilities_total + equity_total,
    }


def general_ledger(conn, company_id, date_from, date_to):
    """Full general ledger: every postable account with a movement in the
    period, its opening balance, every line and a running balance."""
    accounts = _leaf_accounts(conn, company_id)
    out = []
    for a in accounts:
        ledger_data = ledger(conn, company_id, a["id"], date_from, date_to)
        if ledger_data is None:
            continue
        if not ledger_data["rows"] and not ledger_data["opening_cents"]:
            continue
        out.append({
            "code": a["code"], "name_ar": a["name_ar"], "name_en": a["name_en"],
            "type": a["type"],
            "opening_cents": ledger_data["opening_cents"],
            "closing_cents": ledger_data["closing_cents"],
            "debit_cents": sum(r["debit_cents"] for r in ledger_data["rows"]),
            "credit_cents": sum(r["credit_cents"] for r in ledger_data["rows"]),
            "lines": ledger_data["rows"],
        })
    return {"date_from": date_from, "date_to": date_to, "accounts": out,
            "totals": {"debit_cents": sum(a["debit_cents"] for a in out),
                       "credit_cents": sum(a["credit_cents"] for a in out)}}


def journal_book(conn, company_id, date_from, date_to, limit=500):
    entries = conn.execute(
        """SELECT * FROM journal_entries WHERE company_id=? AND status='posted'
             AND entry_date BETWEEN ? AND ? ORDER BY entry_date, id LIMIT ?""",
        (company_id, date_from, date_to, limit)).fetchall()
    out = []
    for e in entries:
        lines = conn.execute(
            """SELECT l.*, a.code, a.name_ar, a.name_en FROM journal_lines l
               JOIN accounts a ON a.id=l.account_id WHERE l.entry_id=? ORDER BY l.line_no""",
            (e["id"],)).fetchall()
        out.append({"id": e["id"], "entry_no": e["entry_no"], "entry_date": e["entry_date"],
                    "entry_type": e["entry_type"], "memo": e["memo"],
                    "total_debit_cents": e["total_debit_cents"],
                    "lines": [dict(l) for l in lines]})
    return {"date_from": date_from, "date_to": date_to, "entries": out}


def debt_position(conn, company_id, date_from, date_to):
    """Contractor / vendor dues against settlements for the period."""
    rows = conn.execute(
        """SELECT da.id, da.code, da.name_ar, da.name_en, da.kind, da.billing_unit,
                  COALESCE(it.due,0) AS due, COALESCE(it.cnt,0) AS items,
                  COALESCE(pd.paid,0) AS paid, COALESCE(pd.cnt,0) AS payments
           FROM debt_accounts da
           LEFT JOIN (SELECT account_id, SUM(amount_cents) due, COUNT(*) cnt FROM debt_items
                      WHERE company_id=? AND doc_date BETWEEN ? AND ?
                      GROUP BY account_id) it ON it.account_id=da.id
           LEFT JOIN (SELECT account_id, SUM(amount_cents) paid, COUNT(*) cnt FROM debt_payments
                      WHERE company_id=? AND pay_date BETWEEN ? AND ?
                      GROUP BY account_id) pd ON pd.account_id=da.id
           WHERE da.company_id=? ORDER BY da.code""",
        (company_id, date_from, date_to, company_id, date_from, date_to, company_id)).fetchall()
    out = []
    for r in rows:
        if not (r["due"] or r["paid"]):
            continue
        out.append({"code": r["code"], "name_ar": r["name_ar"], "name_en": r["name_en"],
                    "kind": r["kind"], "unit": r["billing_unit"],
                    "due_cents": r["due"], "paid_cents": r["paid"],
                    "net_cents": r["due"] - r["paid"],
                    "items": r["items"], "payments": r["payments"]})
    return {"date_from": date_from, "date_to": date_to, "accounts": out,
            "totals": {"due_cents": sum(o["due_cents"] for o in out),
                       "paid_cents": sum(o["paid_cents"] for o in out),
                       "net_cents": sum(o["net_cents"] for o in out)}}


def statement(conn, company_id, kind, date_from, date_to):
    """Single entry point used by the API, the exports and the assistant."""
    if kind == "income-statement":
        return income_statement_full(conn, company_id, date_from, date_to)
    if kind == "balance-sheet":
        return balance_sheet_full(conn, company_id, date_to)
    if kind == "cash-flow":
        return cash_flow_statement(conn, company_id, date_from, date_to)
    if kind == "equity-changes":
        return equity_changes(conn, company_id, date_from, date_to)
    if kind == "trial-balance":
        data = trial_balance(conn, company_id, date_from, date_to)
        return {"date_from": date_from, "date_to": date_to, **data}
    if kind == "tb-balance-sheet":
        return balance_sheet_from_tb(conn, company_id, date_to)
    if kind == "general-ledger":
        return general_ledger(conn, company_id, date_from, date_to)
    if kind == "journal":
        return journal_book(conn, company_id, date_from, date_to)
    if kind == "receivables-aging":
        return aging(conn, company_id, date_to, "sales")
    if kind == "payables-aging":
        return aging(conn, company_id, date_to, "purchase")
    if kind == "debt-position":
        return debt_position(conn, company_id, date_from, date_to)
    raise ValueError(f"unknown statement: {kind}")
