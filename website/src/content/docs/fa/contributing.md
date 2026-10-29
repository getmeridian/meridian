---
title: مشارکت
description: راه‌اندازی توسعه، دستورالعمل PR و رویکرد تست.
---

## گزارش مشکلات

- **باگ پیدا کردید؟** از [قالب گزارش باگ](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) استفاده کنید — ابتدا `meridian doctor` را اجرا کنید
- **مشکل اتصال دارید؟** از [قالب مشکل اتصال](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) استفاده کنید — ابتدا `meridian test` و `meridian check` را اجرا کنید
- **پیشنهاد قابلیت جدید دارید؟** از [قالب درخواست قابلیت](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml) استفاده کنید
- **آسیب‌پذیری امنیتی؟** [امنیت](/fa/security/) را ببینید — issue عمومی باز نکنید

## راه‌اندازی محیط توسعه

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# نصب CLI در حالت قابل ویرایش همراه با وابستگی‌های توسعه
make install

# نصب هوک pre-push (بررسی‌های سریع پیش از هر git push)
make hooks

# اجرای کامل CI روی سیستم محلی
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
- `provision/` — مراحل آماده‌سازی سرور (package‌ها، hardening، سرویس کنترل، Realm)
- `control/` — سرویس کنترل (`meridian serve`): کاربران، اشتراک‌ها، اسناد امضاشدهٔ گره‌ها، پشتیبان‌ها و کنسول وب
- `backup/` — snapshotها، کلیدهای دستگاه و `meridian restore`

agent گره یک crate به زبان Rust در `agent/` در ریشهٔ مخزن است؛ buildهای آن درون بستهٔ Python عرضه می‌شوند.

## Pull requests

1. repo را fork کنید و branch را از `main` ایجاد کنید
2. تغییرات متمرکز و کمینه انجام دهید
3. اطمینان دهید CI موفق است: `make ci`
4. در صورت امکان روی سرور واقعی تست کنید
5. یک PR را با توضیح واضح باز کنید

## قراردادهای کلیدی

- **مقادیر شل با `shlex.quote()` ایمن می‌شوند** — هرگز مقادیر پاک‌سازی‌نشده را مستقیم در دستورات شل قرار ندهید
- **فایل‌های PWA باید همگام بمانند** (`templates/pwa/` و کاتالوگ برنامه‌ها)
- **پیکربندی nginx** به ازای هر منبع در فایل‌های `meridian-v4-*.conf` در مسیرهای `/etc/nginx/stream.d/` و `/etc/nginx/sites-available/` نوشته می‌شود
- **مراحل provisioner** خروجی `StepResult` برمی‌گردانند (`ok`/`changed`/`skipped`/`failed`)

## تست

CI این موارد را اعتبارسنجی می‌کند: تست‌های Python، لینتر `ruff`، بررسی نوع با `mypy`، رندر قالب‌ها و نحو اسکریپت‌های شل. برای تست کامل استقرار، `make system-lab` (استقرار چندنودی در کانتینرهای Docker همراه با تست ترافیک واقعی) را اجرا کنید یا روی یک VPS واقعی (`make real-lab`) آزمایش کنید.
