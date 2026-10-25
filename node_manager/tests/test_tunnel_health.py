from tunnel_health_policy import observation, standard_identity, tunnel_state


NODE = {'uuid': 'de', 'public_ip': '198.51.100.31'}
IDENTITY = standard_identity({'sni': 'example.com', 'user': {'vlessUuid': 'one'}})


def test_a_changed_path_invalidates_old_health_without_changing_user_credentials():
    old={**NODE,'path_revision':'before'}
    row=observation(old,IDENTITY,{},1000,{'https_ms':1,'udp_dns_ms':1})
    assert tunnel_state(report(row),old,IDENTITY,1001)=='healthy'
    assert tunnel_state(report(row),{**NODE,'path_revision':'after'},IDENTITY,1001)=='unknown'


def report(row, **changes):
    return {'identity': IDENTITY, 'dependencies_ready': True, 'nodes': [row], **changes}


def test_two_failed_tunnels_exclude_and_success_restores_node():
    first = observation(NODE, IDENTITY, {}, 1000)
    assert tunnel_state(report(first), NODE, IDENTITY, 1010) == 'retrying'
    second = observation(NODE, IDENTITY, report(first), 1200)
    assert tunnel_state(report(second), NODE, IDENTITY, 1210) == 'excluded'
    third = observation(NODE, IDENTITY, report(second), 1400, {'https_ms': 100, 'udp_dns_ms': 30})
    assert third['failures'] == 0
    assert tunnel_state(report(third), NODE, IDENTITY, 1410) == 'healthy'


def test_stale_foreign_future_and_dependency_failure_reports_cannot_disable_node():
    bad = {'uuid': NODE['uuid'], 'ip': NODE['public_ip'], 'checked_at': 1000, 'ok': False, 'failures': 3}
    for data, node, identity, now in [
        (report(bad), NODE, IDENTITY, 1601),
        (report(bad), NODE, IDENTITY, 999),
        (report(bad), NODE, 'new-identity', 1001),
        (report(bad), {**NODE, 'public_ip': '198.51.100.32'}, IDENTITY, 1001),
        (report(bad, dependencies_ready=False), NODE, IDENTITY, 1001),
    ]:
        assert tunnel_state(data, node, identity, now) == 'unknown'
        assert observation(node, identity, data, now)['failures'] == 1


def test_credentials_change_invalidates_old_health_without_exposing_them():
    old = {'sni': 'example.com', 'user': {'vlessUuid': 'one'}}
    new = {**old, 'user': {'vlessUuid': 'two'}}
    assert standard_identity(new) != standard_identity(old)
    assert len(standard_identity(old)) == 64
