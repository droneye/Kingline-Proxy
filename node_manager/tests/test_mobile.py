from mobile_variant import candidate_config, TAG


def test_mobile_variant_preserves_original_listener_and_private_block():
    main = {'tag': 'KINGLINE_XHTTP_REALITY', 'port': 443, 'settings': {'clients': [], 'flow': 'xtls-rprx-vision'},
        'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
            'privateKey': 'keep-key', 'shortIds': ['keep-short-id'], 'serverNames': ['original.example'],
            'target': 'original.example:443', 'maxTimeDiff': 120000}}}
    block = {'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'BLOCK'}
    base = {'inbounds': [main], 'outbounds': [{'tag': 'DIRECT', 'protocol': 'freedom'}],
            'routing': {'rules': [block]}}
    changed = candidate_config(base)
    assert changed['inbounds'][0] == main and len(base['inbounds']) == 1
    mobile = changed['inbounds'][1]
    assert mobile['tag'] == TAG and mobile['port'] == 36847
    assert mobile['settings']['testseed'] == [900, 500, 900, 256]
    reality = mobile['streamSettings']['realitySettings']
    assert reality['privateKey'] == 'keep-key' and reality['shortIds'] == ['keep-short-id']
    assert reality['target'] == 'aws.amazon.com:443' and reality['maxTimeDiff'] == 0
    assert changed['routing']['rules'][0] == block
    assert changed['routing']['rules'][-1]['inboundTag'] == [TAG]
