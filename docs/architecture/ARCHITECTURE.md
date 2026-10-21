# المعمارية — Eltaranisi ERP

## 1. النمط المعماري: Modular Monolith

```
┌─────────────────────────────────────────────────────────────────────┐
│                        static/ (SPA شيل خفيف)                        │
│  index.html · styles.css · app.js (shell/router) · pages.js · i18n   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP + Bearer token (JSON)
┌───────────────────────────────▼─────────────────────────────────────┐
│                          server.py (API layer)                       │
│  التوجيه · المصادقة · RBAC (require) · التحقق من المدخلات · الأخطاء   │
└───┬───────────────┬───────────────────┬──────────────────┬──────────┘
    │               │                   │                  │
┌───▼──────┐  ┌─────▼──────┐  ┌─────────▼───────┐  ┌───────▼────────┐
│security  │  │accounting  │  │   documents     │  │   reports      │
│كلمات مرور│  │محرك القيد  │  │فواتير/سندات/    │  │قوائم مالية/    │
│جلسات     │  │توازن،ترقيم │  │إهلاك/رواتب/     │  │مديونية/أعمار/  │
│RBAC      │  │ترحيل،عكس   │  │مخزون            │  │موازنات         │
│تدقيق     │  │            │  │                 │  │                │
└───┬──────┘  └─────┬──────┘  └─────────┬───────┘  └───────┬────────┘
    └───────────────┴───────────────────┴──────────────────┘
                                │
                     ┌──────────▼──────────┐
                     │   db.py (SQLite)    │
                     │ معاملات ذرية · WAL  │
                     └─────────────────────┘
```

**لماذا Modular Monolith؟** حدود واضحة بين الوحدات (كل وحدة لا تستورد إلا ما تحتاجه)، نشر بسيط
(عملية واحدة)، ومعاملات قاعدة بيانات حقيقية عبر كل الوحدات — وهو ما تحتاجه المحاسبة (ذرّية القيد +
المستند معًا). لا حاجة لـ Microservices في هذا الحجم، والانتقال إليها ممكن لاحقًا لأن الحدود موجودة.

## 2. حدود الوحدات (Dependency rules)

| الوحدة | تستورد | تُستورد بواسطة | قاعدة |
|---|---|---|---|
| `db` | — | الكل | لا تحتوي منطق أعمال |
| `security` | `db` | الكل | لا تعرف المحاسبة |
| `accounting` | `db`, `security` | `documents`, `server` | **الوحدة الوحيدة** التي تكتب في `journal_entries`/`journal_lines` |
| `documents` | `db`, `accounting`, `security` | `server` | تكتب مستندات تشغيلية فقط |
| `reports` | `db` | `server` | **قراءة فقط** — لا كتابة |
| `server` | الكل | — | تنسيق فقط، بلا منطق محاسبي |

قاعدة ذهبية: أي كتابة مالية جديدة يجب أن تمرّ بـ `accounting.create_entry/auto_post_entry`.

## 3. تدفق العملية المحاسبية (مثال: فاتورة مبيعات)

```
1. POST /api/sales-invoices            → صلاحية sales.create
2. documents.create_sales_invoice
   ├─ التحقق من العميل وحد الائتمان
   ├─ compute_totals (كمية × سعر، خصم، ضريبة كل سطر)
   ├─ ترقيم SI-YYYY-NNNNN
   └─ INSERT sales_invoices + sales_invoice_lines   (مسودة)
3. POST /api/sales-invoices/{id}/post  → صلاحية sales.post
4. documents.post_sales_invoice (داخل معاملة واحدة)
   ├─ ensure_period_open
   ├─ قيد: مدين العملاء / دائن إيرادات + ضريبة مبيعات            → auto_post_entry
   ├─ لكل صنف مخزني: stock_out بالمتوسط المرجح
   ├─ قيد تكلفة: مدين تكلفة المبيعات / دائن المخزون               → auto_post_entry
   ├─ ربط حركات المخزون بالقيد
   └─ تحديث حالة الفاتورة إلى posted
5. أي فشل في أي خطوة → ROLLBACK كامل (لا فاتورة بلا قيد، ولا قيد بلا فاتورة)
```

## 4. دورة حياة القيد اليدوي

```
draft ──submit(غير المنشئ)──> review ──review/approve──> approved ──post──> posted
  ▲                              │                                          │
  └────────reject────────────────┘                                          │
                                                    posted ──reverse──> reversed
                                                    (+ قيد عكسي مرحّل مرتبط)
```
- المنشئ لا يُرسل/يراجع/يعتمد قيده (فصل مهام).
- قيد ≥ 200,000 ج.م: المراجعة تعطي `approved` فقط بعد اعتماد المدير المالي.
- `posted` غير قابل للتعديل أو الحذف؛ التصحيح فقط بقيد عكسي أو تسوية معتمدة.

## 5. نموذج البيانات (مبسّط ERD)

```
organizations 1─* companies 1─* branches
                     │ 1─* fiscal_years 1─* fiscal_periods
                     │ 1─* accounts (self-referencing parent_id)
                     │ 1─* journal_entries 1─* journal_lines *─1 accounts
                     │ 1─* customers ──1 accounts(AR)
                     │ 1─* vendors   ──1 accounts(AP)
                     │ 1─* sales_invoices 1─* sales_invoice_lines
                     │ 1─* purchase_invoices 1─* purchase_invoice_lines
                     │ 1─* payments 1─* payment_allocations ( polymorphic: sales|purchase )
                     │ 1─* bank_accounts 1─* bank_transactions
                     │ 1─* warehouses / products 1─* stock_movements
                     │ 1─* fixed_assets / depreciation_runs 1─* depreciation_lines
                     │ 1─* employees / payroll_runs 1─* payroll_lines
                     │ 1─* budgets, notifications, approval_rules, approval_steps,
                     │     attachments, settings, exchange_rates, audit_logs, document_sequences
```

## 6. الواجهة الأمامية

- **Shell**: `app.js` — الحالة، الـ API، الراوتر (hash)، الـ nav، الإشعارات، البحث الشامل، الثيم.
- **الصفحات**: `pages.js` — كل صفحة `{ perm, render(view) }`؛ الصفحة تعلن صلاحيتها فتُفحص قبل العرض
  (والخادم يفحصها مرة أخرى — لا اعتماد على الواجهة).
- **الحالات**: تحميل (skeleton)، فراغ، خطأ، ورسائل خطأ قابلة للفهم من استجابات API (أكواد مثل
  `UNBALANCED`, `PERIOD_CLOSED`, `SOD_VIOLATION`).
- **الوصولية**: تنقّل بلوحة المفاتيح في النماذج (Enter للحفظ)، تباين مدروس في الوضعين، `dir` يتغير
  تلقائيًا، أرقام بأرقام جدولية `tabular-nums`.
- **لا بيانات وهمية في التقارير**: كل الأرقام من `v_posted_lines`؛ بيانات Seed موسومة كبيئة تطوير
  في صفحة الدخول وتقرير المرحلة.

## 7. نقاط التوسع المستقبلية

| الوحدة القادمة | نقطة الإدخال |
|---|---|
| CRM ونقاط البيع | جداول جديدة + `source_type` في القيد (موجود) |
| التصنيع (BOM/أوامر عمل) | `stock_movements` + `products.is_service` وسيطة |
| الاشتراكات والفوترة المتكررة | جدول `recurring_documents` + نفس `documents.py` |
| تعدد العملات الكامل | `exchange_rates` موجود + دالة `revalue_balances` |
| LLM حقيقي للمساعد | استبدال جسم `/api/assistant/ask` بمزوّد خارجي خلف نفس مخطط الصلاحيات |
