---
title: مرجع CLI
description: مرجع کامل برای تمام دستورات و پرچم‌های Meridian CLI.
order: 10
section: reference
---

## دستورات

### meridian deploy

نصب سرور proxy روی یک VPS.

```
meridian deploy [IP] [flags]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--sni HOST` | www.cloudflare.com | هدف پوشش TLS |
| `--domain DOMAIN` | (هیچ) | دامنه بازگشت CDN Cloudflare |
| `--client-name NAME` | default | نام برای اولین کلاینت |
| `--display-name NAME` | (هیچ) | برچسب برای صفحات اتصال |
| `--icon EMOJI_OR_URL` | (هیچ) | آیکون صفحه — ایموجی یا URL تصویر |
| `--color PALETTE` | ocean | تم رنگ صفحه (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | کاربر SSH |
| `--harden / --no-harden` | فعال | سخت‌سازی SSH + فایروال |
| `--pq / --no-pq` | غیرفعال | رمزنگاری پساکوانتومی — ML-KEM-768 ترکیبی (آزمایشی) |
| `--server NAME` | | سرور هدف (نام یا IP) |
| `--decoy MODE` | none | پاسخ فریبنده (`none` / `403`) |
| `--yes` | | رد شدن از درخواست‌های تأیید |

### meridian client

مدیریت کلیدهای دسترسی کلاینت.

```
meridian client add NAME [--server NAME]
meridian client show NAME [--server NAME]
meridian client list [--server NAME]
meridian client remove NAME [--server NAME]
```

### meridian server

مدیریت سرورهای شناخته‌شده.

```
meridian server add [IP]
meridian server list
meridian server remove NAME
```

### meridian relay

مدیریت نودهای relay — دستگاه‌های ارسال TCP سبکی که ترافیک را از طریق یک سرور داخلی به سرور خروجی در خارج از کشور منتقل می‌کنند.

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--exit/-e EXIT` | (الزامی برای deploy) | IP یا نام سرور خروجی |
| `--name NAME` | (خودکار) | نام دوستانه برای relay (مثلاً "ru-moscow") |
| `--port/-p PORT` | 443 | پورت شنوندگی روی سرور relay |
| `--user/-u USER` | root | کاربر SSH روی relay |
| `--yes/-y` | | عدم تأیید پیش‌ |

**نحوه کار relay**: کلاینت به IP داخلی relay متصل می‌شود. Relay TCP خام را به سرور خروجی در خارج منتقل می‌کند. تمامی رمزگذاری میان کلاینت و سرور خروجی انجام می‌شود — relay هرگز plaintext را نمی‌بیند. تمام پروتکل‌ها (Reality، XHTTP، WSS) از طریق relay کار می‌کنند.

### meridian preflight

تأیید سرور قبل از نصب. SNI، پورت‌ها، DNS، OS، دیسک، ASN را بدون نصب هیچ چیز تست می‌کند.

```
meridian preflight [IP] [--ai] [--server NAME]
```

### meridian scan

یافتن هدف‌های SNI بهینه روی شبکه سرور با استفاده از RealiTLScanner.

```
meridian scan [IP] [--server NAME]
```

### meridian test

تست دسترسی پروکسی و تأیید اتصالات واقعی از دستگاه کلاینت. SSH نیازی نیست.

ابتدا دسترسی پذیری پایه را بررسی می‌کند (TCP، دست‌دهی TLS، HTTPS دامنه). سپس یک باینری کلاینت xray محلی را دانلود می‌کند (پس از اولین استفاده کش شود)، برای هر پروتکل فعال (Reality، XHTTP، WSS) از طریق پروکسی متصل می‌شود و تأیید می‌کند که ترافیک از انتها به انتها جریان دارد.

```
meridian test [IP] [--server NAME]
```

### meridian doctor

جمع‌آوری تشخیص‌های سیستم برای اشکال‌زدایی. نام مستعار: `meridian rage`.

```
meridian doctor [IP] [--ai] [--server NAME]
```

### meridian teardown

حذف proxy از سرور.

```
meridian teardown [IP] [--server NAME] [--yes]
```

### meridian update

بروزرسانی CLI به آخرین نسخه.

```
meridian update
```

### meridian --version

نمایش نسخه CLI.

```
meridian --version
meridian -v
```

## پرچم‌های سراسری

| پرچم | توضیح |
|------|-------------|
| `--server NAME` | هدف قرار دادن یک سرور نام‌گذاری شده خاص |

## تعریف سرور

دستوراتی که به سرور نیاز دارند این اولویت را دنبال می‌کنند:
1. استدلال IP صریح
2. پرچم `--server NAME`
3. تشخیص حالت محلی (اجرا روی خود سرور)
4. انتخاب خودکار سرور تک (اگر فقط یکی ذخیره شده باشد)
5. درخواست تعاملی
