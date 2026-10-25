#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
role=${1:?Specify core or node}
case "$role" in core|node) ;; *) exit 2 ;; esac
exec > >(tee -a /var/log/remnawave-provision-base.log) 2>&1
trap 'code=$?; printf "FAILED line=%s exit=%s\n" "$LINENO" "$code"; exit "$code"' ERR
printf 'Starting base preparation role=%s at %s\n' "$role" "$(date -Is)"

if [[ "$role" == node ]] && [[ -d /usr/local/x-ui || -d /etc/x-ui ]]; then
    backup_dir=/root/backups/3x-ui-before-remnawave-$(date +%Y%m%d-%H%M%S)
    install -d -m 700 "$backup_dir"
    nft list ruleset > "$backup_dir/nftables-before.txt" 2>/dev/null || true
    crontab -l > "$backup_dir/crontab-before.txt" 2>/dev/null || true
    systemctl stop x-ui.service
    paths=()
    for path in /usr/local/x-ui /etc/x-ui /var/log/x-ui /etc/systemd/system/x-ui.service /etc/systemd/system/x-ui.service.d /usr/bin/x-ui /usr/local/bin/x-ui /etc/default/x-ui /etc/sysconfig/x-ui /root/install-3x-ui.sh; do
        if [[ -e "$path" || -L "$path" ]]; then paths+=("${path#/}"); fi
    done
    if ! tar -C / -czf "$backup_dir/3x-ui.tar.gz" "${paths[@]}"; then
        systemctl start x-ui.service
        exit 1
    fi
    tar -tzf "$backup_dir/3x-ui.tar.gz" > "$backup_dir/contents.txt"
    sha256sum "$backup_dir/3x-ui.tar.gz" > "$backup_dir/SHA256SUMS"
    systemctl disable x-ui.service
    for path in "${paths[@]}"; do rm -rf -- "/$path"; done
    systemctl daemon-reload
    systemctl reset-failed x-ui.service 2>/dev/null || true
    if pgrep -x x-ui || pgrep -f '^/usr/local/x-ui/'; then
        printf 'Unexpected remaining 3x-ui processes\n'
        exit 1
    fi
    printf '3x-ui removed; backup=%s\n' "$backup_dir"
fi

. /etc/os-release
if [[ "$ID" == ubuntu || "$ID" == debian ]]; then
    export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
    apt-get update
    apt-get -y -o Dpkg::Options::=--force-confold upgrade
    apt-get -y install ca-certificates curl gnupg wireguard-tools nftables jq logrotate fail2ban
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    chmod 644 /etc/apt/sources.list.d/docker.sources
    apt-get update
    apt-get -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
elif [[ "$ID" == almalinux || "$ID" == rocky || "$ID" == centos ]]; then
    dnf -y --refresh upgrade
    dnf -y install dnf-plugins-core ca-certificates curl wireguard-tools nftables jq logrotate
    if [[ ! -f /etc/yum.repos.d/docker-ce.repo ]]; then
        dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    fi
    dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    printf 'Unsupported distribution: %s\n' "$ID"
    exit 1
fi

install -d -m 755 /etc/docker
if [[ ! -f /etc/docker/daemon.json ]]; then
    cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "local",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
EOF
    chmod 644 /etc/docker/daemon.json
fi
systemctl enable --now docker
docker version --format '{{.Server.Version}}'
docker compose version
install -d -m 700 /etc/wireguard
if [[ ! -f /etc/wireguard/control.key ]]; then wg genkey > /etc/wireguard/control.key; fi
wg pubkey < /etc/wireguard/control.key > /etc/wireguard/control.pub
chmod 600 /etc/wireguard/control.key /etc/wireguard/control.pub
touch /var/lib/remnawave-base-ready
printf 'BASE_READY role=%s at %s\n' "$role" "$(date -Is)"
