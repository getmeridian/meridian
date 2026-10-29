---
title: نصب
description: نصب Meridian CLI در کامپیوتر محلی خود.
---

## نصب سریع

```
curl -sSf https://getmeridian.org/install.sh | bash
```

این اسکریپت:
1. اگر [uv](https://docs.astral.sh/uv/) موجود نباشد آن را نصب می‌کند، pipx یا pip را به‌عنوان جایگزین استفاده می‌کند. نصب‌کنندهٔ uv با نسخه pin شده و پیش از اجرا با SHA-256 بررسی می‌شود.
2. `meridian-vpn` را از PyPI نصب می‌کند (برای نصب یک نسخهٔ دقیق، `MERIDIAN_VERSION=4.0.0` را تنظیم کنید)
3. در صورت نیاز پوشه ابزار را به PATH شل اضافه می‌کند
4. فقط وقتی sudo بدون گذرواژه در دسترس باشد `/usr/local/bin/meridian` را ایجاد می‌کند

## نصب تأییدشده

هر [release در GitHub](https://github.com/getmeridian/meridian/releases) فایل `install.sh` و `SHA256SUMS` آن را پیوست می‌کند. برای بررسی اسکریپت پیش از اجرا، هر دو را از release دانلود کنید و تأیید کنید:

```
VERSION=4.0.0
curl -fLO https://github.com/getmeridian/meridian/releases/download/v$VERSION/install.sh
curl -fLO https://github.com/getmeridian/meridian/releases/download/v$VERSION/SHA256SUMS
sha256sum -c SHA256SUMS          # macOS: shasum -a 256 -c SHA256SUMS
less install.sh                  # optional: read it
MERIDIAN_VERSION=$VERSION bash install.sh
```

برای بررسی نسخهٔ موجود در getmeridian.org، خروجی `curl -sSf https://getmeridian.org/install.sh | sha256sum` را با `SHA256SUMS` همان release مقایسه کنید؛ تا وقتی اسکریپت پس از آن release تغییر نکرده باشد یکسان‌اند. وقتی جدیدترین release یک نسخهٔ بتا باشد، نسخهٔ موجود در وب‌سایت به‌طور پیش‌فرض همان بتا را نصب می‌کند و همان یک خط باعث می‌شود checksum آن با فایل پیوست release فرق کند.

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

Meridian 4 **ارتقای درجا نیست**. پنل 3x-ui با سرویس کنترل خود Meridian جایگزین شده است و لینک‌های سرور 3.x قابل انتقال نیستند: هر کاربر به لینک جدیدی نیاز دارد.

سرورهای 3.x شما همچنان کار می‌کنند. دو راه پیش رو دارید:

- **ادامهٔ مدیریت آن‌ها با 3.x.** نسخهٔ 3.x ابزار CLI را دوباره نصب کنید:
  ```
  uv tool install 'meridian-vpn<4'
  ```
  (یا `pipx install 'meridian-vpn<4'`). در Meridian 3.17.3 و بالاتر، اجرای `meridian update` روی شاخهٔ 3.x می‌ماند، مگر اینکه `--major` بدهید.
- **مهاجرت به Meridian 4.**
  1. یک سرور تازه با Meridian 4 مستقر کنید: `meridian deploy NEW_IP`
  2. صفحه‌های اتصال جدید را به کاربران بدهید (`meridian client add NAME` برای هر نفر)
  3. پس از اینکه همه منتقل شدند، سرور قدیمی را با `meridian teardown OLD_IP` پاک کنید — دستور `teardown` در Meridian 4 پنل 3x-ui را هم حذف می‌کند

Meridian 4 وضعیت خودش را در `~/.config/meridian/` نگه می‌دارد و وضعیت 3.x در `~/.meridian/` را هرگز نمی‌خواند و تغییر نمی‌دهد، پس هر دو نسخه می‌توانند روی یک ماشین کنار هم باشند؛ دستورهای fleet که فقط وضعیت 3.x را پیدا کنند، این گزینه‌ها را توضیح می‌دهند.

ناوگانی که با نسخه‌ای قدیمی‌تر از Meridian 4 deploy شده روی پنل Remnawave اجرا می‌شود. آن را می‌توان در جا و با حفظ همهٔ لینک‌ها منتقل کرد: ببینید [انتقال از Remnawave](/fa/deploy/#انتقال-از-remnawave).

این مستندات Meridian 4 را شرح می‌دهند. [مستندات Meridian 3.17.3](https://github.com/getmeridian/meridian/tree/v3.17.3/website/src/content/docs/fa) برای سرورهایی که روی 3.x نگه می‌دارید در GitHub باقی می‌مانند.

## نیازمندی‌ها

- **Python 3.11+** (uv آن را نصب و مدیریت می‌کند)
- **دسترسی SSH** به سرور شما، به‌عنوان root یا کاربر با sudo بدون گذرواژه. ورود با کلید بهترین است؛ اگر سرور فقط گذرواژه می‌پذیرد، `meridian deploy` و `meridian setup` پیشنهاد می‌کنند برای Meridian کلیدی بسازند و یک بار با همان گذرواژه نصبش کنند (ببینید [سرورهای فقط با گذرواژه](/fa/deploy/#سرورهای-فقط-با-گذرواژه))

کدهای QR ترمینال با بستهٔ موجود `segno` رسم می‌شوند؛ به وابستگی سیستمی نیاز ندارد.

## تأیید نصب

```
meridian --version
```
