"""A browser export restores the panel edit, but the browser never claims an off-server save."""

from pathlib import Path

from playwright.sync_api import Page, expect

from meridian.control.backup_models import BackupReceipt
from meridian.control.models import DeployerStateDeposit, DeviceKeyCreate, RecoveryKeyUpdate
from meridian.control.owner import CONFIRM_SECONDS
from meridian.control.service import ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from tests.e2e.helpers import ready
from tests.e2e.support import OWNER_PASSWORD


def test_owner_saves_an_encrypted_copy_after_identity_confirmation(
    owner: Page, service: ControlService, clock, browser_faults, tmp_path: Path
) -> None:
    ready(owner, "overview")
    card = owner.locator("[data-backup-status]")
    expect(card.get_by_role("button", name="Download encrypted copy")).not_to_be_visible()
    key = generate_key_pair()
    service.backups.set_recovery_key(RecoveryKeyUpdate(public_key=key.public))
    service.backups.deposit(DeployerStateDeposit(cluster_yml="version: 5", servers_json="[]", generation=1))
    owner.locator("#view").evaluate("view => view.meridianLiveStop.refresh(true)")
    expect(card).to_contain_text("No registered managing computer has confirmed")
    # A prior managing-device save is distinct from the browser export tested below.
    device_key = generate_key_pair()
    device = service.backups.register_device(DeviceKeyCreate(name="computer", public_key=device_key.public))
    prior = tmp_path / "previous.snapshot"
    prior.write_bytes(service.snapshot())
    proof = decrypt_snapshot(prior.read_bytes(), [device_key.secret]).save_proof
    assert proof is not None
    confirmed = service.backups.record_save(
        BackupReceipt(device_id=device.id, changes=proof.changes, proof=proof.proof)
    )
    owner.locator("#view").evaluate("view => view.meridianLiveStop.refresh(true)")
    expect(card).to_contain_text("all current changes")

    owner.get_by_role("navigation").get_by_role("link", name="Settings", exact=True).click()
    owner.get_by_role("button", name="Edit connection settings").click()
    edit = owner.get_by_role("dialog", name="Edit connection settings", exact=True)
    edit.get_by_label("Title", exact=True).fill("Edited on my phone")
    edit.get_by_role("button", name="Review changes").click()
    owner.get_by_role("dialog", name="Review changes").get_by_role("button", name="Save", exact=True).click()
    expect(edit).not_to_be_visible()
    owner.get_by_role("navigation").get_by_role("link", name="Overview", exact=True).click()
    expect(card).to_contain_text("Changes since the last confirmed save: 1.")
    clock.advance(seconds=CONFIRM_SECONDS + 1)
    card.get_by_role("button", name="Download encrypted copy").click()
    dialog = owner.get_by_role("dialog", name="Download encrypted copy", exact=True)
    browser_faults.expect(r"console: .*?/api/backup/download .*403")
    dialog.get_by_role("button", name="Prepare file").click()
    confirm = owner.get_by_role("dialog", name="Confirm it's you")
    expect(confirm).to_be_visible()
    confirm.get_by_label("Password", exact=True).fill(OWNER_PASSWORD)
    confirm.get_by_role("button", name="Confirm", exact=True).click()
    with owner.expect_download() as downloaded:
        dialog.get_by_role("link", name="Save file").click()
    artifact = downloaded.value
    assert artifact.suggested_filename == "backup.snapshot"
    filename = tmp_path / artifact.suggested_filename
    artifact.save_as(filename)
    contents = decrypt_snapshot(filename.read_bytes(), [key.secret])
    assert contents.header.fleet_id == service.fleet_id
    recovered_file = tmp_path / "recovered.db"
    recovered_file.write_bytes(contents.database)
    recovered = ControlService(recovered_file, fleet_id=service.fleet_id, version="4")
    try:
        assert recovered.settings().profile_title == "Edited on my phone"
    finally:
        recovered.close()
    dialog.get_by_role("button", name="Close", exact=True).last.click()
    owner.locator("#view").evaluate("view => view.meridianLiveStop.refresh(true)")
    expect(card).to_contain_text("Changes since the last confirmed save: 1.")
    assert service.backups.status().last_saved_changes == confirmed.last_saved_changes
