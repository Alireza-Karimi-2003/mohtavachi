# محتواچی — Telegram AI Content MVP

ربات Telegram برای تولید محتوای فارسی با Brand Profile، Freemium، Referral و Loyalty. Referral: اولین دعوت موفق در هر چرخه ۳۰روزه +۱۵ اعتبار برای دعوت‌کننده، سه دعوت موفق بعدی هرکدام +۵ اعتبار؛ دعوت‌شده +۱۵ اعتبار پس از اولین generation موفق.

## سریع‌ترین راه اجرا

1. Python 3.11+ نصب کنید.
2. `python -m venv .venv`
3. ویندوز: `.venv\\Scripts\\activate`
4. `pip install -r requirements.txt`
5. `.env.example` را به `.env` کپی کنید و `BOT_TOKEN` را وارد کنید.
6. Ollama را نصب و مدل را دریافت کنید:

```bash
ollama pull gemma3:12b
```

7. سرور را اجرا کنید:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

8. یک HTTPS public endpoint بسازید و Telegram webhook را روی:

`POST /telegram/webhook`

با header زیر تنظیم کنید:

`X-Telegram-Bot-Api-Secret-Token: <WEBHOOK_SECRET>`

## نکات مهم

- پرداخت در این نسخه خاموش است.
- `Payment` فقط بعد از انتخاب route نهایی و بررسی قوانین Telegram و درگاه فعال شود.
- Production از PostgreSQL استفاده می‌کند و Docker Compose آن را مدیریت می‌کند.
- Bot username در `app/main.py` با username واقعی جایگزین شود.

## تست

```bash
python -m compileall app
pytest -q
```

### Studio+

The project includes a separate Studio+ tier with higher credits, a dedicated premium-tools section, and configurable premium AI model routing. Studio+ does not change the behavior of the existing plans.
