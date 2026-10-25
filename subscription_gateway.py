"""Subscription response adapter over a private Unix socket. No VPN traffic here."""
import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from fastapi import FastAPI,Request
from fastapi.responses import Response
from subscription_modes import selected,convert,announcement,profile_title

ROOT=Path(os.getenv('KINGLINE_CONTROL','/opt/kingline-control'))
AUTO='auto.edge.kingline.online'
DEVICE=re.compile(r'^[A-Za-z0-9-]{10,64}$')
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
LOG=logging.getLogger('kingline.subscriptions')
HWID_REFUSALS=('x-hwid-not-supported','x-hwid-max-devices-reached','x-hwid-limit')

def failure(reason,mode='unknown',error=None,**details):
    # Never log paths, account/device identifiers, response bodies or exception text.
    LOG.warning('subscription_failure reason=%s mode=%s error=%s details=%s',
                reason,mode,type(error).__name__ if error else 'none',json.dumps(details,sort_keys=True))
    return Response(status_code=503,headers={'Cache-Control':'no-store'})

def transform(data,name,content_type=''):
    old=AUTO.encode();new=name.encode()
    if old in data:return data.replace(old,new)
    try:
        decoded=base64.b64decode(b''.join(data.split()),validate=True)
        if old in decoded:return base64.b64encode(decoded.replace(old,new))
    except ValueError:pass
    return data

def device_headers(headers,short,device,key):
    allowed=('user-agent','accept','x-hwid','x-device-os','x-ver-os','x-device-model')
    result={k:headers[k][:512] for k in allowed if headers.get(k)}
    if device and not result.get('x-hwid'):
        if not DEVICE.fullmatch(device):raise ValueError('invalid_device')
        result['x-hwid']='Kingline-'+hmac.new(key,('desktop|'+short+'|'+device).encode(),hashlib.sha256).hexdigest()[:40]
        result['x-device-os']='Desktop'
        result['x-device-model']='v2rayN device link'
    return result

@app.api_route('/{path:path}',methods=['GET','HEAD'])
def proxy(path:str,request:Request):
    try:
        index=json.loads((ROOT/'sticky-subscriptions.json').read_text())
    except (OSError,ValueError):index={'subscriptions':{}}
    parts=path.strip('/').split('/')
    short=next((p for p in parts if p in index.get('subscriptions',{})),None)
    identity=short or next((p for p in parts if re.fullmatch(r'[A-Za-z0-9_-]{16,64}',p)),None)
    device=request.query_params.get('device')
    try: mode,mode_policy=selected(ROOT,identity)
    except (OSError,ValueError,KeyError,sqlite3.Error) as exc:
        return failure('mode_lookup',error=exc)
    try:
        headers=device_headers(request.headers,identity or '',device,(ROOT/'sticky-dns.key').read_bytes())
        headers['X-Forwarded-Proto']='https'
        if mode=='xhttp':headers['X-Kingline-Transport']='XHTTP'
        import ipaddress
        try:headers['X-Forwarded-For']=str(ipaddress.ip_address(request.headers.get('x-real-ip','127.0.0.1')))
        except ValueError:pass
    except (ValueError,OSError):return Response(status_code=400,headers={'Cache-Control':'no-store'})
    # Strip our device parameter; keep native response-format parameters.
    query=urllib.parse.urlencode([(k,v) for k,v in request.query_params.multi_items() if k!='device'])
    target='http://127.0.0.1:3010/'+urllib.parse.quote(path,safe='/')+('?' + query if query else '')
    req=urllib.request.Request(target,headers=headers,method=request.method)
    try:
        with OPENER.open(req,timeout=20) as response:
            data=response.read(4_000_001);status=response.status;upstream=response.headers
    except urllib.error.HTTPError as exc:
        data=exc.read(4_000_001);status=exc.code;upstream=exc.headers
    except (OSError,ValueError) as exc:return failure('upstream_unavailable',mode,exc)
    if len(data)>4_000_000:return Response(status_code=502,headers={'Cache-Control':'no-store'})
    refused=any(upstream.get(k,'').lower()=='true' for k in HWID_REFUSALS)
    if status==200 and request.method!='HEAD' and mode=='xhttp' and not refused and 'text/html' not in upstream.get('Content-Type','').lower():
        try:data=convert(data,mode_policy)
        except (ValueError,KeyError,UnicodeError) as exc:
            return failure('conversion',mode,exc,body_bytes=len(data),content_type=upstream.get('Content-Type','').split(';')[0][:64],hwid_present=bool(headers.get('x-hwid')))
    if status==200 and short:
        data=transform(data,index['subscriptions'][short],upstream.get('Content-Type',''))
    allowed={'content-type','content-disposition','subscription-userinfo','profile-update-interval','profile-title','support-url','announce','announce-url','routing','location','x-hwid-active','x-hwid-limit','x-hwid-not-supported','x-hwid-max-devices-reached'}
    result={k:v for k,v in upstream.items() if k.lower() in allowed}
    if status==200 and not refused and 'text/html' not in upstream.get('Content-Type','').lower():
        try:
            title=profile_title(ROOT,identity)
            if title is not None:
                result={k:v for k,v in result.items() if k.lower()!='profile-title'}
                result['profile-title']=title
        except (OSError,ValueError,sqlite3.Error) as exc:
            LOG.warning('subscription_title_unavailable error=%s',type(exc).__name__)
        try:
            notice=announcement(ROOT)
            if notice is not None:
                # No announcement means no banner, including no native default/link.
                # "base64:" alone is non-empty metadata and can leave a blank row.
                result={k:v for k,v in result.items() if k.lower() not in {'announce','announce-url'}}
                if notice:result['announce']=notice
        except (OSError,ValueError,sqlite3.Error) as exc:
            # An optional banner must never prevent downloading a working VPN profile.
            LOG.warning('subscription_announcement_unavailable error=%s',type(exc).__name__)
    result['Cache-Control']='no-store';result['X-Content-Type-Options']='nosniff'
    return Response(data,status_code=status,headers=result)
