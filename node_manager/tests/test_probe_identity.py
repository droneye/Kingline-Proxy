import json
import pytest
from node_manager.probe import probe_identity
from node_manager.ssh import SafeError

def test_dedicated_probe_identity_never_overwrites_owner(tmp_path):
    state={'user':{'vlessUuid':'11111111-1111-4111-a111-111111111111'}}
    assert probe_identity(tmp_path,state)==state['user']['vlessUuid']
    path=tmp_path/'probe-user.json'
    path.write_text(json.dumps({'vlessUuid':'22222222-2222-4222-a222-222222222222'}))
    assert probe_identity(tmp_path,state)=='22222222-2222-4222-a222-222222222222'
    assert state['user']['vlessUuid'].startswith('11111111')
    path.write_text('{}')
    with pytest.raises(SafeError,match='probe_identity_invalid'):probe_identity(tmp_path,state)
