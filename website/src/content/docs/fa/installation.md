---
title: نصب
description: نصب Meridian CLI در کامپیوتر محلی خود.
order: 2
section: guides
---

## نصب سریع

```
curl -sSf https://getmeridian.org/install.sh | bash
```

این اسکریپت:
1. اگر موجود نیست [uv](https://docs.astral.sh/uv/) را نصب می‌کند (یا pipx به عنوان fallback استفاده می‌کند)
2. `meridian-vpn` را از PyPI نصب می‌کند
3. یک symlink در `/usr/local/bin/meridian` برای دسترسی سراسری سیستم ایجاد می‌کند

## نصب دستی

با uv (توصیه شده):
```
uv tool install meridian-vpn
```

با pipx:
```
pipx install meridian-vpn
```

## به‌روزرسانی

```
meridian update
```

Meridian به‌طور خودکار برای به‌روزرسانی‌ها بررسی می‌کند:
- **نسخه‌های وصله** (رفع اشکالات) — بدون سر و صدا نصب می‌شود
- **نسخه‌های جزئی** (ویژگی‌های جدید) — از شما خواسته می‌شود به‌روزرسانی کنید
- **نسخه‌های اصلی** (تغییرات شکاف‌دار) — از شما خواسته می‌شود به‌روزرسانی کنید

## نیازمندی‌ها

- **Python 3.11+** (توسط uv/pipx به‌طور خودکار نصب می‌شود)
- **دسترسی کلید SSH** به سرور هدف خود
کدهای QR ترمینال با بستهٔ Python داخلی `segno` ساخته می‌شوند و به وابستگی سیستمی نیاز ندارند.

## تأیید نصب

```
meridian --version
```
