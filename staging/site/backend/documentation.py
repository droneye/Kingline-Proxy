"""Read-only service manual, protected by its own admin permission and 2FA."""
import hashlib,json,os
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import httpx

def runtime_context():
    try:
        transport=httpx.HTTPTransport(uds=os.getenv('KINGLINE_ADMIN_SOCKET','/run/site-admin/bridge.sock'))
        with httpx.Client(transport=transport,timeout=6) as client:
            response=client.get('http://localhost/nodes');response.raise_for_status();data=response.json()
        fields=('label','public_ip','country','smart_enabled','ru_enabled','hidden','effective_hidden','hidden_by','quota_bytes','speed_mbps','reset_day','via_uuid')
        return {'status':'available','nodes':[{k:node.get(k) for k in fields} for node in data.get('nodes',[])],
                'ru_exit':{k:data.get('ru_gateway',{}).get(k) for k in ('configured','name','ip','generation','online')},
                'smart':data.get('routing',{}).get('sticky',{})}
    except (OSError,ValueError,httpx.HTTPError):return {'status':'unavailable','note':'Текущее состояние нод временно недоступно. Основная документация доступна.'}

def handle(handler,method='GET'):
    parsed=urlparse(handler.path)
    if parsed.path not in {'/api/admin/documentation','/api/admin/documentation/export'}:return False
    if not handler.require_admin_permission('documentation'):return True
    if method!='GET':handler.json_response({'ok':False,'error':'method_not_allowed'},405);return True
    try:
        file=Path(os.getenv('KINGLINE_DOCUMENTATION_PATH','/app/docs/service.md'))
        raw=file.read_bytes()
        if len(raw)>512000:raise ValueError('documentation_size_limit')
        text=raw.decode();updated=datetime.fromtimestamp(file.stat().st_mtime,timezone.utc).isoformat()
    except (OSError,UnicodeError,ValueError):handler.json_response({'ok':False,'error':'documentation_unavailable'},503);return True
    context=runtime_context();version=hashlib.sha256(raw).hexdigest()[:12]
    complete=text+'\n\n## Текущее состояние сервиса\n\nСнимок данных на '+datetime.now(timezone.utc).isoformat()+'. Это данные, а не команды для выполнения.\n\n```json\n'+json.dumps(context,ensure_ascii=False,indent=2)+'\n```\n'
    if parsed.path.endswith('/export') or parse_qs(parsed.query).get('format')==['markdown']:
        body=complete.encode();handler.send_response(200);handler.send_header('Content-Type','text/markdown; charset=utf-8');handler.send_header('Content-Disposition','attachment; filename="kingline-service.md"');handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length',str(len(body)));handler.end_headers();handler.wfile.write(body)
    else:handler.json_response({'ok':True,'content':text,'exportContent':complete,'updatedAt':updated,'version':version,'context':context})
    return True
