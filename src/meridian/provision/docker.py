"""Docker provisioning step.

InstallDocker is panel-agnostic — it installs Docker CE from the official
repository. The panel/node container deployment is handled by
remnawave_panel.py and node_deploy.py respectively.
"""

from __future__ import annotations

import shlex

from meridian.facts import ServerFacts
from meridian.provision.ensure import ensure_service_running
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

# Conflicting Docker packages to remove before installing docker-ce
_CONFLICTING_PACKAGES = [
    "docker.io",
    "docker-compose",
    "docker-doc",
    "podman-docker",
    "containerd",
    "runc",
]


class InstallDocker:
    """Install Docker CE from the official repository."""

    name = "Install Docker"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        facts = ServerFacts(conn)
        # Check if Docker is already installed
        docker_state = facts.docker_state()
        docker_installed = docker_state.installed

        if docker_installed:
            # Ensure compose plugin is available (docker.io from distro
            # doesn't include it; docker-ce does but might be missing)
            if not docker_state.compose_available:
                conn.run(
                    "apt-get update -qq && apt-get install -y -qq docker-compose-plugin 2>/dev/null; true",
                    timeout=120,
                    env={"DEBIAN_FRONTEND": "noninteractive"},
                )
                # Verify it's now available
                recheck = conn.run("docker compose version", timeout=15)
                if recheck.returncode != 0:
                    return StepResult(
                        name=self.name,
                        status="failed",
                        detail=(
                            "docker compose plugin not available — "
                            "install docker-compose-plugin or upgrade to docker-ce"
                        ),
                    )

            if docker_state.has_running_containers:
                return StepResult(
                    name=self.name,
                    status="skipped",
                    detail="Docker running with containers",
                )

        # Check if docker-ce is specifically installed
        ce_check = conn.run("dpkg-query -W -f='${Status}' docker-ce 2>/dev/null", timeout=15)
        docker_ce_installed = ce_check.returncode == 0 and "install ok installed" in ce_check.stdout

        if docker_ce_installed:
            # Ensure Docker service is running
            ensure_service_running(conn, "docker", timeout=30)
            # Verify compose plugin (might be missing if manually removed)
            compose_check = conn.run("docker compose version", timeout=15)
            if compose_check.returncode != 0:
                conn.run(
                    "apt-get install -y -qq docker-compose-plugin 2>/dev/null; true",
                    timeout=120,
                    env={"DEBIAN_FRONTEND": "noninteractive"},
                )
            return StepResult(
                name=self.name,
                status="ok",
                detail="docker-ce already installed",
            )

        # Remove conflicting packages (only when docker-ce is NOT installed)
        if not docker_ce_installed:
            pkg_list = " ".join(_CONFLICTING_PACKAGES)
            conn.run(
                f"apt-get remove -y {pkg_list} 2>/dev/null",
                timeout=120,
                env={"DEBIAN_FRONTEND": "noninteractive"},
            )

        # Install prerequisites
        result = conn.run(
            "apt-get install -y -qq ca-certificates curl gnupg",
            timeout=120,
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"prerequisite install failed: {result.stderr.strip()[:200]}",
            )

        # Create keyrings directory
        conn.run("mkdir -p /etc/apt/keyrings && chmod 755 /etc/apt/keyrings", timeout=15)

        # Detect distro for Docker repo
        os_release = facts.os_release()
        distro_name = os_release.id
        distro_codename = os_release.version_codename
        distro_arch = facts.dpkg_arch()

        # Add Docker GPG key
        gpg_url = f"https://download.docker.com/linux/{distro_name}/gpg"
        result = conn.run(
            f"curl -fsSL {shlex.quote(gpg_url)} -o /etc/apt/keyrings/docker.asc"
            " && chmod 644 /etc/apt/keyrings/docker.asc",
            timeout=60,
        )
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to add Docker GPG key: {result.stderr.strip()[:200]}",
            )

        # Add Docker apt repository
        repo_line = (
            f"deb [arch={distro_arch} signed-by=/etc/apt/keyrings/docker.asc] "
            f"https://download.docker.com/linux/{distro_name} "
            f"{distro_codename} stable"
        )
        result = conn.put_text("/etc/apt/sources.list.d/docker.list", repo_line + "\n", mode="644", timeout=15)
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to add Docker repo: {result.stderr.strip()[:200]}",
            )

        # Install Docker CE
        result = conn.run(
            "apt-get update -qq && apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin",
            timeout=300,
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "no longer has a Release file" in stderr:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=(
                        "OS version is end-of-life — package repos have been removed. "
                        "Reinstall with an Ubuntu LTS version"
                    ),
                )
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"docker-ce install failed: {stderr[:200]}",
            )

        # Ensure Docker service is started and enabled
        ensure_service_running(conn, "docker", timeout=15)

        # Disable secretservice credential helper — headless servers lack D-Bus
        # secret service, which makes `docker compose pull` fail even for
        # public images.  Stripping credsStore lets Docker work without a
        # keyring while preserving the rest of the config.
        conn.run(
            "test -f ~/.docker/config.json"
            " && jq 'del(.credsStore)' ~/.docker/config.json > ~/.docker/config.json.tmp"
            " && mv ~/.docker/config.json.tmp ~/.docker/config.json"
            " || true",
            timeout=15,
        )

        return StepResult(name=self.name, status="changed")
