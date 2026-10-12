---
title: مشارکت
description: راه‌اندازی توسعه، دستورالعمل PR و رویکرد تست.
order: 12
section: reference
---

## گزارش مسائل

- **باگ؟** از [الگوی گزارش باگ](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) استفاده کنید — ابتدا `meridian doctor` را اجرا کنید
- **مسئله اتصال؟** از [الگوی مسئله اتصال](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) استفاده کنید — ابتدا `meridian test` و `meridian check` را اجرا کنید
- **ایده ویژگی؟** از [الگوی درخواست ویژگی](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml) استفاده کنید
- **آسیب‌پذیری امنیتی؟** ببینید [Security](/docs/fa/security/) — issue عمومی نباز نکنید

## راه‌اندازی توسعه

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# نصب CLI در حالت قابل ویرایش با وابستگی‌های توسعه
make install

# نصب hook پیش‌فشار (بررسی‌های سریع پیش از هر فشار)
make hooks

# اجرای CI کامل محلی
make ci

# بررسی‌های جداگانه:
make test              # pytest
make lint              # ruff check
make format-check      # ruff format --check
make typecheck         # mypy
make templates         # Jinja2 template validation
```

## ساختار پروژه

CLI یک package Python است (`src/meridian/`) که از طریق PyPI به‌عنوان `meridian-vpn` توزیع می‌شود.

ماژول‌های کلیدی:
- `cli.py` — Typer app، ثبت subcommand
- `commands/` — یک ماژول برای هر subcommand
- `cluster.py` + `cluster_persistence.py` — typed `cluster.yml` state و persistence
- `servers.py` — `ServerRegistry` برای سرورهای شناخته‌شده
- `setup/` — وضعیت ماژول `meridian setup` و runtime به‌اشتراک‌گذاری شده در پس deploy، setup و apply
- `compiler/` — تبدیل topology به لیست منابع مرور‌شده و hash-identified (بدون I/O)
- `reconciler/` — مشاهده و اعمال آن منابع با checkpoints
- `provision/` — مراحل آماده‌سازی سرور (package‌ها، hardening، Docker، Realm)

## Pull requests

1. repo را fork کنید و branch را از `main` ایجاد کنید
2. تغییرات متمرکز و کمینه انجام دهید
3. اطمینان دهید CI موفق است: `make ci`
4. در صورت امکان روی سرور واقعی تست کنید
5. یک PR را با توضیح واضح باز کنید

## کنوانسیون‌های کلیدی

- **مقادیر شل از `shlex.quote()` استفاده می‌کنند** — هیچ‌گاه مقادیر غیرتمیز را تقسیم نکنید
- **PWA assets باید همگام بمانند** (`templates/pwa/` و کاتالوگ برنامه)
- **پیکربندی nginx** به‌عنوان منبع per-resource به‌عنوان فایل‌های `meridian-v4-*.conf` در `/etc/nginx/stream.d/` و `/etc/nginx/sites-available/` نوشته می‌شود
- **مراحل provisioner** `StepResult` را بازمی‌گردانند (ok/changed/skipped/failed)

## تست

CI اعتبارسنجی می‌کند: تست Python، lint ruff، نوع mypy، رندر template و syntax اسکریپت شل. تست deployment کامل نیاز به VPS واقعی دارد.
