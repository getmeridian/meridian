---
title: راه‌اندازی Windows و WSL
description: راه‌اندازی Meridian در Windows با استفاده از Windows Subsystem for Linux.
order: 2.5
section: guides
---

Meridian از یک ترمینال شبیه Unix اجرا می‌شود. در Windows از Windows Subsystem for Linux (WSL) استفاده کنید و Meridian را داخل محیط Linux اجرا کنید.

PowerShell، Command Prompt، Git Bash و Cygwin بومی مسیر پیشنهادی برای Meridian نیستند. WSL همان رفتار shell، SSH و filesystem را مثل Linux به شما می‌دهد و با بقیه مستندات هماهنگ است.

## نصب WSL

PowerShell را به صورت Administrator باز کنید و WSL را نصب کنید:

```powershell
wsl --install
```

اگر درخواست شد سیستم را restart کنید، سپس توزیع Linux نصب‌شده را از Start menu باز کنید.

Ubuntu امن‌ترین پیش‌فرض است، چون Meridian سرورهای Debian و Ubuntu را هدف می‌گیرد و بیشتر مثال‌ها از نام پکیج‌های `apt` استفاده می‌کنند. اگر Debian را از قبل در WSL نصب کرده‌اید، آن هم کار می‌کند.

بررسی کنید که WSL از نسخه 2 استفاده می‌کند:

```powershell
wsl --list --verbose
```

اگر توزیع شما نسخه 1 را نشان می‌دهد، آن را تبدیل کنید:

```powershell
wsl --set-version Ubuntu 2
```

## آماده‌سازی shell در WSL

داخل ترمینال WSL، پکیج‌ها را به‌روز کنید و ابزارهای پایه را نصب کنید:

```bash
sudo apt update
sudo apt install -y curl openssh-client ca-certificates
```

Meridian را نصب کنید:

```bash
curl -sSf https://getmeridian.org/install.sh | bash
```

اگر `meridian` بلافاصله پیدا نشد، ترمینال WSL را restart کنید یا profile شل را دوباره بارگذاری کنید:

```bash
exec "$SHELL" -l
```

نصب را بررسی کنید:

```bash
meridian --version
```

## تنظیم کلیدهای SSH

Meridian از WSL با SSH به VPS شما وصل می‌شود. کلید را داخل WSL بسازید:

```bash
ssh-keygen -t ed25519 -C "meridian-wsl"
```

کلید عمومی را کپی کنید:

```bash
cat ~/.ssh/id_ed25519.pub
```

این کلید عمومی را به پنل ارائه‌دهنده VPS یا به `~/.ssh/authorized_keys` کاربر سرور اضافه کنید.

قبل از اجرای Meridian، SSH را تست کنید:

```bash
ssh root@198.51.100.10
```

اگر VPS شما از کاربر غیر root استفاده می‌کند، همان کاربر را تست کنید و آن را به deploy بدهید:

```bash
meridian deploy 198.51.100.10 --user ubuntu
```

## استفاده از SSH agent

اگر کلید شما passphrase دارد، یک agent داخل WSL شروع کنید:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

این دستورها را فقط وقتی به `~/.profile` یا `~/.bashrc` اضافه کنید که tradeoff امنیتی آن را می‌فهمید. برای بیشتر استقرارهای اول، شروع دستی agent کافی است.

در شروع کار، SSH agentهای Windows و WSL را با هم مخلوط نکنید. تا وقتی deploy کار کند، کلید و agent را داخل WSL نگه دارید.

## اجرای Meridian از WSL

فرایند عادی deploy را از ترمینال WSL اجرا کنید:

```bash
meridian deploy
```

یا IP سرور را مستقیم بدهید:

```bash
meridian deploy 198.51.100.10
```

Meridian اطلاعات ورود را زیر `~/.meridian/` داخل WSL ذخیره می‌کند. دستورهای بعدی را از همان توزیع WSL اجرا کنید تا بتواند اطلاعات ذخیره‌شده سرور را پیدا کند:

```bash
meridian client add alice
meridian client list
meridian test 198.51.100.10
```

## VS Code Remote WSL

اگر از VS Code استفاده می‌کنید، افزونه "WSL" را نصب کنید و workspace مربوط به Meridian را از WSL باز کنید:

```bash
code .
```

فایل‌هایی که با Remote WSL ویرایش می‌شوند در filesystem لینوکس قرار دارند و این کار از مشکل path و permission جلوگیری می‌کند. مسیری زیر home در WSL مثل `~/projects` را ترجیح دهید و state مربوط به Meridian یا کلیدهای SSH را از مسیر `/mnt/c/` ویرایش نکنید.

## مشکلات رایج

### wizard انگار یک prompt را رد می‌کند

برخی promptهای تعاملی ترمینال می‌توانند زیر WSL متفاوت رفتار کنند. اگر wizard مربوط به deploy ورودی را طبق انتظار قبول نمی‌کند، مقدارها را صریح بدهید:

```bash
meridian deploy 198.51.100.10 --sni www.microsoft.com
```

همچنین می‌توانید اول preflight را اجرا کنید:

```bash
meridian preflight 198.51.100.10
```

### خطای Permission denied در SSH

مطمئن شوید کلید داخل WSL وجود دارد، نه فقط در Windows:

```bash
ls -la ~/.ssh
ssh -v root@198.51.100.10
```

کلید خصوصی باید فقط توسط کاربر WSL شما خواندنی باشد:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
```

### Meridian نصب شده اما command پیدا نمی‌شود

شل را دوباره بارگذاری کنید:

```bash
exec "$SHELL" -l
```

اگر هنوز پیدا نشد، مسیرهای رایج uv و binaryهای محلی را بررسی کنید:

```bash
ls ~/.local/bin
```

در صورت نیاز `~/.local/bin` را به profile شل اضافه کنید:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
exec "$SHELL" -l
```

### مسیرهای Windows مشکل permission ایجاد می‌کنند

کلیدهای SSH، اطلاعات Meridian و فایل‌های پروژه را در filesystem مربوط به WSL نگه دارید:

```bash
cd ~
mkdir -p projects
```

از نگهداری `~/.ssh` یا `~/.meridian` زیر `/mnt/c/` خودداری کنید. مسیرهای mount شده از Windows می‌توانند permissionهای متفاوتی داشته باشند و SSH آن‌ها را رد کند.

## قدم‌های بعدی

- [شروع کار](/docs/fa/getting-started/) — اولین سرور خود را deploy کنید
- [نصب](/docs/fa/installation/) — گزینه‌های نصب CLI
- [عیب‌یابی](/docs/fa/troubleshooting/) — مشکلات رایج deploy و اتصال
