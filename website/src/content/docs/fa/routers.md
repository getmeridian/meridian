---
title: استفاده از مریدیان در روتر
description: یک روتر خانگی را به سرور مریدیان شما متصل کنید با podkop بر روی OpenWrt یا XKeen بر روی Keenetic، تا بدون نیاز به اپلیکیشن هر دستگاهی پوشش داده شود.
order: 6.7
section: guides
---

یک روتری که client proxy را اجرا می‌کند کل دستگاه‌های خانه را پوشش می‌دهد، شامل تلوزیون‌ها و کنسول‌هایی که نمی‌توانند اپلیکیشن اجرا کنند. این راهنما دو setup مشترک را نشان می‌دهد: **podkop** بر روی OpenWrt و **XKeen** بر روی Keenetic. هر دو پروژه‌های جامعه‌ای هستند، بنابراین قبل از شروع مراحل فعلی‌شان را بررسی کنید.

## دریافت لینک

روتر را برای خود کلاینت بدهید، تا بتوانید آن را بدون قطع کردن دیگران غیرفعال کنید:

```
meridian client add home-router
meridian client show home-router
```

`client show` صفحه اتصال و URL اشتراک را چاپ می‌کند. صفحه اتصال را باز کنید: هر مسیر (Reality، XHTTP، WSS و هر relay) دکمه **Copy link** دارد که یک لینک `vless://` کپی می‌کند.

کدام یک را paste کنید:

- **Reality اول.** این مسیر اولیه است و تنهایی است که هر client روتر زیر می‌تواند اجرا کند.
- **XHTTP** نیاز به یک client مبتنی‌بر Xray دارد که جدید است و نزدیک نسخهٔ Xray سرور شماست. sing-box استاندارد و بنابراین podkop هیچ حمل XHTTP ندارد؛ XKeen Xray را اجرا می‌کند و می‌تواند آن را استفاده کند.
- **لینک‌های relay** Reality links هستند هم. اگر روتر شما در روسیه است و IP خروجی مسدود است، لینک relay را paste کنید.
- **Hysteria2** UDP است و از طریق relays عبور نمی‌کند. تنها در صورتی استفاده کنید که client روتر شما آن را پشتیبانی کند.

یک لینک paste‌شده یک snapshot است. URL‌های اشتراک خودکار بروزرسانی می‌شوند، اما podkop FAQ می‌گوید هیچ پشتیبانی اشتراک داخلی ندارد، بنابراین زمانی که بعداً exit یا relay اضافه می‌کنید، لینک جدید را paste کنید.

### نسخه‌های Client

سرورهای Xray اخیر، sing-box (podkop) و Mihomo clients را به صورت پیش‌فرض رد می‌کنند، زیرا آن clients یک شماره نسخهٔ قدیمی‌تر را اعلام می‌کنند. خروجی‌های مریدیان بطور صریح هر client که Reality را صحبت می‌کند را می‌پذیرند، بنابراین این شما را تحت تأثیر قرار نمی‌دهد. اگر یک روتر هرگز متصل نشود در حالی که `meridian test` از لپ‌تاپ شما پاس کند، core proxy روتر را بروزرسانی کنید. در Keenetic، `xkeen -ux` XKeen's Xray را بروزرسانی می‌کند.

## OpenWrt با podkop

[podkop](https://podkop.net/) تنها دامنه‌ها و لیست‌هایی که انتخاب می‌کنید را از طریق proxy مسیریابی می‌کند و بقیه را مستقیم ارسال می‌کند. این برای کاربران روسی مناسب است: سایت‌های داخلی روی اتصال عادی بدون قوانین اضافی می‌مانند.

**الزامات** (از [podkop docs](https://podkop.net/docs/install)): OpenWrt 24.10 (25.12 از طریق apk کار می‌کند)، و حدود 20 MB فضای flash خالی. اگر sing-box هنوز نصب نشده باشد، podkop به 40 MB خالی در `/overlay` نیاز دارد. زمانی که فضا کم است، podkop FAQ `sing-box-tiny` را پیشنهاد می‌کند.

**نصب** از shell SSH روتر:

```
sh <(wget -O - https://raw.githubusercontent.com/itdoginfo/podkop/refs/heads/main/install.sh)
```

اگر GitHub از روتر قابل دسترس نیست، podkop در صفحهٔ نصب mirror bootstrap را مستند می‌کند.

**پیکربندی** در LuCI، در صفحهٔ Podkop:

1. در بخش `Main`، **Connection Type** را روی `Proxy` تنظیم کنید.
2. **Configuration Type** را روی `Connection URL` تنظیم کنید و لینک `vless://` Reality را paste کنید.
3. لیست‌های جامعه‌ای را که می‌خواهید از طریق مریدیان ارسال شوند انتخاب کنید یا دامنه‌های خود را اضافه کنید.
4. **Save & Apply**، سپس تب Diagnostics را باز کنید. آن را از LAN اجرا کنید، نه از طریق بازگردان‌دهیٔ درگاه راه دور.

برای failover بین لینک Reality مستقیم و لینک relay، **URLTest** را انتخاب کنید و هر دو لینک را اضافه کنید. سپس sing-box آن‌ها را probe می‌کند و یکی را که کار می‌کند استفاده می‌کند.

## Keenetic با XKeen

[XKeen](https://github.com/jameszeroX/XKeen) Xray را بر روی روترهای Keenetic از طریق Entware نصب و مدیریت می‌کند. انشعاب فعال‌تر نگهداری شده همچنین Mihomo را پشتیبانی می‌کند.

**الزامات:** یک درایو USB که به عنوان EXT4 فرمت شده است با Entware، و اجزای ماژول kernel Netfilter روتر فعال شده. راهنمای XKeen یک partition swap روی درایو و خاموش کردن فشردهٔ RAM را در تنظیمات سیستم روتر توصیه می‌کند.

**نصب** از shell Entware SSH به عنوان root:

```
opkg update && opkg upgrade && opkg install curl tar && cd /tmp
sh -c "$(curl -sSL https://raw.githubusercontent.com/jameszeroX/XKeen/main/install.sh)"
```

**پیکربندی** فایل‌های Xray در `/opt/etc/xray/configs/`. مقادیر لینک Reality را در `04_outbounds.json` قرار دهید:

```json
{
  "outbounds": [
    {
      "tag": "vless-reality",
      "protocol": "vless",
      "settings": {
        "vnext": [{
          "address": "198.51.100.10",
          "port": 443,
          "users": [{ "id": "UUID", "flow": "xtls-rprx-vision", "encryption": "none" }]
        }]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "fingerprint": "chrome",
          "serverName": "www.google.com",
          "publicKey": "PBK",
          "shortId": "SID"
        }
      }
    }
  ]
}
```

لینک هر مقدار را حمل می‌کند: بخش قبل از `@` UUID است، سپس آدرس و درگاه. در query، `pbk` است `publicKey`، `sid` است `shortId`، `sni` است `serverName`، و `fp` است `fingerprint`. `flow` را به عنوان `xtls-rprx-vision` نگه دارید وقتی لینک آن را دارد.

مسیریابی را در `05_routing.json` تنظیم کنید. راهنمای XKeen دو راه را توصیف می‌کند: تنها دامنه‌ها و لیست‌های IP انتخاب‌شده را از طریق proxy ارسال کنید، یا `.ru`، `.su` و `.рф` مستقیم ارسال کنید و همه چیز دیگر را از طریق آن. سپس بررسی و شروع کنید:

```
xkeen -xtest
xkeen -start
```

## سایت‌های داخلی مستقیم می‌مانند

به طور پیش‌فرض خروجی ترافیک را به دامنه‌های روسی و محدوده‌های IP روسی (`--geo-block`) رد می‌کند. روتری که همه چیز را از طریق تونل ارسال می‌کند سایت‌های روسی را می‌شکند. آن‌ها را مستقیم از روتر ارسال کنید؛ podkop این کار را به طور طراحی انجام می‌دهد، و با XKeen شما دوم روتینگ روی را انتخاب می‌کنید. [Split routing](/docs/fa/split-routing/) لایه‌ها را توضیح می‌دهد.

## Pitfalls

- **Flash، RAM و CPU.** podkop برای sing-box در flash فضا نیاز دارد، و روتری که از قبل پر است نصب ناموفق خواهد شد. یک CPU ضعیف throughput را بسیار کمتر از سرعت خط شما محدود می‌کند، بنابراین قبل از اینکه روی آن تکیه کنید از طریق روتر اندازه‌گیری کنید.
- **ساعت اشتباه.** بسیاری از روترها با زمان غلط boot می‌شوند تا زمانی که NTP پاسخ دهد. بررسی‌های گواهی‌نامهٔ TLS سپس برای صفحهٔ اشتراک، XHTTP، WSS و دانلودهای بسته ناموفق می‌شوند. podkop FAQ به شما می‌گوید تاریخ روتر را ابتدا بررسی کنید زمانی که نصب آن با خطای گواهی‌نامه ناموفق می‌شود.
- **DNS leaks.** podkop بر اساس نام دامنه مسیریابی می‌کند، بنابراین دستگاه‌ها باید روتر را به عنوان سرور DNS خود استفاده کنند و مرورگرها نباید آن را با DNS محفوظ خود bypass کنند. یک دستگاه بر روی سرور DNS دیگر به آدرس واقعی resolve می‌شود و تونل را رد می‌کند. به گفتهٔ podkop docs، این رایج‌ترین دلیل "بر روی یک دستگاه کار می‌کند، بر روی دیگری نه" است.
- **VPN apps بر روی دستگاه.** یک تلفن که app VPN خود را اجرا می‌کند مسیریابی روتر را رد می‌کند.
- **یک client غیرفعال.** `meridian client disable home-router` روتر را تا بعدی اعمال که آن را دوباره روشن می‌کند قطع می‌کند.

## تأیید

از یک دستگاه پشت روتر، یک صفحهٔ بررسی IP را باز کنید: باید آدرس خروجی شما را نشان دهد. از ماشین خود، `meridian test` اشتراک انجام‌شدهٔ انتهایی را بررسی می‌کند، و `meridian check` سمت سرور را تأیید می‌کند. اگر نتایج ناتوافق داشته باشند [Troubleshooting](/docs/fa/troubleshooting/) را ببینید.
