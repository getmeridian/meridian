"""Imported snapshot state keeps the destination file's write guard."""

import os

import pytest

from meridian import config
from meridian.cluster import ClusterConfig, ClusterConfigExternallyModifiedError
from meridian.commands import restore as restore_command
from meridian.core.access_profiles import profile_declarations_hash
from tests.test_backup_restore import fleet as fleet
from tests.test_profile_restore_fence import changed_snapshot, open_snapshot


@pytest.mark.parametrize("edited", [False, True], ids=["empty-file", "edited-after-load"])
def test_snapshot_import_binds_its_save_guard_to_the_current_empty_context(fleet, edited: bool):
    _cluster, path = fleet
    snapshot = changed_snapshot(open_snapshot(path), declarations_hash=profile_declarations_hash([]), history=False)
    config.SERVER_PROFILES_FILE.unlink()
    config.CLUSTER_CONFIG.unlink()
    ClusterConfig().save()
    os.utime(config.CLUSTER_CONFIG, ns=(1_000_000_000, 1_000_000_000))
    current = ClusterConfig.load()
    saved = config.CLUSTER_CONFIG.read_bytes()
    if edited:
        os.utime(config.CLUSTER_CONFIG, ns=(2_000_000_000, 2_000_000_000))
        with pytest.raises(ClusterConfigExternallyModifiedError) as refused:
            restore_command._fleet_state(current, snapshot)
        assert "loaded=1000000000, now=2000000000" in str(refused.value)
        assert config.CLUSTER_CONFIG.read_bytes() == saved
        assert not config.SERVER_PROFILES_FILE.exists()
    else:
        restored = restore_command._fleet_state(current, snapshot)
        assert restored.access_profiles_used is True
        assert ClusterConfig.load().access_profiles_used is True
        assert restored._loaded_mtime_ns == config.CLUSTER_CONFIG.stat().st_mtime_ns
        assert config.SERVER_PROFILES_FILE.exists()
