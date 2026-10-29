"""Exercise shipped PWA JavaScript at its DOM, storage, and fetch boundaries."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from meridian.control.formats.browser import subscription_info
from meridian.control.formats.entries import SubscriptionUser
from meridian.render import render_apps_catalog, render_config_json

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="PWA runtime checks need Node, without npm packages")


@pytest.fixture
def page_data() -> dict[str, object]:
    now = datetime.now(UTC)
    user = SubscriptionUser(
        short_id="demo",
        username="client",
        uuid="550e8400-e29b-41d4-a716-446655440000",
        status="active",
        used_bytes=1536,
        limit_bytes=4096,
        lifetime_used_bytes=8192,
        expires_at=now + timedelta(days=7),
    )
    url = "https://198.51.100.1/connect/sub/demo"
    return {
        "config": json.loads(render_config_json([], "198.51.100.1", subscription_url=url)),
        "catalog": json.loads(render_apps_catalog()),
        "info": subscription_info(user, [], url, now),
    }


def run_case(name: str, data: dict[str, object]) -> None:
    result = subprocess.run(
        [NODE or "node", "tests/pwa_cases.cjs", name],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_account_loads_info_without_cache_and_refreshes_on_return(page_data: dict[str, object]) -> None:
    run_case("test_account_loads_info_without_cache_and_refreshes_on_return", page_data)


@pytest.mark.parametrize("status", ["DISABLED", "EXPIRED", "LIMITED"])
def test_suspended_account_explains_and_dims_imports(page_data: dict[str, object], status: str) -> None:
    page_data["status"] = status
    run_case("test_suspended_account_explains_and_dims_imports", page_data)


def test_account_failure_keeps_only_account_snapshot_and_marks_it_unverified(page_data: dict[str, object]) -> None:
    run_case("test_account_failure_keeps_only_account_snapshot_and_marks_it_unverified", page_data)


def test_account_unlimited_and_no_expiry_are_not_zero_or_a_sentinel_date(page_data: dict[str, object]) -> None:
    run_case("test_account_unlimited_and_no_expiry_are_not_zero_or_a_sentinel_date", page_data)


def test_switching_from_persian_to_english_resets_direction_even_without_storage(page_data: dict[str, object]) -> None:
    run_case("test_switching_from_persian_to_english_resets_direction_even_without_storage", page_data)


def test_load_error_uses_saved_language_before_config_arrives(page_data: dict[str, object]) -> None:
    run_case("test_load_error_uses_saved_language_before_config_arrives", page_data)


def test_fixed_catalog_labels_and_accessible_actions_follow_language(page_data: dict[str, object]) -> None:
    run_case("test_fixed_catalog_labels_and_accessible_actions_follow_language", page_data)


def test_support_page_honors_saved_language(page_data: dict[str, object]) -> None:
    run_case("test_support_page_honors_saved_language", page_data)


def test_optional_catalog_never_blocks_config_or_resets_open_content(page_data: dict[str, object]) -> None:
    run_case("test_optional_catalog_never_blocks_config_or_resets_open_content", page_data)


def test_catalog_timeout_ignores_late_answers_without_abort_support(page_data: dict[str, object]) -> None:
    run_case("test_catalog_timeout_ignores_late_answers_without_abort_support", page_data)


@pytest.mark.parametrize("bad", ["version", "app", "installer", "icon", "scheme", "guide"])
def test_bad_optional_catalog_keeps_valid_embedded_apps(page_data: dict[str, object], bad: str) -> None:
    page_data["bad"] = bad
    run_case("test_bad_optional_catalog_keeps_valid_embedded_apps", page_data)


def test_unsupported_config_version_is_rejected_before_rendering(page_data: dict[str, object]) -> None:
    run_case("test_unsupported_config_version_is_rejected_before_rendering", page_data)


def test_quoted_attributes_keep_user_text_literal(page_data: dict[str, object]) -> None:
    page_data["quoted_url"] = "https://198.51.100.1/sub/demo?q=\" onfocus=\"alert(1)&other='test'"
    run_case("test_quoted_attributes_keep_user_text_literal", page_data)


def test_legacy_catalog_platform_labels_remain_usable(page_data: dict[str, object]) -> None:
    run_case("test_legacy_catalog_platform_labels_remain_usable", page_data)


@pytest.mark.parametrize("mode", ["missing", "rejected", "throws", "no_method"])
def test_copy_failure_shows_localized_selectable_text(page_data: dict[str, object], mode: str) -> None:
    page_data["mode"] = mode
    run_case("test_copy_failure_shows_localized_selectable_text", page_data)


def test_diagnostic_report_omits_device_data(page_data: dict[str, object]) -> None:
    run_case("test_diagnostic_report_omits_device_data", page_data)


def test_diagnostic_copy_failure_uses_the_same_selectable_fallback(page_data: dict[str, object]) -> None:
    run_case("test_diagnostic_copy_failure_uses_the_same_selectable_fallback", page_data)


def test_raw_and_percent_deep_links_never_compute_unused_base64(page_data: dict[str, object]) -> None:
    run_case("test_raw_and_percent_deep_links_never_compute_unused_base64", page_data)


def test_unicode_base64_deep_link_contains_an_ascii_url(page_data: dict[str, object]) -> None:
    run_case("test_unicode_base64_deep_link_contains_an_ascii_url", page_data)


def test_one_invalid_import_action_cannot_replace_the_page_with_an_error(page_data: dict[str, object]) -> None:
    run_case("test_one_invalid_import_action_cannot_replace_the_page_with_an_error", page_data)


def test_recommended_app_has_three_localized_connect_steps(page_data: dict[str, object]) -> None:
    run_case("test_recommended_app_has_three_localized_connect_steps", page_data)


def test_manual_import_has_selectable_url_and_the_chosen_apps_menu(page_data: dict[str, object]) -> None:
    run_case("test_manual_import_has_selectable_url_and_the_chosen_apps_menu", page_data)


def test_platform_picker_drives_apps_and_resets_choice(page_data: dict[str, object]) -> None:
    run_case("test_platform_picker_drives_apps_and_resets_choice", page_data)


def test_app_without_import_scheme_shows_manual_route_directly(page_data: dict[str, object]) -> None:
    run_case("test_app_without_import_scheme_shows_manual_route_directly", page_data)


def test_clash_desktop_steps_include_tun_while_mobile_steps_do_not(page_data: dict[str, object]) -> None:
    run_case("test_clash_desktop_steps_include_tun_while_mobile_steps_do_not", page_data)


def test_install_alternatives_are_labelled_and_platform_specific(page_data: dict[str, object]) -> None:
    run_case("test_install_alternatives_are_labelled_and_platform_specific", page_data)


def test_stash_import_is_percent_encoded_and_keeps_additional_status(page_data: dict[str, object]) -> None:
    run_case("test_stash_import_is_percent_encoded_and_keeps_additional_status", page_data)


def test_long_connection_lists_reveal_locally_in_subscription_order(page_data: dict[str, object]) -> None:
    run_case("test_long_connection_lists_reveal_locally_in_subscription_order", page_data)


def test_suspended_account_also_dims_individual_imports(page_data: dict[str, object]) -> None:
    run_case("test_suspended_account_also_dims_individual_imports", page_data)


@pytest.mark.parametrize(
    "case",
    [
        "test_single_platform_labels_follow_persian",
        "test_error_document_title_uses_the_saved_language",
        "test_account_formats_persian_numbers_and_calendar",
        "test_account_not_found_is_verified_and_disables_import",
    ],
)
def test_locale_and_account_boundaries(page_data: dict[str, object], case: str) -> None:
    run_case(case, page_data)
