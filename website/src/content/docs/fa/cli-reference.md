---
title: مرجع CLI
description: مرجع کامل برای تمام دستورات و پرچم‌های Meridian CLI.
order: 10
section: reference
---

## دستورات

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
| `--pq / --no-pq` | غیرفعال | رمزنگاری پساکوانتومی — ML-KEM-768 ترکیبی (آزمایشی) |
| `--warp / --no-warp` | غیرفعال | مسیریابی ترافیک خروجی از طریق Cloudflare WARP |
| `--server NAME` | | سرور هدف (نام یا IP) |
| `--decoy MODE` | نادیده گرفته می‌شود | پاسخ فریبنده؛ مسیرهای نشناخته‌شده همیشه از پاسخ سخت‌سازی‌شده استفاده می‌کنند |
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
meridian client show NAME [--json]
meridian client list [--json]
meridian client remove NAME [--yes] [--json]
meridian client enable NAME [--json]
meridian client disable NAME [--json]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--json` | | صادر کردن نتیجه به عنوان `meridian.output/v1` envelope (تمام دستورات کلاینت) |
| `--yes`, `-y` | | رد شدن از تأیید حذف (بر روی `client remove`) |

**`client add`** — عبور دادن نام‌های متعدد برای افزودن چندین کلاینت به‌طور همزمان (مثل `meridian client add alice bob charlie`). با `--json`، برمی‌گرداند `data.clients[]` با نام‌کاربری، UUID و وضعیت برای هر کلاینت ایجادشده.

**`client list`** — با `--json`، برمی‌گرداند `data.summary` شمارش وضعیت و `data.clients[]` رکوردها با نام‌کاربری، UUID، وضعیت، شمارنده‌های ترافیق، زمان ایجاد و آخرین زمان دیدهشده.

**`client show`** — با `--json`، برمی‌گرداند یک `data.client` رکورد به‌علاوه `data.handoff.*` فراداده‌های دسترسی‌پذیری. دستور انسانی هنوز هم URL‌های اشتراک/اشتراک‌گذاری قابل استفاده را چاپ می‌کند.

**`client enable`** — از سرکشی پیشین کلاینتی را از سر می‌گیرد تا بتوانند دوباره متصل شوند. با `--json`، برمی‌گرداند `data.client` با نام‌کاربری و وضعیت.

**`client disable`** — به‌صورت موقت یک کلاینت را معلق می‌کند. پیکربندی آن‌ها دست نخورده می‌ماند اما نمی‌توانند متصل شوند تا دوباره فعال شوند. با `--json`، برمی‌گرداند `data.client` با نام‌کاربری و وضعیت.

### meridian server

مدیریت سرورهای شناخته‌شده.

```
meridian server add [IP]
meridian server list
meridian server remove NAME
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--name NAME` | (خودکار) | نام نمایش برای سرور |

### meridian node

مدیریت نودهای خروجی اضافی در یک فلیت چندنودی. اولین سرور (میزبان پنل) با `meridian deploy` مستقر می‌شود؛ نودهای خروجی بعدی با `meridian node add` اضافه می‌شوند.

```
meridian node add IP [flags]
meridian node list [--json]
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
| `--warp / --no-warp` | غیرفعال | مسیریابی ترافیق خروجی از طریق Cloudflare WARP روی این نود |
| `--harden / --no-harden` | فعال | سخت‌سازی OS + SSH + فایروال برای نود |
| `--yes` | | رد شدن از درخواست‌های تأیید (بر روی `node remove`) |
| `--force` | | روی `node remove`، ادامه حتی اگر relay‌ها این نود را به عنوان خروجی خود ارجاع دهند |
| `--json` | | صادر کردن `node list` به عنوان `meridian.output/v1` envelope |

**نحوه کار**: `meridian node add` نود میزبان را فراهم می‌کند (بسته‌های OS، Docker، nginx، TLS، کانتینر نود Remnawave)، نود را در برابر REST API پنل ثبت می‌کند و ورودی‌های `reality` و `xhttp` میزبان ایجاد می‌کند تا کلاینت‌ها خروجی جدید را در تازه‌سازی اشتراک بعدی دریافت کنند. ورودی جدید به `nodes[]` در `cluster.yml` اضافه می‌شود؛ `desired_nodes[]` نیز به‌روز می‌شود اگر آن لیست غیرخالی باشد (تزامن هیبریدی). منطق استقرار نود در `node_deploy.py` زندگی می‌کند.

**بررسی‌های سلامت**: `meridian node check` وضعیت پنل، SSH، کانتینر، پورت و بررسی‌های TLS را اجرا می‌کند. وقتی یک بررسی شکست می‌خورد، یک نکته اصلاح را چاپ می‌کند (مثل `Run: docker compose up -d`).

**حذف**: `meridian node remove` با SSH در نود وارد می‌شود تا کانتینر‌ها را متوقف کند قبل از حذف نود از کلاستر و پنل. نود را حذف کردن را نمی‌پذیرد اگر هنوز `exit_node` یک یا چند relay باشد؛ از `--force` برای نادیده گرفتن استفاده کنید.

**خروجی JSON**: `node list --json` از `meridian.output/v1` envelope با `data.nodes[]` استفاده می‌کند که شامل ip، نام، uuid، وضعیت، xray_version و traffic_bytes می‌باشد.

### meridian fleet

بررسی و تعمیر فلیت از API پنل زندگی.

```
meridian fleet status [--json]
meridian fleet inventory [--json]
meridian fleet recover --panel-url URL --api-token TOKEN
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--json` | | صادر کردن وضعیت فلیت به JSON (برای skript سازی / CI) |
| `--panel-url URL` | الزامی | URL HTTPS پنل (برای `fleet recover`) |
| `--api-token TOKEN` | الزامی | توکن API Remnawave (برای `fleet recover`) |

**`fleet status`** — سلامت پنل، اتصال هر نود + نسخه Xray + ترافیق، بالادستی هر relay و شمارش کاربر را نشان می‌دهد. با `--json`، خروجی از `meridian.output/v1` envelope استفاده می‌کند. دسترسی فیلد پایدار درون `data`: `data.panel.url`، `data.panel.healthy`، `data.sources.*`، `data.servers[].roles`، `data.nodes[].status` (`"connected"`، `"disconnected"`، `"disabled"`، `"unknown"`), `data.relays[].health` (`"healthy"`، `"unhealthy"`، `"unknown"`) و `data.summary.health/needs_attention/active_users/disabled_users/unknown_nodes/unhealthy_relays`. `data.summary.health` برابر `"unknown"` است وقتی داده‌های زندگی مورد نیاز جمع‌آوری نشود. `status` سطح بالا اجرای دستور را گزارش می‌دهد، نه سلامت فلیت.

**`fleet inventory`** — پنل پیکربندی شده، نودها، relay‌ها، توپولوژی مطلوب و وضعیت نود پنل زندگی را وقتی قابل دسترسی است نشان می‌دهد. هرگز توکن API پنل یا مسیرهای URL مخفی را چاپ نمی‌کند. با `--json`، خروجی از `meridian.output/v1` envelope استفاده می‌کند. دسترسی فیلد پایدار درون `data` شامل `data.sources.*`، `data.servers[].roles`، `data.summary.*`، `data.nodes[].desired`، `data.nodes[].protocols`، `data.relays[].exit_node_*` و `data.desired_nodes[].present` می‌باشد. فیلدهای حضور inventory حقیقت تطابق نیستند؛ از `plan --json` برای تصمیمات drift/apply استفاده کنید.

**`fleet recover`** — `~/.meridian/cluster.yml` را از پنل زندگی بازسازی می‌کند. هنگامی که فایل محلی گم شود یا هنگام جذب استقرار دیگری استفاده کنید. از طریق SSH متصل می‌شود تا ابرداده سرور‌کنار پایدار را بخواند، سپس پنل API را برای نودها، relay‌ها، inbound‌ها، میزبان‌ها و کاربران جستجو می‌کند.

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
| `--include-schemas` | | شامل کردن JSON Schemas کامل در `api schemas --json` یا خروجی `api commands --json` |
| `--envelope`, `--json` | | بسته‌بندی `api schema NAME` در یک `meridian.output/v1` envelope به جای چاپ JSON Schema خام |

**`api schemas`** — نام‌های schema پایدار را فهرست می‌کند مثل `output-envelope`، `apply-envelope`، `client-list-envelope`، `client-show-envelope`، `deploy-envelope`، `deploy-command-data`، `deploy-request`، `deploy-workflow-answers`، `deploy-result`، `deploy-plan`، `workflow-plan`، `input-field`، `remote-target`، `command-spec`، `remote-command-result`، `plan-envelope`، `fleet-status-envelope`، `fleet-inventory-envelope`، `event`، `apply`، `plan-result`، `fleet-status` و `fleet-inventory`. schema‌های envelope دستور یک ورودی `commands` در کاتالوگ شامل می‌کند.

**`api commands`** — قرارداد دستور مهاجرت شده را با `command`، `argv`، `envelope_schema`، `data_schema`، `statuses` ممکن، `outcomes` ساختاری، معانی exit-code، پرچم‌های ماشین و پایداری فهرست می‌کند. قبل از سیم‌کشی یک UI استفاده کنید تا تصمیم بگیرید که کدام schema بار دستور فیلد مطابقت envelope داده‌شده را تأیید می‌کند. `deploy` تبلیغ می‌کند `--json`، `--events=jsonl`، `--request` و `--dry-run`.

**`api schema NAME`** — یک JSON Schema را چاپ می‌کند. مثال: `meridian api schema output-envelope`.

**`api workflow NAME`** — برنامه‌ریزی workflow قابل رندر کردن برای UI را چاپ می‌کند. مثال: `meridian api workflow deploy --json` بخش‌های wizard deploy و فیلدها را برمی‌گرداند.

### meridian relay

مدیریت نودهای relay — دستگاه‌های ارسال TCP سبکی که ترافیک را از طریق یک سرور داخلی به سرور خروجی در خارج از کشور منتقل می‌کنند.

```
meridian relay deploy RELAY_IP --exit EXIT [flags]
meridian relay list [--exit EXIT] [--json]
meridian relay remove RELAY_IP [--exit EXIT] [--yes]
meridian relay check RELAY_IP [--exit EXIT]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--exit/-e EXIT` | (الزامی برای deploy) | IP یا نام سرور خروجی |
| `--name NAME` | (خودکار) | نام دوستانه برای relay (مثلاً "ru-moscow") |
| `--port/-p PORT` | 443 | پورت شنوندگی روی سرور relay |
| `--user/-u USER` | root | کاربر SSH روی relay |
| `--ssh-port PORT` | 22 | پورت SSH روی سرور relay (در صورت عدم استاندارد) |
| `--yes/-y` | | رد شدن از درخواست‌های تأیید |
| `--json` | | صادر کردن `relay list` به عنوان `meridian.output/v1` envelope |

**نحوه کار relay‌ها**: کلاینت به IP داخلی relay متصل می‌شود. Relay TCP خام را به سرور خروجی در خارج منتقل می‌کند. تمامی رمزگذاری انتها به انتها بین کلاینت و خروجی است — relay هرگز plaintext را نمی‌بیند. تمام پروتکل‌ها (Reality، XHTTP، WSS) از طریق relay کار می‌کنند.

**خروجی JSON**: `relay list --json` از `meridian.output/v1` envelope با `data.relays[]` استفاده می‌کند.

### meridian plan

برنامه‌ریزی تطابق را نشان دهید — آنچه `meridian apply` برای همگرایی کلاستر به وضعیت مطلوب اعلام شده در `cluster.yml` انجام می‌دهد.

`desired_nodes`، `desired_relays`، `desired_clients` و `subscription_page` را از `cluster.yml` می‌خواند، وضعیت واقعی را از پنل واکشی می‌کند و تفاوت Terraform-style را با `+` برای اضافات، `-` برای حذف‌ها، `~` برای به‌روزرسانی‌ها چاپ می‌کند.

```
meridian plan [--json]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--json` | | صادر کردن برنامه‌ریزی به عنوان `meridian.output/v1` JSON envelope برای CI/CD و کلاینت‌های UI. exit code‌های یکسان؛ خروجی برنامه‌ریزی انسانی سرکوب می‌شود |

**Exit codes**:
- `0` — همگرا (هیچ تغییری نیازی نیست)
- `2` — تغییرات معلق (اجرای `meridian apply` برای همگرایی)
- خطاها نیز از exit‌های غیرصفر استفاده می‌کند؛ کلاینت‌های فرایند باید `status` و `errors[].category` JSON را به عنوان معتبر بپذیرند زیرا `2` می‌تواند زمانی که `status` برابر `failed` است یعنی خطای کاربر/پیکربندی نیز باشد

**شکل JSON** (`--json` mode):
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

`data.actions[].from_extras: true` منابعی را پرچم می‌کند که روی پنل موجود اما از `cluster.yml` گم شده‌اند — ورودی‌های `meridian apply --prune-extras` روی آن‌ها کار می‌کند. `execution_order` ترتیبی را نشان می‌دهد که `apply` از آن استفاده می‌کند، که برای جایگزینی ایمنی ممکن است از ترتیب نمایش متفاوت باشد. `operation: "replace"` جایگزینی‌های مخرب مثل تجدید تجهیز relay را علامت می‌گذارد. `status` برابر `no_changes` است وقتی همگرا و `changed` است وقتی apply کارتو انجام دهد؛ `data.exit_code` خروجی فرایند را منعکس می‌کند.

نگاه کنید [Declarative workflow](/docs/en/getting-started/#declarative-workflow) برای نحوه ترکیب `cluster.yml`.

### meridian apply

کلاستر را به وضعیت مطلوب اعلام شده در `cluster.yml` همگرا کنید. داخلی `plan` را اجرا می‌کند، تفاوت را نشان می‌دهد، تأیید درخواست می‌کند، سپس اقدامات را به ترتیب وابستگی اجرا می‌کند (حذف‌ها ابتدا، سپس اضافات، سپس حذف نودها آخر).

```
meridian apply [--yes] [--prune-extras=ask|yes|no] [--json]
```

| پرچم | پیش‌فرض | توضیحات |
|------|---------|-------------|
| `--yes`, `-y` | | رد شدن از درخواست‌های تأیید |
| `--parallel N` | 4 | حداکثر نخ‌های تجهیز نود موازی (هر نود نشست SSH و کلاینت پنل خود را می‌گیرد) |
| `--prune-extras` | `ask` | نحوه مقابله با drift — منابعی روی پنل موجود اما از `cluster.yml` گم شده‌اند. `ask` به ازای هر منبع درخواست می‌کند (به `no` برای ایمنی تحت `--yes` عقب‌تر شود)؛ `yes` خودکار حذف می‌کند؛ `no`건너می‌پرد و خلاصه یک خطی را چاپ می‌کند |
| `--json` | | صادر کردن `meridian.output/v1` نتیجه apply نهایی با وضعیت اجرای هر اقدام |

اقدامات مخرب (حذف‌ها، تجدید تجهیز UPDATE_RELAY) هشدار چاپ می‌کند و تأیید جداگانه‌ای نیاز دارد. شکست اولی در برنامه‌ریزی اقدامات مخرب باقی را رد می‌کند — `cluster.yml` صادق می‌ماند.

با `--json`، `data.plan` برنامه‌ریزی تایپ شده را شامل می‌کند و `data.actions[]` نتایج اجرا را با `status: "succeeded" | "failed" | "skipped"` شامل می‌کند. حالت JSON غیرتعاملی است: اگر تغییرات تأیید نیاز دارند و `--yes` غایب است، Meridian `MERIDIAN_CONFIRMATION_REQUIRED` را با برنامه‌ریزی محاسبه‌شده برمی‌گرداند. اگر drift فقط پنل موجود باشد و `--prune-extras` برابر `ask` باقی باشد، Meridian `MERIDIAN_DRIFT_DECISION_REQUIRED` برمی‌گرداند؛ از `--prune-extras=no` برای نگه‌داشتن drift یا `--prune-extras=yes` برای حذف آن استفاده کنید. قرارداد JSON اجرا را گزارش می‌کند؛ عملیات مخرب را تراکنشی نمی‌سازد. کلاینت‌های UI باید اقدامات ناموفق/رد شده را بررسی کند و پس از حل مشکل زمینی idempotently دوباره اجرا می‌شود.

**مثال drift handling:** اگر `cluster.yml` `desired_clients: ['alice']` فهرست کند اما پنل نیز `bob` داشته باشد (مثل ایجاد شده از طریق UI پنل)، `meridian plan` نشان می‌دهد `- remove client: bob`. با پیش‌فرض `--prune-extras=ask` از شما درخواست می‌شود که `bob` را حذف کنید یا او را نگه‌دارید. `--yes --prune-extras=yes` حذف را خاموشی اجرا می‌کند؛ `--yes` تنها (بدون `--prune-extras` صریح) آن را رد می‌کند.

### meridian preflight

تأیید سرور پیش از پرواز. SNI، پورت‌ها، DNS، OS، دیسک، ASN را بدون نصب هیچ چیز تست می‌کند.

```
meridian preflight [IP] [--ai] [--server NAME]
```

### meridian scan

یافتن هدف‌های SNI بهینه روی شبکه سرور با استفاده از RealiTLScanner.

```
meridian scan [IP] [--server NAME]
```

### meridian test

تست دسترسی پروکسی و تأیید اتصالات واقعی از دستگاه کلاینت. SSH نیازی نیست.

ابتدا دسترسی‌پذیری پایه را بررسی می‌کند (TCP، دست‌دهی TLS، دامنه HTTPS). سپس یک باینری کلاینت xray محلی را دانلود می‌کند (پس از اولین استفاده کش شود)، برای هر پروتکل فعال (Reality، XHTTP، WSS) از طریق پروکسی متصل می‌شود و ترافیک انتها به انتها تأیید می‌کند.

```
meridian test [IP] [--server NAME]
```

### meridian probe

یک سرور را طوری بررسی کنید که یک سانسورچی می‌کند — بررسی کنید اگر استقرار قابل تشخیص است. SSH نیازی نیست. روی هر سروری کار می‌کند، نه فقط استقرارهای Meridian. آدرس‌های IP یا نام‌های دامنه را قبول می‌کند.

9 بررسی را اجرا می‌کند: سطح پورت، پاسخ HTTP، گواهینامه TLS، سازگاری SNI، بررسی مسیر پروکسی، ارتقای WebSocket، reverse DNS، پشتیبانی HTTP/2 و نسخه‌های TLS ورثی.

```
meridian probe [IP|DOMAIN] [--server NAME]
```

### meridian doctor

جمع‌آوری تشخیص‌های سیستم برای اشکال‌زدایی. نام‌مستعار: `meridian rage`.

```
meridian doctor [IP] [--ai] [--server NAME]
```

### meridian teardown

حذف proxy از سرور.

```
meridian teardown [IP] [--server NAME] [--yes]
```

### meridian update

بروزرسانی CLI به آخرین نسخه.

```
meridian update
```

### meridian --version

نمایش نسخه CLI.

```
meridian --version
meridian -v
```

## پرچم‌های سراسری

این پرچم‌ها روی دستوراتی در دسترس هستند که با سرور از طریق SSH (deploy، node، relay، preflight، test، probe، doctor، teardown) تعامل دارند:

| پرچم | توضیح |
|------|-------------|
| `--server NAME` | هدف قرار دادن یک سرور نام‌گذاری شده خاص |
| `--user/-u USER` | کاربر SSH (پیش‌فرض: root، کاربر غیرroot خودکار sudo می‌گیرد) |
| `--sni HOST` | هدف پوشش TLS (استفاده‌شده توسط deploy، preflight، test، doctor) |
| `--domain DOMAIN` | دامنه fallback CDN Cloudflare (استفاده‌شده توسط deploy، preflight، test) |

دستورات کلاینت (`client add/show/list/remove/enable/disable`) روی پنل کلاستر مستقیم کار می‌کند و `--server` را قبول نمی‌کند.

## تعریف سرور

دستوراتی که به سرور نیاز دارند این اولویت را دنبال می‌کند:
1. استدلال IP صریح یا کلمات کلیدی `local` (deploy روی این سرور بدون SSH)
2. پرچم `--server NAME` (همچنین `--server local` را قبول می‌کند)
3. تشخیص حالت محلی (اجرا روی خود سرور)
4. انتخاب خودکار سرور تک (اگر فقط یکی ذخیره شده باشد)
5. درخواست تعاملی
