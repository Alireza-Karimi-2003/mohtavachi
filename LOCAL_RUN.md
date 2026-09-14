# اجرای محلی محتواچی

## ساده‌ترین روش

از داخل ریشه پروژه اجرا کن:

```powershell
python start_local.py
```

این launcher ابتدا بررسی می‌کند container به نام `mohtavachi-postgres` روشن است؛ اگر خاموش باشد آن را روشن می‌کند و بعد Bot را اجرا می‌کند.

اگر container پیدا نشد، آن را با همان تنظیمات PostgreSQL پروژه ایجاد کن؛ این نسخه عمداً دیتابیس را به SQLite fallback نمی‌کند تا داده‌های PostgreSQL دوپاره نشوند.
