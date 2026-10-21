# النشر على Vercel — ELTARANISI ERP

> هذا المستند يشرح كيف يعمل المشروع على Vercel، وما هي القيود الحقيقية لهذا النوع من الاستضافة،
> وكيف تنتقل إلى نشر إنتاجي كامل عند الحاجة. كل ما هو مذكور هنا **تم اختباره فعليًا** عبر
> `tests/test_wsgi_bridge.py` (16 فحصًا) وعبر خادم WSGI حقيقي بنفس صيغة إعادة الكتابة التي يستخدمها Vercel.

---

## 1) الشكل المعماري على Vercel

```
                     ┌──────────────────────────────┐
   المتصفح  ───────► │  Vercel CDN (static/)        │  index.html · app.js · pages.js
                     │  HTML/CSS/JS · الخطوط · SVG  │  i18n.js · styles.css · fonts/*.woff2
                     └──────────────┬───────────────┘
                                    │  fetch('/api/...')
                                    ▼
                     ┌──────────────────────────────┐
                     │  /api/index  (Python 3.12)   │  server.py (بدون أي تغيير في المنطق)
                     │  api/index.py  ← WSGI bridge │  → accounting.py · documents.py · reports.py
                     └──────────────┬───────────────┘
                                    ▼
                            /tmp/eltaranisi-erp/erp.db   (SQLite)
```

| الملف | الدور |
|---|---|
| `vercel.json` | يخدم `static/` كموقع ثابت، ويربط كل `/api/*` بالدالة `api/index.py` |
| `api/index.py` | جسر WSGI رقيق (~150 سطرًا): يعيد بناء الطلب ويشغّل `server.Handler` كما هو |
| `requirements.txt` | لا حزم خارجية — موجود فقط ليتعرّف Vercel على المشروع كتطبيق Python |
| `.vercelignore` | يمنع رفع قاعدة البيانات المحلية ولقطات الشاشة والاختبارات |

**لماذا جسر WSGI وليس إعادة كتابة؟** منطق العمل كله موجود في `server.py` ومُغطّى بـ 28 اختبار تكامل.
إعادة كتابته إلى Flask/FastAPI كانت ستضيف اعتماديات ومخاطر بلا فائدة. `http.server` من المكتبة القياسية،
وVercel يتحدث WSGI — فالجسر يكفي، ولذلك لا يوجد **أي** تغيير في سلوك التطبيق بين التشغيل المحلي والنشر.

---

## 2) النشر في دقيقتين

### الحالة الحالية (نُشر فعليًا)
| البند | القيمة |
|---|---|
| Project | `eltaranisi-erp` (framework = **None**، output = `static`) |
| Production URL | **https://eltaranisi-erp.vercel.app** |
| Auto-deploy | نعم — GitHub webhook: كل push على `main` ينشر إنتاجيًا |
| Deployment Protection | **مُعطَّلة** (`ssoProtection: null`) حتى يكون الرابط عامًا |
| فحص حيّ | `/api/health` → `dev_mode:false` · `/api/dev/accounts` → 404 · PDF عربي 601KB `%PDF-1.7` |

### الطريقة أ — من لوحة Vercel (موصى بها)
1. `github.com` ← المستودع `ahmedeltaranisi-arch/eltaranisi-erp` ← **Add Project** في Vercel.
2. Framework Preset: **Other** (Vercel يقرأ `vercel.json` تلقائيًا).
3. Build Command: *اتركه فارغًا*. Output Directory: `static` (تأتي من `vercel.json`).
4. **Deploy**.

### الطريقة ب — من الطرفية
```bash
npm i -g vercel
cd eltaranisi-erp
vercel            # معاينة
vercel --prod     # إنتاج
```

### الطريقة ج — بسكربت جاهز (يشغّل الفحوصات قبل النشر)
```bash
VERCEL_TOKEN=... ./scripts/deploy-vercel.sh            # إنتاج
VERCEL_TOKEN=... ./scripts/deploy-vercel.sh --preview  # رابط معاينة
```

### متغيرات البيئة (اختيارية — لكلها قيم افتراضية تعمل)
| المتغير | الافتراضي على Vercel | المعنى |
|---|---|---|
| `ERP_DB` | `/tmp/eltaranisi-erp/erp.db` | مسار قاعدة SQLite (القرص الوحيد القابل للكتابة) |
| `ERP_CREDENTIALS_FILE` | `/tmp/eltaranisi-erp/dev-credentials.txt` | ملف بيانات الدخول التجريبية |
| `ERP_SEED_DEMO_DATA` | **`false`** (مغلق) | `true` يفتح لوحة الحسابات التجريبية ويُفعّل `GET /api/dev/accounts` — للعرض فقط |
| `ERP_SESSION_HOURS` | `12` | صلاحية الجلسة |
| **`ERP_SECRET_KEY`** | — | **مهم:** مفتاح ثابت لتوقيع الـtoken. على Vercel لا توجد قاعدة مشتركة بين النسخ (`/tmp` مؤقت)، فالتوكن موقّع ذاتيًا بهذا المفتاح. بدون ضبطه تصنع كل نسخة مفتاحًا خاصًا وتظهر أخطاء 401 / تسجيل خروج عشوائي (خاصة في صفحات البنوك) |
| `ERP_DEV_FIXED_PASSWORDS` | `true` | `false` يولّد كلمات مرور عشوائية وقت التهيئة |

---

## 3) ما يحدث عند أول طلب (Cold start)

1. Vercel يستدعي `api/index.py`.
2. الجسر يضبط `ERP_DB=/tmp/...` **قبل** استيراد `db`.
3. إن لم تكن القاعدة موجودة: `seed.seed()` ينشئ المخطط + 9 أشهر من العمليات الحقيقية (**~0.5 ثانية**).
4. الطلب يُمرَّر إلى `server.Handler` ويُعاد كما هو.

الطلب الأول بعد خمول طويل قد يستغرق ~1–2 ثانية إضافية. الطلبات التالية أسرع.

---

## 4) القيود الحقيقية (اقرأها قبل الاعتماد على النشر)

| القيد | الأثر | الحل عند الحاجة |
|---|---|---|
| **القرص مؤقت** (`/tmp`) | أي قيد أو فاتورة أو دفعة جديدة تُفقد عند إعادة تدوير النسخة (دقائق إلى ساعات) | قاعدة بيانات مُدارة (Postgres/Neon) — انظر القسم 6 |
| **كل نسخة قاعدتها الخاصة** | مستخدمان على نسختين مختلفتين قد لا يريان نفس البيانات في نفس اللحظة | نفس الحل: قاعدة مركزية |
| **بيانات وهمية** | الحسابات التجريبية تُزرع في كل نسخة جديدة. القائمة العامة **مغلقة افتراضيًا** (`/api/dev/accounts` → 404)، لكن كلمات المرور الثابتة موجودة في الكود العام — أي أنها **غير سرية، فقط غير مُعلَنة** | غيّر كلمات المرور من شاشة المستخدمين فور أول دخول، أو `ERP_DEV_FIXED_PASSWORDS=false` |
| **الحجم** | حزمة الدالة محدودة (الخطوط العربية ~1.5 MB — لا مشكلة) | — |
| **PDF/Excel** | يعملان بالكامل (مولِّدان مكتوبان بـ stdlib) | — |

### وضع العرض (Demo) — مفتوح أم مغلق؟
الافتراضي على Vercel **مغلق**: صفحة الدخول لا تعرض الحسابات، و`GET /api/dev/accounts` يُعيد 404.
الدخول يتم يدويًا بالحسابات المنشورة في `README` (`ahmed.taranisi` / `Eltaranisi@2026`).

لتحويل الرابط إلى عرض حيّ يعرض الحسابات بنقرة واحدة (مناسب للبورتفوليو):
```bash
vercel env add ERP_SEED_DEMO_DATA production   # القيمة: true
vercel env add ERP_SECRET_KEY production       # القيمة: python3 -c "import secrets;print(secrets.token_urlsafe(48))"
vercel --prod
```

> **بصراحة:** هذا النشر **عرض حيّ (live demo)** لنظام محاسبي عامل — كل رقم فيه محسوب من قاعدة بيانات
> حقيقية بمحرك القيد المزدوج، لكنه ليس بديلًا عن نشر إنتاجي بقاعدة بيانات دائمة. للنشر الإنتاجي اتبع القسم 6.

---

## 5) قائمة تحقق بعد النشر

```bash
BASE=https://<your-project>.vercel.app

curl -s $BASE/api/health                 # {"status":"ok", ...}
curl -s $BASE/api/dev/accounts | head -c 120   # 7 حسابات تجريبية

# دخول + تنزيل فاتورة PDF (يثبت القاعدة + الجلسة + مُولِّد الـ PDF معًا)
TOKEN=$(curl -s -X POST $BASE/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"ahmed.taranisi","password":"Eltaranisi@2026"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s "$BASE/api/sales-invoices/54/pdf?lang=ar" -H "Authorization: Bearer $TOKEN" -o invoice.pdf
file invoice.pdf                          # PDF document, version 1.7
```

ثم من المتصفح: شاشة الدخول ← «حسابات تجريبية» ← نقرة واحدة ← لوحة التحكم ← فاتورة ← **PDF / طباعة**.

---

## 6) الانتقال إلى إنتاج حقيقي

1. **قاعدة بيانات مركزية:** استبدل `db.py` بـ Postgres (Neon / Supabase / RDS). واجهة `db.read()` /
   `db.transaction()` معزولة في ملف واحد، لذا التغيير محصور هناك + تحويل `INTEGER` إلى `BIGINT`
   (المبالغ بالقروش) و`?` إلى `%s`. جميع الاستعلامات مكتوبة يدويًا بدون ORM — الترحيل ميكانيكي.
2. **أسرار حقيقية:** `ERP_SECRET_KEY` من مخزن أسرار Vercel، و`ERP_SEED_DEMO_DATA=false`، وحذف ملف
   `data/dev-credentials.txt` من أي صورة نشر.
3. **جلسات:** تُخزَّن في جدول `sessions` — تعمل مع قاعدة مركزية كما هي.
4. **ملفات PDF الكبيرة / التقارير الثقيلة:** انتقل إلى Background Functions أو Storage مؤقت (S3).
5. **نسخ احتياطي:** `infra/backup.sh` يعمل على القاعدة المحلية؛ مع Postgres استخدم PITR المزوّد
   (RPO/RTO موثّقة في `docs/deployment/DEPLOYMENT.md`).

---

## 7) استكشاف الأخطاء

| العَرَض | السبب المحتمل | الحل |
|---|---|---|
| `500 INTERNAL_ERROR` مع `serverless bridge failed` | ملف ناقص في الحزمة | تأكد أن `includeFiles` في `vercel.json` يضم `*.py` و`schema.sql` و`assets/**` |
| `/api/...` يعيد 404 لكل المسارات | إعادة الكتابة لم تُطبَّق | ضع `vercel.json` في **جذر** المستودع (وليس داخل مجلد فرعي) |
| `502 ROUTE_NOT_FORWARDED` | الـ rewrite وصل الدالة لكن بدون `__p` | الجسر يشرح السبب في الاستجابة نفسها (هيدر `X-Route-Debug`) — تحقق أن `rewrites` كما في `vercel.json` ولا تستخدم `routes` القديمة |
| الصفحة تفتح لكن بلا تنسيق | `outputDirectory` خاطئ | يجب أن يكون `static` |
| الخط العربي غير محمّل | `fonts/` خارج مجلد الإخراج | الخطوط موجودة في `static/fonts/` — تأكد من وجودها في الريبو |
| البيانات تعود لحالتها كل فترة | هذا هو القيد المذكور في القسم 4 | قاعدة بيانات دائمة |
