"""Tests for OS-level provisioning steps (common.py).

Each step is tested for idempotency: already-done returns "ok"/"skipped",
needs-change returns "changed", and failures return "failed".
"""

from __future__ import annotations

from meridian.provision.common import (
    _SSH_HARDENING_DROPIN,
    AUTO_UPGRADES_CONF,
    FAIL2BAN_JAIL_CONF,
    REQUIRED_PACKAGES,
    ConfigureBBR,
    ConfigureFail2ban,
    ConfigureFirewall,
    EnableAutoUpgrades,
    HardenSSH,
    InstallPackages,
    SetTimezone,
)
from meridian.provision.steps import ProvisionContext
from tests.support.mock_connection import MockConnection

# ---------------------------------------------------------------------------
# InstallPackages
# ---------------------------------------------------------------------------


def _dpkg(installed: list[str], remembered: tuple[str, ...] = ()) -> str:
    """dpkg-query rows: installed packages, plus ones dpkg only remembers ("un")."""
    rows = [f"{name}\tinstall ok installed" for name in installed]
    rows += [f"{name}\tunknown ok not-installed" for name in remembered]
    return "\n".join(rows) + "\n"


class TestInstallPackages:
    def test_package_dpkg_only_remembers_is_installed(self, mock_conn: MockConnection, base_ctx):
        """Debian lists a never-installed recommended package as "un"; it is still missing."""
        mock_conn.when("dpkg-query", rc=1, stdout=_dpkg(REQUIRED_PACKAGES, remembered=("python3-systemd",)))

        result = InstallPackages([*REQUIRED_PACKAGES, "python3-systemd"]).run(mock_conn, base_ctx)

        assert result.status == "changed"
        mock_conn.assert_called_with_pattern("apt-get install -y -qq python3-systemd")

    def test_all_present_returns_ok(self, mock_conn: MockConnection, base_ctx):
        """When all packages are already installed, no apt-get install is called."""
        mock_conn.when("dpkg-query", stdout=_dpkg(REQUIRED_PACKAGES))

        result = InstallPackages().run(mock_conn, base_ctx)

        assert result.status == "ok"
        mock_conn.assert_not_called_with_pattern("apt-get install")

    def test_missing_packages_returns_changed(self, mock_conn: MockConnection, base_ctx):
        """When some packages are missing, apt-get install is called and status is changed."""
        # Return only a subset of required packages
        installed = REQUIRED_PACKAGES[:3]
        mock_conn.when("dpkg-query", stdout=_dpkg(installed))

        result = InstallPackages().run(mock_conn, base_ctx)

        assert result.status == "changed"
        mock_conn.assert_called_with_pattern("apt-get install")

    def test_installs_cron_for_acme_and_watchdog_jobs(self, mock_conn: MockConnection, base_ctx):
        """Cron is required for cert renewal, stats refresh, and the watchdog."""
        installed = [pkg for pkg in REQUIRED_PACKAGES if pkg != "cron"]
        mock_conn.when("dpkg-query", stdout=_dpkg(installed))

        result = InstallPackages().run(mock_conn, base_ctx)

        assert result.status == "changed"
        mock_conn.assert_called_with_pattern("apt-get install -y -qq cron")

    def test_apt_fails_returns_failed(self, mock_conn: MockConnection, base_ctx):
        """When apt-get install fails, status is failed."""
        mock_conn.when("dpkg-query", stdout="")
        mock_conn.when("apt-get install", rc=1, stderr="E: broken packages")

        result = InstallPackages().run(mock_conn, base_ctx)

        assert result.status == "failed"

    def test_eol_apt_update_gives_actionable_error(self, mock_conn: MockConnection, base_ctx):
        """When apt-get update fails with EOL Release file error, message suggests LTS."""
        mock_conn.when("dpkg-query", stdout="")
        mock_conn.when(
            "apt-get update",
            rc=1,
            stderr=(
                "E: The repository 'http://security.ubuntu.com/ubuntu oracular-security Release' "
                "no longer has a Release file."
            ),
        )

        result = InstallPackages().run(mock_conn, base_ctx)

        assert result.status == "failed"
        assert "end-of-life" in result.detail
        assert "Ubuntu LTS" in result.detail


# ---------------------------------------------------------------------------
# EnableAutoUpgrades
# ---------------------------------------------------------------------------


class TestEnableAutoUpgrades:
    def test_already_configured_returns_ok(self, mock_conn: MockConnection, base_ctx):
        """When config file already has expected content, nothing is written."""
        mock_conn.when("cat", stdout=AUTO_UPGRADES_CONF)

        result = EnableAutoUpgrades().run(mock_conn, base_ctx)

        assert result.status == "ok"

    def test_writes_config_returns_changed(self, mock_conn: MockConnection, base_ctx):
        """When config file is missing or empty, it is written."""
        mock_conn.when("cat /etc/apt", stdout="", rc=1)
        mock_conn.when("cat >", rc=0)

        result = EnableAutoUpgrades().run(mock_conn, base_ctx)

        assert result.status == "changed"


# ---------------------------------------------------------------------------
# SetTimezone
# ---------------------------------------------------------------------------


class TestSetTimezone:
    def test_already_utc_returns_ok(self, mock_conn: MockConnection, base_ctx):
        """When timezone is already UTC, no change is made."""
        mock_conn.when("timedatectl", stdout="UTC")

        result = SetTimezone().run(mock_conn, base_ctx)

        assert result.status == "ok"

    def test_changes_timezone_returns_changed(self, mock_conn: MockConnection, base_ctx):
        """When timezone differs, it is changed to UTC."""
        mock_conn.when("timedatectl show", stdout="America/New_York")
        mock_conn.when("timedatectl set-timezone", rc=0)

        result = SetTimezone().run(mock_conn, base_ctx)

        assert result.status == "changed"


# ---------------------------------------------------------------------------
# HardenSSH
# ---------------------------------------------------------------------------


class TestHardenSSH:
    def test_already_hardened_returns_ok(self, mock_conn: MockConnection, base_ctx):
        """When Meridian's drop-in is present and effective config matches, status is ok."""
        mock_conn.when("cat /etc/ssh/sshd_config.d/00-hardening.conf", stdout=_SSH_HARDENING_DROPIN)
        mock_conn.when("sshd -t", rc=0)
        mock_conn.when("sshd -T", rc=0)

        result = HardenSSH().run(mock_conn, base_ctx)

        assert result.status == "ok"

    def test_hardens_ssh_returns_changed(self, mock_conn: MockConnection, base_ctx):
        """When drop-in is missing, SSH is hardened and sshd restarted."""
        mock_conn.when("cat /etc/ssh/sshd_config.d/00-hardening.conf", rc=1)
        mock_conn.when("mkdir -p /etc/ssh/sshd_config.d", rc=0)
        mock_conn.when("rm -f /etc/ssh/sshd_config.d/99-meridian.conf", rc=0)
        mock_conn.when("sshd -t", rc=0)
        mock_conn.when("sshd -T", rc=0)

        result = HardenSSH().run(mock_conn, base_ctx)

        assert result.status == "changed"
        mock_conn.assert_called_with_pattern("systemctl restart sshd")

    def test_migrates_old_dropin_on_fresh_write(self, mock_conn: MockConnection, base_ctx):
        """When writing the new 00-hardening.conf, older project-named drop-ins are removed."""
        mock_conn.when("cat /etc/ssh/sshd_config.d/00-hardening.conf", rc=1)
        mock_conn.when("mkdir -p /etc/ssh/sshd_config.d", rc=0)
        mock_conn.when("rm -f /etc/ssh/sshd_config.d/99-meridian.conf", rc=0)
        mock_conn.when("sshd -t", rc=0)
        mock_conn.when("sshd -T", rc=0)

        result = HardenSSH().run(mock_conn, base_ctx)

        assert result.status == "changed"
        mock_conn.assert_called_with_pattern("rm -f /etc/ssh/sshd_config.d/99-meridian.conf")
        mock_conn.assert_called_with_pattern("/etc/ssh/sshd_config.d/00-meridian.conf")

    def test_sshd_validation_fails_returns_failed(self, mock_conn: MockConnection, base_ctx):
        """When sshd -t validation fails, status is failed."""
        mock_conn.when("cat /etc/ssh/sshd_config.d/00-hardening.conf", rc=1)
        mock_conn.when("sshd -t", rc=1, stderr="sshd_config: bad configuration")

        result = HardenSSH().run(mock_conn, base_ctx)

        assert result.status == "failed"
        assert "validation failed" in result.detail

    def test_effective_setting_mismatch_returns_failed(self, mock_conn: MockConnection, base_ctx):
        """When sshd -T still reports password auth enabled, fail clearly."""
        mock_conn.when("cat /etc/ssh/sshd_config.d/00-hardening.conf", stdout=_SSH_HARDENING_DROPIN.strip())
        mock_conn.when("sshd -t", rc=0)
        mock_conn.when("sshd -T | grep -q '^passwordauthentication no$'", rc=1)

        result = HardenSSH().run(mock_conn, base_ctx)

        assert result.status == "failed"
        assert "effective sshd setting mismatch" in result.detail

    def test_debianbanner_unrecognized_skips_verification(self, mock_conn: MockConnection, base_ctx):
        """When sshd doesn't recognize DebianBanner, skip its verification (issue #20)."""
        mock_conn.when("cat /etc/ssh/sshd_config.d/00-hardening.conf", stdout=_SSH_HARDENING_DROPIN)
        mock_conn.when("sshd -t", rc=0)
        # Required settings pass
        mock_conn.when("sshd -T | grep -q '^passwordauthentication no$'", rc=0)
        mock_conn.when("sshd -T | grep -q '^kbdinteractiveauthentication no$'", rc=0)
        # sshd doesn't recognize DebianBanner — grep for the key returns nothing
        mock_conn.when("sshd -T | grep -qi '^debianbanner'", rc=1)

        result = HardenSSH().run(mock_conn, base_ctx)

        assert result.status == "ok"


# ---------------------------------------------------------------------------
# ConfigureFail2ban
# ---------------------------------------------------------------------------


class TestConfigureFail2ban:
    def test_already_running_with_journal_jail_returns_ok(self, mock_conn: MockConnection, base_ctx):
        """When the jail is current and fail2ban is active, nothing restarts."""
        mock_conn.when("cat /etc/fail2ban/jail.d/sshd-journal.local", stdout=FAIL2BAN_JAIL_CONF)
        mock_conn.when("systemctl is-active fail2ban", stdout="active")

        result = ConfigureFail2ban().run(mock_conn, base_ctx)

        assert result.status == "ok"
        assert not any("restart" in command for command in mock_conn.calls)

    def test_new_jail_restarts_a_running_fail2ban(self, mock_conn: MockConnection, base_ctx):
        """Debian 12 has no auth.log: the journal jail must take effect even if fail2ban runs."""
        mock_conn.when("cat /etc/fail2ban/jail.d/sshd-journal.local", rc=1)
        mock_conn.when("cat >", rc=0)
        mock_conn.when("systemctl is-active fail2ban", stdout="active")
        mock_conn.when("systemctl is-enabled fail2ban", stdout="enabled")
        mock_conn.when("systemctl restart fail2ban", rc=0)

        result = ConfigureFail2ban().run(mock_conn, base_ctx)

        assert result.status == "changed"
        assert any(command.startswith("systemctl restart fail2ban") for command in mock_conn.calls)
        assert mock_conn.write_map["/etc/fail2ban/jail.d/sshd-journal.local"] == FAIL2BAN_JAIL_CONF.encode()

    def test_stopped_restarts(self, mock_conn: MockConnection, base_ctx):
        """When fail2ban is not running, it is enabled and restarted."""
        mock_conn.when("cat /etc/fail2ban/jail.d/sshd-journal.local", stdout=FAIL2BAN_JAIL_CONF)
        mock_conn.when("sleep 2; systemctl is-active fail2ban", stdout="active")
        mock_conn.when("systemctl is-active fail2ban", rc=3, stdout="inactive")
        mock_conn.when("systemctl enable fail2ban", rc=0)
        mock_conn.when("systemctl restart fail2ban", rc=0)

        result = ConfigureFail2ban().run(mock_conn, base_ctx)

        assert result.status == "changed"

    def test_fail2ban_exiting_after_start_fails_with_its_log(self, mock_conn: MockConnection, base_ctx):
        """A start that systemd accepts but fail2ban abandons must not pass."""
        mock_conn.when("cat /etc/fail2ban/jail.d/sshd-journal.local", stdout=FAIL2BAN_JAIL_CONF)
        mock_conn.when("systemctl is-active fail2ban", rc=3, stdout="failed")
        mock_conn.when("systemctl enable fail2ban", rc=0)
        mock_conn.when("systemctl restart fail2ban", rc=0)
        mock_conn.when("journalctl -u fail2ban", stdout="ERROR Have not found any log file for sshd jail")

        result = ConfigureFail2ban().run(mock_conn, base_ctx)

        assert result.status == "failed"
        assert "Have not found any log file" in result.detail

    def test_enable_failure_returns_failed(self, mock_conn: MockConnection, base_ctx):
        """When systemctl enable fails, status is failed."""
        mock_conn.when("cat /etc/fail2ban/jail.d/sshd-journal.local", stdout=FAIL2BAN_JAIL_CONF)
        mock_conn.when("systemctl is-active fail2ban", rc=3, stdout="inactive")
        mock_conn.when("systemctl enable fail2ban", rc=1, stderr="Failed to enable")

        result = ConfigureFail2ban().run(mock_conn, base_ctx)

        assert result.status == "failed"
        assert "failed to enable" in result.detail

    def test_service_start_failure_returns_failed(self, mock_conn: MockConnection, base_ctx):
        """When systemctl restart fails, status is failed."""
        mock_conn.when("cat /etc/fail2ban/jail.d/sshd-journal.local", stdout=FAIL2BAN_JAIL_CONF)
        mock_conn.when("systemctl is-active fail2ban", rc=3, stdout="inactive")
        mock_conn.when("systemctl enable fail2ban", rc=0)
        mock_conn.when("systemctl restart fail2ban", rc=1, stderr="Unit not found")

        result = ConfigureFail2ban().run(mock_conn, base_ctx)

        assert result.status == "failed"
        assert "failed to start" in result.detail


# ---------------------------------------------------------------------------
# ConfigureBBR
# ---------------------------------------------------------------------------


class TestConfigureBBR:
    def test_already_enabled_returns_ok(self, mock_conn: MockConnection, base_ctx):
        """When BBR and fq qdisc are already active, status is ok."""
        mock_conn.when("net.ipv4.tcp_congestion_control", stdout="bbr")
        mock_conn.when("net.core.default_qdisc", stdout="fq")

        result = ConfigureBBR().run(mock_conn, base_ctx)

        assert result.status == "ok"

    def test_enables_bbr_returns_changed(self, mock_conn: MockConnection, base_ctx):
        """When congestion control is cubic, BBR is enabled."""
        mock_conn.when("net.ipv4.tcp_congestion_control", stdout="cubic")

        result = ConfigureBBR().run(mock_conn, base_ctx)

        assert result.status == "changed"
        mock_conn.assert_called_with_pattern("sysctl -w")


# ---------------------------------------------------------------------------
# ConfigureFirewall
# ---------------------------------------------------------------------------


class TestConfigureFirewall:
    def test_ufw_not_found_returns_failed(self, mock_conn: MockConnection, base_ctx):
        """When ufw binary is not found and install fails, status is failed."""
        mock_conn.when("which ufw", rc=1)
        mock_conn.when("apt-get update", stdout="")
        mock_conn.when("apt-get install", stdout="")

        result = ConfigureFirewall().run(mock_conn, base_ctx)

        assert result.status == "failed"
        assert "ufw not available" in result.detail
        mock_conn.assert_called_with_pattern("apt-get install")

    def test_ufw_installed_after_retry(self, mock_conn: MockConnection, base_ctx):
        """When ufw is missing but apt install succeeds, step continues."""
        # First check (with 2>/dev/null) fails; recheck (without) succeeds
        mock_conn.when("which ufw 2>/dev/null", rc=1)
        mock_conn.when("which ufw", rc=0)
        mock_conn.when("apt-get update", stdout="")
        mock_conn.when("apt-get install", stdout="")
        mock_conn.when("ufw status", stdout="Status: inactive")
        mock_conn.when("ufw allow", stdout="Rule added")
        mock_conn.when("ufw delete", rc=0, stdout="Could not")
        mock_conn.when("ufw default", rc=0)
        mock_conn.when("ufw enable", stdout="Firewall is active")

        result = ConfigureFirewall().run(mock_conn, base_ctx)

        assert result.status == "changed"
        mock_conn.assert_called_with_pattern("apt-get install")

    def test_allows_the_live_session_port_even_when_sshd_reports_another(self, mock_conn: MockConnection, base_ctx):
        """A socket-activated or NAT-mapped SSH port must never be locked out."""
        mock_conn.port = 2222
        mock_conn.when("which ufw", rc=0)
        mock_conn.when("ufw status", stdout="Status: inactive")
        mock_conn.when("sshd -T", stdout="port 22\n")
        mock_conn.when("ufw allow", stdout="Rule added")
        mock_conn.when("ufw delete", rc=0, stdout="Could not")
        mock_conn.when("ufw default", rc=0)
        mock_conn.when("ufw enable", stdout="Firewall is active")

        ConfigureFirewall().run(mock_conn, base_ctx)

        allows = [call for call in mock_conn.calls if "ufw allow" in call]
        assert any("22/tcp" in call and "2222" not in call for call in allows)
        assert any("2222/tcp" in call for call in allows)
        enable_index = next(i for i, call in enumerate(mock_conn.calls) if "ufw" in call and "enable" in call)
        port_index = next(i for i, call in enumerate(mock_conn.calls) if "2222/tcp" in call)
        assert port_index < enable_index

    def test_v4_baseline_opens_no_public_port_and_drops_the_untagged_udp_443_rule(self, mock_conn: MockConnection):
        """Earlier 4.0 betas opened an untagged 443/udp from the control-plane bootstrap."""
        mock_conn.when("which ufw", rc=0)
        mock_conn.when("ufw status numbered", stdout="5\n2\n")
        mock_conn.when("ufw status", stdout="Status: active")
        mock_conn.when("ufw allow", stdout="Skipping adding existing rule")
        mock_conn.when("ufw default", rc=0)

        result = ConfigureFirewall().run(mock_conn, ProvisionContext(ip="198.51.100.1", hosted_page=True))

        assert result.status == "changed"
        assert not any("ufw allow 443" in call or "allow 80/tcp" in call for call in mock_conn.calls)
        cleanup = next(call for call in mock_conn.calls if "ufw status numbered" in call)
        assert "443\\/udp" in cleanup and "Anywhere" in cleanup and "sort -rn" in cleanup

    def test_already_active_returns_ok(self, mock_conn: MockConnection, base_ctx):
        """When ufw is active and rules already exist, status is ok."""
        mock_conn.when("which ufw", rc=0)
        mock_conn.when("ufw status numbered", stdout="")  # no untagged 443/udp rule to drop
        mock_conn.when("ufw status", stdout="Status: active\n22/tcp ALLOW\n443/tcp ALLOW")
        # All ufw allow commands return "Skipping" (rule exists)
        mock_conn.when("ufw allow", stdout="Skipping adding existing rule")
        mock_conn.when("ufw delete", rc=0, stdout="Skipping")
        mock_conn.when("ufw default", rc=0)
        mock_conn.when("ufw reload", rc=0)

        result = ConfigureFirewall().run(mock_conn, base_ctx)

        assert result.status == "ok"

    def test_enables_ufw_returns_changed(self, mock_conn: MockConnection, base_ctx):
        """When ufw is inactive, it is enabled and status is changed."""
        mock_conn.when("which ufw", rc=0)
        mock_conn.when("ufw status", stdout="Status: inactive")
        mock_conn.when("ufw allow", stdout="Rule added")
        mock_conn.when("ufw delete", rc=0, stdout="Could not")
        mock_conn.when("ufw default", rc=0)
        mock_conn.when("ufw enable", stdout="Firewall is active")

        result = ConfigureFirewall().run(mock_conn, base_ctx)

        assert result.status == "changed"

    def test_allows_detected_custom_ssh_port(self, mock_conn: MockConnection, base_ctx):
        """The firewall must follow the live sshd port instead of hardcoding 22."""
        mock_conn.when("which ufw", rc=0)
        mock_conn.when("sshd -T", stdout="2222\n")
        mock_conn.when("ufw status", stdout="Status: active")
        mock_conn.when("ufw allow", stdout="Skipping adding existing rule")
        mock_conn.when("ufw delete", rc=0, stdout="Skipping")
        mock_conn.when("ufw default", rc=0)
        mock_conn.when("ufw reload", rc=0)

        ConfigureFirewall().run(mock_conn, base_ctx)

        mock_conn.assert_called_with_pattern("ufw allow 2222/tcp")
        mock_conn.assert_not_called_with_pattern("ufw allow 22/tcp")

    def test_falls_back_to_port_22_when_detection_fails(self, mock_conn: MockConnection, base_ctx):
        mock_conn.when("which ufw", rc=0)
        mock_conn.when("sshd -T", rc=1)
        mock_conn.when("grep -hEi", rc=1)
        mock_conn.when("ufw status", stdout="Status: active")
        mock_conn.when("ufw allow", stdout="Skipping adding existing rule")
        mock_conn.when("ufw delete", rc=0, stdout="Skipping")
        mock_conn.when("ufw default", rc=0)
        mock_conn.when("ufw reload", rc=0)

        ConfigureFirewall().run(mock_conn, base_ctx)

        mock_conn.assert_called_with_pattern("ufw allow 22/tcp")

    def test_preserves_user_managed_tcp_rules(self, mock_conn: MockConnection, base_ctx):
        """Custom non-Meridian ports must not be deleted during cleanup."""
        mock_conn.when("which ufw", rc=0)
        mock_conn.when("ufw status", stdout="Status: active\n22/tcp ALLOW\n443/tcp ALLOW\n9100/tcp ALLOW")
        mock_conn.when("ufw allow", stdout="Skipping adding existing rule")
        mock_conn.when("ufw delete", rc=0, stdout="Skipping")
        mock_conn.when("ufw default", rc=0)
        mock_conn.when("ufw reload", rc=0)

        ConfigureFirewall().run(mock_conn, base_ctx)

        mock_conn.assert_not_called_with_pattern("ufw delete allow 9100/tcp")
