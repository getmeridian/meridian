---
title: Windows and WSL Setup
description: Set up Meridian on Windows using Windows Subsystem for Linux.
order: 2.5
section: guides
---

Meridian runs from a Unix-like terminal. On Windows, use Windows Subsystem for Linux (WSL) and run Meridian inside the Linux environment.

Native PowerShell, Command Prompt, Git Bash, and Cygwin are not the recommended path for Meridian. WSL gives you the same shell, SSH, and filesystem behavior as Linux, which matches the rest of the documentation.

## Install WSL

Open PowerShell as Administrator and install WSL:

```powershell
wsl --install
```

Restart if prompted, then open the installed Linux distribution from the Start menu.

Ubuntu is the safest default because Meridian targets Debian and Ubuntu servers, and most examples use `apt` package names. If you already have Debian installed in WSL, that works too.

Check that WSL is using version 2:

```powershell
wsl --list --verbose
```

If your distribution shows version 1, convert it:

```powershell
wsl --set-version Ubuntu 2
```

## Prepare the WSL shell

Inside the WSL terminal, update packages and install basic tools:

```bash
sudo apt update
sudo apt install -y curl openssh-client ca-certificates
```

Install Meridian:

```bash
curl -sSf https://getmeridian.org/install.sh | bash
```

Restart the WSL terminal or reload your shell profile if `meridian` is not found immediately:

```bash
exec "$SHELL" -l
```

Verify the install:

```bash
meridian --version
```

## Set up SSH keys

Meridian connects from WSL to your VPS over SSH. Generate the key inside WSL:

```bash
ssh-keygen -t ed25519 -C "meridian-wsl"
```

Copy the public key:

```bash
cat ~/.ssh/id_ed25519.pub
```

Add that public key to your VPS provider or to the server user's `~/.ssh/authorized_keys`.

Test SSH before running Meridian:

```bash
ssh root@198.51.100.10
```

If your VPS uses a non-root user, test that user instead and pass it to deploy:

```bash
meridian deploy 198.51.100.10 --user ubuntu
```

## Use an SSH agent

If your key has a passphrase, start an agent inside WSL:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

Add those commands to `~/.profile` or `~/.bashrc` only if you understand the security tradeoff. Starting the agent manually is fine for most first deployments.

Avoid mixing Windows and WSL SSH agents while getting started. Keep the key and agent inside WSL until deployment works.

## Run Meridian from WSL

Run the normal deployment flow from the WSL terminal:

```bash
meridian deploy
```

Or provide the server IP directly:

```bash
meridian deploy 198.51.100.10
```

Meridian stores credentials under `~/.meridian/` inside WSL. Run follow-up commands from the same WSL distribution so it can find the cached server credentials:

```bash
meridian client add alice
meridian client list
meridian test 198.51.100.10
```

## VS Code Remote WSL

If you use VS Code, install the "WSL" extension and open the Meridian workspace from WSL:

```bash
code .
```

Files edited through Remote WSL live in the Linux filesystem, which avoids path and permission issues. Prefer a path under your WSL home directory, such as `~/projects`, instead of editing Meridian state or SSH keys through `/mnt/c/`.

## Common gotchas

### The wizard appears to skip a prompt

Some interactive terminal prompts can behave differently under WSL. If the deploy wizard does not accept input as expected, provide values explicitly:

```bash
meridian deploy 198.51.100.10 --sni www.microsoft.com
```

You can also run a preflight check first:

```bash
meridian preflight 198.51.100.10
```

### Permission denied over SSH

Confirm the key exists in WSL, not only in Windows:

```bash
ls -la ~/.ssh
ssh -v root@198.51.100.10
```

The private key should be readable only by your WSL user:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
```

### Meridian was installed but the command is missing

Reload your shell:

```bash
exec "$SHELL" -l
```

If it still is not found, check common uv and local binary paths:

```bash
ls ~/.local/bin
```

Add `~/.local/bin` to your shell profile if needed:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
exec "$SHELL" -l
```

### Windows paths cause permission problems

Keep SSH keys, Meridian credentials, and project files in the WSL filesystem:

```bash
cd ~
mkdir -p projects
```

Avoid storing `~/.ssh` or `~/.meridian` under `/mnt/c/`. Windows-mounted paths can have different permissions, which SSH rejects.

## Next steps

- [Getting Started](/docs/en/getting-started/) — deploy your first server
- [Installation](/docs/en/installation/) — CLI install options
- [Troubleshooting](/docs/en/troubleshooting/) — common deployment and connection issues
