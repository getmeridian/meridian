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
1. اگر [uv](https://docs.astral.sh/uv/) موجود نباشد آن را نصب می‌کند، pipx یا pip را به‌عنوان جایگزین استفاده می‌کند
2. `meridian-vpn` را از PyPI نصب می‌کند
3. در صورت نیاز پوشه ابزار را به PATH شل اضافه می‌کند
4. فقط وقتی sudo بدون گذرواژه در دسترس باشد `/usr/local/bin/meridian` را ایجاد می‌کند

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

Meridian پیش از دستورهای تعاملی PyPI را بررسی می‌کند و نوتیس می‌دهد وقتی نسخه جدیدتری موجود است. هیچ به‌روزرسانی خودکار نصب نمی‌کند. `meridian update` با هر کدام از package manager‌هایی که ابزار را نصب کردند ارتقا می‌دهد و تأیید می‌کند که دستور فعال `meridian` نسخه جدید را نشان می‌دهد. برای automation از `MERIDIAN_DISABLE_UPDATE_CHECK=1` استفاده کنید.

به‌روزرسانی CLI هیچ تغییری بر روی سرورهای شما ایجاد نمی‌کند. اگر سرور کار می‌کند، عجله‌ای نیست؛ [یادداشت انتشار](https://github.com/getmeridian/meridian/releases) را بخوانید و سپس وقتی آماده باشید دستور `meridian deploy` (یا `meridian apply`) را دوباره اجرا کنید.

## ارتقا از 3.x

Meridian 4.0 **نیست نصب درجا**. جای پنل 3x-ui را Remnawave می‌گیرد و پیوندهای سرور 3.x نمی‌تواند منتقل شود: هر کاربر به پیوند جدید نیاز دارد.

سرورهای 3.x شما کار می‌کنند. دو گزینه دارید:

- **ادامه مدیریت آن‌ها با 3.x.** CLI 3.x را دوباره نصب کنید:
  ```
  uv tool install 'meridian-vpn<4'
  ```
  (یا `pipx install 'meridian-vpn<4'`). Meridian 3.17.3 و بعدی بر روی 3.x می‌مانند وقتی `meridian update` را اجرا می‌کنید، مگر اینکه `--major` را بگذارید.
- **رفتن به 4.0.**
  1. سرور جدید را با 4.0 deploy کنید: `meridian deploy NEW_IP`
  2. برای هر شخص صفحات اتصال جدید انتشار دهید (`meridian client add NAME`)
  3. بعدما که همه تغییر کردند، سرور قدیمی را پاک کنید با `meridian teardown OLD_IP` — teardown 4.0 همچنین 3x-ui را حذف می‌کند

Meridian 4.0 وضعیت 3.x را در `~/.meridian/` نمی‌خواند و تغییر نمی‌دهد؛ دستورهای fleet که آن را پیدا می‌کنند این گزینه‌ها را توضیح می‌دهند.

## نیازمندی‌ها

- **Python 3.11+** (uv آن را نصب و مدیریت می‌کند)
- **دسترسی کلید SSH** به سرور شما، به‌عنوان root یا کاربر با sudo بدون گذرواژه

کدهای QR ترمینال با بستهٔ موجود `segno` رسم می‌شوند؛ به وابستگی سیستمی نیاز ندارد.

## تأیید نصب

```
meridian --version
```
