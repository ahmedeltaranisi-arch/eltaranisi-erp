# التشغيل والنشر — ELTARANISI ERP

## 1. التشغيل المحلي (أسرع طريقة)

```bash
cd eltaranisi-erp
python3 seed.py                 # ينشئ قاعدة بيانات التطوير + بيانات تجريبية (يحذف القديمة!)
PORT=8000 python3 server.py     # http://localhost:8000
```

بيانات الدخول تُكتب في `data/dev-credentials.txt` (لا تُلتزم في المستودع).
المتطلبات: Python 3.10+ فقط — **لا حزم خارجية**.

### متغيرات البيئة

| المتغير | الافتراضي | الوصف |
|---|---|---|
| `PORT` | 8000 | منفذ HTTP |
| `HOST` | 0.0.0.0 | عنوان الاستماع |
| `ERP_DB` | `./data/erp.db` | مسار قاعدة البيانات |
| `ERP_SEED_DEMO_DATA` | true | بيانات تجريبية (تطوير فقط) |

انظر `.env.example` لكل المتغيرات. **لا أسرار في المستودع.**

## 2. النشر بـ Docker

```bash
docker compose -f infra/docker-compose.yml up --build          # app on :8000
docker compose -f infra/docker-compose.yml --profile with-proxy up   # + nginx TLS
```

- الصورة تعمل بمستخدم غير جذري (`erp`, uid 10001).
- `HEALTHCHECK` على `/api/health`.
- البيانات في volume `erp-data` (لا تفقدها عند إعادة البناء).
- ضع `infra/certs/fullchain.pem` و`privkey.pem` قبل تفعيل البروكسي (Let's Encrypt).

## 3. قائمة تحقق قبل الإنتاج

- [ ] HTTPS إلزامي (nginx/ALB/Cloudflare) + HSTS.
- [ ] `ERP_SECRET_KEY` عشوائي 48+ بايت من Secrets Manager.
- [ ] `ERP_SEED_DEMO_DATA=false` وحذف أي بيئة تجريبية.
- [ ] تغيير كل كلمات المرور المولّدة، وتفعيل MFA (عند توفره).
- [ ] نسخ احتياطي مجدول كل 15 دقيقة + تشفير + اختبار استعادة أسبوعي.
- [ ] مراقبة: `/api/health` + تنبيه على أخطاء 5xx + مراقبة زمن الاستجابة.
- [ ] سجلات مركزية (stdout → Loki/CloudWatch) بدون أي بيانات حساسة.
- [ ] مراجعة صلاحيات كل مستخدم قبل التسليم (مبدأ أقل صلاحية).

## 4. الصحة والمراقبة

| الهدف | القيمة |
|---|---|
| استجابة `/api/health` | < 20ms |
| استجابة التقارير (حتى 300 قيد) | < 150ms (مقاسة) |
| p95 API (هدف) | < 400ms |
| التوفر المستهدف | 99.5% |
| RPO | ≤ 15 دقيقة (نسخ تلقائي) |
| RTO | ≤ 30 دقيقة (استعادة + إعادة تشغيل) |

- Health: `GET /api/health` → `{"status":"ok", ...}`.
- Slow query: سجّل أي طلب > 500ms في سجل التطبيق.
- تنبيهات مقترحة: نسبة أخطاء 5xx > 1%، فشل نسخ احتياطي، انتهاء مساحة القرص.

## 5. النسخ الاحتياطي والاستعادة

```bash
BACKUP_DIR=/var/backups/erp BACKUP_AGE_RECIPIENT=age1... ./infra/backup.sh
AGE_IDENTITY=~/.age/erp.key ERP_DB=/srv/erp/data/erp.db ./infra/restore.sh /var/backups/erp/erp-20261020T225436Z.db.gz.age
```

- `VACUUM INTO` يضمن نسخة متسقة أثناء التشغيل.
- السكربت يكتب `sha256` ويتحقق من السلامة (`PRAGMA integrity_check`) عند الاستعادة.
- احتفظ بنسخة أسبوعية خارج الموقع (S3 مع Object Lock أو ما يعادله).
- **تم اختبار السكربتين فعليًا** في هذه البيئة: نسخة 92KB → استعادة integrity ok.

Cron مقترح:
```
*/15 * * * * cd /srv/erp && ./infra/backup.sh >> /var/log/erp-backup.log 2>&1
0 3 * * 0   aws s3 sync /var/backups/erp s3://eltaranisi-backups/weekly/
```

## 6. خطة التراجع (Rollback)

1. أوقف الخدمة.
2. استعد آخر نسخة سليمة: `./infra/restore.sh <archive>`.
3. أعد تشغيل الإصدار السابق من الصورة/الوسم (`docker compose up -d erp:<previous-tag>`).
4. تحقق: `/api/health` + تقرير ميزان المراجعة متوازن + عيّنة من آخر 5 قيود.
5. وثّق الحادثة في `docs/PHASE_REPORT.md` (قسم الحوادث).

## 7. الترحيل إلى PostgreSQL (خطة مقترحة)

| الخطوة | العمل |
|---|---|
| 1 | تحويل `db.py` إلى طبقة اتصال بـ `psycopg` بنفس الواجهة (`query`, `one`, `execute`, `transaction`) |
| 2 | ترحيل المخطط بتحويلات `docs/database/DATABASE.md §6` |
| 3 | Prisma/Drizzle + migrations مُراجَعة في CI |
| 4 | نسخ البيانات بسكربت تحويل + مطابقة أرصدة الأستاذ قبل/بعد |
| 5 | تفعيل RLS على `company_id` واختبار العزل مرة أخرى |
| 6 | تشغيل اختبارات التكامل الـ20 على PostgreSQL كبوابة نشر |

## 8. استكشاف الأخطاء

| العرض | السبب المحتمل | الحل |
|---|---|---|
| `PERIOD_CLOSED` | الفترة مقفلة | افتح الفترة أو غيّر تاريخ القيد |
| `SOD_VIOLATION` | منشئ القيد يحاول اعتماده | استخدم مستخدمًا آخر (هذا مقصود) |
| `NOT_APPROVED` | محاولة ترحيل غير معتمد | مرّر القيد في مسار المراجعة |
| `UNBALANCED` | فرق مدين/دائن | راجع السطور |
| `INSUFFICIENT_STOCK` | صرف أكثر من الرصيد | سجّل استلامًا أولًا |
| فشل الدخول | قفل بعد 8 محاولات | انتظر 15 دقيقة |
| الواجهة لا تُحدّث بعد تعديل الكود | عملية الخادم قديمة | أوقف وأعد تشغيل الخادم |
