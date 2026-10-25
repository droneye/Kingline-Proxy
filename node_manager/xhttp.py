"""The second, loopback-only VLESS inbound. Public TLS stays on the owned HTTPS."""
import copy
import re

PORT=10080
TAG='KINGLINE_XHTTP_TLS'


def add_inbound(config,path,tag=TAG):
    if not re.fullmatch(r'/[A-Za-z0-9/_-]{8,160}/',path):raise ValueError('invalid_xhttp_path')
    result=copy.deepcopy(config)
    if any(i.get('port')==PORT and i.get('tag')!=tag for i in result['inbounds']):raise ValueError('xhttp_port_collision')
    result['inbounds']=[i for i in result['inbounds'] if i['tag']!=tag]+[{
        'tag':tag,'listen':'127.0.0.1','port':PORT,'protocol':'vless',
        'settings':{'clients':[],'decryption':'none'},
        'streamSettings':{'network':'xhttp','security':'none','xhttpSettings':{'path':path,'mode':'auto'}},
        'sniffing':{'enabled':True,'destOverride':['http','tls','quic'],'routeOnly':True}}]
    return result
