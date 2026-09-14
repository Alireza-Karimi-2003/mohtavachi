# محتواچی — Production Deployment

این فایل مسیر استقرار نسخه فعلی روی یک Linux VPS را مشخص می‌کند.

## پیش‌نیازها

- Linux VPS
- یک Domain یا Subdomain مثل `bot.example.com`
- Docker + Docker Compose plugin
- دسترسی DNS برای ساختن A/AAAA record

## 1) DNS

برای Domain یک `A` record به IP سرور بزن.
اگر IPv6 استفاده نمی‌کنی، فعلاً `AAAA` نساز.

## 2) فایل محیطی

`.env.production.example` را به `.env` کپی کن و مقادیر واقعی را وارد کن.

حداقل این‌ها باید واقعی باشند:

- `DOMAIN`
- `PUBLIC_BASE_URL`
- `BOT_TOKEN`
- `ADMIN_TELEGRAM_ID`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- AI provider settings

`PAYMENT_ENABLED` فعلاً `false` بماند.

## 3) اجرای Production

```bash
docker compose -f docker-compose.production.yml up -d --build
```

Migrationها هنگام startup به‌صورت خودکار اجرا می‌شوند.

## 4) بررسی

برای زنده بودن API:

```bash
docker compose -f docker-compose.production.yml ps
curl https://YOUR-DOMAIN/health
```

برای آماده بودن API و دسترسی به PostgreSQL:

```bash
curl https://YOUR-DOMAIN/ready
```

هر دو endpoint در حالت سالم باید `{"ok":true}` برگردانند.

## 5) ثبت Telegram Webhook

بعد از بالا آمدن HTTPS:

```bash
docker compose -f docker-compose.production.yml exec bot python set_webhook.py
```

اسکریپت از `PUBLIC_BASE_URL` و `WEBHOOK_SECRET` استفاده می‌کند.

## 6) نکته مهم درباره AI

Production به‌صورت پیش‌فرض از یک `OpenAI-compatible` provider خارجی استفاده می‌کند. در `.env` این موارد را با مقدار واقعی provider خود تنظیم کن:

- `AI_PROVIDER=openai_compatible`
- `AI_API_BASE_URL=https://...`
- `AI_API_KEY=...`
- `AI_MODEL=...`

همچنین API تصویر باید از همین مسیر خارجی قابل دسترس باشد. مقدارهای `127.0.0.1` و placeholderها برای Production پذیرفته نمی‌شوند.

## 7) Payment

تا وقتی Merchant ID و تست واقعی انجام نشده:

```env
PAYMENT_ENABLED=false
```

را نگه دار.

## 8) لاگ‌ها

```bash
docker compose -f docker-compose.production.yml logs -f bot
```

و برای Caddy:

```bash
docker compose -f docker-compose.production.yml logs -f caddy
```

## 9) Backup

داده اصلی PostgreSQL داخل volume `postgres_data` است. برای backup دستی:

```bash
./deploy/backup_postgres.sh
```

به‌صورت پیش‌فرض backupها در `./backups/postgres` ذخیره می‌شوند و فایل‌های قدیمی‌تر از 7 روز حذف می‌شوند. این پوشه در Git و Docker build نادیده گرفته می‌شود. برای Production بهتر است اجرای روزانه این script را با `cron` یا systemd timer تنظیم کنی.

بعد از راه‌اندازی، حداقل یک بار restore واقعی backup را روی یک PostgreSQL آزمایشی انجام بده.

## معماری

Internet → Caddy (HTTPS :80/:443) → Bot API (:8000 داخلی) → PostgreSQL (:5432 داخلی)

PostgreSQL و port 8000 مستقیماً روی Internet expose نشده‌اند.
