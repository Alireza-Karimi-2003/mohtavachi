from __future__ import annotations

import shutil
import subprocess
import sys

CONTAINER = "mohtavachi-postgres"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True)


def main() -> int:
    if shutil.which("docker") is None:
        print("Docker Desktop پیدا نشد. Docker Desktop را نصب/اجرا کن و دوباره تلاش کن.")
        return 1

    inspect = run("docker", "inspect", "-f", "{{.State.Running}}", CONTAINER)
    if inspect.returncode != 0:
        print(f"کانتینر PostgreSQL با نام {CONTAINER!r} پیدا نشد.")
        print("این پروژه انتظار دارد PostgreSQL با همین نام روی پورت 5432 در دسترس باشد.")
        return 1

    if inspect.stdout.strip().lower() != "true":
        print("PostgreSQL خاموش بود؛ در حال روشن کردن Docker container...")
        start = run("docker", "start", CONTAINER)
        if start.returncode != 0:
            print(start.stderr.strip() or "Docker نتوانست PostgreSQL را روشن کند.")
            return 1
        print("PostgreSQL روشن شد. ✅")
    else:
        print("PostgreSQL از قبل روشن است. ✅")

    print("در حال بررسی و به‌روزرسانی دیتابیس...")
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        text=True,
    )
    if migration.returncode != 0:
        print("به‌روزرسانی دیتابیس انجام نشد.")
        return migration.returncode
    print("Database migration: PASS")

    print("در حال اجرای محتواچی...")
    return subprocess.call([sys.executable, "-m", "app.main"])


if __name__ == "__main__":
    raise SystemExit(main())
