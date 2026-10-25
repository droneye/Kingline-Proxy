import json
from pathlib import Path

import pytest

from node_manager.cover import Cover, cover_addresses, sync_dns
from node_manager.cover_remote import scanner_passed
from node_manager.ssh import SafeError


def test_scanner_uses_completed_handshake_and_rejects_wrong_identity_or_protocol():
    line = ('time=2026-10-22 level=INFO msg="Connected to target" feasible=true ip=127.0.0.1 '
            'tls="TLS 1.3" alpn=h2 cert-domain=edge.kingline.online cert-issuer="Let\'s Encrypt"')
    assert scanner_passed(line, '127.0.0.1', 'edge.kingline.online')
    for bad in [line.replace('feasible=true', 'feasible=false'), line.replace('alpn=h2', 'alpn=http/1.1'),
                line.replace('edge.kingline.online', 'unrelated.example'), line.replace('TLS 1.3', 'TLS 1.2')]:
        assert not scanner_passed(bad, '127.0.0.1', 'edge.kingline.online')


def test_owned_dns_omits_unverified_or_retired_nodes_but_keeps_manual_only_nodes():
    nodes = [dict(public_ip='8.8.4.4', cover={'domain': 'edge.kingline.online'}, provisioned=True,
                  maintenance=True, quota_bytes=1),
             dict(public_ip='9.9.9.9', cover={'domain': 'edge.kingline.online'}, provisioned=False),
             dict(public_ip='1.1.1.1', provisioned=True)]
    assert cover_addresses(nodes) == ['8.8.4.4']


def test_dns_failure_attempts_remaining_replicas_and_reports_failure(tmp_path, monkeypatch):
    from node_manager import cover
    (tmp_path / 'dns-replicas.json').write_text(json.dumps([{'role': r} for r in ['core', 'de', 'dk']]))
    calls = []
    def api(replica, method, body):
        calls.append(replica['role'])
        assert body['rrsets'][0]['changetype'] == 'DELETE'
        if replica['role'] == 'de':
            raise OSError('unreachable')
    monkeypatch.setattr(cover, 'dns_api', api)
    with pytest.raises(SafeError, match='cover_dns_replica_update_failed'):
        sync_dns(tmp_path, [])
    assert calls == ['core', 'de', 'dk']


def test_bad_scanner_hash_fails_before_ssh(tmp_path):
    file = tmp_path / 'scanner'; file.write_bytes(b'changed executable')
    cover = Cover({'domain': 'edge.kingline.online', 'target': '127.0.0.1:8443',
                   'scanner_path': str(file), 'scanner_sha256': '0' * 64})
    with pytest.raises(SafeError, match='scanner_checksum_mismatch'):
        cover.deploy(None)
