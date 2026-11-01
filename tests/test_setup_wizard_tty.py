"""Drive the real `meridian` process through a pseudo-terminal (prompts read /dev/tty)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pexpect = pytest.importorskip("pexpect")


def _spawn(home: Path, *args: str, lang: str = "ru_RU.UTF-8"):
    env = {
        **os.environ,
        "MERIDIAN_HOME": str(home),
        "LANG": lang,
        "LC_ALL": "",
        "MERIDIAN_LANG": "",
        "NO_COLOR": "1",
        "MERIDIAN_DISABLE_UPDATE_CHECK": "1",
        "COLUMNS": "120",
    }
    return pexpect.spawn(
        sys.executable,
        ["-m", "meridian", *args],
        env=env,
        encoding="utf-8",
        timeout=30,
        dimensions=(40, 120),
        use_poll=True,
    )


def test_bare_meridian_offers_setup_asks_language_and_saves_it(tmp_path: Path) -> None:
    child = _spawn(tmp_path)
    child.expect("мастер")
    child.sendline("")
    child.expect("Language / Язык")
    child.sendline("")  # preselected from LANG
    child.expect("local")
    child.sendline("подробнее")
    child.expect(r"никогда\s+не\s+сохраняет\s+пароли")
    child.expect("local")
    child.sendline("выйти")
    child.expect("meridian setup")
    child.expect(pexpect.EOF)
    child.close()

    assert child.exitstatus == 0
    assert json.loads((tmp_path / "preferences.json").read_text(encoding="utf-8")) == {"language": "ru"}


def test_saved_language_skips_the_question_and_lang_flag_overrides_it(tmp_path: Path) -> None:
    (tmp_path / "preferences.json").write_text('{"language": "ru"}', encoding="utf-8")
    child = _spawn(tmp_path, "--lang", "en", "setup", lang="C")
    child.expect("Server address")
    assert "Language /" not in (child.before or "")
    child.sendline("quit")
    child.expect(pexpect.EOF)
    child.close()

    assert child.exitstatus == 0


def test_wizard_process_accepts_a_terminal_descriptor_above_select_limit(tmp_path: Path) -> None:
    resource = pytest.importorskip("resource")
    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] < 2100:
        pytest.skip("the descriptor limit is too low to exercise select's boundary")
    descriptors = []
    try:
        while not descriptors or descriptors[-1] < 2048:
            descriptors.append(os.open(os.devnull, os.O_RDONLY))
        child = _spawn(tmp_path, "--lang", "en", "setup", lang="C")
        try:
            assert child.child_fd > 1024
            child.expect("Server address")
            child.sendline("quit")
            child.expect(pexpect.EOF)
        finally:
            child.close(force=True)
        assert child.exitstatus == 0
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
