# واجهات API — Eltaranisi ERP

- الأساس: `http://localhost:8000`
- المنسّق: JSON (`Content-Type: application/json`)
- المصادقة: `Authorization: Bearer <token>` (من `/api/auth/login`)
- كل المبالغ في الاستجابات **بالقرش** (`*_cents`). للتحويل إلى جنيه: اقسم على 100.
- الأخطاء موحّدة: `{ "error": "CODE: رسالة عربية / English message", "details": {...} }`
  وأكواد الحالة: `401` غير مصادق، `403` صلاحية، `404` غير موجود، `422` قاعدة محاسبية/تحقق، `500` خطأ داخلي.

## المصادقة

| Method | Path | الصلاحية | الوصف |
|---|---|---|---|
| POST | `/api/auth/login` | عام | `{username, password}` → `{token, user}` |
| POST | `/api/auth/logout` | مصادق | إلغاء الجلسة الحالية |
| GET | `/api/auth/me` | مصادق | بيانات المستخدم + صلاحياته |
| PATCH | `/api/auth/preferences` | مصادق | `{language: ar\|en, theme: light\|dark\|system}` |
| POST | `/api/auth/change-password` | مصادق | `{current_password, new_password}` (≥10 أحرف) |

## البيانات المرجعية

| Method | Path | الصلاحية | الوصف |
|---|---|---|---|
| GET | `/api/meta` | مصادق | الشركات، الفروع، مراكز التكلفة، المستودعات، السنوات المالية، الأدوار |
| GET | `/api/health` | عام | فحص صحة |

## لوحة التحكم

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/dashboard?as_of=` | `dashboard.view` |

تُرجع: مؤشرات (إيرادات/مصروفات/صافي ربح/سيولة/مديونية موردين ومتأخراتها/مستحقات عملاء/قيمة مخزون/اعتمادات معلقة)،
سلسلة 6 أشهر، أرصدة بنكية، أعلى مديونيات الموردين، أعمار الديون المدينة والدائنة، آخر القيود.

## دليل الحسابات

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/accounts` | `accounts.view` |
| POST | `/api/accounts` | `accounts.create` |
| PATCH | `/api/accounts/{id}` | `accounts.create` |
| DELETE | `/api/accounts/{id}` | `accounts.create` |

`GET /api/accounts` يعيد لكل حساب `balance_cents` (خام) و`display_balance_cents` (طبيعي: الأصول
والمصروفات مدينة موجبة، والبقية دائنة موجبة).

## القيود اليومية

| Method | Path | الصلاحية | ملاحظات |
|---|---|---|---|
| GET | `/api/journal-entries?status=&q=&from=&to=&page=&per_page=` | `journal.view` | |
| GET | `/api/journal-entries/{id}` | `journal.view` | القيد + السطور |
| POST | `/api/journal-entries` | `journal.create` | `{entry_date, memo, lines:[{account_id, debit, credit, description}], idempotency_key?}` |
| POST | `/api/journal-entries/{id}/submit` | `journal.create` | لا يجوز للمنشئ |
| POST | `/api/journal-entries/{id}/review` | `journal.review` | `{decision:"approve"\|"reject", comment}` |
| POST | `/api/journal-entries/{id}/approve` | `journal.approve` | للقيود ≥ 200,000 ج.م |
| POST | `/api/journal-entries/{id}/post` | `journal.post` | يتطلب `approved` + فترة مفتوحة |
| POST | `/api/journal-entries/{id}/reverse` | `journal.reverse` | `{reason, date}` |

أكواد الأخطاء المحاسبية: `UNBALANCED`, `PERIOD_CLOSED`, `PERIOD_NOT_DEFINED`, `NOT_APPROVED`,
`ALREADY_POSTED`, `SOD_VIOLATION`, `NON_POSTABLE_ACCOUNT`, `INACTIVE_ACCOUNT`, `INVALID_STATE`.

## العملاء والموردون

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/customers` | `customers.view` |
| POST | `/api/customers` | `customers.create` |
| GET | `/api/vendors` | `vendors.view` |
| POST | `/api/vendors` | `vendors.create` |
| GET | `/api/vendors/{id}/statement` | `vendors.view` |

## المبيعات

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/sales-invoices?status=` | `sales.view` |
| GET | `/api/sales-invoices/{id}` | `sales.view` |
| POST | `/api/sales-invoices` | `sales.create` |
| POST | `/api/sales-invoices/{id}/post` | `sales.post` |

طلب الإنشاء:
```json
{
  "customer_id": 1, "invoice_date": "2026-10-20", "due_date": "2026-11-19",
  "warehouse_id": 1, "post": true,
  "lines": [{"product_id": 1, "qty": 4, "unit_price": 150, "discount": 0, "tax_rate_bp": 1400}]
}
```
الأخطاء: `CREDIT_LIMIT`, `DUPLICATE`, `WAREHOUSE_REQUIRED`, `INSUFFICIENT_STOCK`, `NOT_FOUND`.

## المشتريات

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/purchase-invoices?status=` | `purchases.view` |
| GET | `/api/purchase-invoices/{id}` | `purchases.view` |
| POST | `/api/purchase-invoices` | `purchases.create` |
| POST | `/api/purchase-invoices/{id}/post` | `purchases.post` |

طلب الإنشاء يدعم `supplier_ref` (لمنع التكرار)، `po_number` و`grn_number` (للمطابقة الثلاثية).

## المدفوعات والتحصيلات

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/payments` | `payments.view` |
| POST | `/api/payments` | `payments.create` |
| POST | `/api/payments/{id}/post` | `payments.post` |

```json
{
  "payment_type": "receipt",           // receipt | payment | expense | employee_advance
  "payment_date": "2026-10-20", "party_type": "customer", "party_id": 1,
  "method": "bank", "gl_account_id": 7, "amount": 300, "post": true,
  "allocations": [{"invoice_kind": "sales", "invoice_id": 55, "amount": 300}]
}
```
الأخطاء: `OVER_ALLOCATION`, `INVALID_STATE`, `INVALID_ACCOUNT`.

## المديونيات وأعمار الديون

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/payables?as_of=` | `payables.view` |
| GET | `/api/receivables?as_of=` | `customers.view` |

`/api/payables` يعيد لكل مورد: `invoiced_cents`, `paid_cents`, `outstanding_cents`,
`journal_balance_cents`, `overdue_cents`, `next_due_date`, `buckets{current,1_30,31_60,61_90,over_90}`،
بالإضافة إلى `totals` و`aging` و`invoices` (الفواتير المفتوحة).

## المخزون

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/products` | `inventory.view` |
| POST | `/api/products` | `inventory.manage` |
| GET | `/api/inventory` | `inventory.view` |
| POST | `/api/inventory/movements` | `inventory.manage` | `{move_type: in\|out\|transfer\|adjust, product_id, warehouse_id, qty, unit_cost?, target_warehouse_id?}` |

## البنوك والتسويات

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/banks` | `banks.view` |
| GET | `/api/bank-transactions?bank_account_id=` | `banks.view` |
| POST | `/api/bank-transactions` | `banks.manage` |
| GET | `/api/bank-transactions/suggestions` | `banks.manage` |
| POST | `/api/bank-transactions/{id}/reconcile` | `banks.manage` | `{payment_id}` — **لا مطابقة آلية دون تأكيد** |

## الأصول والرواتب والموازنات

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/assets` | `assets.view` |
| POST | `/api/assets` | `assets.manage` |
| POST | `/api/assets/depreciation` | `assets.manage` | `{period_start, period_end}` |
| GET | `/api/employees` | `payroll.view` |
| POST | `/api/employees` | `payroll.manage` |
| GET | `/api/payroll` | `payroll.view` |
| POST | `/api/payroll/run` | `payroll.manage` | `{period: "YYYY-MM"}` |
| GET | `/api/budgets?fiscal_year_id=` | `budgets.view` |
| POST | `/api/budgets` | `budgets.manage` |

## التقارير

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/reports/trial-balance?from=&to=` | `reports.view` |
| GET | `/api/reports/income-statement?from=&to=` | `reports.view` |
| GET | `/api/reports/balance-sheet?as_of=` | `reports.view` |
| GET | `/api/reports/cash-flow?from=&to=` | `reports.view` |
| GET | `/api/reports/aging?kind=sales\|purchase&as_of=` | `reports.view` |
| GET | `/api/reports/vendor-payables?as_of=` | `payables.view` |
| GET | `/api/reports/ledger?account_id=&from=&to=` | `reports.view` |
| GET | `/api/reports/budget-variance` | `budgets.view` |
| GET | `/api/reports/export?kind=trial-balance\|income-statement\|vendor-payables\|aging\|journal` | `reports.export` |

CSV يُصدَّر بـ UTF-8 BOM حتى تُفتح العربية صحيحة في Excel.

## النظام

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/users` | `users.manage` |
| POST | `/api/users` | `users.manage` |
| PATCH | `/api/users/{id}` | `users.manage` |
| GET | `/api/audit-logs?limit=` | `audit.view` |
| GET | `/api/notifications` | مصادق |
| POST | `/api/notifications/{id}/read` | مصادق |
| POST | `/api/notifications/read-all` | مصادق |
| GET | `/api/fiscal-periods` | `reports.view` |
| POST | `/api/fiscal-periods/{id}/close` | `period.close` |
| POST | `/api/fiscal-periods/{id}/reopen` | `period.close` |
| POST | `/api/assistant/ask` | `ai.use` | `{question}` → `{answer, source, data}` |

### تصدير المستندات (PDF / Excel / CSV)

كل المسارات التالية تُعيد ملفًا عبر المغلف `{"__file__", "filename", "content_type", "inline"}` — ويجب أن
يُطلب بـ `Authorization: Bearer <token>` (لا روابط عامة).

| Method | Path | الصلاحية | ملاحظة |
|---|---|---|---|
| GET | `/api/sales-invoices/{id}/pdf?lang=ar\|en` | `sales.view` | PDF بخط عربي مضمَّن + ترويسة الشركة |
| GET | `/api/sales-invoices/{id}/xlsx` | `sales.view` | Excel RTL |
| GET | `/api/sales-invoices/{id}/einvoice` | `sales.view` | JSON جاهزية (UUID + SHA-256، غير موقّع) — يُخزَّن ويُدقَّق |
| GET | `/api/purchase-invoices/{id}/pdf\|xlsx` | `purchases.view` | نفس الصيغ لفاتورة المشتريات |
| GET | `/api/reports/pdf?kind=&lang=&from=&to=` | `reports.view` | `kind` ∈ trial-balance, income-statement, balance-sheet, cash-flow, aging, vendor-payables |
| GET | `/api/reports/xlsx?kind=` | `reports.export` | نفس الأنواع (RTL + تنسيق أرقام) |
| GET | `/api/reports/export?kind=` | `reports.export` | CSV بترميز UTF-8 مع BOM |

### إعدادات الشركة

| Method | Path | الصلاحية | ملاحظة |
|---|---|---|---|
| GET | `/api/company` | `reports.view` | `{company, settings, can_edit}` |
| PATCH | `/api/company` | `settings.manage` | تحديث جزئي: لا يمسح الحقول غير المُرسلة؛ `name_ar/name_en` مطلوبان إن أُرسلا |
| GET | `/api/dev/accounts` | **عام** | حسابات التطوير لنقرة دخول واحدة — 404 عند `ERP_SEED_DEMO_DATA=false` |

## مثال كامل (cURL)

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"mahmoud.morad","password":"Mahmoud@2026"}' | jq -r .token)

curl -s localhost:8000/api/payables -H "Authorization: Bearer $TOKEN" | jq '.totals'
curl -s "localhost:8000/api/reports/trial-balance?from=2026-01-01&to=2026-10-20" \
  -H "Authorization: Bearer $TOKEN" | jq '{balanced,totals}'
curl -s "localhost:8000/api/sales-invoices/54/pdf?lang=ar" \
  -H "Authorization: Bearer $TOKEN" -o invoice.pdf   # PDF عربي بخط مضمَّن
```

## OpenAPI

مخطط OpenAPI 3.1 مختصر في `docs/api/openapi.yaml` (يغطي المسارات الأساسية والصلاحيات) — قابل للاستيراد
في Postman/Swagger UI.
