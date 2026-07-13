---
title: مشارکت
description: تنظیم توسعه، راهنمای PR، و رویکرد تست.
order: 12
section: reference
---

## گزارش مسائل

- **باگ؟** از [template گزارش باگ](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) استفاده کنید — ابتدا `meridian doctor` را اجرا کنید
- **مسئله اتصال؟** از [template مسئله اتصال](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) استفاده کنید — ابتدا `meridian test` و `meridian preflight` را اجرا کنید
- **ایده ویژگی؟** از [template درخواست ویژگی](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml) استفاده کنید
- **آسیب‌پذیری امنیتی؟** [امنیت](/docs/fa/security/) را ببینید — یک issue عمومی باز نکنید

## تنظیم توسعه

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# نصب CLI در حالت قابل ویرایش با وابستگی‌های توسعه
make install

# نصب pre-push hook (11 بررسی سریع قبل از هر push)
make hooks

# اجرای کل CI محلی
make ci

# بررسی‌های جداگانه:
make test              # pytest
make lint              # ruff check
make format-check      # ruff format --check
make typecheck         # mypy
make templates         # Jinja2 template validation
```

## ساختار پروژه

CLI یک بسته Python است (`src/meridian/`) که از طریق PyPI به عنوان `meridian-vpn` توزیع می‌شود.

ماژول‌های کلیدی:
- `cli.py` — Typer app، ثبت subcommand
- `commands/` — یک ماژول در هر subcommand
- `cluster.py` + `cluster_persistence.py` — وضعیت نوع‌دار `cluster.yml` و ذخیره‌سازی آن
- `servers.py` — `ServerRegistry` برای سرورهای شناخته‌شده
- `provision/` — خط لوله مرحله idempotent

## درخواست‌های Pull

1. مخزن را fork کنید و یک شاخه از `main` ایجاد کنید
2. تغییرات متمرکز، حداقلی انجام دهید
3. مطمئن شوید CI موفق است: `make ci`
4. اگر ممکن است روی یک سرور واقعی تست کنید
5. یک PR با توضیح واضح باز کنید

## قراردادهای کلیدی

- **مقادیر Shell از `shlex.quote()` استفاده می‌کنند** — هرگز مقادیر تمیز‌نشده را درون‌یاب نکنید
- **PWA تنها پیاده‌سازی صفحه اتصال است** — دارایی‌های `templates/pwa/` و فهرست برنامه‌ها (`_PWA_APPS` در برابر `website/src/data/apps.json`) باید همگام بمانند
- **پیکربندی nginx** از `/etc/nginx/stream.d/meridian.conf` و `/etc/nginx/conf.d/meridian-http.conf` استفاده می‌کند
- **مراحل Provisioner** `StepResult` برمی‌گردانند (ok/changed/skipped/failed)

## تست

CI تأیید می‌کند: آزمون‌های Python، ruff lint، mypy types، rendering template، و syntax اسکریپت shell. تست نصب کامل نیاز به یک VPS واقعی دارد.
