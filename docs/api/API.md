# واجهات API — ELTARANISI ERP

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
| DELETE | `/api/customers/{id}` | `customers.delete` |
| DELETE | `/api/vendors/{id}` | `vendors.delete` |

## المبيعات

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/sales-invoices?status=` | `sales.view` |
| GET | `/api/sales-invoices/{id}` | `sales.view` |
| POST | `/api/sales-invoices` | `sales.create` |
| POST | `/api/sales-invoices/{id}/post` | `sales.post` |
| DELETE | `/api/sales-invoices/{id}` | `sales.delete` |

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
| DELETE | `/api/purchase-invoices/{id}` | `purchases.delete` |

طلب الإنشاء يدعم `supplier_ref` (لمنع التكرار)، `po_number` و`grn_number` (للمطابقة الثلاثية).

## المدفوعات والتحصيلات

| Method | Path | الصلاحية |
|---|---|---|
| GET | `/api/payments` | `payments.view` |
| POST | `/api/payments` | `payments.create` |
| POST | `/api/payments/{id}/post` | `payments.post` |
| DELETE | `/api/payments/{id}` | `payments.delete` |

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

## الحذف (DELETE) — العقد الكامل

كل حذف يتطلب جسم الطلب:

```json
{ "confirm": true }
```

وبدونه يرد الخادم `428 CONFIRM_REQUIRED` (حماية من طلب مُلفَّق/نقرة خاطئة).

| Method | Path | الصلاحية | ملاحظات |
|---|---|---|---|
| DELETE | `/api/sales-invoices/{id}` | `sales.delete` | يحذف السطور + القيد + حركات المخزون + مستند الفاتورة الإلكترونية |
| DELETE | `/api/purchase-invoices/{id}` | `purchases.delete` | نفس السلوك |
| DELETE | `/api/payments/{id}` | `payments.delete` | يفكّ التخصيصات ويعيد رصيد الفواتير إلى `posted` |
| DELETE | `/api/customers/{id}` | `customers.delete` | `409 HAS_DOCUMENTS` إن كان مرتبطًا بفواتير/سندات |
| DELETE | `/api/vendors/{id}` | `vendors.delete` | نفس القاعدة |
| DELETE | `/api/journal-entries/{id}` | `journal.delete` | قيد يدوي فقط؛ القيود النظامية `409 SYSTEM_ENTRY` |
| DELETE | `/api/products/{id}` | `inventory.manage` | `409 IN_USE` إن استُخدم في فواتير/حركات |
| DELETE | `/api/bank-transactions/{id}` | `banks.manage` | `409 RECONCILED` للحركات المطابَقة |
| DELETE | `/api/inventory/movements/{id}` | `inventory.manage` | حركة يدوية فقط |
| DELETE | `/api/assets/{id}` | `assets.manage` | `409 HAS_DEPRECIATION` بعد الإهلاك |
| DELETE | `/api/employees/{id}` | `payroll.manage` | `409 HAS_PAYROLL` بعد دخوله مسير رواتب |
| DELETE | `/api/budgets/{id}` | `budgets.manage` | بند موازنة واحد |
| DELETE | `/api/accounts/{id}` | `accounts.create` | حساب بلا حركات |

أكواد الأخطاء: `CONFIRM_REQUIRED` (428)، `HAS_PAYMENTS` / `HAS_DOCUMENTS` / `SYSTEM_ENTRY` /
`IN_USE` / `RECONCILED` / `HAS_DEPRECIATION` / `HAS_PAYROLL` (409)، `PERIOD_CLOSED` (422)، `NOT_FOUND` (404).

استجابة ناجحة:

```json
{ "ok": true, "deleted": "sales_invoice", "id": 51, "number": "SI-2026-00051" }
```

كل حذف يُسجَّل في `audit_logs` **بلقطة كاملة للسجل قبل الحذف** (`action = "<entity>.delete"`).

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

---

## 6. Editing records (PATCH)

Every record that can be created can also be corrected — while it is still a
draft, inside an open fiscal period, and by someone who holds the matching
`*.edit` permission. Editing is **not** posting: it never creates a journal
entry, and it never touches a document that already hit the ledger. To change a
posted document, reverse it and re-issue it.

| Endpoint | Permission | Notes |
|---|---|---|
| `PATCH /api/customers/{id}` | `customers.edit` | code must stay unique |
| `PATCH /api/vendors/{id}` | `vendors.edit` | |
| `PATCH /api/products/{id}` | `inventory.edit` | |
| `PATCH /api/employees/{id}` | `payroll.edit` | salary structure editable until a payroll run uses it |
| `PATCH /api/bank-accounts/{id}` | `banks.edit` | new: `POST /api/bank-accounts` creates a bank / cash box |
| `PATCH /api/bank-transactions/{id}` | `banks.edit` | reconciled lines are frozen (409 `RECONCILED`) |
| `PATCH /api/assets/{id}` | `assets.edit` | cost & life locked after depreciation (409 `HAS_DEPRECIATION`) |
| `PATCH /api/budgets/{id}` | `budgets.edit` | |
| `PATCH /api/journal-entries/{id}` | `journal.edit` | manual drafts only; `lines` replaces the whole entry and must balance |
| `PATCH /api/sales-invoices/{id}` | `sales.edit` | drafts only |
| `PATCH /api/purchase-invoices/{id}` | `purchases.edit` | drafts only |
| `PATCH /api/payments/{id}` | `payments.edit` | drafts only |

### Semantics

* **Partial update** — send only the fields you want to change; everything else is
  left untouched. An empty body answers **422 `NOTHING_TO_UPDATE`**.
* **Whitelisted fields** — unknown keys are ignored, never written.
* **Response** `{"ok": true, "updated": "customer", "id": 12, "changed": ["name_ar","phone"]}`.
* **Errors** — `403 FORBIDDEN`, `404 NOT_FOUND`, `409 DUPLICATE`,
  `409 ALREADY_POSTED`, `409 SYSTEM_ENTRY`, `409 RECONCILED`,
  `409 HAS_DEPRECIATION`, `422 PERIOD_CLOSED`, `422 NOTHING_TO_UPDATE`.
* **Company isolation** — the row is looked up with `company_id` in the `WHERE`
  clause, so an id from another company is simply "not found".
* **Audit** — one row per edit: `{before, after, changed}` plus actor, IP and
  timestamp, visible in سجل التدقيق / Audit log.

### Permission model

`*.edit` is a first-class permission in the catalogue, and it is granted
automatically to every role that already holds the matching create/manage right
(`customers.create` → `customers.edit`, `banks.manage` → `banks.edit`, …). It can
also be granted on its own from the roles screen — useful for a data-entry clerk
who may correct typos but not create new records. System roles are re-synced from
code on every server start, so a deployment can add permissions without a reseed.

### UI behaviour

Each list row and each detail page shows a pencil next to the bin. The pencil
opens the same validated form used for creation, pre-filled with the current
values; saving asks **«هل أنت متأكد من تعديل …؟»** naming the record. Nothing is
sent until the user confirms, and if the server rejects the change the form is
restored with the typed values so no input is lost.
