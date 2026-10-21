# قاعدة البيانات — ELTARANISI ERP

المخطط الكامل في `schema.sql` (46 جدولًا + عرض `v_posted_lines`).

## 1. مبادئ التصميم

1. **كل المبالغ أعداد صحيحة** بالوحدة الصغرى (قرش): `*_cents INTEGER`. لا `FLOAT` لأي قيمة مالية.
2. **الكميات** `*_milli INTEGER` (الكمية × 1000) لدعم 3 منازل عشرية.
3. **عزل الشركات** بعمود `company_id` في كل جدول تشغيلي، ويُفحص في كل استعلام على الخادم.
4. **التاريخ** نص ISO `YYYY-MM-DD` — قابل للفرز والمقارنة وقابل للترحيل إلى `DATE` في PostgreSQL.
5. **لا حذف للبيانات المالية**: القيود المرحّلة تُعكس؛ الحسابات المستخدمة تُعطّل لا تُحذف.

## 2. الجداول

| المجموعة | الجداول |
|---|---|
| التأسيس | `organizations`, `companies`, `branches` |
| الهوية والصلاحيات | `users`, `roles`, `sessions`, `user_company_access`, `audit_logs` |
| التقويم المالي | `fiscal_years`, `fiscal_periods` |
| دليل الحسابات | `accounts`, `cost_centers`, `projects` |
| نواة القيود | `journal_entries`, `journal_lines` |
| الأطراف | `customers`, `vendors` |
| المخزون | `warehouses`, `products`, `stock_movements` |
| المبيعات | `sales_invoices`, `sales_invoice_lines` |
| المشتريات | `purchase_invoices`, `purchase_invoice_lines` |
| النقدية | `payments`, `payment_allocations`, `bank_accounts`, `bank_transactions` |
| الأصول | `fixed_assets`, `depreciation_runs`, `depreciation_lines` |
| الموارد البشرية | `employees`, `payroll_runs`, `payroll_lines` |
| المديونية (شحن وتفريغ) | `debt_accounts`, `debt_items`, `debt_payments` |
| التخطيط | `budgets` |
| النظام | `notifications`, `approval_rules`, `approval_steps`, `settings`, `attachments`, `exchange_rates`, `document_sequences` |

### 2.1 نواة القيود (أهم جدولين)

```sql
journal_entries(
  id, company_id, branch_id, entry_no UNIQUE(company,entry_no), entry_date, entry_type,
  memo, status CHECK IN (draft, review, approved, posted, reversed),
  currency, fx_rate, total_debit_cents, total_credit_cents,
  cost_center_id, project_id, source_type, source_id,
  reversal_of_id, reversed_by_id, idempotency_key UNIQUE,
  created_by/at, submitted_by/at, approved_by/at, posted_by/at,
  CHECK (status <> 'posted'   OR total_debit_cents = total_credit_cents),
  CHECK (status <> 'reversed' OR total_debit_cents = total_credit_cents)
)

journal_lines(
  id, entry_id → journal_entries ON DELETE CASCADE, line_no, account_id → accounts,
  debit_cents CHECK >= 0, credit_cents CHECK >= 0, description,
  cost_center_id, project_id, customer_id, vendor_id, foreign_amount,
  CHECK ((debit>0 AND credit=0) OR (credit>0 AND debit=0)),   -- سطر واحد الجانب فقط
  UNIQUE(entry_id, line_no)
)
```

### 2.1.1 جداول المديونية / Debt sub-ledger

الوحدة تحاكي بيان المديونية الورقي لشركة شحن وتفريغ، لذلك لا تعتمد على أصناف
مخزنية بل على **كمية × فئة وحدة**:

| الجدول | الغرض | الحقول المهمة |
|---|---|---|
| `debt_accounts` | حساب مديونية لكل مقاول/مورد | `code`, `kind` (contractor/labor/equipment/transport/fuel/workshop/supplies/other), `billing_unit` (ton/trip/container/hour/month/item), `expense_account_id`, `vendor_id`, `opening_balance_cents`, `is_active` |
| `debt_items` | سطر مستحق (البيان) | `item_no` (DEB-YYYY-NNNNN), `doc_date`, `description`, `operation` (شحن/تفريغ/نقل/إيجار/مشتريات), `qty_milli`, `unit`, `unit_rate_cents`, `amount_cents`, `vessel`, `client_name`, `cargo`, `period_from`, `period_to`, `report_no`, `status`, `journal_entry_id` |
| `debt_payments` | سداد (اذن صرف) | `payment_no` (DPV-YYYY-NNNNN), `voucher_no`, `pay_date`, `amount_cents`, `method`, `gl_account_id`, `payee`, `status`, `journal_entry_id` |

* `amount_cents = round(qty_milli × unit_rate_cents / 1000)` ما لم يُدخَل المبلغ صريحًا.
* **صافي المستحق** = `opening_balance_cents` + مجموع المستحقات المرحّلة − مجموع السدادات المرحّلة، ويُحسب دائمًا على الخادم.
* كل مستحق/سداد مرحّل يملك `journal_entry_id`؛ وعند التعديل تُفصل العلاقة
  (`journal_entry_id = NULL`) ثم يُحذف القيد القديم ويُنشأ قيد جديد — لذلك لا
  تصطدم قيود المفاتيح الأجنبية عند الإلغاء وإعادة الترحيل.
* فهارس: `(company_id, account_id, doc_date)` و`(company_id, account_id, pay_date)` و`(company_id, kind)`.

### 2.2 عرض التقارير

```sql
CREATE VIEW v_posted_lines AS
SELECT l.*, e.company_id, e.branch_id, e.entry_date, e.entry_no, e.entry_type
FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id
WHERE e.status IN ('posted','reversed');
```
كل التقارير المالية تقرأ من هذا العرض فقط ⇒ **لا يمكن أن تظهر مسودة في قوائم مالية**.

## 3. القيود والفهارس المهمة

| النوع | أمثلة |
|---|---|
| UNIQUE | `(company_id, code)` للحسابات/العملاء/الموردين/المخازن/الأصول · `(company_id, entry_no)` · `idempotency_key` · `(fiscal_year_id, period_no)` |
| CHECK | توازن القيد المرحّل · سطر مدين/دائن واحد · `debit_cents >= 0` · `status` من مجموعة محددة · `amount_cents > 0` |
| FOREIGN KEY | كل علاقة بين الكيانات (مع `ON DELETE CASCADE` فقط لسطور المستندات) |
| INDEX | `journal_lines(entry_id)`, `journal_lines(account_id)`, `journal_entries(company_id, entry_date)`, `journal_entries(company_id, status)`, `stock_movements(company_id, product_id, move_date)`, `payment_allocations(invoice_kind, invoice_id)`, `notifications(user_id, is_read)` |

## 4. منع التزامن وإعادة الإرسال

| السيناريو | الحماية |
|---|---|
| إرسال نفس الطلب مرتين | `idempotency_key` فريد على القيود/الفواتير/السندات |
| ترحيل نفس الفاتورة مرتين | فحص الحالة `draft` + معاملة `BEGIN IMMEDIATE` + `UPDATE ... WHERE status='draft'` |
| ترحيل نفس القيد مرتين | `WHERE status='approved'` في جملة التحديث |
| تعارض كتابة متزامن | `BEGIN IMMEDIATE` + `busy_timeout=15s` + WAL |
| ترقيم مكرر | جدول `document_sequences` + تحديث ذري `last_number + 1` داخل نفس المعاملة |
| تخصيص دفعة مرتين لنفس الفاتورة | فحص `≤ total - paid` قبل الإدراج |

## 5. الأرشفة والاحتفاظ

- `audit_logs`: للقراءة فقط من التطبيق (لا يوجد مسار تعديل/حذف في الـ API).
- `attachments`: مسار تخزين خارج المسار العام (`data/uploads`)، مع تحقق نوع/حجم.
- سياسة مقترحة: الاحتفاظ 7 سنوات للسجلات المحاسبية، وتصدير سنوي إلى CSV/أرشيف.

## 6. خطة الترحيل إلى PostgreSQL

SQLite مستخدمة كتنفيذ مرجعي قابل للتشغيل فورًا. للانتقال:

1. `INTEGER` → `BIGINT` (المبالغ)، `TEXT` للتواريخ → `DATE`/`TIMESTAMP`.
2. استبدال `datetime('now')` بـ `now()`.
3. إضافة `jsonb` لـ `roles.permissions` و`audit_logs.details`.
4. تحويل `CHECK` كما هي (متوافقة)، وإضافة partial unique index حيث لزم.
5. إضافة RLS (Row Level Security) على `company_id` كطبقة ثانية للعزل بعد فحص التطبيق.
6. Prisma/Drizzle للمخطط + migrations مُراجَعة في CI.

## 7. حجم البيانات الحالي (بيانات التطوير)

| الجدول | الصفوف |
|---|---|
| accounts | 57 |
| journal_entries | 258 |
| journal_lines | 838 |
| sales_invoices | 54 |
| purchase_invoices | 18 |
| payments | 63 |
| stock_movements | 204 |
| payroll_runs | 8 |
| budgets | 96 |
| audit_logs | 797 |
