# تقرير المرحلة 0 و1 — الأساس المحاسبي والتقني
## ELTARANISI ERP — نسخة التنفيذ المرجعية

> التاريخ: أكتوبر 2026 · النطاق: المرحلة 1 (تحليل المتطلبات والمعمارية) + الأساس التقني (المرحلة 2)
> + نواة المحاسبة (المرحلة 3) + جزء كبير من المرحلة 4 (المعاملات التجارية)

---

## 1. ما تم تنفيذه فعليًا (وليس واجهة فقط)

### 1.1 الأساس التقني
| العنصر | الحالة | الدليل |
|---|---|---|
| قاعدة بيانات حقيقية مع مخطط كامل | ✅ | `schema.sql` — 42 جدولًا + عرض `v_posted_lines`، مفاتيح أجنبية، قيود `CHECK`، فهارس |
| معاملات ذرية (Atomic transactions) | ✅ | `db.transaction()` — كل عملية تُرحّل أو تُلغى بالكامل |
| مبالغ مالية بدون أخطاء عائمة | ✅ | كل المبالغ `INTEGER` بوحدات صغرى (قرش)، والكميات ×1000 |
| مصادقة آمنة | ✅ | PBKDF2-SHA256 (240k تكرار) + ملح لكل مستخدم، جلسات مُهاشّة (SHA-256) بصلاحية 12 ساعة |
| حد محاولات الدخول | ✅ | 8 محاولات → قفل 15 دقيقة |
| صلاحيات على الخادم | ✅ | `require()` تُنفّذ في كل مسار API قبل أي قراءة/كتابة |
| سجل تدقيق | ✅ | `audit_logs` — من، ماذا، أي كيان، متى، IP — لا تُعدّل من الواجهة |
| تعدد الشركات مع عزل البيانات | ✅ | كل استعلام مقيّد بـ `company_id` + اختبار عزل ناجح |
| ترقيم مستندات آمن | ✅ | `document_sequences` لكل شركة/نوع/سنة |
| API موثقة | ✅ | `docs/api/API.md` + أخطاء موحّدة + أكواد حالة صحيحة |
| واجهة عربي/إنجليزي + RTL/LTR + وضع ليلي | ✅ | `static/i18n.js`, `styles.css`, تفضيل محفوظ لكل مستخدم |

### 1.2 نواة المحاسبة
| العنصر | الحالة | ملاحظة |
|---|---|---|
| دليل حسابات هرمي | ✅ | 57 حسابًا، منع الترحيل إلى الحسابات التجميعية (مفروض في المحرك + الاختبارات) |
| قيود يومية بكل الأنواع | ✅ | يدوي، افتتاحي، مصروف، مبيعات، مشتريات، تكلفة مبيعات، رواتب، إهلاك، تحصيل، صرف، عكسي |
| دورة الاعتماد | ✅ | مسودة → مراجعة → اعتماد → ترحيل، مع **فصل مهام** (لا يعتمد المنشئ قيده) |
| عتبة الاعتماد المالي | ✅ | القيود ≥ 200,000 ج.م تحتاج اعتماد المدير المالي |
| منع الترحيل المكرر | ✅ | حالة القيد + `idempotency_key` فريد |
| منع التعديل/الحذف بعد الترحيل | ✅ | القيد المرحّل يُعكس فقط بقيد عكسي مرتبط |
| الفترات المالية والإقفال | ✅ | منع الترحيل لفترة مقفلة، تسجيل من أقفل/أعاد الفتح |
| الأستاذ العام وميزان المراجعة | ✅ | أرصدة افتتاحية/حركة/ختامية لكل حساب + متوازن (مُثبت في الاختبارات) |
| القوائم المالية | ✅ | المركز المالي، الدخل، التدفقات النقدية، ميزان المراجعة — كلها متوازنة ومتسقة داخليًا |

### 1.3 المعاملات التجارية
| العنصر | الحالة | تفاصيل |
|---|---|---|
| المبيعات | ✅ | فاتورة → ترحيل → أثر محاسبي + مخزون + تكلفة مبيعات تلقائيًا |
| فحص حد الائتمان | ✅ | يمنع تجاوز حد العميل |
| المشتريات | ✅ | فاتورة مورد → إدخال مخزون + ضريبة مشتريات + التزام |
| مطابقة ثلاثية | ✅ | أمر شراء + إذن استلام + فاتورة |
| منع فاتورة مورد مكررة | ✅ | بنفس الرقم المرجعي لنفس المورد |
| التحصيلات والمدفوعات | ✅ | سندات قبض/صرف + تخصيص على الفواتير + منع التخصيص الزائد |
| **مديونية الموردين** | ✅ | إجمالي/مسدد/مستحق/متأخر/أقرب استحقاق/أعمار ديون + مطابقة حساب الأستاذ |
| كشف حساب المورد | ✅ | كل فواتيره ومدفوعاته |
| أعمار الديون (مدينة ودائنة) | ✅ | 5 نطاقات زمنية على مستوى الفاتورة والطرف |
| البنوك والصناديق | ✅ | أرصدة من الأستاذ + حركات + مطابقة بنكية مقترحة تحتاج تأكيدًا بشريًا |
| المخزون | ✅ | تقييم المتوسط المرجح، حركة كاملة، استلام/صرف/تحويل/تسوية، منع الرصيد السالب |
| الأصول الثابتة | ✅ | سجل + إهلاك دوري بقيد تلقائي + مجمع إهلاك |
| الرواتب | ✅ | مسير رواتب + قيد استحقاق تلقائي + استقطاعات |
| الموازنات | ✅ | موازنة شهرية مقابل الفعلي + انحرافات + تجاوزات |
| المساعد المالي | ✅ | إجابات من بيانات فعلية وفق الصلاحيات، بدون تنفيذ أي عملية |

### 1.4 المستخدمان المطلوبان
- **أحمد الطرانيسي** — `company_owner`: حساب مستقل، كل الصلاحيات داخل شركته.
- **محمود مراد** — `financial_manager`: حساب مستقل، مراجعة واعتماد وترحيل وتقارير — **بدون** إدارة مستخدمين.
- الأدوار قابلة للتغيير من إعدادات النظام (`/api/users`) دون تعديل كود.
- لا تشترك الحسابات في كلمة مرور، وكل كلمة مرور تُولَّد عشوائيًا وقت التهيئة.

---

## 2. القرارات المعمارية المهمة (مع المبررات)

1. **Modular Monolith**: وحدات منفصلة بحدود واضحة (`accounting`, `documents`, `reports`, `security`)
   داخل عملية واحدة — أبسط في النشر والتشغيل من Microservices، ويحقق الفصل المنطقي المطلوب.
2. **كل أثر مالي يمرّ عبر `accounting.py` فقط**: نقطة واحدة تُطبّق توازن القيد، الفترة المفتوحة،
   الترقيم، ومنع التكرار — وبذلك تكون القواعد قابلة للمراجعة والاختبار.
3. **الأرقام الصحيحة بدل العائمة**: `Decimal` عند الإدخال ثم `Integer` (قرش) في التخزين؛ ضريبة كل سطر
   تُقرَّب نصفًا لأعلى — لا تُستخدم `float` في أي حساب نهائي.
4. **الترحيل الذري**: كل مستند تشغيلي (فاتورة/سند) + أثره المحاسبي + أثره المخزني في معاملة واحدة.
5. **الفصل بين المحرك والواجهة**: الواجهة لا تحسب أرصدة ولا تحدد صلاحيات؛ الخادم هو المرجع دائمًا.
6. **SQLite للتنفيذ المرجعي**: صفر اعتماديات = تشغيل فوري في أي بيئة، مع مخطط متوافق مع PostgreSQL
   (انظر `docs/database/DATABASE.md` لخطة الترحيل).
7. **المساعد المالي بلا وصول مباشر لقاعدة البيانات**: إجابات محسوبة من تقارير محددة الصلاحيات،
   مع ذكر المصدر — يمكن استبداله بمزوّد LLM خلف نفس الواجهة لاحقًا.
8. **واجهة بدون إطار عمل**: HTML/CSS/JS حديث مباشر لتقليل سطح الهجوم والاعتماديات، مع دعم RTL/LTR
   كامل وتصميم متجاوب ووضع ليلي.

---

## 3. خطة قاعدة البيانات (مختصر)

المجموعات: `organizations → companies → branches`؛ `users/roles/permissions`؛
`fiscal_years/periods`؛ `accounts/cost_centers/projects`؛ `journal_entries/journal_lines`؛
`customers/vendors`؛ `sales_invoices(+lines)/purchase_invoices(+lines)`؛ `payments/payment_allocations`؛
`bank_accounts/bank_transactions`؛ `products/warehouses/stock_movements`؛
`fixed_assets/depreciation_runs(+lines)`؛ `employees/payroll_runs(+lines)`؛ `budgets`؛
`notifications/approval_rules/approval_steps/settings/attachments/exchange_rates/audit_logs/document_sequences`.

القيود الجوهرية:
- `journal_entries.status='posted' ⇒ total_debit_cents = total_credit_cents` (قيد مستوى قاعدة البيانات).
- سطر القيد: مدين أو دائن — لا الاثنان، ولا صفر (CHECK).
- `UNIQUE(company_id, code)` على الحسابات/العملاء/الموردين/المخازن/الأصول.
- `UNIQUE(company_id, entry_no)` و`UNIQUE(idempotency_key)`.
- عرض `v_posted_lines` يحصر كل التقارير في القيود المرحّلة والمرحّلة-ثم-معكوسة.

---

## 4. مصفوفة الصلاحيات (مختصرة)

| الدور | إنشاء قيد | مراجعة | اعتماد | ترحيل | عكس | تقارير | مستخدمون | رواتب |
|---|---|---|---|---|---|---|---|---|
| Company Owner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Financial Manager | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | عرض |
| Chief Accountant | ✅ | ✅ | ✅ (>200k) | ✅ | ✅ | ✅ | ❌ | ❌ |
| Accountant | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| Sales / Purchasing / Inventory | ❌ | ❌ | ❌ | ❌ | ❌ | محدود | ❌ | ❌ |
| Auditor | ❌ | ❌ | ❌ | ❌ | ❌ | عرض | ❌ | عرض |

+ **فصل مهام**: المنشئ لا يراجع/يعتمد قيده، ومن اعتمد لا يعدّله.

---

## 5. نتائج التشغيل والاختبارات (آخر تشغيل)

```
Seed:   57 حسابًا · 258 قيدًا · 838 سطر قيد · 54 فاتورة بيع · 18 فاتورة شراء · 63 سندًا
        · 204 حركة مخزون · 4 أصول · 8 مسيرات رواتب · 96 بند موازنة · 797 سجل تدقيق
Tests:  20 اختبار تكامل — OK (0 فشل) — قابلة للتكرار دون إعادة تهيئة
ميزان المراجعة: متوازن ✅   المركز المالي: الأصول = الالتزامات + حقوق الملكية ✅
مديونية الموردين: 4,351,152 ج.م (منها 2,910,876 متأخرة) — مطابقة لحساب الأستاذ 2110 ✅
مؤشرات الأداء: استجابة API للتقرير الكامل < 150ms على البيانات الحالية
```

---

## 6. ما لم يكتمل (بصراحة)

| البند | الحالة | ملاحظة |
|---|---|---|
| MFA وتعدد الأجهزة | ❌ | مخطط في `docs/security/SECURITY.md` |
| رفع المرفقات + OCR | ❌ | جدول `attachments` موجود، واجهة الرفع غير منفذة |
| Excel/PDF (تصدير) | جزئي | CSV مع BOM يعمل؛ PDF عبر الطباعة من المتصفح فقط |
| تعدد العملات وإعادة التقييم | جزئي | `exchange_rates` + `currency` موجودة، لا توجد إعادة تقييم آلية |
| إعادة تقييم/فروق عملة آلية | ❌ | — |
| Workflow متعدد المستويات قابل للتهيئة بالكامل | جزئي | مستوى واحد + عتبة مبلغ؛ الجداول جاهزة لمستويات أكثر |
| الفاتورة الإلكترونية المصرية | ❌ | تحتاج تسجيل واعتماد رسمي ومراجعة مواصفات |
| وحدات CRM / POS / التصنيع | ❌ | نقاط التوسع محددة في المعمارية |
| الأداء تحت حمل (Load test) | ❌ | أهداف الأداء موثقة، لا اختبار حمل فعلي |
| نسخ احتياطي تلقائي مشفر | جزئي | سكربت `infra/backup.sh` جاهز، لا مجدول في هذه البيئة |
| إشعارات بريدية | ❌ | الإشعارات داخل النظام تعمل |

**ما لا أدّعيه**: هذه ليست نسخة إنتاج نهائية؛ هي نواة محاسبية حقيقية قابلة للتوسع، مع توثيق صريح للفجوات.

---

## 6.1 سجل المراحل (ما أُنجز في كل مرحلة)

| المرحلة | المخرجات الفعلية | ما تبقّى منها |
|---|---|---|
| 1 — المتطلبات والمعمارية | هذا التقرير، `ARCHITECTURE.md`, `DATABASE.md` (ERD + قيود)، `API.md` + `openapi.yaml`، مصفوفة الصلاحيات، نموذج مديونية الموردين | — |
| 2 — الأساس التقني | `schema.sql`, `db.py`, `security.py`, المصادقة/الجلسات/RBAC/التدقيق، shell الواجهة + i18n + الثيم | MFA، rate limiting |
| 3 — نواة المحاسبة | `accounting.py` (توازن، ترقيم، ترحيل، عكس، فترات)، القيود اليدوية ودورة الاعتماد، الأستاذ وميزان المراجعة والقوائم المالية | قيود متكررة مجدولة آليًا |
| 4 — المعاملات التجارية | المبيعات/المشتريات/المدفوعات، المخزون بمتوسط مرجح، البنوك والمطابقة، الأصول والإهلاك، الرواتب، الموازنات، مديونية الموردين وأعمار الديون | تعدد العملات الكامل، المرفقات، الفاتورة الإلكترونية |
| 5 — التقارير والتحليلات | 20 صفحة، تقارير مع فلاتر وتصدير CSV، لوحة معلومات، مساعد مالي محدود الصلاحيات | تصدير Excel/PDF أصلي (حاليًا CSV + طباعة المتصفح) |
| 6 — الأمان والموثوقية | فصل مهام، عتبات اعتماد، سجل تدقيق، نسخ احتياطي/استعادة مُختبرة، Docker + nginx، فحوص صحة | اختبار حمل، اختبار اختراق، RPO/RTO مُدار آليًا |
| 7 — التوثيق والتسليم | `README` + 10 مستندات + `.env.example` + `infra/` + خطة اختبار | — |

## 7. الخطوة التالية المقترحة (المرحلة 5+)

1. ترحيل قاعدة البيانات إلى PostgreSQL (مخطط متوافق) + Prisma/Drizzle للـ migrations.
2. إضافة المرفقات + OCR مع مراجعة بشرية إلزامية قبل الترحيل.
3. تعدد العملات الكامل (إعادة تقييم آلية + فروق عملة محاسبية).
4. محرك Workflow متعدد المستويات + تفويض مؤقت.
5. اختبارات حمل تحاكي 5+ محاسبين متزامنين + ضبط الفهارس.
6. MFA + إدارة أجهزة وجلسات + سياسة كلمات المرور.

---

# تقرير المرحلة 6 — الواجهة المتجاوبة وتصدير المستندات

> التاريخ: 21 أكتوبر 2026 · النطاق: تجربة الاستخدام (Responsive + Design system)، تصدير PDF/Excel/CSV،
> إعدادات الشركة، حسابات دخول ثابتة، وأوزان خط Cairo الحقيقية.

## 6.1 ما تغيّر في هذه المرحلة

| العنصر | ماذا نُفِّذ | كيف تم التحقق (لا ادّعاء) |
|---|---|---|
| خط الواجهة | أوزان **Cairo 400/600/700/800** حقيقية: تم تنزيل الخط المتغيّر الرسمي، ثم `fontTools.varLib.instancer` لتثبيت كل وزن، ثم `subset` (لاتيني + عربي) وتحويل `woff2` | `document.fonts` في المتصفح يعرض `Cairo/400..800 loaded`؛ الملفات القديمة المكرَّرة حُذفت و`@font-face` أُعيدت كتابتها |
| تصدير الفواتير | زر واحد يفتح قائمة: معاينة وطباعة · PDF عربي · PDF إنجليزي · Excel · فاتورة إلكترونية | فتح القائمة برمجيًا، تنزيل فعلي، وفحص الناتج: `%PDF-1.7` + `/FontFile2` + `/ToUnicode` (601 KB بسبب تضمين الخط العربي) |
| تصدير التقارير | PDF (عربي/إنجليزي) · Excel RTL · CSV لكل قائمة وميزان مراجعة وأعمار ديون ومديونية موردين | تحميل كل صيغة لكل نوع تقرير في الاختبارات + فحص أن الـ workbook يحمل `rightToLeft="1"` |
| إعدادات الشركة (جديدة) | صفحة `#/settings`: اسم/رقم ضريبي/هاتف/عنوان/بريد/موقع + بيانات البنك وشروط السداد وتذييل الفاتورة، مع **معاينة حيّة لرأس الفاتورة** | `GET/PATCH /api/company`، تحديث جزئي لا يمسح الحقول الأخرى، محمي بـ `settings.manage` (403 لغيره) |
| ترويسة المستندات | بيانات الإعدادات تُطبع فعليًا في PDF (بنك + شروط + تذييل) وتُطابق هوية الشركة | معاينة PDF عربي/إنجليزي بعد الحفظ — التذييل والشروط وبينات البنك ظاهرة وتُقرأ |
| اتجاه النص المختلط | فصل عربي/لاتيني على مستوى السطر: اسم الجهة يمينًا في الحالتين، والطوابع الزمنية والأسماء تُرسم كل واحدة باتجاهها | إصلاح ترويسة الإنجليزية (كانت تخرج من الصفحة) وتذييل مختلط كان يُقرأ معكوسًا |
| شاشة الدخول | لوحة **حسابات تجريبية** بسبعة مستخدمين وأدوارهم، وزر إظهار كلمة المرور، واختصارات | `GET /api/dev/accounts` (عام، 404 عند تعطيل وضع التطوير) + فحص بصري للقائمة |
| التنقل والبحث | زر بحث للجوال يفتح البحث العام، شريط تنقّل سفلي ≤900px، طيّ الشريط الجانبي محفوظ في `localStorage` | أُخذت لقطات على 1440 / 1280 / 768 / 390 بكسل |
| القوائم المنسدلة | إصلاح طبقة العرض: قائمة «تصدير» كانت تُرسم تحت الجدول بسبب `transform` على `.filters` | رفع `z-index` عبر سياق التكديس الصحيح + فحص `elementFromPoint` |
| جدول الفواتير | عمود «أنشأه» يعرض اسم المستخدم بدل المعرّف الرقمي | `LEFT JOIN users` في مسارَي البيع والشراء + فحص الواجهة |
| بيانات التطوير | كلمات مرور **ثابتة** لكل الحسابات السبعة (قرار المستخدم) وملف `data/dev-credentials.txt` | `seed.py` + `tests/test_erp.py::test_57_demo_accounts_endpoint` |

## 6.2 قرارات اتُّخذت في هذه المرحلة

1. **الخط**: كان الحل الأسرع هو تحميل Cairo من Google Fonts، لكن النظام لا يعتمد على أي CDN (شرط العمل
   دون إنترنت). لذلك وُلِّدت أوزان ثابتة من الملف المتغيّر وحُفظت محليًا (≈27KB لكل وزن)، والترخيص OFL
   محفوظ في `static/fonts/OFL-Cairo.txt`.
2. **التصدير يمرّ دائمًا بالمصادقة**: كل تنزيل يمرّ عبر `apiBlob`/`downloadApi` بـ Bearer token — لا
   رابط `<a href>` عام يمكن تخمينه. ولهذا صار زر التصدير `<button>` لا رابطًا.
3. **كل تصدير مسجّل**: `reports.export` و`documents.export` تُكتب في `audit_logs` مع هوية الطالب.
4. **الفاتورة الإلكترونية**: تُصدَّر كـ JSON ثابت (UUID + SHA-256) وتُخزَّن في `einvoice_documents`،
   وتُعرض مع تنبيه صريح «غير موقّعة — يلزم تكامل معتمد». لا توقيع ولا إرسال تلقائي.
5. **إعدادات الشركة**: الاسم العربي/الإنجليزي إلزاميان (منع مسح الهوية)، وبقية الحقول تُحدَّث فقط إن
   أُرسلت — لتجنّب مسح بيانات بالخطأ عند تعديل حقل واحد.

## 6.3 الاختبارات

```
python3 tests/test_erp.py -v   →   Ran 28 tests … OK
```

الاختبارات الثمانية الجديدة:

| # | الاختبار | يثبت ماذا |
|---|---|---|
| `test_50_invoice_pdf_exports` | PDF عربي وإنجليزي: `%PDF-1.7`، حجم > 150KB، `/FontFile2`، `/ToUnicode` |
| `test_51_invoice_xlsx_exports` | XLSX حقيقي (zip + `xl/workbook.xml` + `rightToLeft="1"`) |
| `test_52_einvoice_readiness_is_stable_and_logged` | UUID ثابت للمستند، digest، صف في `einvoice_documents`، أثر في `audit_logs`، **غير موقّع** |
| `test_53_report_pdf_and_excel` | PDF/XLSX لأربعة تقارير مالية |
| `test_54_exports_require_permission` | 403 لأمين المخزن ولبائع المبيعات على مستندات وتقارير خارج صلاحيتهم |
| `test_55_company_settings_roundtrip` | تحديث جزئي لا يمسح الحقول، واسترجاع القيم |
| `test_56_company_settings_need_permission` | 403 للمحاسب على الإعدادات |
| `test_57_demo_accounts_endpoint` | سبعة حسابات تجريبية ثابتة من المسار العام |

## 6.4 ما لم يكتمل في هذه المرحلة (بصراحة)

- **التوقيع/الإرسال الضريبي الفعلي** (ETA) غير منفّذ: المستند جاهز كبيانات، والتصدير PDF/Excel يعمل، لكن
  التكامل المعتمد خارج النطاق ويُصرَّح بذلك في الواجهة.
- **الدفع الإلكتروني / البريد الصادر**: مركز الإشعارات يعمل داخليًا فقط، ولا يوجد مزود SMTP مُهيّأ.
- **تقارير الخلفية (Background jobs)**: التقارير الكبيرة تُصدَّر تزامنيًا؛ وضعها في طابور خلفي مدرج في
  `docs/deployment/DEPLOYMENT.md` كعمل لاحق.
- **اختبارات الحمل p95**: القياس المذكور في التوثيق تم على بيانات التطوير فقط، وليس استيفاءً لمتطلب
  إنتاجي بمقياس كامل.

---

# 7. جولة التعديلات الخامسة (أكتوبر 2026) — الهوية والحذف والتفريغ

جولة تصحيح مبنية على ملاحظات المالك المباشرة. كل بند نُفّذ ثم اختُبر ثم تحقّق بصريًا (لم يُسلَّم
بند «واجهة فقط» بلا اختبار خادم مقابل).

## 7.1 ما تغيّر

| # | المطلوب | ما نُفّذ فعليًا | كيف تحقّقنا |
|---|---|---|---|
| a | تغيير الاسم إلى **ELTARANISI ERP** والشركة إلى **شركة سي سيرفيس للشحن والتفريغ والوكالة الملاحية** + أيقونات أحدث | الاسم في `i18n.js` (عربي/إنجليزي)، `index.html` (title/meta/sidebar)، `exporter.py` (ترويسات وتذييلات PDF)؛ الشركة في `seed.py` (org/company/branches/projects)، ودليل الخدمات أُعيد بناؤه كـ 8 خدمات بحرية `SRV-001…008` وعملاء/موردين بحريين. الأيقونات: خريطة **53 أيقونة SVG** خطية (`ICONS` + `window.icon()`)، استُبدلت بها كل رموز الشريط الجانبي والشريط العلوي والأزرار و KPI والأشرطة والتنبيهات والتصدير | `grep` لا يجد الرموز القديمة؛ `node --check`؛ توليد PDF وفحص الإحداثيات |
| b | تصفير كل الأرصدة | `ERP_SEED_MODE` بوضعين: `clean` (افتراضي) و`demo`. في `clean` يُبنى كل الهيكل (شركة/فروع/أدوار/7 مستخدمين/دليل حسابات/فترات/خدمات/عملاء/موردين) **بدون أي قيد أو رصيد أو فاتورة** | استعلامات عدّ على قاعدة نظيفة: 0 فواتير، 0 مدفوعات، 0 حركات مخزون، 0 أصول، 0 رواتب، 0 موازنات + كل مسارات API ترجع 200 بقوائم فارغة |
| c | إزالة كل `#` واستبداله بـ**الرمز التعريفي** | 17 عنوان عمود في الواجهة + عمود سطور الفاتورة في PDF + ترويسة Excel. صفر بقايا | `grep -rn '"#"'` على `static/` و`*.py` |
| d | حذف لكل عملية إنشاء مع تنبيه تأكيد | وحدة جديدة `deletion.py` + **12 مسار DELETE** + 5 صلاحيات حذف + زر حذف في 9 جداول وصفحتي تفاصيل + نافذة تأكيد تسمّي المستند | 6 اختبارات تكامل جديدة (80–85) + تجربة API مباشرة على قاعدة `demo` |
| e | اسم الشركة مكسور/مزاح في مخرجات الفاتورة والتقرير | `fit_name()` يقيس النص بخط Amiri الفعلي ثم يصغّر أو يلفّ سطرين؛ الاسم يُرسم دائمًا داخل حدود آمنة بعيدًا عن الشعار؛ إصلاح جوهري: في الوضع الإنجليزي كان الاسم يُرسم من الهامش الأيمن باتجاه الخارج (نصفه خارج الصفحة) | فحص إحداثيات آلي (كل نص داخل `[M, W-M]` وخارج منطقة الشعار) + معاينة بصرية بـ PyMuPDF للنسختين العربية والإنجليزية |

## 7.2 قرارات تصميمية

1. **حذف المستند = حذف أثره بالكامل.** لو حُذفت فاتورة مرحّلة وتركت قيدها لاختل ميزان المراجعة.
   لذلك يُحذف القيد وحركات المخزون ومستند الفاتورة الإلكترونية والمرفقات في نفس المعاملة.
2. **لا حذف صامت للفلوس.** فاتورة عليها تحصيل/دفع تُرفض بـ`409 HAS_PAYMENTS` مع ذكر أرقام السندات،
   وسند التحصيل عند حذفه **يُعيد** رصيد الفاتورة وترجع حالتها `posted` — فيبقى المال متتبَّعًا.
3. **البيانات الأساسية محميّة بالمراجع.** عميل/مورد/صنف/موظف مرتبط بمستندات لا يُحذف؛ الرد يحمل قائمة
   المستندات المانعة بدل خطأ قاعدة بيانات.
4. **الحذف حدث مُدقَّق قبل حدوثه.** اللقطة تُكتب في `audit_logs` أولًا، فلا يمكن أن يختفي سجل بلا أثر.
5. **الصلاحيات مُشتقّة من الكود.** `db.migrate()` يزامن صفوف الأدوار النظامية مع `ROLE_DEFINITIONS`،
   فإضافة صلاحية جديدة تصل لقواعد البيانات القديمة عند أول تشغيل بدل أن تظل مخفية.
6. **الأرصدة صفر لا تعني قاعدة فارغة.** الوضع `clean` يبني كل البنية (حسابات، فترات، عملات، بنوك،
   مستودعات، مراكز تكلفة، مشاريع) لتكون النظام جاهزًا للعمل الفعلي من أول قيد.

## 7.3 الاختبارات

```
ERP_SEED_MODE=demo python3 seed.py
PORT=8000 python3 server.py &
python3 tests/test_erp.py          →  Ran 34 tests … OK
python3 tests/test_wsgi_bridge.py  →  20 passed, 0 failed
```

اختبارات الحذف الجديدة:

| # | الاختبار | يثبت ماذا |
|---|---|---|
| `test_80_delete_requires_confirmation` | بدون `{"confirm":true}` يرد الخادم `428` والمستند باقٍ |
| `test_81_receipt_delete_unlinks_invoice` | فاتورة عليها تحصيل → `409`؛ حذف السند يرجّع `paid_cents` إلى 0 والحالة `posted` والميزان متوازن |
| `test_82_invoice_delete_removes_lines_and_entry` | حذف الفاتورة يحذف سطورها وقيدها (404 على الاثنين) ويُبقي الميزان متوازنًا |
| `test_83_master_delete_blocked_when_documents_exist` | عميل مرتبط → `409 HAS_DOCUMENTS`، وعميل جديد يُحذف ثم يعطي `404` عند التكرار |
| `test_84_delete_permission_enforced` | مستخدم المبيعات لا يحذف فواتير ولا عملاء (`403`) |
| `test_85_deletion_is_audited_with_snapshot` | صف تدقيق باسم المنفّذ ورقم المستند داخل اللقطة |

## 7.4 ملاحظة تشغيلية

كلمات المرور الثابتة والقائمة العامة للحسابات التجريبية ما زالت **مقصودة للتطوير** (طلب المالك)،
والنسخة المنشورة تعمل بـ`ERP_SEED_DEMO_DATA=false` فلا تُعرض الحسابات ولا يُتاح `/api/dev/accounts`.

---

## Round «التعديل والحذف لكل سجل» — Edit & delete coverage (2026-10-21)

### 1. What changed

| Area | Before | Now |
|---|---|---|
| Editing | only creation and deletion existed | **12 edit endpoints** (`PATCH`) for customers, vendors, products, employees, bank accounts, bank transactions, fixed assets, budget lines, journal drafts, sales-invoice drafts, purchase-invoice drafts and payment drafts |
| Permissions | a single `*.create` / `*.delete` per module | a dedicated **`*.edit`** family in the catalogue, implied by the matching create/manage right and independently grantable |
| Banks page | read-only accounts, and it could log the user out | full **bank/cash account table** with add / edit / delete, plus a hardened auth path that no longer depends on a session row |
| Icons on row actions | none | pencil (edit) + bin (delete) on every list row and detail page, both SVG |
| Settings page | could blank out on an unexpected payload | guarded accessors, page renders even if a field is missing |

**Why the banks page logged people out.** On Vercel the SQLite file lives in
`/tmp`, which is ephemeral and per-instance: a session row written by instance A
does not exist on instance B, so the next request answered **401** and the global
401 handler sent the user back to the login screen. It was reproducible locally by
deleting the `sessions` rows between two requests with the same token. The fix is
a self-contained token: `base64url(u{uid}.{exp}.{nonce})` plus an HMAC-SHA256
signature, signed with `ERP_SECRET_KEY` (falling back to `data/.token-secret`,
mode 600). `_auth()` still checks the session table first — so revocation keeps
working — and only when `ERP_EPHEMERAL_AUTH=1` (set by `api/index.py`) does a
valid signature with a missing row stay authenticated.

### 2. Decisions

1. **Edit is not posting.** Edits never write to the ledger. Posted documents and
   closed periods stay immutable; the API explains exactly why it refused
   (`ALREADY_POSTED`, `PERIOD_CLOSED`, `RECONCILED`, `HAS_DEPRECIATION`,
   `SYSTEM_ENTRY`).
2. **`*.edit` is implied by create/manage.** Whoever may create a record may fix
   it while it is a draft; a permission can still be granted on its own for a
   clerk who only corrects typos. System roles are re-synced from code at every
   boot, so no reseed is needed when a permission is added.
3. **Partial updates with server-side ownership.** Only whitelisted fields are
   written, everything is scoped by `company_id`, and every edit stores a
   before/after snapshot in `audit_logs`.
4. **Confirmation before saving.** «هل أنت متأكد من تعديل كذا؟» names the record;
   cancelling restores the typed values instead of discarding them.
5. **Bank accounts are documents too.** They got the same treatment as the other
   masters: create, edit, delete — with a hard stop when transactions or vouchers
   already reference the account (deactivate instead of delete).

### 3. Files touched

`security.py` (token signing, `*.edit` permissions, `sync_system_roles`),
`server.py` (12 `PATCH` routes, `POST /api/bank-accounts`, startup role sync),
`editing.py` (new edit service), `deletion.py` (bank-account guard),
`static/app.js` (edit button, confirm modal, delegated handler, edit icon),
`static/pages.js` (edit buttons on 11 pages, banks table, settings guard),
`static/styles.css` (icon system polish), `tests/test_erp.py` (+10 tests),
`docs/` (API, OpenAPI, user guide, test plan).

### 4. Tests

`44 passed` (was 34). New coverage: field whitelisting, audit before/after,
permission enforcement, empty body, unknown id, duplicate code, posted-document
immutability, draft edit, employee + bank-account edit, company scoping, token
tampering, and session-row loss failing closed. A headless sweep renders all 21
pages with the new action buttons (`edit=`/`delete=` counts verified per page).

### 5. Deployment note

The new token scheme needs a stable secret across instances. **Set
`ERP_SECRET_KEY`** in the Vercel project environment (any long random string);
without it each instance mints its own `data/.token-secret` and sessions break
again. `EPHEMERAL_AUTH` is enabled automatically by the serverless entry point.

---

## Round «لوحة المورد» — Vendor panel & optional English name (2026-10-21)

### What changed
1. **الاسم الإنجليزي للمورد اختياري** — `POST /api/vendors` requires only `code`
   and `name_ar`; an empty `name_en` is stored as the Arabic name (the column is
   `NOT NULL`, so the fallback happens server-side). `PATCH` behaves the same, so
   clearing the field in the edit form no longer errors.
2. **رصيد المستحق داخل نموذج التعديل** — the vendor edit form shows a read-only
   balance, taken from the new `GET /api/vendors/{id}` endpoint, which computes it
   from posted and partially-paid purchase invoices (identical to the vendors list).
3. **تبويب الدفعات لكل مورد** — the same endpoint returns that vendor's payments
   with their dates, amounts, allocated/unallocated parts, method, reference and
   status, plus their invoices with the remaining balance per invoice. A
   «تسجيل دفعة» button opens the existing voucher form pre-filled with the vendor,
   supporting allocation to specific invoices and immediate posting.
4. The vendor pencil on **both** the vendors page and the vendor-payables page now
   opens this panel; deletion stayed as-is.

### Decisions
* The balance is never computed in the browser — it comes from the API so the
  panel, the list, the aging report and the GL cannot drift apart.
* Payments are ordinary `payments` rows (draft → post); no parallel table was
  invented, so receivables/payables, reconciliation and the audit trail keep working.
* Fixing the optional name exposed a latent bug in `update_master`: it sent a stale
  parallel value list after the fallback ran. It now rebuilds parameters from the
  changed-fields mapping.

### Tests
`48 passed` (was 44). New: optional English name (create + clear + refill), vendor
detail contract (balance == list balance, dated payments, open-invoice filter),
payment lowering the outstanding balance, and permission enforcement. Headless
render sweep: 21/21 pages, vendor panel HTML verified to contain the balance,
payments tab, dated payment column and the payment button.

---

## Round «الدفعات: نقدًا، تعديل، حذف» (2026-10-21)

### What changed
1. **رصيد المستحق في لوحة المورد** كان يظهر كوسم HTML مهرَّب
   (`<span class="num">…`). السبب: بناء الحقل عبر دالة عامة تُهرِّب كل قيمة. صار
   الرصيد يُعرض في صندوق للقراءة فقط (`readonly-money`) بصيغة مبلغ حقيقية مع
   علامة لونية (مدين = أصفر، صفر/دائن = أخضر) واتجاه LTR للأرقام.
2. **طريقة الدفع «نقدًا»** أُضيفت إلى نموذج السند: نقدًا / تحويل بنكي / شيك، مع
   اختيار تلقائي لصندوق النقدية عند «نقدًا» وللحساب البنكي عند التحويل/الشيك.
   الخادم يوحّد القيم (`cash|bank|cheque|transfer`) ويقبل التسميات العربية.
3. **تعديل وحذف أي سند دفعة** من صفحة المدفوعات ومن تبويب الدفعات داخل المورد.
   تعديل السند **المرحّل** يتم بإلغاء ترحيله ثم إعادة ترحيله داخل معاملة واحدة:
   يُحذف القيد القديم، تُردّ التخصيصات من الفواتير، تُكتب القيم الجديدة، ثم يُنشأ
   قيد واحد جديد. سجل التدقيق يحتفظ بـ `payment.unpost` + `payment.post` +
   `payment.update` مع `{before, after}`.
4. **إلغاء التسوية البنكية** (`POST /api/bank-transactions/{id}/unreconcile` + زر في
   صفحة البنوك) ليصبح بالإمكان تعديل/حذف سند مُطابَق مع كشف — كانت الرسالة تنصح
   بإلغاء التسوية دون وجود وسيلة لذلك.
5. الحرّاس: `409 OVER_ALLOCATION` (المبلغ أقل من المخصص)، `409 RECONCILED`
   (سند مُطابَق)، `422` لطريقة دفع غير معروفة أو طلب فارغ، `422 PERIOD_CLOSED`.

### Files
`editing.py` (rewrote `update_payment`, added `_unpost_payment`, method validation),
`deletion.py` (reconciled guard + unlink on delete), `server.py`
(`_payment_method`, unreconcile route), `static/pages.js` (balance box, voucher
method, shared `PAGES.payments.editForm`, per-row edit/delete, unreconcile button),
`static/styles.css` (`.readonly-money`), tests, docs.

### Tests
`51 passed` (was 48). New: cash method + Jordanian/Arabic alias, posted-voucher
edit re-post (fresh JE, old JE gone, balanced TB, audited), and the guard matrix
(allocation, method, empty body, reconciled edit/delete, unreconcile then delete).
Headless render: 21/21 pages; the vendor panel HTML contains the formatted balance
box and edit/delete buttons on every payment row; the voucher form exposes
نقدًا / تحويل بنكي / شيك.
