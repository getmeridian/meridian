#!/usr/bin/env bash
# Make a lab VM look like a small VPS from another provider, then reboot it.
#
#   tests/realvm/small_vps.sh "$VM_EXIT" 1G           # 1 vCPU, ~960 MB, no swap
#   tests/realvm/small_vps.sh "$VM_EXIT" 2G --swap 2G # what testers add by hand
#
# Always deletes Hetzner's 00InstallRecommends, so apt installs Recommends as
# stock Debian and Ubuntu images do. --swap also sets vm.overcommit_memory=1,
# as the Valkey warning tells people to.
set -euo pipefail

IP=${1:?usage: small_vps.sh IP MEMORY [--swap SIZE]}
MEM=${2:?usage: small_vps.sh IP MEMORY [--swap SIZE]}
SWAP=""
[[ ${3:-} == --swap ]] && SWAP=${4:?--swap needs a size such as 2G}

ssh -o BatchMode=yes "root@$IP" bash -s -- "$MEM" "$SWAP" <<'REMOTE'
set -euo pipefail
mem=$1 swap=$2
rm -f /etc/apt/apt.conf.d/00InstallRecommends
if ! grep -q "mem=$mem maxcpus=1" /etc/default/grub; then
  sed -i -E 's/ ?mem=[0-9]+[MG] maxcpus=1//; s/^GRUB_CMDLINE_LINUX_DEFAULT="/&mem='"$mem"' maxcpus=1 /' /etc/default/grub
  update-grub >/dev/null 2>&1
fi
if [[ -n $swap ]] && ! grep -q '^/swapfile ' /etc/fstab; then
  fallocate -l "$swap" /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'vm.overcommit_memory = 1' > /etc/sysctl.d/90-overcommit.conf
fi
(sleep 1; reboot) >/dev/null 2>&1 &
REMOTE
echo "Rebooting; back in about 30 s with $MEM RAM and one vCPU."
