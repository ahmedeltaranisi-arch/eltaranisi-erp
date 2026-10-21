<div align="center">
  <img src="static/brand/logo-full.png" alt="Eltaranisi ERP" width="220" />
</div>

# Eltaranisi ERP — الطرانيسي ERP

**🌐 نسخة حيّة / Live demo:** https://eltaranisi-erp-eltaranisi.vercel.app

[![CI](https://github.com/ahmedeltaranisi-arch/eltaranisi-erp/actions/workflows/ci.yml/badge.svg)](https://github.com/ahmedeltaranisi-arch/eltaranisi-erp/actions/workflows/ci.yml)
[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/ahmedeltaranisi-arch/eltaranisi-erp)

نظام ERP محاسبي وإداري متكامل (عربي/إنجليزي) — قاعدة بيانات حقيقية، محاسبة مزدوجة القيد، صلاحيات
مفروضة على الخادم، مديونية موردين وأعمار ديون، وإقفال فترات مالية.

A real working ERP core: double-entry ledger, RBAC enforced server-side, vendor payables &
aging, purchase/sales cycles with automatic accounting + stock impact, approval workflow with
segregation of duties, bilingual RTL/LTR UI with dark mode, audit trail and 28 integration tests.

Documents are exportable as Arabic/English PDFs (embedded Arabic font, RTL-aware), RTL Excel
workbooks and CSV — for every invoice and all financial statements — and the company identity
printed on them is editable from **إعدادات الشركة / Company settings**.

---

## 1) التشغيل السريع / Quick start

```bash
cd eltaranisi-erp
python3 seed.py            # ينشئ قاعدة البيانات + بيانات تطوير (9 أشهر من العمليات)
PORT=8000 python3 server.py
# ثم افتح http://localhost:8000
```

> المتطلبات: Python 3.10+ فقط. لا توجد اعتماديات خارجية (stdlib فقط) — لا `pip install`.

**بيانات الدخول للتطوير ثابتة (كما طُلب)** — نفس الحسابات ونفس كلمات المرور في كل تشغيل لـ `seed.py`،
ومسجّلة في `data/dev-credentials.txt`. شاشة الدخول تعرضها للاختيار بنقرة واحدة عبر `GET /api/dev/accounts`
(هذا المسار العام يُعطّل تلقائيًا عند `ERP_SEED_DEMO_DATA=false`):

| المستخدم | الدور |
|---|---|
| المستخدم | كلمة المرور | الدور |
|---|---|---|
| `ahmed.taranisi` | `Eltaranisi@2026` | Company Owner — أحمد الطرانيسي |
| `mahmoud.morad` | `Mahmoud@2026` | Financial Manager — محمود مراد |
| `samy.abdelrahman` | `Samy@2026` | Chief Accountant — سامي عبد الرحمن |
| `mona.selim` | `Mona@2026` | Accountant — منى سليم |
| `karim.fathy` | `Karim@2026` | Inventory user — كريم فتحي |
| `tarek.adel` | `Tarek@2026` | Sales user — طارق عادل |
| `heba.elsherif` | `Heba@2026` | Auditor (read-only) — هبة الشريف |

> ⚠️ هذه كلمات مرور **بيانات تطوير فقط** (قرار مقصود لتسهيل التجربة). عند النشر الفعلي: عيّن
> كلمات مرور جديدة من شاشة المستخدمين، واضبط `ERP_SEED_DEMO_DATA=false` لإخفاء لوحة الحسابات التجريبية
> وإيقاف `GET /api/dev/accounts`.

## 2) الاختبارات / Tests

```bash
python3 seed.py && PORT=8000 python3 server.py &
python3 tests/test_erp.py -v
python3 tests/test_wsgi_bridge.py   # 20 فحصًا لمسار النشر على Vercel (بدون خادم)
```

**28 اختبار تكامل** (20 للنواة المحاسبية + 8 للتصدير والإعدادات والحسابات التجريبية) تغطي: الحسابين المستقلين، سيناريو المبيعات الإلزامي (فاتورة → ترحيل → تحصيل جزئي →
تسوية بنكية → القوائم المالية)، سيناريو الموردين الإلزامي (فاتورة → ترحيل → دفعة جزئية → الرصيد قبل/بعد →
منع الدفع المكرر)، الصلاحيات، فصل المهام، منع التعديل بعد الترحيل، منع الترحيل في فترة مقفلة، عزل
بيانات الشركات، اتساق القوائم المالية، تصدير CSV، نطاق المساعد الذكي، **تصدير PDF/Excel للفواتير والتقارير والتحقق من
الخط العربي المضمَّن**، **ثبات مستند الفاتورة الإلكترونية (UUID/digest) مع تسجيله في سجل التدقيق**،
**إعدادات الشركة (تحديث جزئي آمن + صلاحية)**، و**حظر التصدير على من لا يملك صلاحية**.

## 3) الهيكل / Structure

```
eltaranisi-erp/
  server.py         HTTP API + static server (طبقة العرض والصلاحيات)
  accounting.py     محرك القيد المزدوج (تحقق، ترقيم، ترحيل، عكس، مخزون)
  documents.py      الفواتير والمدفوعات والإهلاك والرواتب + أثرها المحاسبي
  reports.py        القوائم المالية ومديونية الموردين وأعمار الديون والموازنات
  security.py       تشفير كلمات المرور، الجلسات، RBAC، سجل التدقيق
  db.py             طبقة قاعدة البيانات + أدوات المبالغ الصحيحة (Integer cents)
  schema.sql        مخطط قاعدة البيانات (42 جدولًا + عرض تقارير)
  seed.py           بيانات تطوير واقعية (تُرحَّل عبر المحرك نفسه)
  static/           الواجهة (HTML/CSS/JS بدون إطار عمل، RTL/LTR، وضع ليلي)
  api/index.py      نقطة دخول Vercel (WSGI bridge يشغّل server.py كما هو)
  vercel.json       ضبط النشر: static + rewrite لـ /api/*
  tests/            اختبارات تكامل
  docs/             التوثيق الكامل
  infra/            Docker / النسخ الاحتياطي / النشر
```

## 4) التوثيق / Documentation

| الملف | المحتوى |
|---|---|
| `docs/PHASE_REPORT.md` | **تقرير المرحلة**: ما تم تنفيذه، القرارات المعمارية، ما لم يكتمل |
| `docs/architecture/ARCHITECTURE.md` | المعمارية، الحدود بين الوحدات، تدفق العملية المحاسبية |
| `docs/database/DATABASE.md` | مخطط قاعدة البيانات و ERD والقيود والفهارس |
| `docs/api/API.md` | كل مسارات REST والصلاحيات المطلوبة |
| `docs/accounting-rules/ACCOUNTING_RULES.md` | قواعد الترحيل، مصفوفة الاعتماد، سياسات الإقفال والعكس |
| `docs/security/SECURITY.md` | نموذج التهديد، الصلاحيات، التدقيق، الحدود المعروفة |
| `docs/deployment/DEPLOYMENT.md` | Docker، البيئات، CI/CD، المراقبة، النسخ الاحتياطي |
| `docs/deployment/VERCEL.md` | النشر على Vercel: المعمارية، Cold start، القيود، قائمة التحقق، طريق الإنتاج |
| `docs/user-manual/USER_GUIDE.md` | دليل المستخدم (محاسب/مالك/مدير) خطوة بخطوة |
| `docs/user-manual/FINANCIAL_MANAGER_GUIDE.md` | دليل المدير المالي: الروتين اليومي، الإقفال، المطابقة |
| `docs/TEST_PLAN.md` | خطة الاختبار: 20 اختبار تكامل + حالات سلبية + ما لم يُختبر |
| `docs/api/openapi.yaml` | مخطط OpenAPI قابل للاستيراد |

## 5) تنبيهات صريحة / Explicit limitations

- هذا تنفيذ **مرجعي عامل** للنواة المحاسبية والوحدات الأساسية، وليس بديلًا جاهزًا للإنتاج
  (انظر قائمة "ما لم يكتمل" في `docs/PHASE_REPORT.md`).
- كلمات مرور التطوير ثابتة ومقصودة (ليست سرًا) — راجع التنبيه في القسم 1 قبل أي نشر.
- بيانات التطوير تجريبية وموسومة بذلك في الواجهة وعلى شاشة الدخول.
- مستند الفاتورة الإلكترونية يُصدَّر كـ JSON مع UUID وdigest **غير موقّعين**: التكامل المعتمد مع مصلحة
  الضرائب (توقيع/إرسال) خارج نطاق هذا المستودع، والواجهة تصرّح بذلك.
- المساعد المالي يعمل بتحليلات محددة الصلاحيات من القيود المرحّلة (بدون مزود LLM خارجي)،
  ولا ينفّذ أي ترحيل أو دفع.

## 6) حالة التنفيذ المُتحقَّق منها / Verified state

آخر تحقق (أكتوبر 2026) على بيانات التطوير:

| البند | القيمة |
|---|---|
| اختبارات التكامل | **20/20 ناجحة** (قابلة للتكرار على نفس قاعدة البيانات) |
| ميزان المراجعة | متوازن (مدين = دائن) |
| المركز المالي | الأصول = الالتزامات + حقوق الملكية |
| مديونية الموردين | 4,351,152 ج.م — منها 2,910,876 متأخرة، ومطابقة لحساب الأستاذ 2110 |
| مستحقات العملاء | 1,409,518.80 ج.م |
| السيولة | 1,813,513.20 ج.م |
| قيمة المخزون | 5,882,650 ج.م — ومساوية لرصيد حساب 1310 |
| الواجهة | 21 صفحة عربية/إنجليزية، وضع ليلي، RTL/LTR، متجاوبة (1440/1280/768/390) — تم فحصها في المتصفح |
| الهوية البصرية | شعار المشروع الرسمي (`static/brand/`): أيقونة الموقع + favicon + apple-touch + بطاقة OG + ترويسة PDF بألوان الكحلي/الذهبي |
| النشر | Vercel إنتاجي: `/api/health` ✅، تسجيل دخول ✅، تنزيل PDF عربي (601KB) ✅، الوضع التجريبي مغلق (404) ✅ |
| خط الواجهة | Cairo حقيقي بأوزان 400/600/700/800 (woff2 مقطوعة من الخط المتغيّر) — تُحمَّل فعليًا في المتصفح |
| تصدير المستندات | PDF عربي/إنجليزي + Excel RTL + CSV من الفاتورة والتقارير، بترويسة شركة قابلة للتحرير |
| سجل التدقيق | كل دخول/إنشاء/ترحيل/عكس/تصدير مسجّل بهوية المستخدم ووقته |


## 7) النشر على Vercel / Deploying to Vercel

المشروع **جاهز للنشر** بلا أي خطوة بناء: الواجهة تُخدَّم كملفات ثابتة من `static/`، وكل `/api/*` يذهب
إلى دالة Python واحدة عبر جسر WSGI صغير (`api/index.py`) يشغّل `server.py` نفسه دون تغيير أي منطق.

```bash
# الطريقة 1 — من لوحة Vercel: استورد المستودع، Framework = Other، ثم Deploy (vercel.json يتكفّل بالباقي)
# الطريقة 2 — من الطرفية
npm i -g vercel && vercel --prod
```

| ماذا | كيف |
|---|---|
| **الرابط الحالي** | **https://eltaranisi-erp-eltaranisi.vercel.app** (مشروع Vercel باسم `eltaranisi-erp`) |
| **النشر التلقائي** | مربوط بمستودع GitHub: أي `git push` على `main` → نشر إنتاجي تلقائي (تم التحقق) |
| ملف الضبط | `vercel.json` (output `static` + rewrite لـ `/api/*`) |
| نقطة الدخول | `api/index.py` — WSGI bridge، يهيّئ قاعدة SQLite في `/tmp` ويبذرها عند أول طلب (~0.5 ثانية) |
| الاعتماديات | لا شيء — `requirements.txt` موجود فقط ليتعرّف Vercel على Python |
| الاستثناءات | `.vercelignore` يمنع رفع `data/` و`tests/` و`*.db` |

**قيود صريحة لهذا النشر:** القرص مؤقت (`/tmp`)، فأي بيانات تُدخلها على Vercel **تُفقد** عند إعادة تدوير
النسخة، وكل نسخة (instance) لها قاعدتها الخاصة. هذا **عرض حيّ** لنظام عامل وليس نشرًا إنتاجيًا — طريق
الانتقال إلى Postgres وبيانات دائمة موثّق خطوة بخطوة في `docs/deployment/VERCEL.md` (القسم 6).

**وضع العرض مغلق افتراضيًا** على Vercel: لا تُعرض الحسابات التجريبية ولا يعمل `GET /api/dev/accounts`
(يعيد 404)، والدخول يتم يدويًا بالحسابات المذكورة في القسم 1. لفتح العرض بنقرة واحدة:
`vercel env add ERP_SEED_DEMO_DATA production` ثم `vercel --prod`.

> تنبيه أمني: كلمات المرور الثابتة موجودة في الكود العام، لذا فهي **غير سرية**. قبل أي استخدام حقيقي
> غيّر كلمات المرور من شاشة المستخدمين بعد أول دخول، ولا تضع بيانات عملاء على نشر بقرص مؤقت.

قبل الاعتماد على النشر، شغّل الفحص الآلي للجسر:

```bash
python3 tests/test_wsgi_bridge.py     # 20/20 — يثبت المسار الكامل دون أي خادم HTTP (بما فيه الوضع المغلق)
```

### سيناريو تجربة سريع (5 دقائق)

1. ادخل باسم صاحب الشركة (`ahmed.taranisi`) ← لوحة التحكم تعرض أرقامًا من القاعدة.
2. **مديونية الموردين** ← لاحظ عمود «رصيد الأستاذ» مطابقًا للمتبقي.
3. **القيود اليومية** ← افتح قيدًا مرحّلًا ← لن تجد زر تعديل، فقط «عكس القيد».
4. سجّل خروج، وادخل باسم المراجع (`heba.elsherif`) ← محاولة تعديل أي شيء ستُرفض بـ 403.
5. جرّب المساعد المالي: «إجمالي مديونية الموردين والمتأخر منها؟».
