---
title: حالت دامنه
description: از طریق Cloudflare CDN برای تاب‌آوری مقابل مسدودیت IP اضافه کنید.
order: 4
section: guides
---

## حالت دامنه چه می‌افزاید

```
meridian deploy 198.51.100.10 --domain vpn.example.com
```

با دامنه خود، خروجی دو راه دیگر برای ورود کنار Reality ارائه می‌دهد:

- **XHTTP** و **WSS** روی دامنه شما، با یک certificate Let's Encrypt که Meridian صادر و تجدید می‌کند (acme.sh)
- هنگامی که دامنه از طریق Cloudflare proxied می‌شود، هر دو مسیر از طریق CDN Cloudflare می‌رود — بنابراین حتی اگر IP سرور شما مسدود شود کار می‌کنند

پنل و صفحات اتصال روی certificate IP سرور باقی می‌مانند؛ دامنه تنها مسیرهای proxy را حمل می‌کند.

Hysteria2 را نیز بر روی UDP/443 با certificate دامنه ارائه دهید `--hysteria2` را اضافه کنید (ببینید [Hysteria2 و Cloudflare](#hysteria2-و-cloudflare)).

## راه‌اندازی Cloudflare

**این ترتیب را دنبال کنید** تا مشکلات certificate جلوگیری شود:

1. دامنه خود را در Cloudflare اضافه کنید و یک **رکورد A** ایجاد کنید که به IP سرور شما اشاره کند
2. آیکن ابر را **خاکستری** نگاه دارید ("DNS only")
3. `meridian deploy 198.51.100.10 --domain vpn.example.com` را اجرا کنید — Meridian certificate را می‌گیرد
4. رکورد را به **ابر نارنجی** تغییر دهید (Proxied)
5. SSL/TLS را **Full (Strict)** تنظیم کنید و **WebSockets** را روشن کنید (تنظیمات شبکه)

Deploy این مراحل را دوباره زمانی که تمام می‌شود چاپ می‌کند.

> **مهم:** certificate با یک HTTP challenge روی درگاه 80 صادر می‌شود. "Always Use HTTPS" Cloudflare این challenge را خراب می‌کند — آن را خاموش کنید یا `/.well-known/acme-challenge/*` را معاف کنید.

## لینک‌های اتصال

فهرست subscription و صفحه اتصال هر کلاینت هر مسیر را فهرست می‌کند:

| مسیر | مسیر | کی کمک می‌کند |
|------|-------|---------------|
| Reality | مستقیم به IP سرور | اولویت — سریع‌ترین |
| XHTTP | دامنه شما (از طریق Cloudflare زمانی که proxied است) | Reality مسدود یا محدود است |
| WSS | دامنه شما (از طریق Cloudflare زمانی که proxied است) | IP سرور مسدود است |
| Hysteria2 (اختیاری) | IP سرور شما روی UDP/443، با certificate دامنه | شبکه‌های دارای تلفات یا تأخیر زیاد |

برنامه‌هایی که JSON Xray یا subscription Mihomo را وارد می‌کنند به‌طور خودکار بین مسیرها تعویض می‌شوند زمانی که یکی کار می‌کند. برنامه‌های دیگر هر مسیر را به عنوان یک ورودی جداگانه نشان می‌دهند.

## Hysteria2 و Cloudflare

Hysteria2 روی UDP IP سرور شما dial می‌کند و تنها از دامنه برای certificate TLS استفاده می‌کند، بنابراین وقتی رکورد از طریق Cloudflare proxied می‌شود کار می‌کند (که نمی‌تواند آن را حمل کند). QUIC در برخی مناطق محدود است؛ `--hysteria2` را تنها فعال کنید اگر کاربران شما آن را نیاز دارند.

## اضافه کردن دامنه بعداً

دستور deploy خود را دوباره اجرا کنید `--domain` اضافه‌شده. لینک‌های Reality موجود کار می‌کنند. برای حذف دامنه بعداً، بدون `--domain` بازاستقرار کنید: مسیرهای XHTTP و WSS حذف می‌شوند و اشتراک‌ها به‌روزرسانی می‌شوند.
