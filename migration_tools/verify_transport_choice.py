"""Read private fixtures only on the server. Never print/store issued credentials."""
import base64,json,sys,sqlite3,urllib.parse,uuid
from pathlib import Path
import urllib.request,urllib.error,http.cookiejar
from types import SimpleNamespace

class Client:
    def __init__(self,base_url,headers,timeout):
        self.base_url=base_url;self.headers=headers;self.timeout=timeout
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def request(self,method,url,json=None,headers=None):
        h={**self.headers,**(headers or {})};data=None
        if json is not None:data=__import__("json").dumps(json).encode();h["Content-Type"]="application/json"
        request=urllib.request.Request(url if url.startswith("https://") else self.base_url+url,data=data,headers=h,method=method)
        try:r=self.opener.open(request,timeout=self.timeout)
        except urllib.error.HTTPError as error:r=error
        with r:body=r.read(4000000);status=r.status
        return SimpleNamespace(status_code=status,content=body,json=lambda:__import__("json").loads(body))
    def get(self,url,**kw):return self.request("GET",url,**kw)
    def post(self,url,**kw):return self.request("POST",url,**kw)

sys.path[:0]=['/opt/kingline-manager/app','/opt/kingline-control']
from remna_api import RemnaAPI
from node_manager.store import atomic_json
from node_manager.manager import Manager
from node_manager.ru_monitor_worker import attempt
C=Path('/opt/kingline-control');root=Path('/opt/kingline-staging');access=json.loads((root/'secrets/test-access.json').read_text());site=access['site'];headers={'Origin':site,'X-Kingline-Request':'fetch'};report={};api=RemnaAPI();identity=None;previous=None;DEVICE='Kingline-Transport-QA-v1'
with Client(base_url=site,headers=headers,timeout=30) as client:
    assert client.post('/api/account/vpn-mode',json={'transport':'xhttp'}).status_code==401
    r=client.post('/api/auth/login',json={'email':access['test_email'],'password':access['test_password']});assert r.status_code==200,'fixture_login_failed'
    account=r.json();previous=account['subscription']['transport'];url=account['subscription']['subscriptionUrl'];balance=account['subscription']['balanceRub'];expiry=account['subscription']['expiresAt']
    assert account['subscription']['xhttpAvailable']
    bid=urllib.parse.urlsplit(url).path.rsplit('/',1)[-1]
    with sqlite3.connect('file:'+str(root/'data/vpn_bot.db')+'?mode=ro',uri=True) as db:
        identity=db.execute('SELECT remna_id FROM remna_links WHERE sub_id=?',(bid,)).fetchone()[0]
    old_devices=api.call('hwid/devices/'+str(identity));report['guest_denied']=True
    try:
        unsafe=client.post('/api/account/vpn-mode',json={'transport':'xhttp'},headers={'X-Kingline-Request':'wrong'});assert unsafe.status_code==403
        assert client.post('/api/account/vpn-mode',json={'transport':'unknown'}).status_code==400
        report['csrf_and_validation']=True;configs={}
        for mode in ['raw','xhttp','raw']:
            r=client.post('/api/account/vpn-mode',json={'transport':mode});assert r.status_code==200
            sub=r.json()['subscription'];assert sub['transport']==mode and sub['subscriptionUrl']==url
            assert sub['balanceRub']==balance and sub['expiresAt']==expiry
            assert client.get('/api/auth/me').json()['subscription']['transport']==mode
            for agent,platform in [('v2rayTun/2.4.7','iOS'),('v2rayN/7.19.0','Windows'),('Happ/3.0.0','Android')]:
                response=client.get(url,headers={'User-Agent':agent,'X-Hwid':DEVICE,'X-Device-OS':platform,'X-Device-Model':'Kingline transport check'});assert response.status_code==200,'subscription_failed'
                text=base64.b64decode(response.content).decode();links=[urllib.parse.urlsplit(s) for s in text.splitlines() if s.startswith('vless://')];assert links,'empty_subscription'
                params=[urllib.parse.parse_qs(link.query) for link in links]
                assert all(p['type']==(['xhttp'] if mode=='xhttp' else ['tcp']) for p in params),'wrong_issued_mode'
                if mode=='xhttp':assert all(p['security']==['tls'] and 'flow' not in p and p['alpn']==['h2'] for p in params)
                configs[mode]=links;report[mode+'_'+agent]={'profiles':len(links),'correct_mode':True}
        report['same_link_balance_expiry']=True;report['persisted']=True
        m=Manager('/opt/kingline-manager/data');manifest=m.reviews.manifest();targets=manifest['targets']
        for mode,links in configs.items():
            link=next(link for link in links if '\u0414\u0430\u043d\u0438\u044f' in urllib.parse.unquote(link.fragment));params={k:v[-1] for k,v in urllib.parse.parse_qs(link.query).items()}
            target=next(t for t in targets if '\u0414\u0430\u043d\u0438\u044f' in t['label'])
            target=target|{'address':link.hostname,'port':link.port,'probe_uuid':link.username,'sni':params['sni'],'fingerprint':params['fp']}
            if mode=='xhttp':target.update(transport='xhttp',xhttp_path=params['path'])
            else:target.update(public_key=params['pbk'],short_id=params['sid'])
            actual=attempt(target,manifest,binary=C/'client-26.6.27/xray')
            report[mode+'_issued_key_tunnel']={'ok':actual['ok'],'error':actual.get('error'),'bytes':actual.get('large',{}).get('bytes',0),'curl_code':actual.get('large',actual.get('small',{})).get('curl_code')}
            assert actual['ok'],'issued_key_does_not_connect'
        report['ok']=True
    finally:
        if previous is not None:assert client.post('/api/account/vpn-mode',json={'transport':previous}).status_code==200
        if identity is not None:
            old=old_devices.get('devices',old_devices.get('items',[])) if isinstance(old_devices,dict) else old_devices
            if not any(d.get('hwid')==DEVICE for d in old):api.call('hwid/devices/delete','POST',{'userId':identity,'hwid':DEVICE})
        atomic_json(C/'transport-http-verification.json',report)
atomic_json(C/'transport-http-verification.json',report);print(json.dumps(report))
