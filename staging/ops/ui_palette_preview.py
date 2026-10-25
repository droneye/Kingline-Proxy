"""Local visual fixture: compiled UI, synthetic data only, no real accounts or services."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import json
import datetime
import time

DIST=Path(__file__).resolve().parents[1]/'site/dist'
now=datetime.datetime.now(datetime.timezone.utc)
iso=lambda dt:dt.isoformat()
permissions={k:True for k in ('analytics','balance','message','block','promos','support','payments','broadcast','delete_users','nodes','technical','documentation','user_transport')}
permissions['node_credentials']=False
permissions['vpn_config']=True
user={'tgId':999999,'username':'preview_user','name':'Пример пользователя','email':'preview@example.invalid','balance':300,'balanceRub':300,'tariff':'premium','keyExpiresAt':iso(now+datetime.timedelta(days=20)),'subscriptionExpiresAt':iso(now+datetime.timedelta(days=20)),'isBlocked':False,'totalBytes':2.5e9,'onlineSeconds':12400,'requestCount':243,'lastSeenAt':iso(now),'onlineNow':True,'panels':['DE']}
user.update(blocked=False,startedAt=iso(now-datetime.timedelta(days=10)),expiresAt=iso(now+datetime.timedelta(days=20)),transport='raw',transportManual=False,xhttpAvailable=True)
expiring_started_at=None
announcement={'text':'','revision':0,'updatedAt':None,'managed':False,'maxLength':500}

def bootstrap():
    return {'ok':True,'admin':{'isOwner':True,'canManageAdmins':True,'permissions':permissions},'users':[user],'payments':[],'promos':[],
            'support':[],'admins':[{'id':1,'email':'owner@example.invalid','isOwner':True,'permissions':permissions}]}
series=[{'label':f'{i:02d}:00','at':iso(now-datetime.timedelta(hours=12-i)),'totalBytes':(200+i*17+(i%4)*40)*1e6,'activeUsers':3+(i%5),'onlineSeconds':100+i*20,'revenue':300*(1+i%3),'amount':300*(1+i%3)} for i in range(12)]
nodes=[{'uuid':f'11111111-1111-4111-a111-{i:012d}','name':name,'label':flag+' '+name,'display_name':name,'flag_code':country,'country':country,'public_ip':ip,'quota_bytes':quota,'speed_mbps':1000,'reset_day':19,'maintenance':False,'ru_enabled':country!='DE','smart_enabled':enabled,'provisioned':True,'removable':True,'admin':{'monthly_cost':4.9 if i==1 else 2.15,'currency':'EUR','description':'Пример сервера','provider_url':'https://provider.example.invalid/account' if i==1 else '', 'stored_credentials':False}} for i,(name,flag,country,ip,quota,enabled) in enumerate((('Германия','🇩🇪','DE','203.0.113.10',5e12,True),('Дания','🇩🇰','DK','203.0.113.20',1e12,True),('Турция','🇹🇷','TR','203.0.113.30',2e12,False)),1)]
candidate_jobs={}
def candidate_job(qa):
    if not qa.startswith('candidate'):return None
    if qa not in candidate_jobs:
        passed=qa!='candidate-failed'
        observation={'ok':passed,'attempts':[{'ok':passed,'tcp':{'ok':passed},'error':None if passed else 'tcp_unreachable'}]}
        candidate_jobs[qa]={'id':'44444444-4444-4444-a444-444444444444','ip':'203.0.113.88',
            'state':'waiting_review','phase':'awaiting_approval','created':time.time()-480,'updated':time.time(),
            'review':{'pending':True,'revision':'a'*64,'report':{'ok':passed,'source_ok':True,'at':time.time(),
                'transports':{'raw':observation,'xhttp':observation}}},
            'events':[{'at':time.time()-300+i*25,'phase':phase} for i,phase in enumerate(
                ['ssh_preflight','packages','container','panel_registration','verifying_tunnel','checking_from_ru','awaiting_approval'])]}
    return candidate_jobs[qa]

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw):super().__init__(*a,directory=str(DIST),**kw)
    def log_message(self,*a):pass
    def send_json(self,data):
        raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        global expiring_started_at
        path=urlparse(self.path).path
        qa=parse_qs(urlparse(self.headers.get('Referer','')).query).get('qa',[''])[0]
        twofa=qa=='2fa'
        if path=='/api/admin/infrastructure/job':return self.send_json({'ok':True,'job':candidate_job(qa)})
        if path=='/api/auth/me':
            expiry=now+datetime.timedelta(days=20)
            if qa=='expired':expiry=now-datetime.timedelta(hours=2)
            if qa=='expires-soon':
                if expiring_started_at is None:expiring_started_at=datetime.datetime.now(datetime.timezone.utc)
                expiry=expiring_started_at+datetime.timedelta(seconds=18)
            return self.send_json({'ok':True,'account':{'id':999,'email':'preview@example.invalid','name':'Пример пользователя','hasRealEmail':False,'emailVerified':True,'isAdmin':True},
                'subscription':{'active':qa!='expired','expiresAt':iso(expiry),'balanceRub':300,'premiumPrice':300,'transport':'raw','xhttpAvailable':True,'tariff':'premium',
                                'subscriptionUrl':'https://example.invalid/sub/demo','telegramBonusClaimed':True,'linkedTgId':999,'canUseTrial':False},'topupAmounts':[300,600,900]})
        if path.startswith('/api/admin/documentation'):
            content=(Path(__file__).resolve().parents[1]/'docs/service.md').read_text()
            return self.send_json({'ok':True,'version':'preview','updatedAt':iso(now),'content':content,'exportContent':content,'context':{'status':'available','example':True}})
        if path=='/api/admin/infrastructure/node-reviews':
            bad={'ok':False,'code':'flow_stall_suspected','title':'Передача начинается и зависает','core_ok':True,'at':time.time(),'attempts':[{'ok':False,'tcp':{'ok':True},'error':'timeout','large':{'ok':False,'bytes':16384}}]*3}
            good={'ok':True,'code':'healthy','title':'Проверка пройдена','at':time.time(),'attempts':[{'ok':True,'tcp':{'ok':True},'large':{'ok':True,'bytes':1048576}}]}
            return self.send_json({'ok':True,'source':{'enabled':True,'last_at':time.time(),'last_full_at':time.time(),'running':False},'entry_ids':[nodes[0]['uuid'],nodes[1]['uuid']],'latest':{nodes[0]['uuid']:bad,nodes[1]['uuid']:good},'reviews':[{'id':1,'node':nodes[0]['uuid'],'label':nodes[0]['label'],'ip':nodes[0]['public_ip'],'code':'flow_stall_suspected','title':bad['title'],'state':'open','updated':time.time(),'check':bad,'candidates':[{'uuid':nodes[1]['uuid'],'label':nodes[1]['label']}]}],'hidden_nodes':[]})
        if path=='/api/admin/infrastructure/ru-egress':
            progress=parse_qs(urlparse(self.headers.get('Referer','')).query).get('qa')==['ru-progress']
            job={'id':'33333333-3333-4333-a333-333333333333','ip':'203.0.113.55','state':'running','phase':'verifying_private','progress':{'current_node':'Дания','node_total':2,'verified_nodes':1},'events':[{'at':time.time()-60+i*8,'phase':phase} for i,phase in enumerate(['queued','ssh_preflight','managed_key','packages','management_network','private_peers','container','panel_registration','verifying_private'])]}
            return self.send_json({'ok':True,'gateway':{'configured':True,'name':'РФ-выход · Raspberry Pi','online':True,'ip':'203.0.113.40','generation':0},'jobs':[job] if progress else []})
        if path=='/api/admin/bootstrap':
            if twofa:return self.send_json({'ok':True,'admin2faRequired':True,'telegramHint':'Telegram','telegramAvailable':True,'sent':True})
            return self.send_json(bootstrap())
        if path=='/api/admin/messages/announcement':return self.send_json({'ok':True,'announcement':announcement})
        if path=='/api/admin/online':
            online_users=[{**user,'registeredDevices':2,'deviceLimit':3,'locations':[{'code':'DE','activeIps':1},{'code':'DK','activeIps':1}]}]
            if qa.startswith('online'):
                online_users=[{**user,'username':f'preview_{i}','name':name,'registeredDevices':devices,'deviceLimit':3,'locations':locations,'panels':[]}
                    for i,(name,devices,locations) in enumerate((
                        ('Владислав',2,[{'code':'LT','activeIps':1},{'code':'DK','activeIps':2}]),
                        ('Анна',1,[{'code':'DE','activeIps':1}]),
                        ('Александр с очень длинным именем',3,[{'code':'NL','activeIps':2},{'code':'TR','activeIps':None},{'code':'LT','activeIps':1}]),
                        ('Мария',0,[{'code':'DK','activeIps':1}]),
                        ('Константин',None,[]),
                        ('Алексей',1,[{'code':'NL','activeIps':1}]),
                    ))]
                if qa=='online-many':online_users=[{**online_users[i%6],'username':f'preview_{i}'} for i in range(23)]
                if qa in ('online-empty','online-error'):online_users=[]
            return self.send_json({'ok':True,'count':len(online_users),'users':online_users,'panels':[],
                                   'stale':qa=='online-stale','error':qa=='online-error'})
        if path=='/api/admin/analytics':return self.send_json({'ok':True,'end':iso(now),'totals':{'totalBytes':2.5e9,'avgBytesPerDay':5e8,'onlineSeconds':12400},'series':series,'users':[user]})
        if path=='/api/admin/analytics/user-insights':return self.send_json({'ok':True,'username':user['username'],'accountDevices':{'devices':[{'family':'ios','label':'iOS / iPadOS','source':'miniapp','lastSeenAt':iso(now)},{'family':'android','label':'Android','source':'site','lastSeenAt':iso(now)}],'transport':'raw','selectionSource':'manual','manual':True,'autoDueAt':None},'network':{'kind':'home','isp':'Пример оператора'},'categories':[],'timeline':[],'topSites':[],'ipHistory':[]})
        if path=='/api/admin/analytics/live-sites':return self.send_json({'ok':True,'updatedAt':iso(now),'users':[{**user,'sites':[{'name':'example.org','domain':'example.org','count':24},{'name':'example.net','domain':'example.net','count':10}]}]})
        if path=='/api/admin/infrastructure/nodes':
            resources={'at':int(time.time()),'cpu_percent':12.4,'ram_percent':42.8,'ram_used':.856*1024**3,'ram_total':2*1024**3,'disk_percent':28,'disk_used':11.2*1024**3,'disk_total':40*1024**3}
            candidate=candidate_job(qa)
            return self.send_json({'ok':True,'nodes':[{**n,'resources':resources} for n in nodes],'core':{'name':'Главный сервис','ip':'203.0.113.5','resources':resources},'service_nodes':[{'name':'Российский выход','ip':'203.0.113.40','online':True,'resources':resources}],'routing':{'sticky':{'enabled':True,'minimum_hold_seconds':86400},'nodes':[{'ip':n['public_ip'],'weight':100 if n['smart_enabled'] else 0,'used_bytes':1.5e11} for n in nodes]},'jobs':[candidate] if candidate else []})
        if path in ('/api/admin/infrastructure/node-network','/api/admin/infrastructure/node-order'):
            return self.send_json({'ok':True,'revision':'a'*64,'common_sni':'example.org','nodes':[{'uuid':n['uuid'],'label':n['label'],'ip':n['public_ip'],'sni':'example.org','target':'example.org:443','via':nodes[0]['uuid'] if n['country']=='TR' else None,'auto_eligible':n['country']!='TR','ru_enabled':n['ru_enabled']} for n in nodes],'ru_egress':{'configured':True,'name':'РФ-выход','online':True,'ip':'203.0.113.40','generation':0},'keys':[{'uuid':'22222222-2222-4222-a222-222222222222','label':'⚡ Умный выбор','smart':True}]+[{'uuid':n['uuid'],'label':n['label'],'smart':False} for n in nodes],'jobs':[]})
        if path=='/api/admin/infrastructure/logs':return self.send_json({'ok':True,'counts':{'ERROR':1,'WARNING':2},'health':[{'name':'Сайт','ok':True,'detail':'Работает'},{'name':'Telegram-бот','ok':True,'detail':'Работает'}],'events':[{'ts':iso(now),'level':'INFO','service':'preview','event':'service_started'}],'timeline':[{'at':now.timestamp()-i*3600,'count':i%3} for i in range(24)]})
        if path=='/api/admin/payments/analytics':return self.send_json({'ok':True,'totals':{'revenue':1200,'amount':1200,'count':4},'series':series})
        if path.startswith('/api/'):return self.send_json({'ok':True,'items':[],'messages':[],'users':[]})
        if not Path(path).suffix:self.path='/index.html'
        super().do_GET()
    def do_POST(self):
        length=int(self.headers.get('Content-Length',0));body=json.loads(self.rfile.read(length) or '{}')
        if urlparse(self.path).path=='/api/admin/infrastructure/update':
            node=next(n for n in nodes if n['uuid']==body['uuid'])
            node['admin'].update(body.get('metadata',{}))
            for key,value in body.get('capacity',{}).items():
                if key=='quota_tb':node['quota_bytes']=value*1e12
                else:node[key]=value
            return self.send_json({'ok':True})
        if urlparse(self.path).path=='/api/admin/messages/announcement':
            announcement.update(text=body['text'].strip(),revision=announcement['revision']+1,updatedAt=iso(now),managed=True)
            return self.send_json({'ok':True,'announcement':announcement})
        if urlparse(self.path).path=='/api/admin/infrastructure/installation-action':
            qa=parse_qs(urlparse(self.headers.get('Referer','')).query).get('qa',[''])[0]
            candidate=candidate_job(qa)
            if body['operation']=='test-key':return self.send_json({'ok':True,'test_key':'vless://00000000-0000-4000-a000-000000000000@candidate.example.invalid:443?type=tcp#Demo','expires':time.time()+7200})
            if body['operation']=='check':candidate['review']['report']['ok']=True
            if body['operation'] in ('approve','cancel'):
                candidate.update(state='ready' if body['operation']=='approve' else 'cancelled',phase='ready' if body['operation']=='approve' else 'cancelled')
                candidate['review']['pending']=False
            candidate['updated']=time.time()
            return self.send_json({'ok':True,'job':candidate})
        if urlparse(self.path).path=='/api/admin/user/vpn-mode':
            if body.get('tgId')==user['tgId'] and body.get('transport') in ('raw','xhttp'):
                user.update(transport=body['transport'],transportManual=True)
                return self.send_json(bootstrap())
        if urlparse(self.path).path=='/api/admin/2fa/send':return self.send_json({'ok':True,'admin2faRequired':True,'telegramHint':'Telegram','telegramAvailable':True,'sent':True})
        self.send_json({'ok':True})

if __name__=='__main__':
    print('Synthetic UI fixture: http://127.0.0.1:5197/admin',flush=True)
    ThreadingHTTPServer(('127.0.0.1',5197),Handler).serve_forever()
