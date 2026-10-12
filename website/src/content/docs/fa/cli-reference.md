---
title: مرجع CLI
description: مرجع کامل برای تمام دستورات و پرچم‌های CLI مریدیان.
order: 10
section: reference
---

## دستورات

### meridian deploy

یک سرور استقرار دهید و لینک اتصالی قابل اشتراک دریافت کنید. سرور هم control plane (پنل Remnawave) و هم exit می‌شود. Deploy توپولوژی بررسی‌شده‌ای مشابه `meridian setup` ایجاد می‌کند، آن را با checkpoints اعمال می‌کند، ترافیق واقعی را از طریق اشتراک تحویل‌داده‌شده تأیید می‌کند، و صفحه اتصال و URL اشتراک را چاپ می‌کند. دوباره اجرای دستور یکسان یک استقرار قطع‌شده را از سر می‌گیرد یا بازاستقرار می‌کند، مسیرها، کلیدها و کاربران را نگه می‌دارد.

```
meridian deploy [IP|local] [flags]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--sni HOST` | (خودکار) | هدف camouflage. بدون آن، سرور `www.google.com`، `dl.google.com`، `www.cloudflare.com` را امتحان می‌کند و از اول‌ی آن‌های که ترافیق حمل می‌کنند استفاده می‌کند؛ بازاستقرار هدف ذخیره‌شده را نگه می‌دارد. `--sni` صریح هرگز جابه‌جا نمی‌شود |
| `--domain DOMAIN` | (هیچ) | دامنه شما برای XHTTP و WSS (CDN Cloudflare fallback). پنل و صفحات اتصال روی گواهینامه IP می‌مانند |
| `--hysteria2` | خاموش | همچنین Hysteria2 را روی UDP/443 ارائه دهید. `--domain` نیاز دارد؛ QUIC در برخی از مناطق throttled است |
| `--client-name NAME` | default | نام کلاینت اول |
| `--display-name NAME` | (هیچ) | برچسب برای صفحات اتصال |
| `--icon EMOJI_OR_URL` | (هیچ) | آیکون صفحه — emoji یا URL تصویر |
| `--color PALETTE` | ocean | تم رنگ صفحه (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | کاربر SSH (non-root نیاز به sudo بدون رمز عبور دارد) |
| `--ssh-port PORT` | 22 | پورت SSH، اگر 22 نیست |
| `--server NAME` | | سرور ذخیره‌شده برای استقرار (نام یا IP) |
| `--harden / --no-harden` | فعال | ورود تنهای کلید SSH، fail2ban و firewall UFW. `--no-harden` برای سروری که سرویس‌های دیگری روی آن دارند است |
| `--warp / --no-warp` | خاموش | ترافیک خروجی را از طریق Cloudflare WARP ارسال کنید |
| `--geo-block / --no-geo-block` | فعال | دامنه‌های روسی و IP‌های روسی را block کنید (`geosite:category-ru` + `geoip:ru`) |
| `--yes` | | پرسش تأیید را رد کنید |
| `--json` | | نتیجه deploy نهایی را به عنوان پاکت `meridian.output/v1` ارسال کنید |
| `--events=jsonl` | | رویدادهای تعریف‌شده پیشرفت را به عنوان JSONL روی stderr جریان دهید |
| `--request FILE` | | پیوند JSON `deploy-request` را از فایل بخوانید؛ از `-` برای stdin استفاده کنید |
| `--dry-run` | | deploy را بدون SSH یا تغییرات محلی compile و پیش‌نمایش کنید |

بدون IP، deploy برای یکی درخواست می‌کند. `local` روی ماشینی که اجرا می‌کنید استقرار می‌دهد. Deploy فقط سروری را بازاستقرار می‌کند که قبلاً exit در فلیت شما است؛ سروری دیگر با `meridian node add` اضافه کنید. تغییرات اضافی هستند: حذف چیزی که قبلاً استقرار یافته (مثل drop `--domain`) رد می‌شود.

**جریان Machine/UI**: `meridian api workflow deploy --json` قرارداد wizard renderable برمی‌گرداند. UI آن فیلدها را جمع‌آوری می‌کند، آن‌ها را برای `deploy-request` تأیید می‌کند، سپس `meridian deploy --request deploy.json --json --events=jsonl` را اجرا می‌کند. Machine deploys غیرتعاملی هستند: درخواست باید بعد از تأیید کاربر `yes: true` داشته باشد. `--dry-run --json` `deploy-plan` را تحت `data` برمی‌گرداند، بنابراین UI می‌تواند حالت، پورت‌ها و مسیرهای تولید‌شده را پیش‌نمایش کند قبل از باز کردن SSH.

### meridian setup

چندین سرور را با wizard راهنمایی resumable پیکربندی کنید: exits، transparent Realm relay chains، routing gateways با failover pools و access users.

```
meridian setup [--intent FILE|-] [--restart] [--yes]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--intent FILE` | (هیچ) | از بررسی توپولوژی کامل JSON شروع کنید؛ از `-` برای stdin استفاده کنید |
| `--restart` | | پیشرفت setup ذخیره‌شده را قبل از شروع دور کنید |
| `--yes`, `-y` | | بررسی و اعمال را بدون پرسش تأیید کنید |

wizard از طریق سروری، نقش، مسیرهای پروتکل، مسیریابی، دسترسی، بررسی و اعمال راه‌اندازی می‌شود. در هر prompt می‌توانید `back`، `details`، `save` یا `quit` تایپ کنید. پیشرفت بعد از هر مرحله در `~/.meridian/setup.json` ذخیره می‌شود، بنابراین `meridian setup` دوباره اجرا resume می‌شود؛ رمزهای عبور و کلیدهای خصوصی هرگز در آنجا ذخیره نمی‌شوند. `--intent` draft موجود را جایگزین نمی‌کند مگر `--restart` همچنین داده شود. هنگامی‌که اعمال موفق باشد، setup هر گره و اشتراک را تأیید کرده و صفحه اتصال هر کاربر را چاپ می‌کند.

### meridian plan

نشان دهید `meridian apply` چه تغییر می‌دهد، توپولوژی در `~/.meridian/cluster.yml` را با سروری زندگی و پنل مقایسه کنید. هیچ چیزی تغییر نمی‌یابد.

```
meridian plan [--json]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--json` | | برنامه را به عنوان پاکت `meridian.output/v1` برای CI و UI clients ارسال کنید |

خروجی hash برنامه، تعداد منابع روی نوع و هر منابعی که نیاز به تعمیر دارد یا نتوانست مشاهده شود را نشان می‌دهد. JSON `data` شامل `plan_hash`، `converged`، `resources[]`، `drifted_resources[]`، `observation_errors[]` و `state_changes[]` است.

**کدهای خروج**:
- `0` — converged، کار نداریم
- `1` — برنامه‌ریزی نکنیم (هنوز فلیت نیست، یا وضعیت محلی بدون استفاده)
- `2` — تغییرات معلق (اجرا کنید `meridian apply`)
- `3` — شواهد الزامی (panel، SSH، وضعیت محلی) دسترس نبود

topology را در `cluster.yml` دستی ویرایش نکنید. آن را با `meridian setup`، `deploy`، `client add`، `node add` یا `relay deploy` تغییر دهید.

### meridian apply

توپولوژی بررسی‌شده در `cluster.yml` را برای سروری شما اعمال کنید. Apply hash برنامه و تعداد منابع را نشان می‌دهد، برای تأیید درخواست می‌کند، سپس دقیقاً برنامه را اعمال می‌کند که نشان داده، با checkpoints. استقرار قطع‌شده جایی‌که متوقف شد از سر می‌گیرد.

```
meridian apply [--yes] [--json]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--yes`, `-y` | | تأیید را رد کنید |
| `--json` | | نتیجه نهایی اعمال را به عنوان پاکت `meridian.output/v1` ارسال کنید |

JSON `data` شامل `plan_hash`، `generation` و `actions[]` با `status: "converged" | "applied" | "failed" | "unknown" | "skipped"` است. JSON mode هرگز پرسش نمی‌کند: بدون `--yes` `MERIDIAN_CONFIRMATION_REQUIRED` را با hash برنامه بازگردانده و exit `2` می‌شود. منابع شکسته exit `3`؛ علت را رفع کنید و اعمال را دوباره اجرا کنید. اگر وضعیت محلی نتوانست بعد از تغییرات راه دور ذخیره شود، اعمال exit `3` و می‌گوید؛ `~/.meridian` permissions را تعمیر کنید، سپس `plan` و `apply` را دوباره اجرا کنید.

حذف کاربران یا مسیرهای پروتکل اعمال می‌شود: منابع حذف‌شده بعد از اینکه همه چیزی که در برنامه باقی است همگرا شود حذف می‌شود، و `plan` آن‌ها را اول فهرست می‌کند. حذف exit، relay یا routing gateway برای اکنون رد می‌شود.

### meridian client

افرادی را که متصل می‌شوند مدیریت کنید.

```
meridian client add NAME [NAME...] [--json]
meridian client show NAME [--repair-page] [--json]
meridian client list [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
meridian client remove NAME [--yes] [--json]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--json` | | نتیجه را به عنوان پاکت `meridian.output/v1` ارسال کنید (تمام دستورات client) |
| `--repair-page` | | صفحه اتصال کلاینت را دوباره منتشر کنید (`client show`) |
| `--yes`, `-y` | | تأیید `client remove`؛ با `--json` الزامی است |

**`client add`** — یک یا بیشتر افراد را به topology اضافه کرده، آن را اعمال کرده و صفحه اتصال و URL اشتراک هر فرد را چاپ می‌کند. صفحه از اشتراک واقعی آن‌ها ایجاد می‌شود، پس relay و Hysteria2 در حضور نمایش داده می‌شوند. نام‌های قبلاً در topology یا قبلاً در پنل رد می‌شوند. اگر لینک اشتراک کلاینت نتوانست ایجاد شود، کلاینت همچنان اضافه می‌شود و دستور exit `3` می‌شود؛ صفحه اتصال گم تنهایی یک هشدار است (آن را با `client show --repair-page` تعمیر کنید).

**`client show`** — صفحه اتصال، URL اشتراک، وضعیت، ترافیق و زمان آخری‌بار دیده‌شده را چاپ می‌کند. `--repair-page` صفحه گم را دوباره منتشر می‌کند؛ اگر ناموفق باشد، پیوندها همچنان با هشدار نشان داده می‌شوند و exit `3` می‌شود.

**`client list`** — کلاینت‌ها را با وضعیت، ترافیق، زمان ایجاد و آخری دیده‌شده فهرست می‌کند. با `--json`، `data.summary` counts و `data.clients[]` برمی‌گرداند.

**`client enable` / `client disable`** — کلاینت را فوراً ازسرگیری یا تعلیق کنید. تعلیق تا بعدی اعمال دوام می‌آورد (شامل بازاستقرار و `client add`، `node add`، `relay deploy`)، که هر کلاینت اعلام‌شده را دوباره روشن می‌کند.

**`client remove`** — کلاینت را از topology حذف می‌کند، کاربر پنل و صفحه اتصال آن‌ها را حذف می‌کند. تأیید می‌خواهد مگر `--yes`. آخرین کلاینت نمی‌تواند حذف شود. کاربران را در پنل مستقیماً حذف نکنید؛ Meridian هر کلاینتی را که در topology باقی است در بعدی اعمال دوباره ایجاد می‌کند.

### meridian server

سروری SSH ذخیره‌شده مدیریت کنید (`~/.meridian/servers.json`).

```
meridian server add IP [--name NAME] [--user USER] [--ssh-port PORT]
meridian server list
meridian server remove NAME_OR_IP [--yes]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--name NAME` | (هیچ) | نام نمایشی برای سرور |
| `--user/-u USER` | root | کاربر SSH |
| `--ssh-port PORT` | 22 | پورت SSH |
| `--yes`, `-y` | | تأیید حذف را رد کنید (`server remove`) |

نام‌ها و آدرس‌ها باید منحصر به‌فرد باشند؛ `server add` جای‌نشانی را رد می‌کند بجای retargeting سرور موجود. `server remove` فقط پروفایل ذخیره‌شده را حذف می‌کند و رد می‌کند در حالی‌که سرور هنوز نقش در topology دارد.

### meridian node

سروری exit را مدیریت کنید. سرور اول با `meridian deploy` استقرار می‌یابد (یا `meridian setup`)؛ با `meridian node add` exit دیگر اضافه کنید.

```
meridian node add IP [flags]
meridian node list [--json]
meridian node check NAME_OR_IP [--user USER]
meridian node remove NAME_OR_IP [--yes] [--force]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--name NAME` | (خودکار) | نام دوستانه برای گره |
| `--user USER` | root | کاربر SSH روی گره |
| `--ssh-port PORT` | 22 | پورت SSH روی گره |
| `--sni HOST` | www.google.com | هدف camouflage Reality برای این گره |
| `--domain DOMAIN` | (هیچ) | دامنه گره؛ XHTTP، WSS و مسیرهای Hysteria2 را روی آن اضافه می‌کند |
| `--harden / --no-harden` | فعال | تقویت سیستم‌عامل، SSH و firewall. سروری فلیت همیشه آن را نگه می‌دارند؛ `--no-harden` رد می‌شود |
| `--yes` | | پرسش تأیید را رد کنید |
| `--force` | | `node remove`: همچنین زنجیرهای relay که به این exit مسیریابی می‌کنند را بردارید |
| `--json` | | `node list` را به عنوان پاکت `meridian.output/v1` ارسال کنید |

**`node add`** — از طریق SSH متصل شده، سرور را به عنوان exit در topology شما اضافه کرده و برنامه بررسی‌شده را اعمال می‌کند (hardening، Docker، Xray، ثبت‌نام panel). کلاینت‌های موجود exit جدید را در بعدی تازه‌سازی اشتراک دریافت می‌کنند.

**`node list`** — گره‌ها را با وضعیت panel زندگی، نسخه Xray و ترافیق فهرست می‌کند. اگر وضعیت panel دسترس نباشد، گره‌های پیکربندی‌شده به عنوان `unknown` نشان داده می‌شوند و دستور exit `3` می‌شود.

**`node check`** — اتصال panel، SSH، containers، پورت‌های عمومی پیکربندی‌شده گره، گواهینامه‌های TLS و فضای دیسک را بررسی کرده و برای هر شکست راه‌حل چاپ می‌کند. Exit `0` سالم است، `4` مسائل پیدا شده و `3` شواهد الزامی دسترس نبود.

**`node remove`** — یک exit یا دروازه مسیریابی را از topology شما حذف می‌کند. هر منابعی را که حذف خواهد شد فهرست می‌کند (پنل node و profile، container node، قوانین firewall، منتشر شده Hosts)، تأیید می‌خواهد، و دقیقاً آن بررسی را اعمال می‌کند؛ سرور در سرورهای ذخیره‌شده باقی می‌ماند. برای سرور کنترل (از `meridian teardown` استفاده کنید) رد می‌شود، درحالی که relays هنوز به exit مسیریابی می‌کنند (ابتدا آنها را بردارید یا `--force` را منتقل کنید)، درحالی که یک دروازه مسیریابی از طریق آن وارد فلیت می‌شود، و وقتی نتیجه topology را می‌شکند (برای مثال یک حوزه egress خالی یا egress پیش‌فرض). قوانین مسیریابی محدود به یک دروازه حذف‌شده با آن حذف می‌شوند.

### meridian relay

relay را مدیریت کنید — Realm TCP forwarders که به کاربران نقطه ورود داخلی برای exit خارج دهند. Relay ترافیق Reality را حمل می‌کند.

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
meridian relay check RELAY [--exit EXIT] [--user USER] [--json]
meridian relay remove RELAY [--exit EXIT] [--yes]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--exit/-e EXIT` | (الزامی برای deploy) | IP یا نام سرور exit |
| `--name NAME` | (خودکار) | نام دوستانه برای relay (مثل "ru-moscow") |
| `--port/-p PORT` | 443 | پورت گوش دهی روی سرور relay |
| `--sni HOST` | هدف exit | هدف camouflage Reality برای این relay؛ برای استفاده از exit حذف کنید |
| `--user/-u USER` | root | کاربر SSH روی relay |
| `--ssh-port PORT` | 22 | پورت SSH روی سرور relay |
| `--yes/-y` | | پرسش تأیید را رد کنید |
| `--json` | | `relay list` (`data.relays[]`) یا `relay check` را به عنوان پاکت `meridian.output/v1` ارسال کنید |

**`relay deploy`** — relay تک‌hop را به topology شما اضافه کرده و آن را اعمال می‌کند. کلاینت‌ها آن را در بعدی تازه‌سازی اشتراک دریافت می‌کنند. برای زنجیری با بیش از یک hop، از `meridian setup` استفاده کنید.

**`relay remove`** — `RELAY` یک IP سرور relay یا نام ذخیره‌شده یا ID زنجیره relay است. هر زنجیره‌ای را از طریق آن سرور می‌اندازد (آن را با `--exit` محدود کنید)، فهرست می‌کند که چه چیزی حذف خواهد شد (واحدهای Realm، قوانین firewall، شنونده سمت exit، منتشر شده Hosts)، تأیید می‌کند و حذف بررسی‌شده را اعمال می‌کند. سرورها در سرورهای ذخیره‌شده باقی می‌مانند.

**`relay check`** — هر hop Realm را که سرور relay اجرا می‌کند بررسی می‌کند (یا هر hop یک زنجیره): از طریق SSH، که سرویس hop فعال است و می‌تواند TCP را به hop بعدی باز کند؛ از دستگاه شما، که ورود عمومی زنجیره TCP را می‌پذیرد. Exit `0` به معنی هر بررسی پاس شده، `4` به معنی مسائل پیدا شده و `3` به معنی یک hop را نمی‌توان بررسی کرد. از `meridian test` برای فشار ترافیک واقعی از طریق کل مسیر استفاده کنید.

### meridian fleet

سلامتی و موجودی fleet از panel زندگی.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--json` | | نتیجه را به عنوان پاکت `meridian.output/v1` ارسال کنید |

**`fleet status`** — سلامتی panel، heartbeats گره، relay listeners و تعداد کلاینت. کلاینت‌های اعلام‌شده missing یا inactive سلامتی را کاهش می‌دهند. relay listener قابل دسترس فقط TCP را ثابت می‌کند، پس مسیر relay غیر‌تأیید‌شده سلامتی را `unknown` نگه می‌دارد؛ از `meridian test` برای بررسی مسیر کامل استفاده کنید. Exit `0` سلامت، `4` کاهش و `3` نامعلوم است.

**`fleet inventory`** — سروری، نقش، گره و relay پیکربندی‌شده شما، با وضعیت panel زندگی زمانی‌که قابل دسترس باشد. هرگز API token یا مسیرهای مخفی panel را چاپ نمی‌کند. اگر وضعیت زندگی دسترس نباشد، موجودی پیکربندی‌شده هنوز نشان داده می‌شود، با هشدار، و دستور exit `3` می‌شود. `plan --json` را برای تصمیم‌گیری‌اینکه آیا اعمال کار دارد استفاده کنید.

### meridian preflight

قبل از استقرار یک سرور بررسی کنید: هدف camouflage، دسترسی و reachability خارجی پورت 443، DNS، سیستم‌عامل، دیسک و ساعت. هیچ چیزی نصب نمی‌شود. DNS از resolver خود سرور استفاده می‌کند؛ آدرس سرور به سرویس third-party ارسال نمی‌شود.

```
meridian preflight [IP] [--domain NAME] [--sni HOST] [--user USER]
                   [--ssh-port PORT] [--ai] [--server NAME]
```

`--domain` بررسی می‌کند که دامنه به سرور resolve شود. `--sni` هدف camouflage خاصی را بررسی می‌کند، `--user` و `--ssh-port` کاربر SSH ذخیره‌شده و پورت را جایگزین می‌کنند و `--ai` prompt تشخیصی آماده‌شده برای AI را به clipboard کپی می‌کند.

Preflight IP یا hostname عمومی را قبول می‌کند، listener کوتاه‌مدت باز می‌کند زمانی‌که پورت 443 آزاد است، و هرگز شواهد missing را موفقیت نمی‌دانند. Exit `0` compatible، `4` مسائل پیدا شده، `3` شواهد الزامی دسترس نبود و `2` input نامعتبر است.

### meridian scan

با استفاده از pinned RealiTLScanner release روی شبکه خود سرور هدف‌های camouflage پیدا کنید. Meridian قبل از اجرا SHA-256 checksum آن را تأیید می‌کند و workspace راه دور را بعد از آن حذف می‌کند.

```
meridian scan [IP] [--user USER] [--server NAME]
```

RealiTLScanner محدوده IPv4 را اسکن می‌کند؛ سرور IPv6-only exit `3` می‌شود. انتخاب نتیجه دستور را برای استفاده آن چاپ می‌کند — topology استقرار‌یافته را تغییر نمی‌دهد. رد کردن exit `0`، انتخاب نامعتبر exit `2`، بدون هدف مناسب exit `4` و scanner یا دانلود failures exit `3`.

### meridian test

ترافیق proxy تحویل‌داده‌شده را از دستگاه خود تست کنید. SSH نیاز نیست.

تست کامل کلاینت فعال را انتخاب کرده، دقیقاً Xray JSON که Remnawave به آن‌ها تحویل می‌دهد را fetch کرده و ترافیق واقعی را از طریق fallback خودکار و از طریق هر مسیر سرور هدف هدایت می‌کند. اول‌ین اجرا ممکن است تا 120 ثانیه برای دانلود و تأیید Xray runtime pin صرف کند. بررسی ترافیق observers IP مستقل را چرخش می‌دهد و آن‌ها را با درخواست مستقیم مقایسه می‌کند، پس outage observer inconclusive نیست‌اندتر از شکست false. `MERIDIAN_CONNECT_TEST_URL` observer را برای محیط‌های کنترل‌شده جایگزین می‌کند.

outage panel، HTTP `429` یا HTTP `5xx` اشتراک را دسترس نمی‌کند، پس اجرا exit `3` می‌شود. هر انکار اشتراک دیگری مثل `400` یا `422` شکست تکمیل‌شده و exit `4` است.

```
meridian test [IP|DOMAIN] [--server NAME] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--timeout SECONDS] [--json]
```

`--client` کلاینت فعال خاصی را تست می‌کند. `--basic` فقط بررسی شبکه و TLS را بدون دانلود Xray اجرا می‌کند، و نتوانست UDP-only paths را تأیید کند؛ نتوانست با `--client` ترکیب شود (exit `2`). `--timeout` (پیش‌فرض `5`، محدوده `1`–`30`) هر عملیات شبکه را محدود می‌کند، جدا از اول‌ین Xray download.

Exit `0` هر بررسی قبول شد، `4` تست مسائل پیدا کرد و `3` شواهد الزامی دسترس نبود. `--json` پاکت `meridian.output/v1` با هر بررسی و finding ارسال می‌کند.

### meridian probe

سرور را مثل سانسورکننده probe کنید، برای بررسی‌اینکه آیا deployment خود را فاش می‌کند. SSH نیاز نیست. روی هر IP یا دامنه کار می‌کند، و بررسی‌های سخت‌گیرانه‌تر اعمال می‌کند زمانی‌که هدف به topology ذخیره‌شده شما تعلق دارد.

بررسی‌ها از deployment واقعی پیروی می‌کنند: exposure پورت عمومی و داخلی، رفتار HTTP/TLS، سازگاری و camouflage SNI، مسیرهای proxy و panel معمول، WebSocket upgrades، reverse DNS، HTTP/2، نسخه‌های TLS legacy و دامنه‌های پیکربندی‌شده. `--sni` SNI را برای probes TLS عام جایگزین می‌کند. UDP-only listeners به عنوان نیاز `meridian test` کامل به جای حدس گزارش می‌شوند.

```
meridian probe [IP|DOMAIN] [--server NAME] [--sni NAME]
               [--timeout SECONDS] [--json]
```

کدهای خروج test یکسان: `0` قبول شد، `4` مسائل پیدا کرد، `3` inconclusive. `--timeout` پیش‌فرض `5` ثانیه (محدوده `1`–`30`). بررسی‌های حذف‌شده هرگز به عنوان passes گزارش نمی‌شوند.

### meridian doctor

جمع‌آوری تشخیص سیستم برای bug reports. نام مستعار: `meridian rage`.

```
meridian doctor [IP] [--sni HOST] [--user USER] [--ai] [--server NAME]
```

`--sni` هدف camouflage را برای تشخیص انتخاب می‌کند، `--user` کاربر SSH ذخیره‌شده را جایگزین می‌کند و `--ai` گزارش redacted، آماده‌شده برای AI را به clipboard کپی می‌کند بجای چاپ issue template.

Doctor از نقش‌های سرور پیروی می‌کند: سرور relay-only به عنوان exit تشخیص نمی‌شود، و routing gateways به طور جداگانه برچسب‌گذاری می‌شوند. ابزارهای missing یا اتصال SSH گم گزارش را inconclusive می‌کند (exit `3`)؛ بخش‌های قبلاً جمع‌آوری‌شده همچنان نشان داده می‌شوند.

### meridian teardown

Meridian را از سرور حذف کنید.

```
meridian teardown [IP] [--user USER] [--server NAME] [--yes]
```

در یک deployment تنهای سرور، teardown همه چیز را حذف می‌کند: پنل، گره، پیکربندی nginx، گواهینامه‌ها، relay و قوانین firewall روی سرور (Docker و package‌های سیستم می‌مانند)، سپس `cluster.yml` محلی و پروفایل سرور ذخیره‌شده. همچنین 3x-ui را از Meridian 3.x حذف می‌کند. در فلیت چند‌سروری، teardown سروری را رد می‌کند که هنوز نقش در topology دارد.

`--yes` تأیید را رد می‌کند؛ بررسی‌های نقش همچنان اول اجرا می‌شوند. رد کردن exit `1` می‌شود. `--server` نامعلوم، یا صفر یا چندین هدف ممکن، exit `2` بجای prompt.

### meridian update

CLI را به آخرین release نسخه اصلی خود روی PyPI به‌روزرسانی کنید. نسخه اصلی جدید فقط اعلام می‌شود؛ `--major` بعد از خواندن یادداشت‌های release برای آن جابه‌جا می‌شود. نسخه beta نسخه‌های beta جدیدتر نسخه اصلی خود را دنبال می‌کند و سپس release پایدار. Meridian uv، pipx و pip را امتحان می‌کند، و فقط موفقیت را گزارش می‌کند زمانی‌که دستور `meridian` فعال نسخه جدید را نشان می‌دهد. Exit `0` قبلاً جاری یا به‌روزرسانی‌شده؛ `3` به‌روزرسانی یا تأیید آن ناموفق بود. به‌روزرسانی CLI سروری شما را تغییر نمی‌دهد.

```
meridian update [--major]
```

### meridian studio

Meridian Studio، یک browser UI تجربی را با Engine API محلی آن باز کنید. سرور فقط به `127.0.0.1` bind می‌شود.

```
meridian studio [--port PORT] [--assets-dir DIRECTORY] [--no-open]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--port PORT` | 0 | پورت localhost از 0 تا 65535؛ `0` پورت free high را انتخاب می‌کند |
| `--assets-dir DIRECTORY` | bundled assets | Built Studio asset directory را بجای کپی package استفاده کنید |
| `--no-open` | | URL محلی را بدون باز کردن مرورگر چاپ کنید |

Studio واضح‌طور failure می‌کند اگر پورت اشغال‌شده یا assets missing باشند. directory دارائی صریح باید `studio/index.html` داشته باشد.

### meridian api

قرارداد machine-readable پشت JSON output را برای اسکریپت‌ها و UI clients بازرسی کنید.

```
meridian api schemas [--json] [--include-schemas]
meridian api commands [--json] [--include-schemas]
meridian api schema NAME [--envelope|--json]
meridian api workflow NAME [--json]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--json` | | کاتالوگ را به عنوان پاکت `meridian.output/v1` ارسال کنید |
| `--include-schemas` | | JSON Schemas کامل را شامل کنید (JSON output را نتیجه می‌دهد) برای `api schemas` یا `api commands` |
| `--envelope`, `--json` | | `api schema NAME` را در پاکت `meridian.output/v1` wrap کنید بجای چاپ schema خام |

**`api schemas`** — نام‌های schema مثل `output-envelope`، `deploy-request`، `deploy-result`، `deploy-plan`، `plan-envelope`، `apply-envelope`، `client-list-envelope`، `fleet-status-envelope` و `event` را فهرست می‌کند.

**`api commands`** — قرارداد دستور را فهرست می‌کند: argv، schema envelope و data، statuses و outcomes ممکن، معانی کد خروج، پرچم‌های machine و `interrupt_behavior`. Ctrl-C exit `130` بدون JSON envelope می‌شود (`interrupt_behavior: "exit_130_without_envelope"`).

**`api schema NAME`** — یک JSON Schema چاپ می‌کند، مثل `meridian api schema output-envelope`.

**`api workflow NAME`** — workflow UI-renderable چاپ می‌کند، مثل `meridian api workflow deploy --json`.

## گزینه‌های سراسری

گزینه‌های سراسری قبل دستور می‌روند، مثل `meridian --quiet doctor`. گزینه‌های دستور مثل `test --json` بعد از آن می‌روند.

| پرچم | توضیح |
|------|-------------|
| `--version`, `-v` | نسخه CLI را نشان داده و خروج کنید |
| `--verbose` | فعال کردن debug logging |
| `--quiet`, `-q` | output پیشرفت را suppress کنید |
| `--json` | درخواست JSON envelope از دستور که آن را پشتیبانی می‌کند؛ دستورات دیگر صریح ناموفق می‌شوند |
| `--install-completion` | shell completion برای shell فعلی نصب کنید |
| `--show-completion` | completion script برای shell فعلی چاپ کنید |

JSON output پاکت `meridian.output/v1` را استفاده می‌کند (`schema`، `command`، `operation_id`، `status`، `summary`، `data`، `warnings`، `errors`). توسط deploy، plan، apply، test، probe، تمام client commands، fleet status/inventory، node/relay list و api commands پشتیبانی می‌شود. اسرار قبل از JSON خروج process redacted می‌شوند. `meridian api commands --json` را برای اکتشاف قرارداد اجرا کنید.

## گزینه‌های سرور معمول

این گزینه‌ها در دستورات عود می‌آیند؛ هر دستور فقط گزینه‌های فهرست‌شده در بخش خود را قبول می‌کند.

| پرچم | استفاده‌شده توسط |
|------|---------|
| `--server NAME` | deploy، preflight، scan، test، probe، doctor، teardown |
| `--user`, `-u USER` | deploy، node add/check، relay commands، server add، preflight، scan، doctor، teardown |
| `--sni HOST` | deploy، node add، relay deploy، preflight، test، probe، doctor |
| `--domain DOMAIN` | deploy، node add، preflight، test |

دستورات client از API panel مستقیم استفاده می‌کنند و `--server` نمی‌گیرند. `test` و `probe` از دستگاه شما اجرا می‌شوند و SSH استفاده نمی‌کنند.

## تفکیک سرور

برای دستورات SSH، هدف را به عنوان argument یا با `--server NAME` پاس دهید؛ argument جایی‌که هر دو قبول می‌شوند برنده است. بدون هیچ یک، Meridian حالت محلی (زمانی‌که روی سرور استقرار‌یافته اجرا می‌شود) یا سرور تنهای ذخیره‌شده را استفاده می‌کند. صفر یا چندین نامزد exit `2` بجای prompt. `test` و `probe` argument و `--server` را مع رد می‌کنند.
