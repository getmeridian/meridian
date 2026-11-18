import type { LandingStrings } from './en';

export const fa: LandingStrings = {
  // Page metadata
  'page.title': 'Meridian — VPN خودمیزبان، با استقرار درست',
  'page.description':
    'با یک دستور، یک سرور VPN مقاوم‌شده با VLESS+Reality، پنل وب توکار و لینک اشتراک‌گذاری آماده می‌شود. متن‌باز.',
  'page.ogAlt': 'Meridian: VPN خودتان، با یک لینک خصوصی.',

  // Hero
  'hero.kicker': 'متن‌باز / روی سرور شما',
  'hero.title': 'VPN خودتان، <em>با یک لینک خصوصی.</em>',
  'hero.sub': 'Meridian روی سرور شما VPN راه می‌اندازد. ترافیک اتصال Reality شبیه HTTPS معمولی است و هر نفر با لینک خصوصی خودش وصل می‌شود.',
  'hero.cta': 'راه‌اندازی Meridian',
  'hero.demo': 'دیدن نمونه‌ها',
  'hero.docs': 'مطالعهٔ مستندات',
  'hero.pause': 'مکث در پویانمایی کرهٔ زمین',
  'hero.replay': 'پخش دوبارهٔ پویانمایی کرهٔ زمین',
  'hero.resume': 'ادامهٔ پویانمایی کرهٔ زمین',
  'hero.figure': 'شکل ۰۱ / نقشهٔ اتصال',
  'hero.server': 'سرور',
  'hero.device': 'دستگاه',
  'hero.mapAlt': 'تصویر کرهٔ زمین با سه دستگاه متصل به یک سرور خصوصی.',

  // Server requirements and project license
  'spec.server': 'سیستم‌عامل سرور',
  'spec.serverValue': 'Debian یا Ubuntu',
  'spec.protocols': 'روش‌های اتصال',
  'spec.license': 'مجوز',
  'spec.licenseValue': 'متن‌باز (MIT)',

  // How it works
  'setup.label': 'نصب',
  'setup.title': 'از سرور تا VPN',
  'setup.desc': 'یک VPS تازه با Debian یا Ubuntu آماده کنید. در ترمینال رایانه‌تان نصب‌کننده را اجرا کنید و مراحل راهنما را دنبال کنید.',
  'setup.step1.label': '۰۱ / نصب ابزار خط فرمان',
  'setup.step2.label': '۰۲ / اجرای راه‌انداز',
  'setup.hint': 'پیش از تغییر سرور، راه‌انداز برنامهٔ کار را نشان می‌دهد. <a href="{base}/getting-started/">راهنمای راه‌اندازی</a>.',
  'setup.video':
    'کل راه‌اندازی با <code>meridian setup</code> از ابتدا تا انتها: ۳٫۵ دقیقه، به انگلیسی با زیرنویس. <a href="/video/meridian-setup.mp4" download>دانلود ویدیو</a>',
  'setup.videoLabel': 'ویدیو: راه‌اندازی VPN شخصی با Meridian',
  'setup.details': 'پشت صحنه چه اتفاقی می‌افتد',
  'setup.idempotent':
    'اجرای دوباره بی‌خطر است. اجرای دوم اعتبارنامه‌ها و کلیدها را نگه می‌دارد و استقرار را ادامه می‌دهد یا در همان‌جا به‌روز می‌کند.',

  // Behind-the-scenes timeline (title, then the text that follows it)
  'step.1.title': 'نصب سرویس کنترل و پنل وب',
  'step.1.text': ' همراه با agent گره که <strong>Xray</strong> را اجرا می‌کند — بدون Docker و بدون پنل شخص ثالث',
  'step.2.title': 'ساخت جفت‌کلید x25519',
  'step.2.text': ' — کلیدهای رمزنگاری یکتا برای احراز هویت Reality',
  'step.3.title': 'مقاوم‌سازی سرور',
  'step.3.text':
    ' — UFW فقط پورت SSH شما، پورت 443 (TCP و UDP) و پورت 80 را برای گواهی‌ها باز می‌کند؛ ورود SSH فقط با کلید، fail2ban و کنترل ازدحام <strong>BBR</strong>',
  'step.4.title': 'پیکربندی VLESS+Reality',
  'step.4.text': ' روی TCP 443، جایی که پروب‌ها گواهی یک وب‌سایت واقعی را می‌بینند',
  'step.5.title': 'افزودن XHTTP و Hysteria2',
  'step.5.text': ' — XHTTP از طریق nginx روی همان پورت، و Hysteria2 روی UDP 443 با مبهم‌سازی salamander',
  'step.6.title': 'نمایش کد QR',
  'step.6.text': ' و صفحهٔ اتصال، پس از آنکه ترافیک واقعی از اشتراک جدید عبور کرد',

  // Command builder section
  'builder.title': 'دستور خود را بسازید',
  'builder.manual': 'راهنمای خط فرمان: فرمان‌ها، گزینه‌ها و نمونه‌ها',
  'builder.desc':
    'گزینه‌ها را انتخاب کنید و دستور کامل را کپی کنید. شامل استقرار، رله‌ها، بررسی‌ها، اسکن سایت‌های استتار، عیب‌یابی و حذف است.',

  // Command builder widget
  'builder.tab.deploy': 'استقرار',
  'builder.tab.relay': 'رله',
  'builder.tab.check': 'بررسی',
  'builder.tab.scan': 'اسکن SNI',
  'builder.tab.doctor': 'عیب‌یابی',
  'builder.tab.teardown': 'حذف',
  'builder.desc.deploy':
    'استقرار Reality، XHTTP و Hysteria2 روی یک سرور. سرویس کنترل، پنل وب، nginx، فایروال و TLS را راه‌اندازی می‌کند.',
  'builder.desc.relay':
    'یک رله TCP روی سروری داخل کشور اضافه می‌کند که ترافیک را به سرور خروجی در خارج از کشور هدایت می‌کند. سرور خروجی باید از قبل مستقر شده باشد.',
  'builder.desc.check':
    'بررسی می‌کند که آیا سرور جدید می‌تواند Meridian را اجرا کند (SNI، پورت‌ها، DNS، سیستم‌عامل، دیسک) یا سرور مستقرشده سالم است.',
  'builder.desc.scan':
    'با RealiTLScanner سایت‌های استتار (SNI) را در شبکهٔ خود سرورتان پیدا کنید و یکی را انتخاب کنید.',
  'builder.desc.doctor': 'یک گزارش تشخیصی برای گزارش باگ جمع‌آوری می‌کند. اطلاعات حساس حذف می‌شوند.',
  'builder.desc.teardown':
    'Meridian را از یک سرور حذف می‌کند. استقرار تک‌سروری به‌طور کامل حذف می‌شود، از جمله پیکربندی محلی.',
  'builder.field.server': 'سرور',
  'builder.field.server.placeholder': '198.51.100.10 یا local',
  'builder.field.server.hint':
    'IP سرور، نام یک سرور ذخیره‌شده، یا «local» برای اجرا روی خود سرور. برای اینکه از شما پرسیده شود، خالی بگذارید.',
  'builder.field.relayServer': 'سرور رله',
  'builder.field.relayServer.hint': 'IP سروری داخل کشور که ترافیک را هدایت می‌کند.',
  'builder.field.user': 'کاربر SSH',
  'builder.field.user.hint': 'پیش‌فرض: کاربر ذخیره‌شده، وگرنه root. کاربران غیر root به sudo بدون رمز عبور نیاز دارند.',
  'builder.field.sshPort': 'پورت SSH',
  'builder.field.sshPort.hint': 'پیش‌فرض: پورت ذخیره‌شده، وگرنه 22.',
  'builder.field.exit': 'سرور خروجی',
  'builder.field.exit.placeholder': '198.51.100.10 یا نام سرور',
  'builder.field.exit.hint': 'IP یا نام سرور خروجی مستقرشده‌ای که این رله به آن هدایت می‌کند.',
  'builder.field.domain': 'دامنه',
  'builder.field.domain.hint':
    'اختیاری. XHTTP را به دامنهٔ شما منتقل می‌کند و WSS اضافه می‌کند؛ هر دو می‌توانند از CDN Cloudflare عبور کنند.',
  'builder.field.sni': 'سایت استتار (SNI)',
  'builder.field.sni.placeholder': 'خودکار',
  'builder.field.sni.hint':
    'سایتی که Reality از آن تقلید می‌کند. روی خودکار بگذارید تا deploy سایتی را که از سرور شما کار می‌کند انتخاب کند، یا با زبانهٔ اسکن SNI یکی پیدا کنید.',
  'builder.field.relaySni.hint': 'روی خودکار بگذارید تا سایت استتار سرور خروجی به ارث برسد.',
  'builder.field.clientName': 'نخستین کلاینت',
  'builder.field.clientName.hint': 'نام اولین کسی که وصل می‌شود. پیش‌فرض: «default».',
  'builder.field.displayName': 'عنوان صفحه',
  'builder.field.displayName.placeholder': 'VPN آلیس',
  'builder.field.displayName.hint': 'برچسبی که در صفحه‌های اتصال نشان داده می‌شود.',
  'builder.field.icon': 'نماد صفحه',
  'builder.field.icon.hint': 'یک اموجی یا نشانی تصویر برای صفحهٔ اتصال.',
  'builder.field.color': 'رنگ صفحه',
  'builder.field.color.hint': 'طرح رنگی صفحهٔ اتصال.',
  'builder.field.color.default': 'پیش‌فرض (اقیانوس)',
  'builder.palette.ocean': 'اقیانوس',
  'builder.palette.sunset': 'غروب',
  'builder.palette.forest': 'جنگل',
  'builder.palette.lavender': 'اسطوخودوس',
  'builder.palette.rose': 'رز',
  'builder.palette.slate': 'سربی',
  'builder.field.relayName': 'نام رله',
  'builder.field.relayName.hint': 'نام دلخواه و اختیاری، مانند «ru-moscow».',
  'builder.field.port': 'پورت شنود',
  'builder.field.port.hint': 'پورتی که رله روی آن گوش می‌دهد. پیش‌فرض: 443.',
  'builder.field.harden': 'SSH و فایروال دست‌نخورده بماند',
  'builder.field.harden.hint': 'فقط وقتی سرویس‌های دیگری روی این سرور هستند؛ مقاوم‌سازی به‌طور پیش‌فرض روشن است.',
  'builder.field.ai': 'خروجی آمادهٔ هوش مصنوعی',
  'builder.field.ai.hint': 'یک پرامپت برای ChatGPT یا Claude در کلیپ‌بورد کپی می‌کند.',
  'builder.field.yes': 'رد کردن تأیید',
  'builder.field.yes.hint': 'برای اسکریپت‌ها و نشست‌های SSH غیرتعاملی.',
  'builder.command': 'دستور',
  'builder.copy': 'کپی',
  'builder.copied': 'کپی شد',
  'builder.wizard':
    '<strong>اولین بار است؟</strong> فقط <code>meridian setup</code> را اجرا کنید — ویزارد هر چیزی را که لازم دارد می‌پرسد.',
  'builder.status.invalid': 'یک آدرس IP، نام سرور ذخیره‌شده یا «local» وارد کنید.',
  'builder.status.local': 'روی خود سرور و بدون SSH اجرا می‌شود، با کاربر root یا با sudo بدون رمز عبور.',
  'builder.status.deploy': 'Reality، XHTTP و Hysteria2 را مستقر می‌کند.',
  'builder.status.deployDomain': 'Reality، XHTTP و Hysteria2 را مستقر می‌کند، به‌علاوهٔ WSS روی دامنهٔ شما.',
  'builder.status.relayNeed': 'سرور رله و سرور خروجی‌ای را که به آن هدایت می‌کند وارد کنید.',
  'builder.status.relayOk': 'ورودی‌های Reality‌ای اضافه می‌کند که از طریق رله وارد می‌شوند؛ روش‌های دیگر همچنان مستقیم به سرور خروجی وصل می‌شوند.',
  'builder.status.checkNeed': 'بدون سرور، کل مجموعه سرورهای ذخیره‌شده را بررسی می‌کند.',
  'builder.status.checkOk': 'بررسی‌ها را اجرا می‌کند، بدون اینکه چیزی را تغییر دهد.',
  'builder.status.scanNeed': 'سرور خود را وارد کنید تا سایت‌های استتار نزدیک آن پیدا شوند.',
  'builder.status.scanOk': 'شبکهٔ خود سرور را اسکن می‌کند، اجازه می‌دهد سایتی را انتخاب کنید و دستور استقرار را چاپ می‌کند.',
  'builder.status.doctorNeed': 'بدون سرور، دربارهٔ سرور ذخیره‌شده گزارش می‌دهد.',
  'builder.status.doctorOk': 'گزارشی با اطلاعات حساس حذف‌شده جمع می‌کند. برای داشتن پرامپت آماده، «خروجی آمادهٔ هوش مصنوعی» را روشن کنید.',
  'builder.status.doctorAi': 'گزارشی با اطلاعات حساس حذف‌شده جمع می‌کند و یک پرامپت آمادهٔ هوش مصنوعی را در کلیپ‌بورد کپی می‌کند.',
  'builder.status.teardownNeed': 'بدون سرور، اگر فقط یک سرور ذخیره شده باشد از همان استفاده می‌کند.',
  'builder.status.teardownOk': 'اگر چند سرور دارید، ابتدا نقش سرور را با meridian node remove بردارید.',

  // How it avoids detection
  'tech.label': 'یادداشت‌های فنی',
  'tech.title': 'درون شبکه',
  'tech.desc': 'ببینید اتصال‌ها چطور به سرور می‌رسند و ناظر ترافیک شبکه چه چیزی می‌بیند.',
  'tech.group': 'آنچه در شبکه دیده می‌شود',
  'tech.dpi': 'بازرسی عمیق بسته (DPI)',
  'tech.dpi.body':
    '<p>DPI الگوهای ترافیک را تحلیل می‌کند تا پروتکل‌های VPN و پراکسی را شناسایی کند. OpenVPN و WireGuard دست‌دادن‌هایی (handshake) دارند که شناسایی و مسدود کردنشان آسان است.</p><p><strong>VLESS+Reality</strong> داخل یک اتصال استاندارد TLS 1.3 روی پورت 443 اجرا می‌شود، پس DPI چیزی می‌بیند که شبیه HTTPS معمولی به سایت استتار است. هر سرور XHTTP و Hysteria2 را هم ارائه می‌دهد، بنابراین اگر یک روش مسدود شود، روش‌های دیگر همچنان کار می‌کنند.</p>',
  'tech.probe': 'کاوش فعال',
  'tech.probe.body':
    '<p>سانسورگران به سرورهای مشکوک وصل می‌شوند و می‌کوشند اثرانگشت آن‌ها را بگیرند. اگر سرور متفاوت از وب‌سایتی که ادعا می‌کند پاسخ دهد، مسدود می‌شود.</p><p><strong>Reality</strong> هویت TLS یک وب‌سایت واقعی را قرض می‌گیرد: به‌طور پیش‌فرض سایتی که نزدیک سرور شما میزبانی می‌شود، یا <code>www.google.com</code>. پروبی که کلید شما را ندارد به همان سایت منتقل می‌شود و گواهی اصلی آن را می‌بیند. فقط کلاینت‌هایی که کلید شما را دارند وارد تونل VPN می‌شوند.</p>',
  'tech.fp': 'اثرانگشت TLS',
  'tech.fp.body':
    '<p>هر کلاینت TLS یک اثرانگشت یکتای «Client Hello» می‌فرستد. سانسورگران اتصال‌هایی را که اثرانگشتشان با برنامهٔ ادعاشده نمی‌خواند علامت‌گذاری می‌کنند.</p><p>برنامه‌های کلاینت با <strong>uTLS</strong> همان Client Hello مرورگر Chrome را می‌فرستند. Meridian برای هر ورودی Reality و XHTTP یک نسخهٔ Firefox هم منتشر می‌کند، پس اگر سانسورگران یک اثرانگشت را علامت بزنند، دیگری همچنان کار می‌کند.</p>',

  // Showcase
  'showcase.label': 'آشنایی با Meridian',
  'showcase.title': 'دو نمای یک VPN',
  'showcase.desc': 'هر دو نمونه با داده‌های آزمایشی کار می‌کنند. در پنل یک نفر اضافه کنید یا مراحل راه‌اندازی را در صفحهٔ اتصال ببینید.',
  'showcase.page.role': 'برای کسانی که دعوت می‌کنید',
  'showcase.console.role': 'برای مدیر سرور',
  'showcase.page.title': 'صفحهٔ اتصال',
  'showcase.page.text': 'لینک شخصی را باز کنید، برنامه را انتخاب کنید و با یک ضربه VPN را اضافه کنید. میزان مصرف و تاریخ انقضا هم در همان صفحه است.',
  'showcase.page.try': 'باز کردن نمونهٔ اتصال',
  'showcase.console.title': 'پنل مدیریت',
  'showcase.console.text': 'افراد را دعوت کنید، کد QR بدهید و برای زمان یا حجم مصرف سقف بگذارید. وضعیت سرورها هم در همین پنل دیده می‌شود.',
  'showcase.console.try': 'باز کردن نمونهٔ پنل',

  // Connect
  'connect.title': 'از چه برنامه‌هایی می‌توانم استفاده کنم؟',
  'connect.apps.more':
    'صفحه‌های اتصال ابتدا همین برنامه‌ها را فهرست می‌کنند و برای هر پلتفرم به برنامه‌های دیگر لینک می‌دهند. <a href="/demo/?lang=fa">دیدن نمونه</a>.',
  'connect.clock':
    'تاریخ و ساعت خودکار را روی همهٔ دستگاه‌ها روشن کنید. اگر ساعت دستگاه اشتباه باشد، ممکن است اتصال برقرار نشود.',

  // Architecture
  'arch.title': 'پیکربندی‌های سرور',
  'arch.figure': 'مشاهدهٔ نمودار سرور',
  'arch.docs': 'راهنمای معماری',
  'arch.alt':
    'معماری Meridian: nginx اتصال‌های TCP 443 را بر اساس SNI به Xray Reality، XHTTP و صفحه‌های اتصال هدایت می‌کند؛ Hysteria2 روی UDP 443 گوش می‌دهد',
  'arch.standalone': 'حالت مستقل (بدون دامنه)',
  'arch.standalone.body':
    "<p><strong>nginx</strong> مالک پورت TCP 443 است و اتصال‌ها را با ماژول stream خود بر اساس SNI در TLS هدایت می‌کند. اتصال‌های Reality به <strong>Xray</strong> می‌روند. بقیه به ماژول http در nginx می‌رسند که صفحه‌های اتصال، اشتراک‌ها و XHTTP را روی HTTPS ارائه می‌دهد، با یک گواهی کوتاه‌مدت Let's Encrypt برای IP سرور (صادرشده با acme.sh). <strong>Hysteria2</strong> روی UDP 443 با مبهم‌سازی salamander گوش می‌دهد.</p><p><strong>سرویس کنترل</strong> Meridian پشت nginx روی یک مسیر مخفی قرار دارد: کاربران شما را نگه می‌دارد، اشتراک‌هایشان را ارائه می‌کند و پنل وب را بدون نیاز به تونل SSH روی HTTPS در دسترس قرار می‌دهد. یک agent کوچک روی هر سرور خودش به آن وصل می‌شود، پس گره‌ها پورت اضافه‌ای باز نمی‌کنند.</p>",
  'arch.domain': 'حالت دامنه (جایگزین CDN)',
  'arch.domain.body':
    "<p>با <code>--domain</code>، nginx برای دامنهٔ شما گواهی Let's Encrypt می‌گیرد و آن SNI را به مسیرهای دامنه هدایت می‌کند. XHTTP به دامنه منتقل می‌شود و <strong>VLESS+WSS</strong> اضافه می‌شود. وقتی Cloudflare دامنه را پراکسی کند، هر دو از CDN عبور می‌کنند و حتی اگر IP سرور شما مسدود شود کار می‌کنند. سرویس کنترل و صفحه‌های اتصال روی گواهی IP می‌مانند.</p>",
  'arch.relay': 'حالت رله (نقطهٔ ورود داخلی)',
  'arch.relay.body':
    '<p>یک فورواردر TCP از نوع <strong>Realm</strong> روی سروری داخل کشور شما، اتصال‌ها را به سرور خروجی شما در خارج منتقل می‌کند. رله‌ها فقط Reality را حمل می‌کنند؛ XHTTP، WSS و Hysteria2 همچنان مستقیم به سرور خروجی وصل می‌شوند. رمزنگاری سرتاسری بین برنامه و سرور خروجی برقرار است، پس رله هرگز متن آشکار را نمی‌بیند.</p><p>با <code>meridian relay add RELAY_IP --exit EXIT</code> یکی اضافه کنید. اشتراک‌ها و صفحه‌های اتصال ورودی‌های رله را خودکار برمی‌دارند.</p>',

  // Closing call to action
  'cta.title': 'با یک سرور شروع کنید',
  'cta.desc': 'سرور اول را راه بیندازید و لینک اتصال را برای یک نفر بفرستید. هر وقت خواستید، افراد یا سرورهای بیشتری اضافه کنید.',
};
