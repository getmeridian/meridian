"""The public demos use current service responses and stay outside Python distributions."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from meridian.control.ui_routes import UI_DIR
from scripts.build_showcase import export_showcase, validate_recordings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def showcase(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("showcase")
    export_showcase(directory)
    return directory


def recordings(directory: Path) -> dict:
    return json.loads((directory / "console/demo-data.json").read_text())


def test_showcase_copies_the_real_console_and_loads_the_shim_before_startup(showcase: Path) -> None:
    for source in UI_DIR.rglob("*"):
        if source.is_file() and source.name != "CLAUDE.md":
            assert (showcase / "console/ui" / source.relative_to(UI_DIR)).read_bytes() == source.read_bytes()
    html = (showcase / "console/index.html").read_text()
    assert html.index('src="demo.js"') < html.index('src="ui/app.js"')
    assert (showcase / "console/demo.js").is_file()
    assert "noindex" in html


def test_recorded_responses_validate_against_current_models_and_reject_drift(showcase: Path) -> None:
    data = recordings(showcase)
    validate_recordings(data["responses"])
    data["responses"]["GET api/users"]["users"][0]["status"] = "imaginary"
    with pytest.raises(ValidationError):
        validate_recordings(data["responses"])


def test_both_demos_share_people_individual_connections_notice_and_contact(showcase: Path) -> None:
    data = recordings(showcase)
    name = data["page_person"]
    responses = data["responses"]
    user = next(user for user in responses["GET api/users"]["users"] if user["username"] == name)
    config = json.loads((showcase / "sub" / user["sub_token"] / "config.json").read_text())
    share = responses[f"GET ui/api/people/{name}/share"]
    info = responses[f"GET sub/{user['sub_token']}/info"]
    assert config["client_name"] == name
    assert config["subscription_url"] == share["page_url"] == share["subscription_url"]
    assert [entry["url"] for entry in config["protocols"]] == [entry["uri"] for entry in share["connections"]]
    assert config["owner_contact"] == responses["GET api/settings"]["owner_contact"]
    assert config["notice"]["text"] == info["notice"]["text"]
    assert info["user"]["username"] == user["username"]
    assert (showcase / "sub" / user["sub_token"] / "info").is_file()
    assert "SW disabled" in (showcase / "sub/~pwa/sw.js").read_text()


def test_a_built_wheel_and_sdist_exclude_demo_shim_data_and_seeder(showcase: Path, tmp_path: Path) -> None:
    checkout = tmp_path / "package"
    checkout.mkdir()
    for filename in ("pyproject.toml", "VERSION", "README.md", "LICENSE", "CHANGELOG.md"):
        shutil.copy2(ROOT / filename, checkout / filename)
    shutil.copytree(ROOT / "src", checkout / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (checkout / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/hatch_build.py", checkout / "scripts/hatch_build.py")
    shutil.copy2(ROOT / "scripts/build_showcase.py", checkout / "scripts/build_showcase.py")
    shutil.copytree(ROOT / "website/demo", checkout / "website/demo")
    shutil.copytree(showcase, checkout / "website/dist/demo")
    studio = checkout / "website/dist/studio"
    studio.mkdir()
    (studio / "index.html").write_text("<!doctype html><title>Studio</title>")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from hatchling.build import build_wheel, build_sdist; build_wheel('dist'); build_sdist('dist')",
        ],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    wheel = next((checkout / "dist").glob("*.whl"))
    sdist = next((checkout / "dist").glob("*.tar.gz"))
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "meridian/studio_assets/studio/index.html" in names
        assert "meridian/control/ui/app.js" in names
        assert not any(
            "demo.js" in name or "demo-data" in name or "build_showcase" in name or "showcase_seed" in name
            for name in names
        )
    with tarfile.open(sdist) as archive:
        assert not any(
            "demo.js" in name or "demo-data" in name or "build_showcase" in name or "showcase_seed" in name
            for name in archive.getnames()
        )
