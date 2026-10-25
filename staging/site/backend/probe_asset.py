"""Fixed incompressible connectivity fixture; no account data, writes or reflection."""
import hashlib,ipaddress
from urllib.parse import urlparse
BODY=hashlib.shake_256(b'Kingline connectivity probe v1').digest(1024*1024)
SHA256=hashlib.sha256(BODY).hexdigest()
def handle(handler):
    if urlparse(handler.path).path!='/api/connectivity/probe.bin':return False
    ranged=handler.headers.get('Range')
    if ranged not in (None,'bytes=0-2047'):
        handler.send_response(416);handler.send_header('Content-Length','0');handler.end_headers();return True
    body=BODY[:2048] if ranged else BODY
    handler.send_response(206 if ranged else 200)
    handler.send_header('Content-Type','application/octet-stream');handler.send_header('Content-Length',str(len(body)))
    handler.send_header('Cache-Control','public, max-age=3600');handler.send_header('Accept-Ranges','bytes');handler.send_header('X-Content-Type-Options','nosniff')
    try:source=str(ipaddress.ip_address(handler.client_ip()))
    except ValueError:source='0.0.0.0'
    handler.send_header('X-Kingline-Probe-IP',source)
    if ranged:handler.send_header('Content-Range',f'bytes 0-2047/{len(BODY)}')
    handler.end_headers()
    try:handler.wfile.write(body)
    except (BrokenPipeError,ConnectionResetError):pass
    return True
