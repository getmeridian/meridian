---
title: مدیریت کلاینت‌ها
description: افزودن کاربران، اشتراک‌گذاری اطلاعات اتصال و مدیریت کلیدهای دسترسی.
order: 5
section: guides
---

## افزودن کلاینت

```
meridian client add alice
```

این دستور برای `alice` دسترسی یکتا می‌سازد و URL اشتراک و کد QR را نشان می‌دهد. در استقرار legacy، Meridian همچنین تلاش می‌کند یک صفحه PWA جداگانه روی میزبان پنل مستقر کند. در V4، کاربر به `topology_intent.access.users` افزوده می‌شود، intent تازه apply می‌شود و فقط اشتراک رسمی Remnawave تحویل داده می‌شود؛ صفحه PWA جداگانه ساخته نمی‌شود.

چندین نام را ارسال کنید تا چندین کلاینت به‌طور همزمان اضافه شوند:

```
meridian client add alice bob charlie
```

در legacy خطاها برای هر کلاینت جداگانه گزارش می‌شوند؛ کلاینت‌های موفق حفظ می‌شوند و batch نیمه‌موفق یا شکست بارگذاری صفحه با کد `3` پایان می‌یابد. در V4 همه نام‌ها ابتدا به access intent افزوده و سپس plan حاصل apply می‌شود.

### آنچه گیرنده می‌بیند

در legacy، صفحه اتصالی که با موفقیت مستقر شده شامل این موارد است:
- دستورالعمل‌های گام‌به‌گام برای نصب برنامه VPN (v2RayTun، v2rayNG، Hiddify یا v2rayN)
- کدهای QR برای هر پروتکل اتصال
- پیوندهای عمیق "باز در برنامه" تک‌ضربتی
- وضعیت اتصال و آمار استفاده

اگر صفحه در دسترس نیست یا استقرار V4 است، URL اشتراک یا کد QR آن را ارسال کنید؛ اشتراک رسمی همیشه منبع معتبر تنظیمات اتصال است.

## نمایش اطلاعات اتصال

برای نمایش مجدد اطلاعات اتصال برای یک کلاینت موجود در هر زمان:

```
meridian client show alice
```

این دستور بدون ساخت کلید جدید، کد QR و URL اشتراک را دوباره نشان می‌دهد. لینک PWA فقط وقتی چاپ می‌شود که شواهد مثبت استقرار روی دیسک وجود داشته باشد. برای تعمیر صریح صفحه legacy گم‌شده اجرا کنید:

```
meridian client show alice --repair-page
```

از `show` استفاده کنید وقتی:
- نیاز دارید صفحه اتصال را با کسی دوباره اشتراک‌گذاری کنید
- کد QR یا پیوند صفحه اصلی را گم کرده‌اید
- می‌خواهید اشتراک رسمی را دوباره دریافت کنید

در V4، `show` و `list` فقط کاربران اعلام‌شده در access intent را نمایش می‌دهند؛ کاربری که مستقیم در پنل ساخته شده خودکار تحت مالکیت Meridian قرار نمی‌گیرد. `--repair-page` فقط برای صفحه legacy است.

## فهرست کلاینت‌ها

```
meridian client list
```

وضعیت و آمار کلاینت‌ها را نشان می‌دهد. در V4 فهرست به کاربران access intent محدود است.

## حذف کلاینت

```
meridian client remove alice
```

در legacy کاربر پنل و صفحه محلی تأییدشده او را حذف می‌کند. در V4 این دستور عمداً رد می‌شود، چون بازنشستگی امن کاربر access مدیریت‌شده هنوز پیاده‌سازی نشده است. برای لغو فوری و موقت از `client disable` استفاده کنید و کاربر را مستقیم از پنل حذف نکنید.

## تعلیق کلاینت

```
meridian client disable alice
```

به‌صورت موقت اتصال کلاینت را مسدود می‌کند و کلید و اشتراک را نگه می‌دارد. در V4 فقط کاربران اعلام‌شده پذیرفته می‌شوند و disable فقط تا `meridian apply` بعدی دوام دارد؛ apply دسترسی اعلام‌شده در intent را دوباره active می‌کند.

برای فعال‌سازی مجدد: `meridian client enable alice`

## فعال‌سازی مجدد کلاینت

```
meridian client enable alice
```

یک کلاینت معلق پیشین را از سرکشی درمی‌آورد. آن‌ها می‌توانند بلافاصله با کلیدهای موجود و URL اشتراک خود متصل شوند.

## محل ذخیره اطلاعات اعتبار

Meridian توپولوژی فلیت را به‌صورت محلی در `~/.meridian/cluster.yml` ذخیره می‌کند — URL پنل، توکن API، اعتبارات مدیر، نودها و relay‌ها. وضعیت کلاینت (کاربران، UUID‌ها، ترافیق) در پایگاه داده PostgreSQL پنل Remnawave زندگی می‌کند، که منبع حقیقت است.

```
~/.meridian/cluster.yml                 # توپولوژی فلیت + دسترسی پنل
```

دستورهای کلاینت با توکن ذخیره‌شده به REST API پنل وصل می‌شوند. عملیات معمول SSH نمی‌خواهد؛ استثنا، استقرار، بررسی، تعمیر و حذف صفحه PWA قدیمی روی میزبان پنل است.

پس از از دست رفتن وضعیت محلی، `meridian fleet recover --legacy --panel-url https://HOST/SECRET_PATH` می‌تواند یک profile مشترک legacy بدون ابهام را import کند. V4 topology intent از پنل قابل بازسازی نیست؛ برای V4 از backup استفاده کنید یا دوباره `meridian setup` را اجرا کنید.

## پنل وب

Meridian [Remnawave](https://remna.st/) پنل مدیریت را برای نظارت بر ترافیک، مدیریت کاربر و پیکربندی پیشرفته مستقر می‌کند. توسط nginx در یک مسیر HTTPS تصادفی پروکسی معکوس می‌شود — تونل SSH نیازی نیست. URL و اعتبارات مدیر را در `~/.meridian/cluster.yml` پیدا کنید:

```
grep -A6 "^panel:" ~/.meridian/cluster.yml
```

فیلدهای مرتبط:

```yaml
panel:
  url: https://<your-server-ip>/<secret_path>/
  admin_user: admin
  admin_pass: <generated>
  api_token: <JWT used by Meridian CLI>
  secret_path: <random>
  sub_path: <random>   # subscription page path
```

`url` را در مرورگر باز کنید و با `admin_user` / `admin_pass` وارد شوید.

ویرایش منابع مدیریت‌شده در پنل به‌صورت drift ظاهر می‌شود. در legacy فهرست مطلوب از `desired_clients` می‌آید؛ در V4 مرجع معتبر `topology_intent.access.users` است. پیش از `apply` همیشه plan، به‌ویژه حذف منابع اضافه، را بازبینی کنید.

## نحوه کار

هر کلاینت Meridian یک کاربر Remnawave با UUID است. در legacy کاربر به Internal Squad مشترک متصل می‌شود؛ در V4 دسترسی از access intent کامپایل و با resource driver مدیریت می‌شود. URL اشتراک Remnawave فقط endpointهای مجاز آن کاربر را دارد و منبع رسمی برنامه‌های کلاینت است.

برنامه‌های کلاینت (v2rayNG، Streisand، Hiddify، sing-box) URL اشتراک را به عنوان یک منبع حقیقت واحد بدون می‌کند: تازه‌سازی آن inbound‌های جدید را زمانی که نود خروجی جدید استقرار دهید، relay اضافه کنید یا کلیدهای Reality چرخش کنید کشید می‌کند.

## دسترسی اعلانی

در استقرار legacy می‌توانید `desired_clients` را در `~/.meridian/cluster.yml` تعریف کنید:

```yaml
desired_clients:
  - alice
  - bob
  - charlie
```

سپس `meridian plan` آن را با کاربران پنل مقایسه و `meridian apply` همگرا می‌کند. در V4، معادل آن `topology_intent.access.users` است: کاربر را با `meridian client add` اضافه کنید یا intent را در `meridian setup` بازبینی کنید؛ `desired_clients` مرجع V4 نیست.
