"""Controlled reproduction of the owner's working AWS mobile listener."""
import copy

TAG = 'KINGLINE_MOBILE_AWS'
SNI = 'aws.amazon.com'
PORT = 36847
FINGERPRINT = 'chrome'
SEED = [900, 500, 900, 256]


def candidate_config(base):
    config = copy.deepcopy(base)
    assert not any(i['tag'] == TAG for i in config['inbounds']), 'Mobile variant already installed'
    inbound = copy.deepcopy(next(i for i in config['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY'))
    inbound.update(tag=TAG, port=PORT)
    inbound['settings']['testseed'] = SEED.copy()
    stream = inbound['streamSettings']
    stream['network'] = 'tcp'
    stream['tcpSettings'] = {'header': {'type': 'none'}, 'acceptProxyProtocol': False}
    reality = stream['realitySettings']
    reality.pop('dest', None)
    reality.update(target=SNI + ':443', serverNames=[SNI], maxTimeDiff=0, show=False, xver=0)
    config['inbounds'].append(inbound)
    config['outbounds'].append({'tag': 'MOBILE_DIRECT', 'protocol': 'freedom',
        'settings': {'domainStrategy': 'UseIPv4'},
        'streamSettings': {'sockopt': {'tcpFastOpen': True, 'tcpKeepAliveInterval': 15, 'tcpMptcp': True}}})
    # Existing private-address rejection stays before this per-inbound rule.
    config['routing']['rules'].append({'type': 'field', 'inboundTag': [TAG], 'outboundTag': 'MOBILE_DIRECT'})
    config['dns'] = {'servers': ['1.1.1.1', '1.0.0.1'], 'queryStrategy': 'UseIPv4'}
    return config
