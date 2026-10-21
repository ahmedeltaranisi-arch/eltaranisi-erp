# قاعدة البيانات على Cloudflare D1 — Database on Cloudflare D1

> **لماذا؟** في نشر Vercel كان ملف SQLite يُكتب في `/tmp`، وهو قرص مؤقت لكل نسخة من
> الدالة: تسجيل الخروج ثم الدخول يبدو سليمًا، لكن إعادة النشر أو إعادة تشغيل النسخة
> تمسح كل ما أُدخل. D1 قاعدة بيانات مُدارة داخل Cloudflare، تبقى بعد إعادة التشغيل
> والنشر، ويصلها التطبيق عبر HTTPS.

> **Why?** On Vercel the SQLite file lived in `/tmp` — a temporary disk per function
> instance. Data vanished on redeploy/restart. D1 is Cloudflare's managed SQLite:
> durable, reachable over HTTPS from anywhere, no native file needed.

---

## 1. إنشاء التوكن — Create the API token

1. افتح <https://dash.cloudflare.com/profile/api-tokens> → **Create Token**.
2. **Custom token**، واضبط:
   - **Permissions:** `Account` → `D1` → **Edit**
   - (اختياري لكن مفيد) `Account` → `Account Settings` → **Read**
   - **Account Resources:** الحساب الخاص بك
3. انسخ التوكن (يظهر مرة واحدة).

المتغيّرات المطلوبة:

| Variable | القيمة | ملاحظات |
|---|---|---|
| `CF_API_TOKEN` | التوكن أعلاه | سرّي — يُضاف في Vercel فقط، ولا يُكتب في المستودع |
| `CF_ACCOUNT_ID` | من لوحة Cloudflare (Overview → Account ID) | أو اتركه فارغًا ليكتشفه السكربت |
| `CF_D1_DATABASE_ID` | يُطبعها سكربت التهيئة | أو `CF_D1_DATABASE=eltaranisi-erp` بالاسم |

---

## 2. التهيئة — Provision the database

```bash
export CF_API_TOKEN=...          # لا يُخزَّن في أي ملف
python3 scripts/setup_cloudflare_d1.py \
        --account <ACCOUNT_ID> --name eltaranisi-erp --seed
```

السكربت:

1. يتحقق من التوكن ويجد الحساب،
2. ينشئ قاعدة البيانات إن لم تكن موجودة،
3. يطبّق `schema.sql` (47 جدولًا + الفهارس والـ views) على دفعات ذرّية،
4. يزرع البيانات التجريبية (`--seed`) — **ولن يزرع فوق قاعدة فيها بيانات**،
5. يطبع متغيّرات البيئة الجاهزة للصق في Vercel.

`--dry-run` يطبع ما سيفعله دون تنفيذ.
تشغيل السكربت مرة ثانية آمن: يكتشف القاعدة ويخرج.

---

## 3. متغيّرات Vercel

Vercel → المشروع `eltaranisi-erp` → **Settings → Environment Variables (Production)**:

```
ERP_DB_BACKEND=d1
CF_ACCOUNT_ID=<account id>
CF_D1_DATABASE_ID=<uuid>
CF_API_TOKEN=<token>
ERP_EPHEMERAL_AUTH=0
ERP_SECRET_KEY=<نفس المفتاح المستخدم حاليًا لتوقيع الجلسات>
ERP_SEED_MODE=demo        # أو اتركه فارغًا لقاعدة نظيفة
```

ثم **Redeploy**. عند أول طلب تُهيّأ القاعدة (migrations) ويصبح
`GET /api/health → storage.durable = true`.

> على Vercel لا تُنشئ دالة الخادم ملف SQLite محليًا عندما `ERP_DB_BACKEND=d1`
> (انظر `api/index.py`)، و`ERP_EPHEMERAL_AUTH` يعود إلى `0` لأن جدول الجلسات
> أصبح مشتركًا — أي أن إبطال الجلسة يعمل فعليًا من جديد.

---

## 4. التحقق — Verify

```bash
curl -s https://<deployment>/api/health | python3 -m json.tool
# "storage": { "durable": true }

curl -s -H "Authorization: Bearer <token>" https://<deployment>/api/admin/storage
# engine: "cloudflare-d1" · path: "cloudflare-d1:<uuid>" · durable: true

curl -s -H "Authorization: Bearer <token>" https://<deployment>/api/admin/integrity
# passed: true · checks_passed == checks_total
```

أو من الواجهة: **الإعدادات → قاعدة البيانات والنسخ الاحتياطي**
(نوع القاعدة، حالة الحفظ، العدّادات، زرّان: نسخة احتياطية / فحص سلامة الدفاتر).

اختبار الحفظ الحقيقي: أضف بند مستحق → سجّل الخروج → أعد الدخول → البند موجود،
ثم أعد النشر (Redeploy) → البند ما زال موجودًا.

---

## 5. النسخ الاحتياطي والاسترجاع — Backup & restore

D1 لا يعطيك ملف `.db` (لا يوجد ملف)، فالنسخة الاحتياطية **تفريغ SQL منطقي**:

```bash
curl -OJ -H "Authorization: Bearer <token>" \
     https://<deployment>/api/admin/backup
# → eltaranisi-erp-backup-20261021-110039.sql  (~1 MB، كل الجداول والصفوف)

# استرجاع إلى قاعدة نظيفة
ERP_DB=/tmp/inspect.db python3 scripts/restore_backup.py eltaranisi-erp-backup-*.sql

# استرجاع إلى D1 نفسه (يمسح القاعدة ثم يعيد تحميلها)
ERP_DB_BACKEND=d1 CF_ACCOUNT_ID=… CF_API_TOKEN=… CF_D1_DATABASE_ID=… \
    python3 scripts/restore_backup.py eltaranisi-erp-backup-*.sql
```

الاسترجاع يتحقق قبل أن يمسح شيئًا: وجود الجداول الأساسية، توازن دفتر اليومية،
وسلامة المفاتيح الأجنبية (`PRAGMA foreign_key_check`). وللقاعدة المحلية يحتفظ
بالنسخة القديمة باسم `erp-replaced-<ts>.db`.

بديل إضافي من Cloudflare نفسه (خارج التطبيق):

```bash
npx wrangler d1 export eltaranisi-erp --remote --output=backup.sql
npx wrangler d1 time-travel  # استرجاع لحظي (Time Travel) في الخطط المدفوعة
```

---

## 6. ما الذي يتغيّر في السلوك — Behaviour that changes

| | SQLite محلي | Cloudflare D1 |
|---|---|---|
| المعاملات التفاعلية (`BEGIN … COMMIT`) | حقيقية | **غير متاحة عبر HTTPS** |
| العبارة الواحدة | ذرّية | ذرّية |
| عدة عبارات | معاملة واحدة | `conn.batch([...])` ذرّية (تُستخدم حيث يلزم) |
| السلامة | مضمونة بالمحرك | يتحقق منها `scripts/check_integrity.py` + `/api/admin/integrity` |
| النسخة الاحتياطية | ملف `.db` ثنائي | تفريغ `.sql` (قابل للاسترجاع) |
| الجلسات | في نفس الملف | جدول مشترك — الإبطال يعمل عبر كل النسخ |

باختصار: D1 يعطي **حفظًا دائمًا**، لكن الكتابة فيه عبارة واحدة لكل طلب ذهابًا
وإيابًا. لهذا:

* كل الروابط المالية تُنفَّذ ثم تُفحص فورًا عبر `/api/admin/integrity`
  (دفتر متوازن، قيود ذات طرفين، لا سطور يتيمة، أرقام مستندات فريدة، ميزان مراجعة مسطّح).
* يمكن تشغيل `python3 scripts/check_integrity.py` من cron خارجي بعد أي نشر.
* أي عملية يجب ألا تنقسم (مثل ترحيل دفعة من السطور) تُرسل عبر `conn.batch()`.

---

## 7. البديل: تشغيل التطبيق نفسه على Cloudflare

إن أردت أن تكون قاعدة البيانات على Cloudflare **وأيضًا** التشغيل:

* **Cloudflare Containers** (Docker) — تشغّل نفس `server.py` داخل شبكة Cloudflare،
  وتصل إلى D1 عبر HTTPS بنفس متغيّرات البيئة أعلاه (لا شيء في الكود يتغيّر).
* **Cloudflare Workers (Python)** — ممكنة لكنها Beta، ولا تتيح محرّك WSGI كاملًا
  كما في `server.py`؛ لا ننصح بها لهذا النظام حاليًا.

Vercel يبقى مناسبًا: الدالة لا تحتاج ملفًا محليًا، فكل شيء يمر عبر HTTPS إلى D1.

---

## 8. الحدود والتكلفة — Limits & cost

الخطة المجانية لـ D1 تكفي شركة بحجم هذه تقريبًا: 5 GB تخزين، 5 ملايين صف
مقروء و100 ألف صف مكتوب يوميًا، مع نسخ احتياطي لحظي (Time Travel 30 يومًا في
الخطة المدفوعة). تجاوز الحد اليومي يعيد خطأ `429`؛ لذلك يظهر في `/api/health`
الحالة، ويكفي الترقية أو تقليل استعلامات التقارير الثقيلة (كل التقارير مُصفّحة
ومفهرسة بالفعل).

---

## 9. استكشاف الأخطاء — Troubleshooting

| العرض | السبب | الحل |
|---|---|---|
| `CF_D1_DATABASE_ID or CF_D1_DATABASE is required` | لم تُضبط المتغيّرات | أضفها في Vercel وأعد النشر |
| `401 / authentication error` | التوكن بلا صلاحية D1:Edit أو محذوف | أنشئ توكنًا جديدًا |
| `no such table` بعد النشر | لم يُطبَّق `schema.sql` | `python3 scripts/setup_cloudflare_d1.py --name …` |
| `429` من Cloudflare | تجاوز حد القراءة اليومي | انتظر تجديد الحد أو رقِّ الخطة |
| البيانات لا تظهر بعد النشر | `ERP_DB_BACKEND` غير مضبوط في بيئة Production | تأكد أنه `d1` في **Production** لا Preview فقط |
| `durable: false` في `/api/health` | ما زال على SQLite او `/tmp` | راجع المتغيّرات وأعد النشر |

---

## 10. مسار التطوير محليًا بدون حساب Cloudflare

مُحاكي D1 مرفق، يتكلم نفس واجهة Cloudflare v4:

```bash
python3 tools/d1_emulator.py --port 8787 --db /tmp/d1emu/erp.db &

export ERP_DB_BACKEND=d1 \
       CF_D1_API_BASE=http://127.0.0.1:8787/client/v4 \
       CF_ACCOUNT_ID=local CF_API_TOKEN=local CF_D1_DATABASE_ID=local-1
ERP_SEED_MODE=demo ERP_SEED_FORCE=1 python3 seed.py
ERP_SEED_MODE=demo PORT=8000 python3 server.py
```

عليه تُشغَّل كل الاختبارات: `pytest tests/test_erp.py` (71 اختبارًا) و
`python3 tests/test_wsgi_bridge.py` (22 فحصًا) — أي أن مسار Vercel→D1 مُختبَر
بالكامل قبل لمس الحساب الحقيقي.
