"""Permission/2FA protected proxy to the host's private Unix socket; no root token in Docker."""
import json
import os
from urllib.parse import urlparse
import httpx
from diagnostics import event, REQUEST_ID

ACTIONS={'installation-action','review-action','review-check','node-visibility','add','preflight','update','remove','credentials','retry','node-connection','node-routes','node-order','node-rollback','ru-replace','ru-retry'}
READS={'node-reviews','nodes','job','logs','health','node-network','node-order','node-job','ru-egress','ru-job'}

def handle(handler, method):
    parsed=urlparse(handler.path)
    prefix='/api/admin/infrastructure/'
    if not parsed.path.startswith(prefix): return False
    action=parsed.path[len(prefix):]
    if (method=='GET' and action not in READS) or (method=='POST' and action not in ACTIONS):
        handler.json_response({'ok':False,'error':'not_found'},404);return True
    permission='technical' if action in {'logs','health'} else 'nodes'
    account=handler.require_admin_permission(permission)
    if not account:return True
    if action in {'node-network','node-connection','node-routes','node-rollback'} and not handler.require_admin_permission('vpn_config'):return True
    if action=='credentials' and not handler.require_admin_permission('node_credentials'):return True
    body=None
    if method=='POST':
        if handler.validate_post_request(parsed.path) is not True:return True
        length=handler.headers.get('Content-Length','')
        if not length.isdigit() or int(length)>65536:
            handler.json_response({'ok':False,'error':'body_too_large'},413);return True
        body=handler.read_json()
        if body is None:return True
        if action=='review-action' and body.get('operation')=='via' and not handler.require_admin_permission('vpn_config'):return True
    try:
        transport=httpx.HTTPTransport(uds=os.getenv('KINGLINE_ADMIN_SOCKET','/run/site-admin/bridge.sock'))
        with httpx.Client(transport=transport,timeout=70,headers={'X-Request-ID':REQUEST_ID.get() or ''}) as client:
            response=client.request(method,'http://localhost/'+action+('?' + parsed.query if parsed.query else ''),json=body if method=='POST' else None)
            result=response.json()
        handler.json_response(result,response.status_code)
        if method=='POST':event('owner_infrastructure_action',operation=action,status=response.status_code,actor_account_id=account['id'])
    except (httpx.HTTPError,ValueError,OSError) as exc:
        event('infrastructure_unavailable','ERROR',error=type(exc).__name__,operation=action)
        handler.json_response({'ok':False,'error':'infrastructure_unavailable','requestId':REQUEST_ID.get()},503)
    return True
