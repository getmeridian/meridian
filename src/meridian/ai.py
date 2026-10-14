"""AI prompt building, clipboard support, and bundled AI docs."""

from __future__ import annotations

import platform
import shutil
import subprocess
from importlib.resources import files as pkg_files

from meridian.console import info, ok


def load_ai_docs() -> str:
    """Load AI reference docs bundled in the package."""
    ref = pkg_files("meridian") / "data" / "ai-reference.md"
    try:
        return ref.read_text(encoding="utf-8")
    except OSError:
        return ""


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    if platform.system() == "Darwin":
        cmd = ["pbcopy"]
    elif shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    elif shutil.which("xsel"):
        cmd = ["xsel", "--clipboard", "--input"]
    else:
        return False

    try:
        subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def build_ai_prompt(command_name: str, output: str, version: str) -> None:
    """Build an AI-ready diagnostic prompt and copy to clipboard."""
    docs = load_ai_docs()

    prompt_text = f"""You are a Meridian VPN proxy troubleshooting assistant.

Meridian is a Python CLI tool for deploying censorship-resistant VLESS+Reality proxy servers.
The output below comes from Meridian {version}.

## Reference Documentation

{docs}

## Command Output: `meridian {command_name}`

```
{output}
```

Analyze the output above and provide specific recommendations."""

    size_kb = len(prompt_text.encode()) / 1024

    if copy_to_clipboard(prompt_text):
        ok(f"AI prompt copied to clipboard ({size_kb:.1f} KB)")
        info("Paste into ChatGPT, Claude, or any AI assistant for analysis")
    else:
        info(f"AI prompt ({size_kb:.1f} KB) — copy the text below:")
        print(prompt_text)
