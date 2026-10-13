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
meridian deploy [SERVER] [flags]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--sni HOST` | (خودکار) | هدف camouflage. بدون آن، استقرار اول ابتدا سایت‌هایی را در زیرشبکهٔ خود سرور امتحان می‌کند، سپس `www.google.com`، `dl.google.com`، `www.cloudflare.com` را، و از اولین موردی که ترافیک از آن عبور می‌کند استفاده می‌کند؛ بازاستقرار هدف ذخیره‌شده را نگه می‌دارد. `--sni` صریح هرگز جابه‌جا نمی‌شود |
| `--domain DOMAIN` | (هیچ) | دامنه شما: XHTTP به آن منتقل می‌شود و WSS اضافه می‌شود، هر دو از طریق CDN Cloudflare (XHTTP بدون دامنه هم روی IP کار می‌کند). پنل و صفحات اتصال روی گواهینامه IP می‌مانند |
| `--hysteria2/--no-hysteria2` | فعال | Hysteria2 را روی UDP/443، با تعویض salamander ارائه دهید، به عنوان آخرین fallback. دامنه‌ای را استفاده می‌کند یا بدون دامنه IP گواهینامه |
| `--fingerprint NAME` | chrome | اثر انگشت TLS که کلاینت‌ها در Reality، XHTTP و WSS نشان می‌دهند (`chrome`، `firefox`، `safari`، `ios`، `android`، `edge`، `random`، `randomized`). اگر منطقه شما شروع به پرچم‌گذاری کردن آن کند تغییر دهید؛ بازاستقرار مقدار ذخیره‌شده را نگه می‌دارد |
| `--client-name NAME` | default | نام کلاینت اول |
| `--display-name NAME` | (هیچ) | برچسب برای صفحات اتصال |
| `--icon EMOJI_OR_URL` | (هیچ) | آیکون صفحه — emoji یا URL تصویر |
| `--color PALETTE` | ocean | تم رنگ صفحه (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | ذخیره‌شده، وگرنه root | کاربر SSH (non-root نیاز به sudo بدون رمز عبور دارد) |
| `--ssh-port PORT` | ذخیره‌شده، وگرنه 22 | پورت SSH |
| `--harden / --no-harden` | فعال | ورود تنهای کلید SSH، fail2ban و firewall UFW. `--no-harden` برای سروری که سرویس‌های دیگری روی آن دارند است |
| `--warp / --no-warp` | خاموش | ترافیک خروجی را از طریق Cloudflare WARP ارسال کنید |
| `--geo-block / --no-geo-block` | فعال | دامنه‌های روسی و IP‌های روسی را در exit block کنید (`geosite:category-ru` + `geoip:ru`). اشتراک‌های Xray JSON و Mihomo قبلاً آن‌ها را مستقیم از دستگاه ارسال می‌کنند |
| `--client-ips / --no-client-ips` | خاموش | IP واقعی هر کلاینت را به Xray و پنل منتقل می‌کند، برای محدودیت IP و لاگ اتصال هر کاربر. وقتی خاموش است، هر اتصال TCP انگار از خود سرور آمده است |
| `--country CC` | رجیستری آدرس | کشور دو‌حرفی که در نام اتصال‌ها و به‌صورت پرچم گره در پنل نمایش داده می‌شود. بدون آن، در اولین استقرار از رجیستری اینترنتی منطقه‌ای (RIR) آدرس سرور پرسیده می‌شود (از طریق RDAP bootstrap مربوط به IANA، بدون سرویس geo-IP)؛ در بازاستقرار، کشور ذخیره‌شده حفظ می‌شود |
| `--yes` | | پرسش تأیید را رد کنید |
| `--json` | | نتیجه deploy نهایی را به عنوان پاکت `meridian.output/v1` ارسال کنید |
| `--events=jsonl` | | رویدادهای تعریف‌شده پیشرفت را به عنوان JSONL روی stderr جریان دهید |
| `--request FILE` | | پیوند JSON `deploy-request` را از فایل بخوانید؛ از `-` برای stdin استفاده کنید |
| `--dry-run` | | deploy را بدون SSH یا تغییرات محلی compile و پیش‌نمایش کنید |

بدون IP، deploy برای یکی درخواست می‌کند. `local` روی ماشینی که اجرا می‌کنید استقرار می‌دهد. Deploy فقط سروری را بازاستقرار می‌کند که قبلاً exit در فلیت شما است؛ سروری دیگر با `meridian node add` اضافه کنید. تغییرات اضافی هستند: حذف چیزی که قبلاً استقرار یافته (مثل drop `--domain`) رد می‌شود.

**جریان Machine/UI**: `meridian api workflow deploy --json` قرارداد wizard renderable برمی‌گرداند. UI آن فیلدها را جمع‌آوری می‌کند، آن‌ها را برای `deploy-request` تأیید می‌کند، سپس `meridian deploy --request deploy.json --json --events=jsonl` را اجرا می‌کند. Machine deploys غیرتعاملی هستند: درخواست باید بعد از تأیید کاربر `yes: true` داشته باشد. `--dry-run --json` `deploy-plan` را تحت `data` برمی‌گرداند، بنابراین UI می‌تواند حالت، پورت‌ها و مسیرهای تولید‌شده را پیش‌نمایش کند قبل از باز کردن SSH.

### meridian setup

یک wizard راهنمایی resumable برای یک یا بسیاری سرور: exits، Realm relays، routing gateways با failover pools و users. این برای افرادی نوشته شده است که هرگز VPN را تنظیم نکرده‌اند: هر سؤال از اصطلاح واقعی (exit، relay، SNI) استفاده می‌کند و آن را در یک جملهٔ ساده با پیش‌فرض ایمن توضیح می‌دهد که می‌توانید با فشار دادن Enter بپذیرید.

```
meridian setup [--intent FILE|-] [--restart] [--yes]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--intent FILE` | (هیچ) | از بررسی توپولوژی کامل JSON شروع کنید؛ از `-` برای stdin استفاده کنید |
| `--restart` | | پیشرفت setup ذخیره‌شده را قبل از شروع دور کنید |
| `--yes`, `-y` | | بررسی و اعمال را بدون پرسش تأیید کنید |

اولین سؤال زبان است (English, Русский, فارسی, 中文)، از ترمینال شما پیش‌انتخاب‌شده و در `~/.config/meridian/preferences.json` به‌خاطر سپرده می‌شود؛ `--lang` یا `MERIDIAN_LANG` آن را رد می‌کنند. سپس wizard از طریق سروری (یک IP، `user@IP:port` یا `local` برای این ماشین)، آنچه که هر سرور انجام می‌دهد، روش‌های اتصال، مسیریابی (تنها زمانی پرسیده می‌شود که routing gateway داشته باشید)، users، خلاصهٔ ساده‌زبان و اعمال راه‌اندازی می‌شود. در هر prompt می‌توانید `back`، `details`، `save` یا `quit` تایپ کنید (یا همان کلمات به زبان شما). اجرای `meridian` بدون فرمان در یک ترمینال، قبل از آن‌که VPN موجود باشد، برای شروع wizard پیشنهاد می‌دهد. پیشرفت بعد از هر مرحله در `~/.config/meridian/setup.json` ذخیره می‌شود، بنابراین `meridian setup` دوباره اجرا resume می‌شود؛ رمزهای عبور و کلیدهای خصوصی هرگز در آنجا ذخیره نمی‌شوند. `--intent` draft موجود را جایگزین نمی‌کند مگر `--restart` همچنین داده شود. هنگامی‌که اعمال موفق باشد، setup هر گره و اشتراک را تأیید کرده و صفحه اتصال هر کاربر را چاپ می‌کند.

### meridian plan

نشان دهید `meridian apply` چه تغییر می‌دهد، توپولوژی در `~/.config/meridian/cluster.yml` را با سروری زندگی و پنل مقایسه کنید. هیچ چیزی تغییر نمی‌یابد.

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

topology را در `cluster.yml` دستی ویرایش نکنید. آن را با `meridian setup`، `deploy`، `client add`، `node add` یا `relay add` تغییر دهید.

### meridian apply

توپولوژی بررسی‌شده در `cluster.yml` را برای سروری شما اعمال کنید. Apply hash برنامه و تعداد منابع را نشان می‌دهد، برای تأیید درخواست می‌کند، سپس دقیقاً برنامه را اعمال می‌کند که نشان داده، با checkpoints. استقرار قطع‌شده جایی‌که متوقف شد از سر می‌گیرد.

```
meridian apply [--yes] [--json]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--yes`, `-y` | | تأیید را رد کنید |
| `--json` | | نتیجه نهایی اعمال را به عنوان پاکت `meridian.output/v1` ارسال کنید |

JSON `data` شامل `plan_hash`، `generation` و `actions[]` با `status: "converged" | "applied" | "failed" | "unknown" | "skipped"` است. JSON mode هرگز پرسش نمی‌کند: بدون `--yes` `MERIDIAN_CONFIRMATION_REQUIRED` را با hash برنامه بازگردانده و exit `2` می‌شود. منابع شکسته exit `3`؛ علت را رفع کنید و اعمال را دوباره اجرا کنید. اگر وضعیت محلی نتوانست بعد از تغییرات راه دور ذخیره شود، اعمال exit `3` و می‌گوید؛ `~/.config/meridian` permissions را تعمیر کنید، سپس `plan` و `apply` را دوباره اجرا کنید.

حذف‌ها نیز اعمال می‌شوند: هر چیزی که topology را ترک کرده (کاربران، مسیرهای پروتکل، exitها، relayها، routing gatewayها) بعد از اینکه همه چیزی که در برنامه باقی است همگرا شود حذف می‌شود، و `plan` آن را اول فهرست می‌کند. سرور کنترل فقط با `meridian teardown` حذف می‌شود.

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

**`client enable` / `client disable`** — کلاینت را فوراً فعال یا تعلیق کنید. کلاینت تعلیق‌شده تا وقتی `client enable` را اجرا نکنید غیرفعال می‌ماند؛ apply (از جمله بازاستقرار و `client add`، `node add`، `relay add`) وضعیت کلاینت را تغییر نمی‌دهد.

**`client remove`** — کلاینت را از topology حذف می‌کند، کاربر پنل و صفحه اتصال آن‌ها را حذف می‌کند. تأیید می‌خواهد مگر `--yes`. آخرین کلاینت نمی‌تواند حذف شود. کاربران را در پنل مستقیماً حذف نکنید؛ Meridian هر کلاینتی را که در topology باقی است در بعدی اعمال دوباره ایجاد می‌کند.

### meridian server

سروری SSH ذخیره‌شده مدیریت کنید (`~/.config/meridian/servers.json`).

```
meridian server add [USER@]IP[:PORT]|local [--name NAME] [--user USER] [--ssh-port PORT]
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
meridian node add [USER@]IP[:PORT] [flags]
meridian node list [--json]
meridian node remove NAME_OR_IP [--yes] [--force]
meridian node replace EXIT [USER@]NEW_IP[:PORT] [--name NAME] [--yes]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--name NAME` | (خودکار) | نام دوستانه برای گره |
| `--user USER` | ذخیره‌شده، وگرنه root | کاربر SSH روی گره |
| `--ssh-port PORT` | ذخیره‌شده، وگرنه 22 | پورت SSH روی گره (`node add`, `node replace`) |
| `--sni HOST` | www.google.com | هدف camouflage Reality برای این گره |
| `--domain DOMAIN` | (هیچ) | دامنه گره: XHTTP به آن منتقل می‌شود و WSS اضافه می‌شود، هر دو از طریق CDN Cloudflare؛ Hysteria2 از گواهینامه آن استفاده می‌کند |
| `--country CC` | رجیستری آدرس | کشور نمایش‌داده‌شده در نام اتصال‌ها؛ وقتی رجیستری اشتباه است (anycast یا بازه‌های اجاره‌ای) آن را مشخص کنید |
| `--harden / --no-harden` | فعال | تقویت سیستم‌عامل، SSH و firewall. سروری فلیت همیشه آن را نگه می‌دارند؛ `--no-harden` رد می‌شود |
| `--yes` | | پرسش تأیید را رد کنید |
| `--force` | | `node remove`: همچنین زنجیرهای relay که به این exit مسیریابی می‌کنند را بردارید |
| `--json` | | `node list` را به عنوان پاکت `meridian.output/v1` ارسال کنید |

**`node add`** — از طریق SSH متصل شده، سرور را به عنوان exit در topology شما اضافه کرده و برنامه بررسی‌شده را اعمال می‌کند (hardening، Docker، Xray، ثبت‌نام panel). کلاینت‌های موجود exit جدید را در بعدی تازه‌سازی اشتراک دریافت می‌کنند.

**`node list`** — گره‌ها را با وضعیت panel زندگی، نسخه Xray و ترافیق فهرست می‌کند. اگر وضعیت panel دسترس نباشد، گره‌های پیکربندی‌شده به عنوان `unknown` نشان داده می‌شوند و دستور exit `3` می‌شود.

**`node remove`** — یک exit یا دروازه مسیریابی را از topology شما حذف می‌کند. هر منابعی را که حذف خواهد شد فهرست می‌کند (پنل node و profile، container node، قوانین firewall، منتشر شده Hosts)، تأیید می‌خواهد، و دقیقاً آن بررسی را اعمال می‌کند؛ سرور در سرورهای ذخیره‌شده باقی می‌ماند. برای سرور کنترل (از `meridian teardown` استفاده کنید) رد می‌شود، درحالی که relays هنوز به exit مسیریابی می‌کنند (ابتدا آنها را بردارید یا `--force` را منتقل کنید)، درحالی که یک دروازه مسیریابی از طریق آن وارد فلیت می‌شود، و وقتی نتیجه topology را می‌شکند (برای مثال یک حوزه egress خالی یا egress پیش‌فرض). قوانین مسیریابی محدود به یک دروازه حذف‌شده با آن حذف می‌شوند.

**`node replace`** — یک exit را به سرور دیگر منتقل می‌کند. `EXIT` ID exit یا نام ذخیره‌شده سرور یا IP است؛ سرور جدید syntax هم‌انندی `node add` (نام ذخیره‌شده یا `local` هم‌چنین کار می‌کند) استفاده می‌کند و مانند `server add` ذخیره می‌شود. exit ID خود را، کلیدهای Reality، مسیرهای پروتکل، SNI، WARP و تنظیمات geo خود را نگاه می‌دارد، و هر relay که به آن اشاره می‌کند، بنابراین relay کاربران کار می‌کند و کاربران مستقیم آدرس جدید را در تازه‌سازی اشتراک بعدی خود دریافت می‌کنند. بررسی آنچه روی سرور جدید مستقر خواهد شد فهرست می‌کند و هر منابع تمیز شدهٔ سرور قدیم (node container، image و directory؛ فایل‌های nginx، قوانین firewall و گواهینامه‌ها)؛ Docker و سرور ذخیره‌شده باقی می‌مانند. برای سرور کنترل (منتقل کردن پنل هر URL اشتراک را تغییر می‌دهد) و برای سرور جدید که قبلاً نقش دارد رد می‌شود.

### meridian relay

relay را مدیریت کنید — Realm TCP forwarders که به کاربران نقطه ورود داخلی برای exit خارج دهند. Relay ترافیق Reality را حمل می‌کند.

```
meridian relay add [USER@]RELAY_IP[:PORT] --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
meridian relay remove RELAY [--exit EXIT] [--yes]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--exit/-e EXIT` | (الزامی برای add) | IP یا نام سرور exit |
| `--name NAME` | (خودکار) | نام دوستانه برای relay (مثل "ru-moscow") |
| `--port/-p PORT` | 443 | پورت گوش دهی روی سرور relay |
| `--sni HOST` | هدف exit | هدف camouflage Reality برای این relay؛ برای استفاده از exit حذف کنید |
| `--user/-u USER` | ذخیره‌شده، وگرنه root | کاربر SSH روی relay |
| `--ssh-port PORT` | ذخیره‌شده، وگرنه 22 | پورت SSH روی سرور relay (`relay add`) |
| `--yes/-y` | | پرسش تأیید را رد کنید |
| `--json` | | `relay list` (`data.relays[]`) را به عنوان پاکت `meridian.output/v1` ارسال کنید |

**`relay add`** — relay تک‌hop را به topology شما اضافه کرده و آن را اعمال می‌کند. کلاینت‌ها آن را در بعدی تازه‌سازی اشتراک دریافت می‌کنند. برای زنجیری با بیش از یک hop، از `meridian setup` استفاده کنید.

**`relay remove`** — `RELAY` یک IP سرور relay یا نام ذخیره‌شده یا ID زنجیره relay است. هر زنجیره‌ای را از طریق آن سرور می‌اندازد (آن را با `--exit` محدود کنید)، فهرست می‌کند که چه چیزی حذف خواهد شد (واحدهای Realm، قوانین firewall، شنونده سمت exit، منتشر شده Hosts)، تأیید می‌کند و حذف بررسی‌شده را اعمال می‌کند. سرورها در سرورهای ذخیره‌شده باقی می‌مانند.

برای بررسی hopهای relay، از `meridian check RELAY` استفاده کنید.

### meridian route

مقاصد انتخاب‌شده را از exit از طریق Cloudflare WARP، مستقیم، یا هیچ‌کدام ارسال کنید، پیش از egress پیش‌فرض exit.

```
meridian route add EXIT VALUE... --via warp|direct|block [--name ID] [--json]
meridian route list [EXIT] [--json]
meridian route remove EXIT ROUTE [--yes] [--json]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--via ACTION` | (الزامی برای add) | `warp`، `direct` یا `block` |
| `--name ID` | action، numbered | Route ID نشان داده‌شده توسط `route list` و استفاده شده توسط `route remove` |
| `--yes/-y` | | تأیید حذف را رد کنید (با `--json` الزامی است) |
| `--json` | | پاکت `meridian.output/v1` ارسال کنید (`data.override` برای add/remove، `data.exits[]` برای list) |

`EXIT` ID exit، نام سرور ذخیره‌شده یا IP است. هر `VALUE` دامنه (subdomains شامل)، `geosite:NAME` یا `geoip:NAME` است؛ یک route یک نوع را نگاه می‌دارد.

**`route add`** — route را به exit در topology شما اضافه کرده و آن را اعمال می‌کند. Routes به ترتیبی که اضافه شدند match می‌کند. `warp` route روی exit بدون WARP تنظیم WARP برای آن route تنها می‌کند؛ ترافیق دیگر مستقیم می‌ماند.

**`route list`** — هر exit's egress پیش‌فرض و routes آن را به ترتیب match نشان می‌دهد.

**`route remove`** — یک route را حذف کرده و تغییر را اعمال می‌کند؛ مقاصد آن egress پیش‌فرض exit را دنبال می‌کند دوباره. WARP روی سرور نصب شده باقی می‌ماند اما هیچ چیزی را حمل نمی‌کند تا route یا `--warp` آن را استفاده کند.

### meridian fleet

سلامت و موجودی fleet از پنل زنده، و ارتقای Remnawave.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet upgrade [--tested] [--yes]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--json` | | نتیجه را به عنوان پاکت `meridian.output/v1` ارسال کنید (`status`, `inventory`) |
| `--tested` | | `fleet upgrade`: فقط از نسخه‌های Remnawave استفاده کنید که این نسخه Meridian با آن‌ها تست شده، بدون پرسیدن از Docker Hub |
| `--yes`, `-y` | | `fleet upgrade`: تأیید را رد کنید |

**`fleet status`** — سلامتی panel، heartbeats گره، relay listeners و تعداد کلاینت. کلاینت‌های اعلام‌شده missing سلامتی را کاهش می‌دهند؛ کلاینت‌های غیرفعال، محدود یا منقضی شده شمرده می‌شوند اما انتخاب شما هستند، نه خرابی. relay listener قابل دسترس فقط TCP را ثابت می‌کند، پس مسیر relay غیر‌تأیید‌شده سلامتی را `unknown` نگه می‌دارد؛ از `meridian test` برای بررسی مسیر کامل استفاده کنید. Exit `0` سلامت، `4` کاهش و `3` نامعلوم است.

**`fleet inventory`** — سروری، نقش، گره و relay پیکربندی‌شده شما، با وضعیت panel زندگی زمانی‌که قابل دسترس باشد. هرگز API token یا مسیرهای مخفی panel را چاپ نمی‌کند. اگر وضعیت زندگی دسترس نباشد، موجودی پیکربندی‌شده هنوز نشان داده می‌شود، با هشدار، و دستور exit `3` می‌شود. `plan --json` را برای تصمیم‌گیری‌اینکه آیا اعمال کار دارد استفاده کنید.

**`fleet upgrade`** — Remnawave را بدون انتظار برای به‌روزرسانی Meridian به جدیدترین نسخه‌اش می‌برد. از Docker Hub جدیدترین نسخه‌های backend، node و subscription page را در همان major که این نسخه Meridian پشتیبانی می‌کند می‌پرسد (pre-release‌ها و majorهای دیگر نادیده گرفته می‌شوند)، هر کدام را با digest چندمعماری‌اش pin می‌کند و انتخاب را در `cluster.yml` ذخیره می‌کند تا applyهای بعدی آن را نگه دارند. pin جدیدتر در نسخه بعدی Meridian دوباره جایگزین می‌شود. `--tested` روی نسخه‌هایی می‌ماند که این نسخه با آن‌ها تست شده است. PostgreSQL و Valkey همیشه روی pinهای Meridian می‌مانند. سپس image‌های backend، subscription page، PostgreSQL، Valkey و node را که هر سرور اجرا می‌کند می‌خواند (read-only `docker inspect` روی SSH) و هر یک را `current`، `upgrade`، `install` یا نگه‌داشته نشان می‌دهد. سیاست نسخه برابر setup است: نسخهٔ tag جدیدتر یا tag floating (`latest`، `3`) که انتخاب کردید نگه داشته می‌شود و گزارش می‌شود، هرگز downgrade نمی‌شود. یک PostgreSQL major هرگز در جای خود منتقل نمی‌شود — major قدیم اجرا را نگاه می‌دارد و دستور dump-and-restore آن را نیاز دارد توضیح می‌دهد. بعد از تأیید، برنامهٔ بررسی‌شده را اعمال می‌کند و `meridian check` را روی هر سرور اجرا می‌کند. سروری که تصاویر آن نمی‌توانند خوانده شود دستور را با exit `3` متوقف می‌کند قبل از هیچ تغییری؛ اگر اعمال همگرا شود اما سرور هنوز تصویر قدیمی اجرا می‌کند، exit `3` هم می‌شود.

### meridian check

سلامتی سرور را بررسی کنید، یا آیا یک سرور جدید می‌تواند Meridian را اجرا کند. آنچه اجرا می‌شود به نقش سرور در فلیت شما بستگی دارد:

- **هنوز در فلیت نیست** — بررسی‌های پیش‌استقرار: reachability هدف camouflage و DNS، دسترسی و reachability خارجی پورت 443، DNS دامنه، سیستم‌عامل، دیسک و ساعت. هیچ چیزی نصب نمی‌شود؛ listener کوتاه‌مدت زمانی‌که پورت 443 آزاد است firewall را تست می‌کند. DNS از resolver خود سرور استفاده می‌کند.
- **کنترل، exit یا routing gateway** — اتصال panel، SSH (یا دسترسی محلی)، containers (شامل سلامتی صفحه اشتراک)، هر پورت عمومی و داخلی compiled، گواهینامه‌های TLS و فضای دیسک. اگر از دستگاه دیگری اجرا کنید، سرور کنترل نیز بررسی جریان داده را دریافت می کند: دانلود ~50 کیلوبایتی که هنگامی که پس از حدود 16-24 کیلوبایت منجمد می شود هشدار می دهد (تنظیم سرعت، اندازه گیری شده از جایی که شما هستید).
- **Relay** — هر hop Realm روی سرور: سرویس فعال است و می‌تواند hop بعدی را reach کند، و ورود عمومی زنجیره TCP را می‌پذیرد از دستگاه شما.

بدون سرور، هر سرور در فلیت بررسی می‌شود.

```
meridian check [SERVER] [--domain NAME] [--sni HOST] [--user USER]
               [--ssh-port PORT] [--ai] [--json]
```

`--domain` و `--sni` برای سروری که هنوز deploy نشده‌اند اعمال می‌شود: بررسی می‌کنند که دامنه به سرور resolve شود و هدف camouflage خاص قابل‌دسترس است. `--user` و `--ssh-port` کاربر SSH ذخیره‌شده و پورت را جایگزین می‌کنند. `--ai` prompt تشخیصی آماده‌شده برای AI را به clipboard کپی می‌کند. `--json` یک پاکت `meridian.output/v1` با گزارش در هر سرور و scope ارسال می‌کند.

شواهد missing هرگز به عنوان موفقیت تلقی نمی‌شود. Exit `0` هر بررسی پاس شده، `4` مسائل پیدا شده، `3` شواهد الزامی دسترس نبود و `2` input نامعتبر است. هر شکست fix خود را چاپ می‌کند. از `meridian test` برای فشار ترافیق واقعی از طریق کل مسیر استفاده کنید.

### meridian scan

با استفاده از pinned RealiTLScanner release روی شبکه خود سرور هدف‌های camouflage پیدا کنید. Meridian قبل از اجرا SHA-256 checksum آن را تأیید می‌کند و workspace راه دور را بعد از آن حذف می‌کند.

```
meridian scan [SERVER] [--user USER] [--ssh-port PORT]
```

RealiTLScanner محدوده IPv4 را اسکن می‌کند؛ سرور IPv6-only exit `3` می‌شود. انتخاب نتیجه دستور را برای استفاده آن چاپ می‌کند — topology استقرار‌یافته را تغییر نمی‌دهد. رد کردن exit `0`، انتخاب نامعتبر exit `2`، بدون هدف مناسب exit `4` و scanner یا دانلود failures exit `3`.

### meridian test

ترافیق proxy تحویل‌داده‌شده را از دستگاه خود تست کنید. SSH نیاز نیست.

تست کامل کلاینت فعال را انتخاب کرده، دقیقاً Xray JSON که Remnawave به آن‌ها تحویل می‌دهد را fetch کرده و ترافیق واقعی را از طریق fallback خودکار و از طریق هر مسیر سرور هدف هدایت می‌کند. اول‌ین اجرا ممکن است تا 120 ثانیه برای دانلود و تأیید Xray runtime pin صرف کند. بررسی ترافیق observers IP مستقل را چرخش می‌دهد و آن‌ها را با درخواست مستقیم مقایسه می‌کند، پس outage observer inconclusive نیست‌اندتر از شکست false. `MERIDIAN_CONNECT_TEST_URL` observer را برای محیط‌های کنترل‌شده جایگزین می‌کند.

outage panel، HTTP `429` یا HTTP `5xx` اشتراک را دسترس نمی‌کند، پس اجرا exit `3` می‌شود. هر انکار اشتراک دیگری مثل `400` یا `422` شکست تکمیل‌شده و exit `4` است.

```
meridian test [SERVER|DOMAIN] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--probe] [--timeout SECONDS] [--json]
```

بدون هدف، `test` سرور ذخیره‌شده‌ای را که روی آن اجرا می‌شود یا تنها سرور ذخیره‌شده شما را تست می‌کند. اگر چند سرور باشد، هر نقطه ورود عمومی فلیت شما را به‌ترتیب تست می‌کند (هر exit، سپس نقطه ورود هر relay) و با بدترین نتیجه خارج می‌شود؛ در این حالت `--json` برای هر سرور یک نتیجه در `data.results` برمی‌گرداند.

`--client` کلاینت فعال خاصی را تست می‌کند. `--basic` فقط بررسی شبکه و TLS را بدون دانلود Xray اجرا می‌کند، و نتوانست UDP-only paths را تأیید کند؛ نتوانست با `--client` ترکیب شود (exit `2`). `--timeout` (پیش‌فرض `5`، محدوده `1`–`30`) هر عملیات شبکه را محدود می‌کند، جدا از اول‌ین Xray download.

Exit `0` هر بررسی قبول شد، `4` تست مسائل پیدا کرد و `3` شواهد الزامی دسترس نبود. `--json` پاکت `meridian.output/v1` با هر بررسی و finding ارسال می‌کند.

هر مسیر زمان رسیدن به اولین بایت را گزارش می دهد. وقتی یک مسیر ناموفق است، Meridian یک اتصال TCP ساده به نقطه پایانی باز می کند تا بگوید آیا **متصل می شود اما ترافیک عبور نمی کند** (سرور پاسخ می دهد اما جلسه پروکسی هیچ چیزی حمل نمی کند: اعتبارات نادرست یا قطع شدن جلسه) یا **غیرقابل دسترس** (چیزی در آن پورت پاسخ نمی دهد). با `--json`، `data.transports` نتیجه هر مسیر را (`ok`, `no_traffic`, `unreachable`, `failed`, `egress_mismatch`, `inconclusive`, `invalid`) و `first_byte_ms` فهرست می کند.

#### نقطه نظر سانسورکننده: `meridian test --probe`

`--probe` سرور را مثل سانسورکننده فعال probe می‌کند، برای بررسی‌اینکه آیا deployment خود را فاش می‌کند، بجای ارسال ترافیق. روی هر IP یا دامنه کار می‌کند، و بررسی‌های سخت‌گیرانه‌تر اعمال می‌کند زمانی‌که هدف به topology ذخیره‌شده شما تعلق دارد.

بررسی‌ها از deployment واقعی پیروی می‌کنند: exposure پورت عمومی و داخلی، رفتار HTTP/TLS، سازگاری و camouflage SNI، مسیرهای proxy و panel معمول، WebSocket upgrades، reverse DNS، HTTP/2، نسخه‌های TLS legacy و دامنه‌های پیکربندی‌شده. `--sni` SNI را برای probes TLS عام جایگزین می‌کند. UDP-only listeners به عنوان نیاز `meridian test` کامل به جای حدس گزارش می‌شوند.

`--probe` همچنین به سوال «چه چیزی مسدود است؟» پاسخ می‌دهد. TLS را به سرور با SNI تمویه آن باز می‌کند؛ اگر این کار شکست بخورد، بدون SNI دوباره تلاش می‌کند و سایت تمویه واقعی را مستقیماً باز می‌کند، سپس گزارش می‌دهد که آیا **IP سرور** یا **SNI تمویه** مسدود به نظر می‌رسد. اگر رسیدن به سایت واقعی نیز ممکن نباشد، نتیجه نامعلوم است نه یک یافته. در سرور کنترل، دانلود اسکریپت صفحه اتصال (~50 کیلوبایت) را دانلود می‌کند و برای انتقالی که پس از حدود 16-24 کیلوبایت متوقف می‌شود، که نحوه رفتار IP‌های خارجی تنظیم‌شده است، نظارت می‌کند. هنگامی که مسیر WSS از طریق CDN عبور می‌کند، همان دانلود از طریق CDN انجام می‌شود. این نتایج تنها زمانی معنی دارند که آنها را از داخل شبکه سانسور شده اجرا کنید. `--probe` را نمی‌توان با `--basic`، `--client` یا `--domain` ترکیب کرد. کدهای خروج مانند `test` هستند؛ بررسی‌های پرشده هرگز به عنوان موارد قبول گزارش نمی‌شوند.

`meridian probe [SERVER|DOMAIN] [--sni NAME] [--timeout SECONDS] [--json]` همان `meridian test IP --probe` است.

### meridian doctor

جمع‌آوری تشخیص سیستم برای bug reports.

```
meridian doctor [SERVER] [--sni HOST] [--user USER] [--ssh-port PORT] [--ai]
```

`--sni` هدف camouflage را برای تشخیص انتخاب می‌کند، `--user` و `--ssh-port` کاربر SSH ذخیره‌شده و پورت را جایگزین می‌کنند و `--ai` گزارش redacted، آماده‌شده برای AI را به clipboard کپی می‌کند بجای چاپ issue template.

Doctor از نقش‌های سرور پیروی می‌کند: سرور relay-only به عنوان exit تشخیص نمی‌شود، و routing gateways به طور جداگانه برچسب‌گذاری می‌شوند. ابزارهای missing یا اتصال SSH گم گزارش را inconclusive می‌کند (exit `3`)؛ بخش‌های قبلاً جمع‌آوری‌شده همچنان نشان داده می‌شوند.

### meridian panel

URL پنل مدیریت Remnawave و نام کاربری مدیریت را نشان دهید، و آنچه Meridian در آنجا مدیریت می‌کند درمقابل آنچه برای تغییر مال شما است.

```
meridian panel [--show-password]
```

| پرچم | پیش‌فرض | توضیح |
|------|---------|-------------|
| `--show-password` | | همچنین رمز عبور مدیریت را چاپ کنید |

ویرایش‌های وضعیت کلاینت، محدودیت‌های ترافیق، انقضا و توضیح و کاربرانی که در پنل ایجاد می‌کنید هر اعمال را survive می‌کنند. ویرایش‌های nodes، profiles، Hosts، squads و templates که Meridian ایجاد کرد توسط بعدی اعمال reverted می‌شوند؛ `meridian plan` ابتدا آن‌ها را نشان می‌دهد.

### meridian teardown

Meridian را از سرور حذف کنید.

```
meridian teardown [SERVER] [--user USER] [--ssh-port PORT] [--yes]
```

در یک deployment تنهای سرور، teardown همه چیز را حذف می‌کند: پنل، گره، پیکربندی nginx، گواهینامه‌ها، relay و قوانین firewall روی سرور (Docker و package‌های سیستم می‌مانند)، سپس `cluster.yml` محلی و پروفایل سرور ذخیره‌شده. همچنین 3x-ui را از Meridian 3.x حذف می‌کند. در فلیت چند‌سروری، teardown سروری را رد می‌کند که هنوز نقش در topology دارد.

`--yes` تأیید را رد می‌کند؛ بررسی‌های نقش همچنان اول اجرا می‌شوند. رد کردن exit `1` می‌شود. نام سرور نامعلوم، یا صفر یا چندین هدف ممکن، exit `2` بجای prompt.

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

### meridian help

Meridian را به یک عامل هوش مصنوعی معرفی می‌کند، یا راهنمای یک دستور را نشان می‌دهد.

```
meridian help [agents | COMMAND...]
```

**`meridian help agents`** یک راهنمای Markdown برای عامل‌های هوش مصنوعی چاپ می‌کند: Meridian چه کاری انجام می‌دهد، fleet و توپولوژی بازبینی‌شده‌اش چطور به هم مربوط‌اند، وضعیت کجا نگه‌داری می‌شود، قواعد اجرای امن CLI (پرچم‌های غیرتعاملی، پاکت‌های JSON، plan پیش از apply، تأیید پیش از حذف، لینک‌ها به‌عنوان رمز)، کدهای خروج و کارهای رایج. خروجی همیشه Markdown ساده است، پس عامل می‌تواند آن را از pipe بخواند.

**`meridian help COMMAND`** همان راهنمای `meridian COMMAND --help` را نشان می‌دهد، مثلاً `meridian help node add`. بدون آرگومان، راهنمای سطح بالا را چاپ می‌کند. موضوع ناشناخته با کد `2` خارج می‌شود.

## گزینه‌های سراسری

گزینه‌های سراسری قبل دستور می‌روند، مثل `meridian --quiet doctor`. گزینه‌های دستور مثل `test --json` بعد از آن می‌روند.

| پرچم | توضیح |
|------|-------------|
| `--version`, `-v` | نسخه CLI را نشان داده و خروج کنید |
| `--verbose` | فعال کردن debug logging |
| `--quiet`, `-q` | output پیشرفت را suppress کنید |
| `--lang CODE` | زبان برای wizard setup: `en`, `ru`, `fa` یا `zh` (پیش‌فرض: انتخاب ذخیره‌شده، وگرنه پرسیده می‌شود) |
| `--json` | درخواست JSON envelope از دستور که آن را پشتیبانی می‌کند؛ دستورات دیگر صریح ناموفق می‌شوند |
| `--install-completion` | shell completion برای shell فعلی نصب کنید |
| `--show-completion` | completion script برای shell فعلی چاپ کنید |

JSON output پاکت `meridian.output/v1` را استفاده می‌کند (`schema`، `command`، `operation_id`، `status`، `summary`، `data`، `warnings`، `errors`). توسط deploy، plan، apply، test، probe، تمام client commands، fleet status/inventory، node/relay list و api commands پشتیبانی می‌شود. اسرار قبل از JSON خروج process redacted می‌شوند. `meridian api commands --json` را برای اکتشاف قرارداد اجرا کنید.

## گزینه‌های سرور معمول

این گزینه‌ها در دستورات عود می‌آیند؛ هر دستور فقط گزینه‌های فهرست‌شده در بخش خود را قبول می‌کند.

| پرچم | استفاده‌شده توسط |
|------|---------|
| `SERVER` argument | deploy، check، scan، test، doctor، teardown، node add، relay add، server add |
| `--user`, `-u USER` | deploy، check، node add، relay add، server add، scan، doctor، teardown |
| `--ssh-port PORT` | deploy، check، node add، relay add، server add، scan، doctor، teardown |
| `--sni HOST` | deploy، check، node add، relay add، test، doctor |
| `--domain DOMAIN` | deploy، check، node add، test |

دستورات client از API panel مستقیم استفاده می‌کنند و سرور نمی‌گیرند. `test` از دستگاه شما اجرا می‌شود و SSH استفاده نمی‌کند.

## تفکیک سرور

سرور را مانند `ssh` اول بنویسید: یک IP، یک نام ذخیره‌شده، `user@host:port` یا `local` برای اجرا روی همین ماشین بدون SSH. `--user` و `--ssh-port` بخش‌هایی را که در هدف نیامده کامل می‌کنند و نباید با آن تناقض داشته باشند. کاربر یا پورت خالی یعنی مقدار ذخیره‌شده، و اگر نباشد `root` و `22`. بدون سرور، Meridian از حالت محلی (هنگام اجرا روی سرور مستقرشده) یا تنها سرور ذخیره‌شده استفاده می‌کند. اگر هیچ نامزدی نباشد یا چند نامزد باشد، فرمان به‌جای پرسیدن با کد `2` خارج می‌شود.
