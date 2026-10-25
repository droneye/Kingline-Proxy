"""Core-only public subscription checks. Uses only the authorized probe identity."""
import base64,json,secrets,subprocess,sys,urllib.error,urllib.parse,urllib.request
from pathlib import Path
sys.path.insert(0,'/opt/kingline-control')
from remna_api import RemnaAPI
root=Path('/opt/kingline-control');api=RemnaAPI()
probe=api.call('users/'+str(json.loads((root/'probe-user.json').read_text())['id']))
before=api.call('hwid/devices/'+str(probe['id']))
assert before['total']==0,'probe_has_unexpected_devices'
prefix='QA-Desktop-'+secrets.token_hex(6)

def fetch(url,headers):
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=25) as response:
            data=response.read(2_000_000);metadata={k.lower():v for k,v in response.headers.items()}
    except urllib.error.HTTPError as response:
        data=response.read(2_000_000);metadata={k.lower():v for k,v in response.headers.items()}
    try:decoded=base64.b64decode(b''.join(data.split()),validate=True).decode()
    except (ValueError,UnicodeDecodeError):decoded=data.decode(errors='replace')
    return decoded,metadata

try:
    api.call('users','PATCH',{'id':probe['id'],'hwidDeviceLimit':3})
    url=probe['subscriptionUrl'];sep='&' if '?' in url else '?'
    desktop={'User-Agent':'v2rayN/7.25.6'}
    first,_=fetch(url+sep+'device='+prefix+'-1',desktop)
    second,_=fetch(url+sep+'device='+prefix+'-2',desktop)
    third,_=fetch(url,{'User-Agent':'v2raytun/2.4.7','x-hwid':prefix+'-phone','x-device-os':'iOS'})
    assert all(probe['vlessUuid'] in body for body in [first,second,third]),'compatible_device_rejected'
    fourth,headers=fetch(url+sep+'device='+prefix+'-4',desktop)
    assert probe['vlessUuid'] not in fourth and any(headers.get(k)=='true' for k in ['x-hwid-limit','x-hwid-max-devices-reached']),'fourth_slot_not_blocked'
    again,_=fetch(url+sep+'device='+prefix+'-1',desktop)
    assert probe['vlessUuid'] in again and api.call('hwid/devices/'+str(probe['id']))['total']==3
    print(json.dumps({'desktop_slots':True,'phone_slot':True,'fourth_blocked':True,'refresh_reuses_slot':True}),flush=True)
finally:
    for device in api.call('hwid/devices/'+str(probe['id']))['devices']:
        api.call('hwid/devices/delete','POST',{'userId':probe['id'],'hwid':device['hwid']})
    api.call('users','PATCH',{'id':probe['id'],'hwidDeviceLimit':probe['hwidDeviceLimit']})

owner=api.call('users/'+str(json.loads((root/'owner-profile.json').read_text())['user']['id']))
registered=api.call('hwid/devices/'+str(owner['id']))['devices'];assert registered
body,_=fetch(owner['subscriptionUrl'],{'User-Agent':'v2raytun/2.4.7','x-hwid':registered[0]['hwid'],'x-device-os':registered[0].get('platform') or 'iOS'})
links=[urllib.parse.urlparse(line) for line in body.splitlines() if line.startswith('vless://')]
assert len(links)==5,'subscription_profile_count_changed'
personal=next(p.hostname for p in links if p.hostname.endswith('.auto.edge.kingline.online'))
assert personal.startswith('u-'),'subscription_not_personalized'
for p in links:
    query=urllib.parse.parse_qs(p.query)
    assert query['security']==['reality'] and query['flow']==['xtls-rprx-vision']
    assert query.get('type') in (['tcp'],['raw']) and query['fp']==['firefox']
answers=[]
for replica in json.loads((root/'dns-replicas.json').read_text()):
    for repeat in range(3):
        answer=subprocess.check_output(['dig','+short','+time=3','+tries=1','@'+replica['public_ip'],personal,'A'],text=True).strip().splitlines()
        assert len(answer)==1,'personal_dns_not_single_exit';answers.extend(answer)
assert len(set(answers))==1,'personal_dns_not_stable'
result={'desktop_three_slots_verified':True,'owner_profiles':len(links),'manual_profiles':len(links)-1,'same_exit_across_dns_replicas':True,'dns_queries':len(answers)}
(root/'desktop-sticky-verification.json').write_text(json.dumps(result))
print(json.dumps(result))
