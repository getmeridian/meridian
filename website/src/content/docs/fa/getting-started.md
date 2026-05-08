---
title: شروع کار
description: نصب Meridian و استقرار اولین سرور پروکسی خود را در دو دقیقه انجام دهید.
order: 1
section: guides
---

## پیش‌نیازها

شما نیاز دارید:
- یک **VPS** که بر روی Debian یا Ubuntu اجرا می‌شود (دسترسی کلید SSH به صورت root)
- یک **ترمینال** در کامپیوتر محلی خود (macOS، Linux یا [WSL در Windows](/docs/fa/wsl/))

## نصب CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

این کد دستور `meridian` را از طریق [uv](https://docs.astral.sh/uv/) (ترجیح داده‌شده) یا pipx نصب می‌کند.

## استقرار

```
meridian deploy
```

جادوگر تعاملی برای IP سرور، کاربر SSH و هدف پنهان‌کاری (SNI) خود را می‌پرسد. مقادیر پیش‌فرض هوشمند برای همه چیز فراهم شده‌اند.

یا هر چیز را از قبل مشخص کنید:

```
meridian deploy 1.2.3.4 --sni www.microsoft.com
```

## چه اتفاقی می‌افتد

1. **Docker را نصب می‌کند** و Xray را از طریق پنل مدیریت 3x-ui مستقر می‌کند
2. **جفت کلید x25519 ایجاد می‌کند** — کلیدهای منحصر به فرد برای احراز هویت Reality
3. **سرور را سخت‌تر می‌کند** — دیوار آتش UFW، احراز هویت کلید SSH تنها، کنترل ترافیک BBR
4. **VLESS+Reality را پیکربندی می‌کند** در پورت 443 — شخصیت‌سازی سرور TLS واقعی
5. **XHTTP transport را فعال می‌کند** — لایه پنهان‌کاری اضافی، هدایت‌شده از طریق nginx
6. **کدهای QR و صفحه اتصال HTML را خروجی می‌دهد** و آن را ذخیره می‌کند

## اتصال

دستور deploy خروجی می‌دهد:
- یک **کد QR** که می‌توانید آن را با تلفن خود اسکن کنید
- یک **فایل HTML** با پیوندهای اتصال برای اشتراک با خانواده
- یک **URL قابل اشتراک** (اگر صفحات میزبانی‌شده توسط سرور فعال باشند)

یکی از این برنامه‌ها را نصب کنید، سپس کد QR را اسکن کنید یا روی "باز کردن در برنامه" ضربه بزنید:

| پلتفرم | برنامه |
|----------|-----|
| iOS | [v2RayTun](https://apps.apple.com/app/v2raytun/id6476628951) |
| Android | [v2rayNG](https://github.com/2dust/v2rayNG/releases/latest) |
| Windows | [v2rayN](https://github.com/2dust/v2rayN/releases/latest) |
| تمام پلتفرم‌ها | [Hiddify](https://github.com/hiddify/hiddify-app/releases/latest) |

## افزودن کاربران بیشتر

```
meridian client add alice
```

هر کلاینت کلید و صفحه اتصال خود را دارد. با `meridian client list` کلاینت‌ها را فهرست کنید، با `meridian client remove alice` لغو کنید.

## مدیریت سرورها

وقتی چندین VPS را مدیریت می‌کنید:

```
meridian server list                # مشاهده تمام سرورها
meridian server add 5.6.7.8        # افزودن سرور موجود
meridian server remove finland     # حذف از رجیستری
```

فلگ `--server` به شما اجازه می‌دهد سرور خاصی را برای هر دستور مشخص کنید: `meridian client add alice --server finland`.

## مراحل بعدی

- [راهنمای استقرار](/docs/fa/deploy/) — راهنمای استقرار کامل با تمام گزینه‌ها
- [گره‌های relay](/docs/fa/relay/) — مسیریابی از طریق IP داخلی برای تاب‌آوری در صورت مسدود شدن IP
- [حالت دامنه](/docs/fa/domain-mode/) — افزودن fallback CDN از طریق Cloudflare
- [IP مسدود شده است؟](/docs/fa/recovery/) — بازیابی گام به گام در صورت مسدود شدن سرور
- [حل مشکلات](/docs/fa/troubleshooting/) — مشکلات معمول و راه‌حل‌ها
