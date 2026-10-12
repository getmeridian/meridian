---
title: شروع کار
description: نصب Meridian و deploy سرور proxy اول خود در دو دقیقه.
order: 1
section: guides
---

یک دستور سرور جدید را به proxy سخت‌گیرانه و غیرقابل کشف تبدیل می‌کند و لینک قابل اشتراک می‌دهد. این راهنما شما را از صفر تا اتصال کار‌کننده در چند دقیقه می‌برد.

## نیازمندی‌ها

شما نیاز دارید:
- **VPS** درحال اجرای Debian یا Ubuntu که می‌توانید از طریق SSH با کلید، به‌عنوان root یا کاربر با sudo بدون گذرواژه دسترسی داشته باشید
- **ترمینال** روی کامپیوتر خود (macOS، Linux یا [WSL روی Windows](/docs/fa/wsl/))

## نصب CLI

```
curl -sSf https://getmeridian.org/install.sh | bash
```

این دستور `meridian` را با [uv](https://docs.astral.sh/uv/) (یا pipx به‌عنوان fallback) نصب می‌کند. گزینه‌های دیگر را در [Installation](/docs/fa/installation/) ببینید.

Meridian را روی ماشین خود (نه روی VPS) اجرا کنید: روی SSH متصل می‌شود، اعتبارنامه‌ها را روی دستگاه نگه می‌دارد و `meridian test` سرور را از خارج بررسی می‌کند.

> از Meridian 3.x می‌آیید؟ 4.0 ارتقا درجا **نیست**. ابتدا [Upgrading from 3.x](/docs/fa/installation/#upgrading-from-3x) را بخوانید.

## Deploy

```
meridian deploy 198.51.100.10
```

`meridian deploy` را بدون IP اجرا کنید و IP پرسش می‌کند. اگر شما به‌صورت root روی VPS وارد شده‌اید، `meridian deploy local` SSH را رد می‌کند.

## چه اتفاقی می‌افتد

1. **Hardens سرور** — SSH تنها کلیدی، fail2ban، firewall UFW که فقط آنچه deployment نیاز دارد باز می‌کند، congestion control BBR
2. **Control plane را نصب می‌کند** — پنل [Remnawave](https://remna.st/)، بعلاوه node اجرا‌شده توسط Xray، همه در Docker
3. **Camouflage target انتخاب می‌کند** — سرور `www.google.com`، `dl.google.com` و `www.cloudflare.com` را امتحان می‌کند و اولی را استفاده می‌کند (پاس `--sni` برای انتخاب خود)
4. **VLESS+Reality را روی پورت 443 پیکربندی می‌کند** پشت nginx، با گواهینامه Let's Encrypt برای IP سرور
5. **ترافیق واقعی تأیید می‌کند** — Meridian ترافیق را از طریق اشتراک تحویل‌شده، قبل از گفتن "Done" و رفتن به camouflage target بعدی اگر ترافیق عبور نکند، فشار می‌دهد
6. **صفحه اتصال برای اولین کلاینت منتشر می‌کند**

Deploy checkpointed است. اگر قطع شود، دستور یکسان را دوباره اجرا کنید و در جایی که متوقف شده بود به اتمام می‌رسد.

## اتصال

وقتی deploy تمام شود، این را چاپ می‌کند:

- **لینک صفحه اتصال** با QR کد — این را برای شخصی که متصل می‌شود ارسال کنید. صفحه دارای لینک‌های دانلود برنامه، QR کدها و دکمه‌های "Open in app" است.
- **URL اشتراک** — آن را در هر برنامه Xray/V2Ray paste کنید. برنامه‌ها آن را refresh می‌کنند، بنابراین سرورها و relays جدید خودکار نشان داده می‌شوند.

برنامه‌های توصیه‌شده:

| Platform | App |
|----------|-----|
| iOS | [v2RayTun](https://apps.apple.com/app/v2raytun/id6476628951) |
| Android | [v2rayNG](https://github.com/2dust/v2rayNG/releases/latest) |
| Windows | [v2rayN](https://github.com/2dust/v2rayN/releases/latest) |
| All platforms | [Hiddify](https://github.com/hiddify/hiddify-app/releases/latest) |

سرور را از ماشین خود در هر زمان بررسی کنید:

```
meridian test 198.51.100.10
```

## افزودن مردم بیشتر

```
meridian client add alice
```

هر شخص کلید خود، صفحه اتصال و URL اشتراک دارند. `meridian client list` همه را نشان می‌دهد و `meridian client disable alice` کسی را فوری قطع می‌کند. [Client management](/docs/fa/client-management/) را ببینید.

## محل اقامت چیزها

| What | Where |
|------|-------|
| Topology، دسترسی پنل و کلیدها | `~/.meridian/cluster.yml` روی ماشین شما |
| سرورهای SSH ذخیره شده | `~/.meridian/servers.json` روی ماشین شما |
| سرویس‌های Proxy | Docker، Xray، nginx روی VPS |

دستورات کلاینت با Remnawave panel API صحبت می‌کنند که در `cluster.yml` ضبط شده است؛ SSH نیاز ندارند. `~/.meridian/` را backup کنید — نحوه مدیریت سرورهای Meridian است.

## بیش از یک سرور

`meridian deploy` یک سرور را پوشش می‌دهد. برای چندین exits، relays از طریق سرور داخلی یا درب‌های routing با failover، از ماژول guided استفاده کنید:

```
meridian setup
```

شما می‌توانید یک deployment را یک بار در یک بار با `meridian node add` و `meridian relay deploy` رشد کنید. هر تغییر از طریق topology بررسی‌شده یکسان می‌رود؛ `meridian plan` نشان می‌دهد چه تغییر می‌کند و `meridian apply` آن را اعمال می‌کند.

## مراحل بعدی

- [Deploy guide](/docs/fa/deploy/) — هر گزینه deploy، camouflage targets، redeploys
- [Domain mode](/docs/fa/domain-mode/) — CDN Cloudflare fallback با دامنه خود اضافه کنید
- [Relay nodes](/docs/fa/relay/) — به کاربران نقطه‌ورود داخلی دهید
- [IP blocked?](/docs/fa/recovery/) — وقتی سرور block شود چه کنید
- [Troubleshooting](/docs/fa/troubleshooting/) — مسائل و راه‌حل‌های رایج
