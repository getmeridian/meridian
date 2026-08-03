---
title: شروع کار
description: نصب Meridian و استقرار اولین سرور پروکسی خود را در دو دقیقه انجام دهید.
order: 1
section: guides
---

## پیش‌نیازها

شما نیاز دارید:
- یک **VPS** که بر روی Debian یا Ubuntu اجرا می‌شود (دسترسی کلید SSH به صورت root)
- یک **ترمینال** در کامپیوتر محلی خود (macOS، Linux یا WSL)

## نصب CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

این کد دستور `meridian` را از طریق [uv](https://docs.astral.sh/uv/) (ترجیح داده‌شده) یا pipx نصب می‌کند.

می‌توانید Meridian را روی رایانه محلی خود یا مستقیماً روی VPS اجرا کنید. اجرای آن روی رایانه محلی توصیه می‌شود، چون می‌توانید `meridian test` را از بیرون سرور اجرا کنید و اطلاعات ورود روی دستگاه خودتان باقی می‌ماند.

## استقرار

برای توپولوژی مدیریت‌شده جدید V4 از `meridian setup` استفاده کنید؛ intent گراف معتبر سرورها، مسیرها و access userها می‌شود. `meridian deploy` در ادامه مسیر سریع legacy تک‌میزبان را نگه می‌دارد.

```
meridian deploy
```

جادوگر تعاملی برای IP سرور، کاربر SSH و هدف پنهان‌کاری (SNI) خود را می‌پرسد. مقادیر پیش‌فرض هوشمند برای همه چیز فراهم شده‌اند.

یا هر چیز را از قبل مشخص کنید:

```
meridian deploy 198.51.100.10 --sni www.microsoft.com
```

اگر مستقیماً روی VPS و با کاربر root هستید، SSH را کاملاً کنار بگذارید:

```
meridian deploy local
```

## چه اتفاقی می‌افتد

1. **Docker را نصب می‌کند** و Xray را زیر مدیریت پنل Remnawave مستقر می‌کند
2. **جفت کلید x25519 ایجاد می‌کند** — کلیدهای منحصر به فرد برای احراز هویت Reality
3. **سرور را سخت‌تر می‌کند** — دیوار آتش UFW، احراز هویت کلید SSH تنها، کنترل ترافیک BBR
4. **VLESS+Reality را پیکربندی می‌کند** در پورت 443 — شخصیت‌سازی سرور TLS واقعی
5. **XHTTP transport را فعال می‌کند** — لایه پنهان‌کاری اضافی، هدایت‌شده از طریق nginx
6. **در legacy یک PWA قابل اشتراک مستقر می‌کند**؛ V4 بدون صفحه جداگانه URL اشتراک رسمی را تحویل می‌دهد

## فایل‌ها و سرویس‌ها کجا هستند

Meridian از طریق SSH به VPS وصل می‌شود (یا با `deploy local` مستقیماً روی آن اجرا می‌شود). پس از استقرار، وضعیت به‌صورت محلی ذخیره می‌شود:

| مورد | محل |
|------|-----|
| توپولوژی، توکن پنل و کلیدها | `~/.meridian/cluster.yml` روی رایانه شما |
| پروفایل‌های SSH سرورها | `~/.meridian/servers.json` روی رایانه شما |
| سرویس‌های پروکسی | Docker، Xray و nginx روی VPS |

در legacy، `meridian client add alice` کاربر پنل می‌سازد؛ در V4 کاربر را به access intent اضافه و plan را apply می‌کند. بیشتر عملیات فقط API Remnawave را به‌کار می‌برند؛ SSH تنها برای صفحه‌های PWA قدیمی لازم است. در استقرار چندسروری `--server NAME` را فقط برای فرمان‌های میزبان به‌کار ببرید.

## اتصال

Legacy deploy یک URL اشتراک و پس از بارگذاری موفق صفحه، URL PWA می‌دهد. V4 setup همیشه URL رسمی اشتراک را تحویل می‌دهد و ادعای وجود صفحه PWA قدیمی را ندارد.

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

هر کلاینت دسترسی و اشتراک مستقل دارد. در V4، `client list` فقط access userهای اعلام‌شده را نشان می‌دهد. `client remove alice` در legacy پشتیبانی می‌شود؛ V4 هنوز بازنشستگی امن کاربر مدیریت‌شده را نمی‌پذیرد، پس برای لغو فوری و موقت از `client disable alice` استفاده کنید (تا `meridian apply` بعدی).

## مدیریت سرورها

وقتی چندین VPS را مدیریت می‌کنید:

```
meridian server list                # مشاهده تمام سرورها
meridian server add 198.51.100.11  # افزودن سرور موجود
meridian server remove finland     # حذف از رجیستری
```

فلگ `--server` در فرمان‌هایی که با سرور کار می‌کنند، یک میزبان مشخص را هدف می‌گیرد؛ برای مثال `meridian doctor --server finland`. فرمان‌های `client` مستقیماً با پنل کلاستر کار می‌کنند و این فلگ را نمی‌پذیرند.

## گردش‌کار اعلانی

V4 مدل بازبینی‌شده فلیت را به‌عنوان `topology_intent` در `cluster.yml` ذخیره می‌کند. آن را با `meridian setup` بسازید یا ویرایش کنید؛ intent شامل control plane، exitها، زنجیره‌های مسیریابی، مسیرهای protocol و access userها است. با `plan` تغییرات resourceهای کامپایل‌شده را پیش‌نمایش دهید و با `apply` آن‌ها را همگرا کنید.

```
meridian plan                      # پیش‌نمایش تغییرات resourceهای کامپایل‌شده
meridian apply --yes               # همگرا کردن topology بازبینی‌شده
```

`meridian plan` در حالت همگرا با کد `0` و هنگامی که تغییری در انتظار است با کد `2` خارج می‌شود، پس می‌توانید CI را بر اساس آن کنترل کنید. برای بررسی plan نوع‌دار از `meridian plan --json` و برای دریافت نتیجه نهایی اجرا در process یا UI از `meridian apply --json --yes` استفاده کنید. هر دو از envelope نوع‌دار `meridian.output/v1` استفاده می‌کنند. گزینه‌های کامل را در [مرجع CLI](/docs/fa/cli-reference/#meridian-plan) ببینید.

کلاسترهای legacy همچنان از همگرایی اختیاری `desired_nodes`، `desired_relays` و `desired_clients` پشتیبانی می‌کنند. این فیلدها مرجع V4 نیستند. برای نمونه حالت توضیح‌داده‌شده، [`cluster.example.yml`](https://github.com/getmeridian/meridian/blob/v4/cluster.example.yml) را ببینید.

## مراحل بعدی

- [راهنمای استقرار](/docs/fa/deploy/) — راهنمای استقرار کامل با تمام گزینه‌ها
- [گره‌های relay](/docs/fa/relay/) — مسیریابی از طریق IP داخلی برای تاب‌آوری در صورت مسدود شدن IP
- [حالت دامنه](/docs/fa/domain-mode/) — افزودن fallback CDN از طریق Cloudflare
- [IP مسدود شده است؟](/docs/fa/recovery/) — بازیابی گام به گام در صورت مسدود شدن سرور
- [حل مشکلات](/docs/fa/troubleshooting/) — مشکلات معمول و راه‌حل‌ها
