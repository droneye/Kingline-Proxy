#!/bin/sh
set -eu
release_dir=$(cat /opt/kingline-staging/run/sticky-ru-release-path)
tar -C /opt/kingline-manager/app -xzf /opt/kingline-manager/ru-manager-update.tar.gz
cp /opt/kingline-manager/app/node_manager/kingline-ru-egress.service /etc/systemd/system/
cp /opt/kingline-manager/app/node_manager/kingline-ru-egress.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart kingline-node-manager kingline-site-admin
systemctl start kingline-ru-egress.service
systemctl enable --now kingline-ru-egress.timer
systemctl is-active kingline-node-manager kingline-site-admin kingline-ru-egress.timer
printf 'Manager backup: %s\n' "$release_dir"
