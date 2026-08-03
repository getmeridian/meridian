---
title: مرجع CLI
description: مرجع کامل برای تمام دستورات و پرچم‌های Meridian CLI.
order: 10
section: reference
---

## دستورات

### meridian setup

پیکربندی یک توپولوژی کامل V4 با راهنمای مرحله‌ای و قابل‌ازسرگیری.

```
meridian setup [--intent FILE|-] [--restart] [--yes]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|---------|
| `--intent FILE` | (هیچ) | شروع از مرحله بازبینی با یک سند کامل JSON از نوع `SetupIntent`؛ `-` ورودی stdin را می‌خواند |
| `--restart` | | حذف صریح پیشرفت ذخیره‌شده پیش از شروع |
| `--yes`, `-y` | | تأیید برنامه بازبینی‌شده و اعمال آن بدون تأیید دوباره |

Setup پیشرفت بدون اطلاعات محرمانه را در `~/.meridian/setup.json` ذخیره می‌کند؛ اجرای دوباره از آخرین مرحله کامل ادامه می‌دهد. اعتبارنامه‌ها و کلیدهای خصوصی در این پیش‌نویس ذخیره نمی‌شوند. پیش‌نویس موجود با `--intent` جایگزین نمی‌شود، مگر اینکه `--restart` نیز داده شود.

### meridian deploy

نصب سرور proxy روی یک VPS.

```
meridian deploy [IP] [flags]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--sni HOST` | www.microsoft.com | هدف پوشش TLS |
| `--domain DOMAIN` | (هیچ) | دامنه بازگشت CDN Cloudflare |
| `--client-name NAME` | default | نام برای اولین کلاینت |
| `--display-name NAME` | (هیچ) | برچسب برای صفحات اتصال |
| `--icon EMOJI_OR_URL` | (هیچ) | آیکون صفحه — ایموجی یا URL تصویر |
| `--color PALETTE` | ocean | تم رنگ صفحه (ocean/sunset/forest/lavender/rose/slate) |
| `--user USER` | root | کاربر SSH |
| `--harden / --no-harden` | فعال | سخت‌سازی SSH + فایروال |
| `--warp / --no-warp` | غیرفعال | مسیریابی ترافیک خروجی از طریق Cloudflare WARP |
| `--server NAME` | | سرور هدف (نام یا IP) |
| `--geo-block` / `--no-geo-block` | فعال | مسدود کردن دومنه‌ها و IP‌های روسیه (geosite:category-ru + geoip:ru) |
| `--ssh-port PORT` | 22 | پورت SSH (در صورت عدم استاندارد) |
| `--yes` | | رد شدن از درخواست‌های تأیید |
| `--json` | | صادر کردن نتیجه deploy نهایی به عنوان `meridian.output/v1` envelope |
| `--events=jsonl` | | جریان رویدادهای پیشرفت تایپ‌شده به عنوان JSONL روی stderr |
| `--request FILE` | | خواندن بار JSON `deploy-request` از یک فایل؛ از `-` برای stdin استفاده کنید |
| `--dry-run` | | تأیید و برنامه‌ریزی deploy بدون SSH یا تغییر پنل |

**جریان Machine/UI**: `meridian api workflow deploy --json` یک قرارداد wizard قابل رندر کردن برمی‌گرداند. یک UI آن فیلدها را جمع‌آوری می‌کند، در برابر `deploy-request` تأیید می‌کند، سپس `meridian deploy --request deploy.json --json --events=jsonl` را اجرا می‌کند. deploy های Machine غیرتعاملی هستند: درخواست باید شامل `yes: true` باشد بعد از تأیید کاربر. `--dry-run --json` برنامه‌ریزی deploy را تحت `data` برمی‌گرداند تا یک UI بتواند حالت، پورت‌ها و مسیرهای تولید شده را مشاهده کند قبل از باز کردن SSH.

### meridian client

مدیریت کلیدهای دسترسی کلاینت و اطلاعات اتصال.

```
meridian client add NAME [NAME...]  [--json]
meridian client show NAME [--repair-page] [--json]
meridian client list [--json]
meridian client remove NAME [--yes] [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--json` | | صادر کردن نتیجه به عنوان `meridian.output/v1` envelope (تمام دستورات کلاینت) |
| `--yes`, `-y` | | رد شدن از تأیید حذف (بر روی `client remove`) |
| `--repair-page` | | فقط در استقرار legacy، صفحه اتصال گم‌شده را هنگام `client show` دوباره بسازید |

**`client add`** — چند نام را برای افزودن هم‌زمان چند کلاینت بدهید (مثل `meridian client add alice bob charlie`). با `--json`، آرایه `data.clients[]` همه ایجادهای موفق را نگه می‌دارد، حتی اگر ایجاد بعدی، تحویل اطلاعات اتصال یا ذخیره state محلی در legacy شکست بخورد. تغییرات انجام‌شده روی سرور حفظ می‌شوند، خطاهای قابل‌تلاش مجدد در `warnings[]` می‌آیند و دستور در حالت انسانی و JSON با کد `3` خارج می‌شود.

**`client list`** — با `--json`، برمی‌گرداند `data.summary` شمارش وضعیت و `data.clients[]` رکوردها با نام‌کاربری، UUID، وضعیت، شمارنده‌های ترافیق، زمان ایجاد و آخرین زمان دیدهشده.

**`client show`** — با `--json`، یک رکورد `data.client` و فراداده دسترس‌پذیری `data.handoff.*` را برمی‌گرداند. لینک صفحه اتصال فقط وقتی چاپ می‌شود که استقرار آن روی دیسک تأیید شده باشد؛ در legacy، `--repair-page` یک صفحه گم‌شده را به‌طور صریح بازسازی می‌کند. اگر این تعمیر شکست بخورد، شواهد client و handoff همچنان همراه با هشدار برگردانده می‌شوند و کد خروج `3` است. V4 صفحه سفارشی PWA ایجاد نمی‌کند و URL اشتراک رسمی پنل را نشان می‌دهد.

**`client enable`** — از سرکشی پیشین کلاینتی را از سر می‌گیرد تا بتوانند دوباره متصل شوند. با `--json`، برمی‌گرداند `data.client` با نام‌کاربری و وضعیت.

**`client disable`** — به‌صورت موقت یک کلاینت را معلق می‌کند. پیکربندی آن‌ها دست نخورده می‌ماند اما نمی‌توانند متصل شوند تا دوباره فعال شوند. با `--json`، برمی‌گرداند `data.client` با نام‌کاربری و وضعیت.

**مالکیت V4** — `client add` کاربران را به access intent اضافه می‌کند و همان intent را apply می‌کند. `list` و `show` فقط کاربران اعلام‌شده در intent را نمایش می‌دهند و `enable`/`disable` نیز فقط همان کاربران را می‌پذیرند. غیرفعال‌سازی V4 تا اجرای بعدی `meridian apply` موقت است. `client remove` در V4 رد می‌شود، چون بازنشستگی امن کاربر مدیریت‌شده هنوز پیاده‌سازی نشده است؛ برای قطع فوری و موقت از `client disable` استفاده کنید. رفتار batch نیمه‌موفق و صفحه‌های PWA بالا مربوط به legacy است.

**`client remove`** — اگر کاربر در پنل حذف شود اما ذخیره state محلی یا پاک‌سازی share-page در legacy کامل نشود، حذف یک نتیجه جزئی تایپ‌شده با کد خروج `3` است. خروجی JSON رکورد حذف‌شده را در `data.client` نگه می‌دارد و کارهای تکمیلی ناتمام را در `warnings[]` گزارش می‌کند.

**دستورهای تغییردهنده در حالت JSON** غیرتعاملی‌اند. `client remove --json` به `--yes` نیاز دارد؛ بدون آن Meridian به‌جای prompt یک خطای تایپ‌شده تأیید برمی‌گرداند.

### meridian server

مدیریت سرورهای شناخته‌شده.

```
meridian server add IP
meridian server list
meridian server remove NAME [--yes]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--name NAME` | (خودکار) | نام نمایش برای سرور |
| `--user/-u USER` | root | کاربر SSH روی سرور |
| `--ssh-port PORT` | 22 | پورت SSH سرور در صورت غیراستاندارد بودن |
| `--yes`, `-y` | | رد کردن تأیید حذف (برای `server remove`) |

`server add` پیش از ذخیره، SSH را تأیید می‌کند و نام، host و شناسه پایدار باید در registry یکتا باشند؛ profile موجود را نمی‌توان بی‌صدا به host دیگری متصل کرد. `server remove` به‌طور پیش‌فرض تأیید می‌خواهد و فقط profile محلی را حذف می‌کند. تا وقتی profile در legacy state یا V4 topology intent استفاده شود، دستور رد می‌شود؛ ابتدا نقش سرور را با setup/apply حذف کنید. `--yes` را فقط برای حذف غیرتعاملی ازپیش‌بازبینی‌شده استفاده کنید.

### meridian node

مدیریت نودهای خروجی اضافی در یک فلیت چندنودی. اولین سرور (میزبان پنل) با `meridian deploy` مستقر می‌شود؛ نودهای خروجی بعدی با `meridian node add` اضافه می‌شوند.

```
meridian node add IP [flags]
meridian node list
meridian --json node list
meridian node remove IP [--yes] [--force]
meridian node check IP
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--user USER` | root | کاربر SSH روی نود |
| `--ssh-port PORT` | 22 | پورت SSH روی نود (در صورت عدم استاندارد) |
| `--name NAME` | (خودکار، از IP) | نام دوستانه نشان‌داده‌شده در پنل / اشتراک |
| `--domain DOMAIN` | (هیچ) | دامنه هر نود برای fallback WSS/CDN |
| `--sni HOST` | www.microsoft.com | هدف پوشش Reality برای این نود |
| `--harden / --no-harden` | فعال | سخت‌سازی OS + SSH + فایروال برای نود |
| `--yes` | | رد شدن از تأیید `node add` یا `node remove` |
| `--force` | | در `node remove` ابتدا relayهای وابسته و سپس نود را حذف می‌کند |
| `--json` سراسری | | آن را پیش از `node` قرار دهید تا `node list` به صورت envelope نوع `meridian.output/v1` صادر شود |

**Legacy**: `meridian node add` میزبان نود را آماده و از طریق API پنل ثبت می‌کند، سپس ورودی‌های `reality`، `xhttp` و `hysteria2` (و با دامنه، `wss`) را می‌سازد. ورودی به `nodes[]` افزوده می‌شود و اگر `desired_nodes[]` فعال باشد، همگام می‌شود. `node remove` کانتینرها را پیش از حذف از پنل و کلاستر متوقف می‌کند؛ اگر relay وابسته باشد رد می‌شود و `--force` ابتدا همان relayها را حذف می‌کند.

**V4**: `node add` یک exit را به `topology_intent` اضافه و برنامه بازبینی‌شده را apply می‌کند. سخت‌سازی اجباری است و `--no-harden` رد می‌شود. بدون `--domain` فقط مسیر Reality اعلام می‌شود؛ دامنه مسیر TLS/WSS را نیز فعال می‌کند. `node remove` برای نود مدیریت‌شده V4 رد می‌شود؛ نقش exit یا routing-gateway را در `meridian setup` حذف و سپس برنامه را apply کنید.

**بررسی‌های سلامت**: `meridian node check` وضعیت پنل، SSH، کانتینر، پورت و TLS را بررسی می‌کند. در توپولوژی V4، پورت‌های عمومی تعریف‌شده در مسیرهای پروتکل بررسی می‌شوند، نه یک پورت ثابت 443. کد `0` یعنی سالم، `4` یعنی بررسی کامل شده و مشکل یافته است، و `3` یعنی شواهد الزامی در دسترس نیست. نبود ابزار بازرسی یا قطع اتصال SSH شواهد ناموجود محسوب می‌شود، نه نتیجه منفی.

**خروجی JSON**: `meridian --json node list` از `meridian.output/v1` envelope با `data.nodes[]` استفاده می‌کند که شامل ip، نام، uuid، وضعیت، xray_version و traffic_bytes می‌باشد. اگر وضعیت پنل در دسترس نباشد، نودهای پیکربندی‌شده با وضعیت `unknown` حفظ می‌شوند؛ Meridian هشدار می‌دهد و با کد `3` خارج می‌شود.

### meridian fleet

بررسی و تعمیر فلیت از API پنل زندگی.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet recover --legacy --panel-url URL [--api-token-file FILE]
                       [--profile NAME_OR_UUID] [--squad NAME_OR_UUID]
                       [--panel-node SELECTOR]
                       [--panel-server IP] [--user USER] [--ssh-port PORT] [--force]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--json` | | صادر کردن وضعیت فلیت به JSON (برای skript سازی / CI) |
| `--panel-url URL` | الزامی | URL کامل HTTPS پنل، شامل مسیر محرمانه آن |
| `--api-token-file FILE` | prompt امن یا environment | خواندن token از فایل mode-600 به‌جای `MERIDIAN_API_TOKEN` یا prompt |
| `--profile NAME_OR_UUID` | خودکار اگر یکتا باشد | انتخاب دقیق profile قدیمی وقتی پنل بیش از یک profile دارد |
| `--squad NAME_OR_UUID` | خودکار اگر فقط یکی واجد شرایط باشد | انتخاب squad دسترسی legacy وقتی چند squad همه inboundهای بازیابی‌شده را مجاز می‌کنند |
| `--panel-node SELECTOR` | خودکار اگر بدون ابهام باشد | مشخص کردن میزبان پنل با نام دقیق نود، UUID یا آدرس |
| `--panel-server IP` | IP موجود در URL پنل | IPv4/IPv6 عمومی SSH میزبان Remnawave؛ وقتی URL پنل دامنه است الزامی است |
| `--user`, `-u USER` | root | کاربر SSH سرور عمومی پنل |
| `--ssh-port PORT` | 22 | پورت SSH سرور عمومی پنل، بین 1 تا 65535 |
| `--legacy` | الزامی | تأیید اینکه هدف یک استقرار legacy با profile مشترک است |
| `--force` | | پشتیبان‌گیری و جایگزینی `cluster.yml` محلی موجود |

**`fleet status`** — سلامت پنل، heartbeat نودها، listenerهای relay عمومی، hopهای داخلی relay در V4 و تعداد کاربران مدیریت‌شده را نشان می‌دهد. کاربران دسترسی اعلان‌شده V4 که وجود ندارند یا فعال نیستند، سلامت را degraded می‌کنند و در `data.summary.missing_access_users` و `data.summary.nonactive_access_users` گزارش می‌شوند. در دسترس بودن listener یک relay فقط شواهد TCP است؛ بنابراین هر مسیر relay که از راه دیگری تأیید نشده باشد، سلامت فلیت را `unknown` نگه می‌دارد. برای تأیید کامل مسیر رمزگذاری‌شده `meridian test` را اجرا کنید. کد خروج `0` یعنی کاملاً مشاهده‌شده و سالم، `4` یعنی degraded و `3` یعنی unknown یا unavailable.

**`fleet inventory`** — پنل پیکربندی‌شده، نودها، relayها، topology مطلوب و وضعیت زنده نودهای پنل را در صورت دسترسی نشان می‌دهد. اگر شواهد زنده پنل در دسترس نباشد، inventory پیکربندی‌شده در نتیجه حفظ می‌شود، sourceهای مربوطه `unavailable` علامت می‌خورند، هشدارها کمبود شواهد را توضیح می‌دهند و دستور به‌جای حذف داده‌های جزئی با کد `3` خارج می‌شود. هرگز توکن API پنل یا مسیرهای مخفی URL را چاپ نمی‌کند. دسترسی پایدار به فیلدهای داخل `data` شامل `data.sources.*`، `data.servers[].roles`، `data.summary.*`، `data.nodes[].desired`، `data.nodes[].protocols`، `data.relays[].exit_node_*` و `data.desired_nodes[].present` است. فیلدهای حضور inventory حقیقت همگرایی نیستند؛ برای تصمیم‌گیری درباره drift و apply از `plan --json` استفاده کنید.

**`fleet recover`** — فقط یک profile مشترک legacy و بدون ابهام را از پنل زنده import می‌کند. `--legacy` الزامی است و `--panel-url` باید URL کامل HTTPS همراه با secret path واقعی باشد، نه ریشه سایت. Recovery، access squad و panel node را اثبات می‌کند، share path را با SSH می‌خواند، `servers.json` را دوباره پر می‌کند و metadata مربوط به WSS را نمی‌پذیرد مگر اینکه دقیقاً یک domain معتبر با اطمینان قابل تخصیص باشد. URL دامنه پنل به `--panel-server` نیاز دارد. V4 intent از پنل قابل بازسازی نیست؛ backup را بازیابی کنید یا دوباره `meridian setup` را اجرا کنید. توکن از prompt امن، `MERIDIAN_API_TOKEN` یا فایل mode-600 خوانده می‌شود. state محلی موجود فقط با `--force` و پس از تهیه backup جایگزین می‌شود.

### meridian api

قرارداد meridian-core قابل خواندن ماشین را بررسی کنید که توسط خروجی JSON و کلاینت‌های UI آینده استفاده می‌شود.

```
meridian api schemas [--json] [--include-schemas]
meridian api commands [--json] [--include-schemas]
meridian api schema NAME [--envelope|--json]
meridian api workflow NAME [--json]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--json` | | صادر کردن کاتالوگ schema به عنوان `meridian.output/v1` envelope |
| `--include-schemas` | | شامل کردن JSON Schemaهای کامل و فعال‌کردن خودکار خروجی JSON envelope برای `api schemas` یا `api commands` |
| `--envelope`, `--json` | | بسته‌بندی `api schema NAME` در یک `meridian.output/v1` envelope به جای چاپ JSON Schema خام |

**`api schemas`** — نام‌های schema پایدار را فهرست می‌کند مثل `output-envelope`، `apply-envelope`، `client-list-envelope`، `client-show-envelope`، `deploy-envelope`، `deploy-command-data`، `deploy-request`، `deploy-workflow-answers`، `deploy-result`، `deploy-plan`، `workflow-plan`، `input-field`، `remote-target`، `command-spec`، `remote-command-result`، `plan-envelope`، `fleet-status-envelope`، `fleet-inventory-envelope`، `event`، `apply`، `plan-result`، `fleet-status` و `fleet-inventory`. schema‌های envelope دستور یک ورودی `commands` در کاتالوگ شامل می‌کند.

**`api commands`** — قراردادهای دستور را با `command`، `argv`، `envelope_schema`، `data_schema`، `statuses` ممکن، `outcomes` ساختاری، معانی exit-code، پرچم‌های ماشین، پایداری و `interrupt_behavior` فهرست می‌کند. `statuses` و `outcomes` فقط envelopeهای JSON تکمیل‌شده را توصیف می‌کنند. مقدار `interrupt_behavior: "exit_130_without_envelope"` یعنی Ctrl-C/SIGINT با کد `130` خارج می‌شود و هیچ JSON روی stdout تضمین نمی‌شود. پیش از اتصال UI از این کاتالوگ برای انتخاب schema بار دستور استفاده کنید. `deploy` پرچم‌های `--json`، `--events=jsonl`، `--request` و `--dry-run` را اعلام می‌کند.

**`api schema NAME`** — یک JSON Schema را چاپ می‌کند. مثال: `meridian api schema output-envelope`.

**`api workflow NAME`** — برنامه‌ریزی workflow قابل رندر کردن برای UI را چاپ می‌کند. مثال: `meridian api workflow deploy --json` بخش‌های wizard deploy و فیلدها را برمی‌گرداند.

### meridian studio

Meridian Studio و Engine API محلی آن را باز می‌کند. سرور فقط روی `127.0.0.1` گوش می‌دهد.

```
meridian studio [--port PORT] [--assets-dir DIRECTORY] [--no-open]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|---------|
| `--port PORT` | 0 | پورت localhost از 0 تا 65535؛ مقدار `0` یک پورت آزاد بالا انتخاب می‌کند |
| `--assets-dir DIRECTORY` | فایل‌های داخلی | استفاده از پوشه فایل‌های build‌شده Studio به‌جای نسخه داخل بسته |
| `--no-open` | | نمایش URL محلی بدون باز کردن مرورگر |

اگر پورت اشغال باشد یا assetهای داخلی/صریح موجود نباشند، Studio با خطای روشن متوقف می‌شود. پوشه asset صریح باید `studio/index.html` داشته باشد.

### meridian relay

مدیریت نودهای relay — دستگاه‌های ارسال TCP سبکی که ترافیک را از طریق یک سرور داخلی به سرور خروجی در خارج از کشور منتقل می‌کنند.

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT]
meridian --json relay list [--exit EXIT]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--exit/-e EXIT` | (الزامی برای deploy) | IP یا نام سرور خروجی |
| `--name NAME` | (خودکار) | نام دوستانه برای relay (مثلاً "ru-moscow") |
| `--port/-p PORT` | 443 | پورت شنوندگی روی سرور relay |
| `--sni HOST` | (خودکار) | هدف استتار Reality مخصوص relay |
| `--user/-u USER` | root | کاربر SSH روی relay |
| `--ssh-port PORT` | 22 | پورت SSH روی سرور relay (در صورت عدم استاندارد) |
| `--yes/-y` | | رد شدن از درخواست‌های تأیید |
| `--json` سراسری | | آن را پیش از `relay` قرار دهید تا `relay list` به صورت envelope نوع `meridian.output/v1` صادر شود |

**Legacy**: اگر `--sni` حذف شود، `relay deploy` با RealiTLScanner یک هدف محلی انتخاب می‌کند. Realm فقط Reality را منتشر می‌کند، چون مسیر اختصاصی SNI به inbound Reality ختم می‌شود. `relay check` سرویس، مسیر TCP به exit، listener عمومی و Hostهای پنل را بررسی می‌کند؛ `0` سالم، `4` مشکل و `3` نبود شواهد الزامی است. نبود ابزار راه‌دور، پایان مهلت دستور یا قطع اتصال SSH با کد `3` گزارش می‌شود، نه به‌عنوان مشکل قطعی با کد `4`. `relay remove` سرویس، binary/config، قانون دقیق UFW، routing روی exit و Hostهای پنل را پاک می‌کند و تا تکمیل پاک‌سازی state محلی را نگه می‌دارد.

**V4**: `relay deploy` زنجیره را به intent اضافه و apply می‌کند؛ `--exit` باید یک exit موجود V4 را مشخص کند و SNI خالی مسیر Reality خروجی را به ارث می‌برد. `relay check` و `relay remove` برای زنجیره مدیریت‌شده رد می‌شوند. سلامت انتهابه‌انتها را با `meridian test` بررسی کنید و حذف زنجیره را در `meridian setup` بازبینی و apply کنید.

**خروجی JSON**: `meridian --json relay list` از `meridian.output/v1` envelope با `data.relays[]` استفاده می‌کند.

### meridian plan

برنامه‌ریزی تطابق را نشان دهید — آنچه `meridian apply` برای همگرایی کلاستر به وضعیت مطلوب اعلام شده در `cluster.yml` انجام می‌دهد.

در V4، `topology_intent` کامپایل‌شده‌ای که setup ساخته است مرجع اصلی است. خروجی انسانی hash برنامه و شمار منابع کامپایل‌شده را نشان می‌دهد و سپس تعمیرهای لازم، مشاهده‌های در دسترس نبودنی و تغییرات state نسل ذخیره‌شده را گزارش می‌کند. داده JSON شامل `plan_hash`، منابع `resources[]` با hash مطلوب، `drifted_resources[]`، `observation_errors[]` و `state_changes[]` است و از نمادهای عمومی Terraform استفاده نمی‌کند.

در legacy، منابع اختیاری `desired_nodes`، `desired_relays`، `desired_clients` و `subscription_page` ورودی مطلوب هستند. فقط plan در legacy تفاوت actionها را به سبک Terraform با `+` برای افزودن، `-` برای حذف و `~` برای به‌روزرسانی چاپ می‌کند.

```
meridian plan [--json]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--json` | | صادر کردن برنامه‌ریزی به عنوان `meridian.output/v1` JSON envelope برای CI/CD و کلاینت‌های UI. exit code‌های یکسان؛ خروجی برنامه‌ریزی انسانی سرکوب می‌شود |

**Exit codes**:
- `0` — همگرا (هیچ تغییری نیازی نیست)
- `2` — تغییرات معلق (اجرای `meridian apply` برای همگرایی)
- `3` — شواهد مشاهده الزامی در دسترس نیست؛ از جمله وقتی بررسی صفحه اشتراک legacy از راه SSH timeout شود، ارتباط قطع شود یا دستور بررسی قابل اجرا نباشد
- خطاها نیز از exit‌های غیرصفر استفاده می‌کند؛ کلاینت‌های فرایند باید `status` و `errors[].category` JSON را به عنوان معتبر بپذیرند زیرا `2` می‌تواند زمانی که `status` برابر `failed` است یعنی خطای کاربر/پیکربندی نیز باشد

**شکل JSON در legacy** (`--json` mode):
```json
{
  "schema": "meridian.output/v1",
  "meridian_version": "4.x.x",
  "command": "plan",
  "operation_id": "9d0f...",
  "started_at": "2026-05-04T21:00:00Z",
  "duration_ms": 128,
  "status": "changed",
  "exit_code": 2,
  "summary": {
    "text": "Plan: 1 to add, 1 to remove",
    "changed": true,
    "counts": {"actions": 2, "adds": 1, "updates": 0, "replacements": 0, "removes": 1,
               "destructive": 1, "from_extras": 1}
  },
  "data": {
    "converged": false,
    "summary": "Plan: 1 to add, 1 to remove",
    "exit_code": 2,
    "counts": {"actions": 2, "adds": 1, "updates": 0, "replacements": 0, "removes": 1,
               "destructive": 1, "from_extras": 1},
    "actions": [
      {"plan_index": 0, "execution_order": 2, "kind": "add_client", "operation": "add", "resource_type": "client",
       "resource_id": "alice", "target": "alice", "detail": "create client alice",
       "phase": "provision", "requires_confirmation": false,
       "destructive": false, "replacement": false, "replacement_strategy": "none",
       "destructive_reason": "", "from_extras": false,
       "change_set": [], "symbol": "+", "can_run_parallel": false},
      {"plan_index": 1, "execution_order": 1, "kind": "remove_client", "operation": "remove", "resource_type": "client",
       "resource_id": "ghost", "target": "ghost", "detail": "delete client ghost",
       "phase": "deprovision", "requires_confirmation": true,
       "destructive": true, "replacement": false, "replacement_strategy": "none",
       "destructive_reason": "delete client ghost",
       "from_extras": true, "change_set": [], "symbol": "-", "can_run_parallel": false}
    ]
  },
  "warnings": [],
  "errors": []
}
```

در نتیجه legacy، `data.actions[].from_extras: true` منابعی را پرچم می‌کند که روی پنل موجود اما از `cluster.yml` گم شده‌اند — ورودی‌هایی که `meridian apply --prune-extras` روی آن‌ها کار می‌کند. `execution_order` ترتیبی را نشان می‌دهد که `apply` استفاده می‌کند و برای ایمنی جایگزینی ممکن است با ترتیب نمایش متفاوت باشد. `operation: "replace"` جایگزینی‌های مخرب مانند تجهیز دوباره relay را علامت می‌زند. در هر دو قرارداد plan، هنگام همگرایی `status` برابر `no_changes` و هنگام وجود کار برای apply برابر `changed` است؛ شکست مشاهده با `failed` و کد `3` گزارش می‌شود. `data.exit_code` کد خروج فرایند را منعکس می‌کند.

برای گردش setup/plan/apply در V4، [گردش‌کار اعلانی](/docs/fa/getting-started/#گردشکار-اعلانی) را ببینید.

### meridian apply

کلاستر را به وضعیت مطلوب اعلام‌شده در `cluster.yml` همگرا کنید. Apply در legacy برنامه actionها را اجرا می‌کند، تفاوت را نشان می‌دهد، تأیید می‌گیرد و actionها را به ترتیب وابستگی اجرا می‌کند. V4 گراف منابع کامپایل‌شده و بازبینی‌شده را اعمال و hash برنامه، نسل و وضعیت مشاهده هر منبع را گزارش می‌کند.

```
meridian apply [--yes] [--parallel N] [--prune-extras=ask|yes|no] [--json]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--yes`, `-y` | | رد شدن از درخواست‌های تأیید |
| `--parallel N` | 4 | فقط legacy: بین 1 تا 32 نخ تجهیز موازی نود (هر کدام نشست SSH و کلاینت پنل خود را می‌گیرد) |
| `--prune-extras` | `ask` | فقط legacy: نحوه برخورد با منابع موجود در پنل و غایب از `cluster.yml`. مقدار `ask` برای هر منبع پرسش می‌کند (با `--yes` برای ایمنی به `no` تبدیل می‌شود)؛ `yes` خودکار حذف می‌کند و `no` رد می‌شود و خلاصه یک‌خطی چاپ می‌کند |
| `--json` | | صادر کردن `meridian.output/v1` نتیجه apply نهایی با وضعیت اجرای هر اقدام |

V4 پرچم‌های `--yes` و `--json` را می‌پذیرد. تنظیم غیراصلی گزینه‌های مخصوص legacy (`--parallel` به‌جز `4` یا `--prune-extras` به‌جز `ask`) پیش از شروع apply به‌عنوان خطای کاربر با کد `2` رد می‌شود.

V4 در سراسر apply، checkpointهای محلی دارای برچسب provenance را ذخیره می‌کند. اگر ذخیره checkpoint یا state همگرایی شکست بخورد، Meridian با کد `3` یک خطای system برمی‌گرداند و هشدار می‌دهد که state راه‌دور ممکن است تغییر کرده باشد. محل ذخیره state محلی را تعمیر کنید و سپس `meridian plan` و `meridian apply` را دوباره اجرا کنید تا نتیجه مشاهده و همگرا شود.

اقدامات مخرب (حذف‌ها، تجدید تجهیز UPDATE_RELAY) هشدار چاپ می‌کند و تأیید جداگانه‌ای نیاز دارد. شکست اولی در برنامه‌ریزی اقدامات مخرب باقی را رد می‌کند — `cluster.yml` صادق می‌ماند.

در خروجی `--json` مربوط به legacy، `data.plan` برنامه تایپ‌شده و `data.actions[]` نتایج اجرا با `status: "succeeded" | "failed" | "skipped"` را دارد. V4 در عوض actionهای کامپایل‌شده با `status: "converged" | "applied" | "failed" | "unknown" | "skipped"` برمی‌گرداند. حالت JSON غیرتعاملی است: اگر تغییرات تأیید بخواهند و `--yes` غایب باشد، Meridian خطای `MERIDIAN_CONFIRMATION_REQUIRED` را همراه با برنامه محاسبه‌شده یا پیش‌نمایش برنامه کامپایل‌شده برمی‌گرداند. drift فقط پنل در legacy با `--prune-extras=ask` خطای `MERIDIAN_DRIFT_DECISION_REQUIRED` می‌دهد؛ برای حفظ drift از `--prune-extras=no` و برای حذف آن از `--prune-extras=yes` استفاده کنید. اگر actionهای legacy اجرا شوند اما ذخیره state پس از تغییر، از جمله به‌دلیل ویرایش هم‌زمان `cluster.yml`، شکست بخورد، envelope نتایج actionها را نگه می‌دارد و با کد `3` و `MERIDIAN_STATE_SAVE_FAILED` برمی‌گردد. قرارداد JSON اجرا را گزارش می‌کند و عملیات مخرب را تراکنشی نمی‌سازد. کلاینت‌های UI باید actionهای ناموفق یا ردشده را بررسی و پس از رفع مشکل، عملیات را به‌شکل idempotent دوباره اجرا کنند.

**مثال legacy برای drift:** اگر `cluster.yml` شامل `desired_clients: ['alice']` باشد اما پنل `bob` را نیز داشته باشد، `meridian plan` عبارت `- remove client: bob` را نشان می‌دهد. `--prune-extras=ask` تصمیم صریح می‌خواهد؛ `--yes --prune-extras=yes` extra را حذف می‌کند و `--yes` بدون تصمیم صریح آن را رد می‌کند.

### meridian preflight

اعتبارسنجی سرور پیش از استقرار. SNI، پورت‌ها، DNS، سیستم‌عامل، دیسک و ساعت را بدون نصب چیزی بررسی می‌کند. DNS از resolver تنظیم‌شدهٔ خود سرور استفاده می‌کند و Meridian نشانی سرور را به سرویس ASN شخص ثالث نمی‌فرستد. کد خروج `0` یعنی همهٔ بررسی‌ها موفق بوده‌اند، `4` یعنی موارد قابل‌اقدام پیدا شده‌اند و `3` یعنی شواهد الزامی در دسترس نبوده‌اند.

```
meridian preflight [IP] [--domain NAME] [--sni HOST] [--user USER]
                   [--ai] [--server NAME]
```

`--domain` بررسی می‌کند دامنه به سرور هدف resolve شود. `--sni` هدف استتار را جایگزین می‌کند، `--user` کاربر SSH ذخیره‌شده را تغییر می‌دهد و `--ai` یک گزارش تشخیصی آماده برای AI می‌سازد.

### meridian scan

یافتن هدف‌های SNI بهینه در شبکهٔ سرور با نسخهٔ ثابت RealiTLScanner. Meridian پیش از اجرا SHA-256 منتشرشده را بررسی می‌کند و پس از اسکن فضای کاری ایزولهٔ راه‌دور را حذف می‌کند.

```
meridian scan [IP] [--user USER] [--server NAME]
```

RealiTLScanner فقط IPv4 را پشتیبانی می‌کند؛ هدف IPv6-only با کد `3` غیرقطعی پایان می‌یابد. هدف انتخاب‌شده فقط برای نود legacy ثبت‌شده ذخیره می‌شود. در V4، Meridian انتخاب را گزارش می‌کند اما intent بازبینی‌شده را تغییر نمی‌دهد؛ آن را با `meridian setup` اعمال کنید. پاسخ خالی کد `0`، انتخاب نامعتبر کد `2`، نبود نامزد کد `4` و خطای scanner یا دانلود کد `3` برمی‌گرداند.

### meridian test

تست دسترسی پروکسی و تأیید اتصالات واقعی از دستگاه کلاینت. SSH نیازی نیست.

آزمون کامل پیش‌فرض، یک کلاینت فعال را طبق ترتیبی ثابت انتخاب می‌کند، JSON دقیق اشتراک Xray را که Remnawave تحویل می‌دهد دریافت می‌کند و fallback خودکار به‌همراه تک‌تک outboundهای پشتیبانی‌شده برای هدف انتخاب‌شده را اجرا می‌کند. در V4 انتخاب فقط به کاربران `topology_intent.access.users` محدود است؛ service userها نه به‌صورت صریح و نه از مسیر fallback انتخاب نمی‌شوند. اجرای نخست ممکن است برای دانلود و تأیید runtime ثابت Xray از GitHub تا 120 ثانیه زمان ببرد. بررسی‌های ترافیک چند IP observer مستقل را می‌چرخانند و نتیجه را با یک درخواست control مستقیم مقایسه می‌کنند؛ بنابراین خرابی observer به‌اشتباه شکست protocol گزارش نمی‌شود. برای محیط‌های کنترل‌شده می‌توان observer را با `MERIDIAN_CONNECT_TEST_URL` جایگزین کرد.

`--client` یک کلاینت فعال و مدیریت‌شده مشخص را انتخاب می‌کند. `--basic` اجرا را به مشاهده‌های شبکه/TLS محدود می‌کند و نمی‌تواند ترافیک فقط-UDP را تأیید کند. `--basic` و `--client NAME` ناسازگارند؛ استفاده هم‌زمان از آن‌ها پیش از هر I/O شبکه با خطای کاربر و کد `2` متوقف می‌شود. مقدار پیش‌فرض `--timeout` برابر `5` ثانیه است و بازه `1` تا `30` را می‌پذیرد؛ این مقدار هر عملیات شبکه را جدا از bootstrap بار اول Xray محدود می‌کند.

در آزمون کامل، خطای transport پنل، پاسخ HTTP `429` یا HTTP `5xx` یعنی شواهد اشتراک رسمی در دسترس نیست (`PANEL_UNAVAILABLE`) و نتیجه با کد `3` غیرقطعی است. در مقابل، رد قطعی و غیرمرتبط با authentication درخواست اشتراک با HTTP `4xx`، مانند `400` یا `422` و به‌جز `429`، یک یافته منفی تکمیل‌شده (`PANEL_REQUEST_FAILED`) است و با کد `4` خارج می‌شود.

```
meridian test [IP|DOMAIN] [--server NAME] [--domain NAME] [--sni NAME]
              [--client NAME] [--basic] [--timeout SECONDS] [--json]
```

کد خروج `0` یعنی همه بررسی‌های الزامی موفق بوده‌اند، `4` یعنی آزمون با یافته‌های منفی به پایان رسیده است و `3` یعنی شواهد الزامی در دسترس نبوده است. `--json` خروجی را در پوشش نوع‌دار `meridian.output/v1` همراه با همه بررسی‌ها و یافته‌ها ارائه می‌دهد.

### meridian probe

سرور را از دید یک سامانه سانسور بررسی کنید تا مشخص شود آیا استقرار قابل شناسایی است. SSH لازم نیست. روی IP یا دامنه دلخواه کار می‌کند و هنگامی که هدف بخشی از توپولوژی ذخیره‌شده Meridian باشد، به‌طور خودکار سیاست سخت‌گیرانه‌تری اعمال می‌کند.

بررسی‌ها بر اساس شکل واقعی استقرار انتخاب می‌شوند: در معرض‌بودن پورت‌های عمومی و داخلی، رفتار HTTP/TLS، سازگاری و استتار SNI، مسیرهای متداول پروکسی/پنل، ارتقاهای WebSocket، DNS معکوس، HTTP/2، نسخه‌های قدیمی TLS، مسیرهای ریشه سخت‌سازی‌شده و دامنه‌های پیکربندی‌شده. `--sni` مقدار TLS SNI را برای هدف‌های عمومی بازنویسی می‌کند. شنونده‌های مختص UDP از یک دیتاگرام خالی حدس زده نمی‌شوند؛ probe آن‌ها را نیازمند آزمون کامل اتصالِ مرجع گزارش می‌کند.

```
meridian probe [IP|DOMAIN] [--server NAME] [--sni NAME]
               [--timeout SECONDS] [--json]
```

Probe از همان قرارداد خروج `test` استفاده می‌کند: `0` یعنی موفق، `4` یعنی تکمیل‌شده با یافته‌های منفی و `3` یعنی غیرقطعی. `--timeout` بین 1 و 30 ثانیه و برای هر عملیات است؛ TARGET و `--server` متقابلاً انحصاری‌اند. مشاهده‌های ناقص و تفاوت certificate میان edgeهای معتبر CDN به‌جای شکست، غیرقطعی گزارش می‌شوند؛ trust یا hostname نامعتبر همچنان شکست است. شواهد بررسی‌نشده هرگز موفقیت محسوب نمی‌شود.

### meridian doctor

جمع‌آوری تشخیص‌های سیستم برای اشکال‌زدایی. نام‌مستعار: `meridian rage`.

```
meridian doctor [IP] [--sni HOST] [--user USER] [--ai] [--server NAME]
```

`--sni` هدف استتار مورد تشخیص را انتخاب می‌کند، `--user` کاربر SSH ذخیره‌شده را تغییر می‌دهد و `--ai` گزارش پاک‌سازی‌شده و آماده برای AI را کپی می‌کند.

Doctor بخش‌ها و پورت‌های listener را از نقش‌های سرور و پورت‌های عمومی پیکربندی‌شده V4 استخراج می‌کند. نبود ابزار بازرسی یا قطع اتصال SSH گزارش را غیرقطعی می‌کند و کد `3` می‌دهد؛ بخش‌هایی که جمع‌آوری شده‌اند همچنان نمایش داده می‌شوند.

### meridian teardown

حذف proxy از سرور.

```
meridian teardown [IP] [--user USER] [--server NAME] [--yes]
```

`--yes` تأیید عملیات مخرب را رد می‌کند و `--user` کاربر SSH ذخیره‌شده را تغییر می‌دهد. انتخاب هدف غیرتعاملی است: `--server` ناشناخته، یا نبودن یا چندمعنا بودن هدف ضمنی، بدون درخواست IP با کد `2` خارج می‌شود. رد کردن تأیید عملیات با کد `1` خارج می‌شود. در V4، teardown تا وقتی سرور نقش control، exit، routing-gateway، relay hop یا workload فعال دارد رد می‌شود؛ ابتدا نقش را در setup حذف و plan را apply کنید. میزبان پنل تا وقتی نود یا relay دیگری باقی مانده باشد حذف نمی‌شود. هدف relay از مسیر پاک‌سازی relay عبور می‌کند و profile سرور فقط پس از پاک‌سازی موفق حذف می‌شود.

### meridian update

به‌روزرسانی CLI به آخرین نسخه PyPI. کد `0` یعنی نسخه از قبل جدید بوده یا با موفقیت به‌روزرسانی شده است؛ کد `3` یعنی بررسی نسخه یا ارتقا شکست خورده است.

```
meridian update
```

## گزینه‌های سراسری

گزینه‌های سراسری باید پیش از دستور بیایند؛ برای نمونه `meridian --quiet doctor`. گزینه‌های محلی دستور مانند `test --json` پس از دستور قرار می‌گیرند.

| پرچم | توضیح |
|------|-------|
| `--version`, `-v` | نمایش نسخه CLI و خروج |
| `--verbose` | فعال کردن گزارش‌های اشکال‌زدایی |
| `--quiet`, `-q` | پنهان کردن خروجی پیشرفت |
| `--json` | درخواست envelope تایپ‌شده JSON از دستور پشتیبانی‌شده؛ دستورهای پشتیبانی‌نشده صریحاً خطا می‌دهند |
| `--install-completion` | نصب تکمیل خودکار برای shell جاری |
| `--show-completion` | چاپ اسکریپت تکمیل خودکار برای shell جاری |

`--json` سراسری برای deploy، plan، apply، test، probe، همه دستورهای client، دستورهای fleet status/inventory، node/relay list و API پشتیبانی می‌شود. برای دستورهایی که `--json` محلی دارند، می‌توان آن را پس از دستور نیز نوشت. قراردادهای ماشینی پایدار را با `meridian api commands --json` ببینید.

## گزینه‌های مشترک سرور

این نام‌ها در دستورهای مرتبط با سرور تکرار می‌شوند، اما هر دستور فقط گزینه‌های بخش خودش را می‌پذیرد:

| پرچم | دستورها |
|------|---------|
| `--server NAME` | deploy، preflight، scan، test، probe، doctor، teardown |
| `--user`, `-u USER` | deploy، node add/check، دستورهای relay، preflight، scan، doctor، teardown |
| `--sni HOST` | deploy، node add، relay deploy، preflight، test، probe، doctor |
| `--domain DOMAIN` | deploy، node add، preflight، test |

دستورهای client مستقیماً روی پنل کلاستر کار می‌کنند و `--server` را نمی‌پذیرند. `test` و `probe` روی دستگاه کلاینت اجرا می‌شوند و از SSH استفاده نمی‌کنند.

## تعریف سرور

دستورهایی که از resolver مشترک SSH استفاده می‌کنند این اولویت را دنبال می‌کنند:
1. استدلال IP صریح یا کلمات کلیدی `local` (deploy روی این سرور بدون SSH)
2. پرچم `--server NAME` (همچنین `--server local` را قبول می‌کند)
3. تشخیص حالت محلی (اجرا روی خود سرور)
4. انتخاب خودکار سرور تک (اگر فقط یکی ذخیره شده باشد)

اگر هیچ سروری یا چند سرور قابل انتخاب باشد، همه دستورها از جمله `teardown` با کد `2` متوقف می‌شوند و نام صریح می‌خواهند؛ resolver مشترک prompt باز نمی‌کند. `test` و `probe` resolver خارجی خود را دارند: TARGET یا `--server`، نه هر دو؛ سپس local detection، سپس تنها profile ذخیره‌شده، و در حالت صفر/چند profile خطا.
