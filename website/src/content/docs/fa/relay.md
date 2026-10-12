---
title: نودهای Relay
description: مسیریابی ترافیک از طریق سرور داخلی برای مقاومت در برابر مسدودسازی IP.
order: 6
section: guides
---

## آنچه relay حل می‌کند

زمانی که IP سرور خروجی مسدود می‌شود، کاربران دسترسی را از دست می‌دهند. relay به آن‌ها یک نقطه ورود خانگی می‌دهد که مسدود کردن آن سخت‌تر است:

```
کلاینت → Relay (IP خانگی) → سرور خروجی (خارج) → اینترنت
```

سانسور‌کنندگان ترافیک را به IP خانگی می‌بینند. relay TCP خام را به خروجی فوروارد می‌کند؛ رمزگذاری انتهایی بین کلاینت و خروجی است، بنابراین relay هرگز plaintext را نمی‌بیند.

## چگونه relays کار می‌کند

relay [Realm](https://github.com/zhboner/realm) را اجرا می‌کند، یک TCP forwarder کوچک و بدون کپی. روی درگاه 443 (قابل تنظیم) گوش می‌دهد و به listener Reality خروجی فوروارد می‌کند. Docker و پنلی روی relay اجرا نمی‌شود.

Relays ترافیک **Reality** را حمل می‌کند. XHTTP، WSS و Hysteria2 روی آدرس و دامنه خود خروجی می‌مانند و در subscription به عنوان جایگزین‌ها می‌مانند.

## استقرار relay

اولین استقرار خروجی، سپس relay آن را به آن اشاره کنید:

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10
```

Meridian relay را به توپولوژی اضافه می‌کند و اعمال می‌کند:
1. سرور relay را آماده می‌کند (بسته‌ها، BBR، سخت‌سازی SSH، firewall که تنها SSH و درگاه relay را باز می‌کند)
2. Realm را نصب می‌کند (نسخه‌پین، SHA256-verified) به عنوان سرویس systemd
3. relay را به listener Reality خروجی اشاره می‌کند
4. relay را در subscription هر کلاینت منتشر می‌کند

### پرچم‌ها

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--exit/-e EXIT` | (برای deploy مورد نیاز) | سرور خروجی IP یا نام |
| `--name NAME` | (خودکار) | نام دوستانه برای relay (مثلاً `ru-moscow`) |
| `--port/-p PORT` | 443 | درگاه گوش‌کننده روی سرور relay |
| `--sni HOST` | هدف خروجی | هدف پنهان‌سازی Reality برای این relay؛ برای استفاده از خروجی حذف کنید |
| `--user/-u USER` | root | کاربر SSH روی relay |
| `--ssh-port PORT` | 22 | درگاه SSH روی سرور relay |
| `--yes/-y` | | تأیید prompts را پرش کنید |

### مثال با تمام گزینه‌ها

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10 --name ru-moscow \
  --port 443 --sni dl.google.com --user ubuntu --ssh-port 2222 --yes
```

## چگونه کلاینت‌ها متصل می‌شوند

کلاینت‌های موجود relay را در بازخوانی subscription بعدی خود دریافت می‌کنند و صفحات اتصال آن را فهرست می‌کند. کلاینت‌های جدید آن را خودکار می‌گیرند:

```bash
meridian client add alice   # relay شامل است
```

## مدیریت relays

```bash
meridian relay list                       # تمام relays
meridian relay list --exit 198.51.100.10  # relays برای یک خروجی
```

hopهای relay را بررسی کنید — هر سرویس Realm در حال اجرا است، هر hop را به حالت بعدی می‌رساند و ورود عمومی اتصالات را می‌پذیرد — سپس ترافیک واقعی را از طریق کل مسیر آزمایش کنید:

```bash
meridian relay check 203.0.113.10
meridian test
```

برای بازنشستگی relay، زنجیرهای را از طریق آن بردارید. Meridian نشان می‌دهد که چه چیزی حذف خواهد شد، تأیید می‌خواهد و سرویس Realm، قوانین firewall و نقاط ورود منتشر شده را تمیز می‌کند؛ کلاینت‌های موجود relay را در بعدی تازه‌سازی اشتراک خود از دست می‌دهند:

```bash
meridian relay remove 203.0.113.10
```

## relays متعدد

چند relay را به یک خروجی الحاق کنید، برای نمونه در شهرها یا شبکه‌های مختلف:

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.10 --name ru-moscow
meridian relay add 203.0.113.11 --exit 198.51.100.10 --name ru-spb
```

کلاینت‌ها هر relay را در subscription خود دریافت می‌کنند. برای زنجیره‌های relay با بیش از یک hop، از `meridian setup` استفاده کنید.

## رفع مشکلات

### تضاد درگاه

سرویس دیگری از درگاه گوش‌کننده روی relay استفاده می‌کند. با `ss -tlnp sport = :443` بررسی کنید و آن را متوقف کنید یا درگاه دیگری با `--port 8443` انتخاب کنید.

### firewall مسدود می‌کند

مطمئن شوید درگاه relay در firewall cloud provider یا security group باز است، نه تنها در UFW.

### خروجی دسترس‌پذیر نیست

relay باید به درگاه Reality خروجی برسد. `meridian test` را برای بررسی مسیر انتهایی اجرا کنید و `meridian node check EXIT` را برای بررسی خروجی خود اجرا کنید.

### سرویس relay اجرا نمی‌شود

روی سرور relay، سرویس‌های Realm Meridian را فهرست کنید و لاگ خود را بخوانید:

```bash
systemctl list-units 'meridian-realm-*'
journalctl -u 'meridian-realm-*' --no-pager -n 20
```
