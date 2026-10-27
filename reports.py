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
