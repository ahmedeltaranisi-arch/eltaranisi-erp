# خطة الاختبار — ELTARANISI ERP

## 1. كيف تُشغَّل

```bash
cd eltaranisi-erp
ERP_SEED_MODE=demo python3 seed.py   # يعيد بناء قاعدة بيانات التطوير (مع 9 أشهر عمليات)
PORT=8000 python3 server.py &        # خادم اختبار
ERP_DB=data/erp.db python3 tests/test_erp.py -v   # 34 اختبار تكامل
```

- الاختبارات تعمل على خادم حقيقي عبر HTTP (`ERP_BASE` افتراضيًا `http://127.0.0.1:8000`) — لا mock.
- كل تشغيل ينشئ بياناته الخاصة (عميل/مورد/فاتورة جديدة) ولا يعتمد على ترتيب سابق.
- **قابلة للتكرار**: يمكن تشغيلها عدة مرات متتالية على نفس قاعدة البيانات (المرجعية والشركة التجريبية
  تُنشأ بقيم فريدة/upsert). كل تشغيل يضيف فاتورة اختبار؛ أعِد `python3 seed.py` لبيانات نظيفة.
- تم التحقق: `Ran 34 tests … OK` (21 أكتوبر 2026) — تشغيل متكرر على نفس قاعدة البيانات.
- **مهم**: اختبارات النواة تحتاج البذرة `demo` (مخزون مفتوح وأرصدة افتتاحية). البذرة الافتراضية `clean`
  مقصودة للإنتاج: هيكل كامل وأرصدة صفر.
- `tests/test_wsgi_bridge.py` لا يحتاج خادمًا (20 فحصًا لمسار Vercel) ويضبط بيئته بنفسه.

## 2. تغطية الاختبارات (34)

| # | المحور | ما يُثبت |
|---|---|---|
| 1 | حسابات مستقلة | أحمد ومحمود IDs مختلفة، كل منهما يستطيع الدخول |
| 2 | رفض كلمة مرور خاطئة | `401 INVALID_CREDENTIALS` |
| 3 | رفض طلب غير مصادق | `401` على مسار محمي |
| 4–9 | سيناريو مبيعات كامل | 4×(150+14%) = 684 → AR +684، إيراد +600، ضريبة 84، COGS ومخزون؛ تحصيل جزئي 300 → `partially_paid`، البنك +300؛ مطابقة بنكية بتأكيد بشري |
| 10 | تكامل القوائم | ميزان المراجعة متوازن، المركز المالي متوازن بعد كل الترحيلات |
| 11 | منع الترحيل المزدوج | `422 INVALID_STATE` |
| 12–17 | سيناريو مورد كامل | 1000+14% = 1140 → مديونية المورد وحساب 2110؛ منع تكرار `supplier_ref`؛ سداد 500 → `partially_paid` وتخفيض الحسابين؛ منع التخصيص الزائد |
| 18 | مطابقة المديونية | مجموع متبقي الموردين = رصيد الأستاذ (اختبار تقاطع) |
| 19–21 | الصلاحيات | مراجع/مبيعات 403 على إنشاء قيد؛ محاسب 403 على الترحيل؛ 403 على سجل التدقيق والتصدير |
| 50 | تصدير PDF للفاتورة | `%PDF-1.7` + `/FontFile2` + `/ToUnicode` (601KB) عربي وإنجليزي |
| 51 | تصدير Excel | zip صالح + `xl/workbook.xml` + `rightToLeft="1"` |
| 52 | جاهزية الفاتورة الإلكترونية | UUID ثابت بين طلبين، digest، صف محفوظ، أثر تدقيق، `signed=false` |
| 53 | تصدير التقارير | PDF/XLSX لأربعة تقارير مالية |
| 54 | حظر التصدير | 403 لأمين المخزن على مستندات المحاسبة وتقاريرها، وللبائع على فواتير المشتريات |
| 55 | إعدادات الشركة | تحديث جزئي دون مسح بقية الحقول + استرجاع |
| 56 | صلاحية الإعدادات | 403 للمحاسب |
| 57 | مسار الحسابات التجريبية | 7 حسابات ثابتة عبر `GET /api/dev/accounts` |
| 80 | تأكيد الحذف إلزامي | حذف بدون `confirm` → `428 CONFIRM_REQUIRED` والمستند باقٍ |
| 81 | حذف سند التحصيل | فاتورة عليها تحصيل → `409 HAS_PAYMENTS`؛ حذف السند يرجّع `paid_cents=0` والحالة `posted` والميزان متوازن |
| 82 | حذف فاتورة مرحّلة | السطور والقيد والحركات تُحذف معًا (`404` على الفاتورة والقيد) والميزان متوازن |
| 83 | حماية البيانات الأساسية | عميل مرتبط → `409 HAS_DOCUMENTS`؛ عميل جديد يُحذف ثم `404` عند التكرار |
| 84 | صلاحية الحذف | مستخدم المبيعات → `403` على الفواتير والعملاء |
| 85 | تدقيق الحذف | صف في `audit_logs` باسم المنفّذ ورقم المستند داخل اللقطة المحفوظة |
| 22 | فصل المهام | اعتماد المنشئ لقيده → `422 SOD_VIOLATION` |
| 23 | دورة الاعتماد | `NOT_APPROVED` قبل الاعتماد؛ ترحيل ثم عكس ينشئ قيدًا عكسيًا مرحّلًا |
| 24–26 | قواعد المحاسبة | `UNBALANCED`، `NON_POSTABLE_ACCOUNT`، `PERIOD_CLOSED` كلها 422 |
| 27 | عزل الشركات | شركة جديدة: صفر عملاء وصفر مديونية، وميزانها متوازن |
| 28 | اتساق التقارير | إيراد قائمة الدخل = حركة دائن حساب الإيرادات في الميزان |
| 29 | تصدير CSV | UTF-8 BOM + رأس `Code` (يُفتح في Excel بعربية سليمة) |
| 30 | المساعد المالي | يعيد أرقامًا من البيانات الفعلية، و403 بدون صلاحية `ai.use` |
| 31 | سجل التدقيق | يسجل `auth.login`, `journal.post`, `sales_invoice.post`, `payment.post` |

آخر تشغيل: **Ran 20 tests — OK**.

## 3. حالات اختبار يدوية (تم تنفيذها في المتصفح)

- [x] دخول عربي/إنجليزي، تبديل RTL/LTR، وضع ليلي.
- [x] لوحة المعلومات: كل المؤشرات تعرض أرقامًا من القاعدة (ومطابقة للأستاذ).
- [x] إنشاء قيد من الواجهة (`JE-2026-00269`, 1,250 ج.م) ثم ظهوره بالحالة «مسودة» ومسار «بانتظار المراجعة».
- [x] محاولة حفظ قيد غير متوازن → يُمنع.
- [x] فتح تفاصيل فاتورة بيع: السطور + الأثر المحاسبي + سجل الحالة.
- [x] تقرير مديونية الموردين وتقرير الميزان.
- [x] المساعد المالي: «مديونية الموردين» و«صافي الربح» مع ذكر المصدر.
- [x] تصدير CSV وفتحه (BOM سليم).
- [x] صفحات: العملاء، الموردين، المشتريات، المخزون، البنوك، الأصول، الرواتب، الموازنات، المستخدمون، الفترات، سجل التدقيق.

## 4. ما لم يُختبر بعد (شفافية)

| البند | السبب |
|---|---|
| اختبار حمل 5+ محاسبين متزامنين | لا يوجد أداة حمل في البيئة؛ الهدف p95 < 400ms موثق فقط |
| حذف المستخدمين/الفروع من الواجهة | الحذف متاح للمستندات والبيانات الأساسية والحركات؛ حذف المستخدمين يبقى عبر التعطيل (سلامة سجل التدقيق) |
| MFA / إدارة الأجهزة | غير منفذة |
| رفع المرفقات وOCR | غير منفذة |
| تعدد العملات وإعادة التقييم | جزئي |
| الفاتورة الإلكترونية | غير منفذة |
| اختبار اختراق خارجي | خارج النطاق |
| اختبار استعادة قاعدة البيانات في CI | نُفّذ يدويًا (integrity ok) |

## 5. معايير قبول الإصدار

1. جميع اختبارات التكامل ناجحة.
2. ميزان المراجعة متوازن والمركز المالي متوازن على بيانات التطوير.
3. صفر أخطاء 5xx أثناء جولة المتصفح اليدوية.
4. لا كلمات مرور أو مفاتيح في المستودع (`git grep -i password` لا يعيد أسرارًا).
5. كل تقرير في واجهة المستخدم يعرض «من أين جاء الرقم».

### Round «edit everywhere» — automated coverage

| Test | What it proves |
|---|---|
| `test_86_customer_edit_updates_and_audits` | PATCH changes only the sent fields, keeps the rest, and writes `{before, after, changed}` to the audit log with the actor |
| `test_87_edit_requires_permission` | the auditor role gets **403** on `PATCH /api/customers/{id}` |
| `test_88_edit_rejects_empty_and_unknown_records` | empty body → **422 NOTHING_TO_UPDATE**, unknown id → **404** |
| `test_89_duplicate_code_is_rejected` | changing a code to one already used → **409 DUPLICATE** |
| `test_90_posted_invoice_cannot_be_edited` | posted sales invoice → **409 ALREADY_POSTED** (immutability preserved) |
| `test_91_draft_invoice_edit_is_allowed` | draft invoice header edit → 200 and the new value is persisted |
| `test_92_employee_and_bank_account_edit` | employee salary/department and bank-account name edits both persist |
| `test_93_edit_respects_company_scope` | a record id from another company is unreachable (404), same rule as reads |
| `test_94_auth_survives_missing_session_rows` | with ephemeral auth disabled, a lost session row fails **closed** (401) |
| `test_95_tampered_token_is_rejected` | a modified bearer token is rejected (HMAC verification) |

Manual checks performed in this round:

* every list page renders with the pencil + bin (`21/21` pages, harness sweep);
* the confirm modal names the record and cancels cleanly;
* editing a *posted* invoice from the UI shows the server's Arabic explanation;
* bank page loads repeatedly with a single token on a stateless server (no logout).

### Round «vendor panel» — coverage

| Test | What it proves |
|---|---|
| `test_96_vendor_english_name_is_optional` | creating a vendor with only `code` + `name_ar` works and `name_en` falls back to Arabic; clearing it later also falls back instead of 500 |
| `test_97_vendor_detail_exposes_balance_payments_and_open_invoices` | `GET /api/vendors/{id}` returns the same balance as the list, payments with dates, and only posted/partially-paid open invoices |
| `test_98_vendor_payment_reduces_the_outstanding_balance` | recording an allocated payment lowers the vendor's outstanding balance by exactly the paid amount and prepends the dated payment |
| `test_99_vendor_panel_endpoints_require_permission` | roles without `vendors.view` get 403 |

Manual checks: the vendor panel renders both tabs (details + payments) with the
balance field, the payments table and the «تسجيل دفعة» button; the payables page
opens the same panel.

### Round «طرق الدفع وتعديل السندات» — coverage

| Test | What it proves |
|---|---|
| `test_A100_cash_payment_method` | a `cash` voucher credits the cash-box GL (1111), and the Arabic label «نقداً» is normalised to `cash` |
| `test_A101_posted_payment_can_be_edited_and_is_reposted` | editing a posted voucher's method/account/amount creates a **fresh** journal entry, removes the superseded one, keeps the trial balance balanced, and audits unpost + post + update |
| `test_A102_payment_edit_guards` | amount below the allocated total → 409; unknown method → 422; empty body → 422; a reconciled voucher cannot be edited or deleted (409) until `POST /api/bank-transactions/{id}/unreconcile`, after which deletion restores the invoice balance |

Manual: the vendor balance renders as a formatted amount inside the edit form
(not as escaped HTML), the voucher form offers نقدًا/تحويل بنكي/شيك, and every
payment row in the vendor panel and the payments page carries edit + delete.

### Round j — «فاتورة الشراء تُحفظ» + «المديونية» coverage

| Test | What it proves |
|---|---|
| `test_B110_purchase_invoice_saves_with_a_free_text_service_line` | the reported defect: a purchase invoice with a free-text service line (no catalogue item) **saves and posts**, gets a `PI-` number, debits cost of service (5100) — **not** inventory — and credits payables by the same amount |
| `test_B111_purchase_invoice_accepts_a_service_product_line` | catalogue service items (`is_service=1`) post to 5100 with VAT 14% and require no warehouse |
| `test_B120_debt_account_creation_and_seeded_chart` | the seeded chart mirrors the paper statement (tonnage contractor `DB-0003`, container office `DB-0006`) and every account starts at zero |
| `test_B121_debt_item_computes_quantity_by_unit_rate_and_posts` | 31,882.74 ton × 3 = 9,564,822 piastres, `DEB-` numbering, Dr 5100 / Cr 2110 balanced entry |
| `test_B122_debt_payment_voucher_and_statement_totals` | settlement with اذن صرف posts Dr 2110 / Cr 1111; statement totals = due, paid, net; the consolidated summary’s grand total equals the sum of its accounts |
| `test_B123_debt_item_edit_reposts_and_keeps_the_ledger_balanced` | editing a posted due line creates a fresh entry, drops the superseded one, and the trial balance stays balanced |
| `test_B124_debt_deletes_require_confirmation_and_reverse_the_ledger` | `428` without `confirm`, `409 HAS_CHILDREN` while children exist, deleting a payment/due line restores the AP balance exactly, and all six audit actions are written |
| `test_B125_debt_summary_and_statement_exports` | summary XLSX is a real `PK…` package and the statement PDF starts with `%PDF` |
| `test_B126_debt_permissions_are_enforced_server_side` | sales role → 403; auditor may read but not post |

Manual / headless checks: the debt page renders 7 type groups with edit + delete
side by side on every row; the statement page shows the invoice-style table
(`البيان | الوحدة | الكمية | فئة الوحدة | الإجمالي المستحق`), the payments table
with اذن صرف, and the three totals (المستحق / المدفوع / الصافي).
