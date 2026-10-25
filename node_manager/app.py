import contextlib
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
import secrets
import time
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .manager import Manager
from . import diagnostics
from .models import AddNode, Capacity, Credentials, ProbeReport, RemoveNode, VPNDraft, VPNApply, VPNRollback, NodeConnection, NodeRoutes, NodeOrder, RUReceiver, NodeVisibility, ReviewAction, ReviewCheck, InstallationAction
from .ssh import SafeError
from .store import atomic_json


def create_app(manager=None, run_worker=True):
    root = Path(os.environ.get('KINGLINE_MANAGER_ROOT', '/opt/kingline-manager/data'))
    if os.environ.get('KINGLINE_DIAGNOSTICS_PATH'): diagnostics.configure('node-manager')
    manager = manager or Manager(root)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if run_worker:
            manager.start()
        yield
        if run_worker:
            manager.close()

    app = FastAPI(title='Kingline Node Manager', version='1.2.0', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.manager = manager

    def admin(request:Request,authorization: Annotated[str | None, Header()] = None):
        expected = 'Bearer ' + (manager.root / 'admin.token').read_text().strip()
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(401, 'unauthorized')
        ru=getattr(manager,'ru_installer',None)
        if ru and request.method in {'POST','PATCH','PUT','DELETE'} and request.url.path.startswith(('/v1/nodes','/v1/node-network','/v1/vpn/')) and ru.active():
            raise HTTPException(409,'ru_installation_busy')

    def probe_auth(authorization: Annotated[str | None, Header()] = None):
        if not authorization or not authorization.startswith('Bearer '):
            raise HTTPException(401, 'unauthorized')
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with manager.store.db() as db:
            row = db.execute('SELECT * FROM probes WHERE token_hash=?', (digest,)).fetchone()
        if row is None:
            raise HTTPException(401, 'unauthorized')
        return dict(row)

    @app.middleware('http')
    async def private_response(request: Request, call_next):
        incoming=request.headers.get('x-request-id','')
        rid=incoming if re.fullmatch(r'[a-f0-9]{32}',incoming) else uuid.uuid4().hex
        token=diagnostics.REQUEST_ID.set(rid);started=time.monotonic();response=None
        try:
            if request.method in ('POST', 'PATCH', 'PUT', 'DELETE'):
                length = request.headers.get('content-length', '')
                if not length.isdigit() or int(length) > 65536:
                    return JSONResponse({'detail': 'body_length_required_max_65536'}, status_code=413)
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Request-ID'] = rid
            return response
        finally:
            diagnostics.observe('node_manager_http',(time.monotonic()-started)*1000,target=diagnostics.safe_route(request.url.path),status=response.status_code if response else 500)
            diagnostics.REQUEST_ID.reset(token)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        # FastAPI's normal response includes input values. Do not echo SSH credentials.
        return JSONResponse({'detail': [{'field': list(e['loc']), 'type': e['type']} for e in error.errors()]}, status_code=422)

    @app.exception_handler(ValueError)
    async def conflict(request, error):
        allowed = {'idempotency_key_conflict', 'ip_already_enrolled_or_reserved', 'queue_full',
                   'node_name_already_used', 'management_address_pool_exhausted', 'job_is_not_failed',
                   'protected_server_address', 'server_is_already_managed', 'retired_address_requires_manual_reset'}
        return JSONResponse({'detail': str(error) if str(error) in allowed else 'invalid_request'}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({'detail': 'not_found'}, status_code=404)

    @app.exception_handler(SafeError)
    async def safe_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.exception_handler(Exception)
    async def internal(request, error):
        fields=diagnostics.exception_fields(error);fields.pop('message',None)
        diagnostics.event('node_manager_exception', 'ERROR', **fields)
        return JSONResponse({'detail': 'operation_failed'}, status_code=500)

    @app.get('/healthz')
    def health():
        alive=bool(manager.thread and manager.thread.is_alive())
        vpn_alive=bool(manager.vpn_config.thread and manager.vpn_config.thread.is_alive())
        relay_alive=bool(manager.network.thread and manager.network.thread.is_alive())
        ru_alive=bool(manager.ru_installer.thread and manager.ru_installer.thread.is_alive())
        review_alive=bool(manager.reviews.thread and manager.reviews.thread.is_alive())
        return {'ok':not run_worker or (alive and vpn_alive and relay_alive and ru_alive and review_alive),'worker_alive':alive,
                'vpn_worker_alive':vpn_alive,'relay_accounting_alive':relay_alive,'ru_worker_alive':ru_alive,'review_worker_alive':review_alive}

    @app.get('/v1/openapi.json', dependencies=[Depends(admin)])
    def schema():
        return app.openapi()

    @app.get('/v1/ru-egress',dependencies=[Depends(admin)])
    def ru_status():return {'ok':True,'gateway':manager.ru_installer.gateway.public(),'jobs':manager.ru_installer.jobs()}

    @app.post('/v1/ru-egress',dependencies=[Depends(admin)],status_code=202)
    def ru_replace(body:RUReceiver):return {'ok':True,'job':manager.ru_installer.submit(body)}

    @app.get('/v1/ru-egress/jobs/{identifier}',dependencies=[Depends(admin)])
    def ru_job(identifier:str):return {'ok':True,'job':manager.ru_installer.public(manager.ru_installer.read(identifier))}

    @app.post('/v1/ru-egress/jobs/{identifier}/retry',dependencies=[Depends(admin)],status_code=202)
    def ru_retry(identifier:str,body:Credentials):return {'ok':True,'job':manager.ru_installer.retry(identifier,body)}

    @app.get('/v1/node-reviews',dependencies=[Depends(admin)])
    def reviews():return manager.reviews.snapshot()

    @app.post('/v1/node-reviews/check',dependencies=[Depends(admin)])
    def review_check(body:ReviewCheck):return manager.reviews.request(body.node,identifier=body.request_id)

    @app.post('/v1/node-reviews/action',dependencies=[Depends(admin)])
    def review_action(body:ReviewAction):return manager.reviews.action(body.review_id,body.operation,body.parent,body.request_id)

    @app.post('/v1/node-reviews/visibility',dependencies=[Depends(admin)])
    def node_visibility(body:NodeVisibility):return manager.reviews.visibility(body.uuid,body.hidden,body.request_id)

    @app.post('/v1/node-reviews/bot/claim',dependencies=[Depends(admin)])
    def bot_claim(body:dict):return {'ok':True,'items':manager.reviews.claim_outbox(body.get('actor_id'))}

    @app.post('/v1/node-reviews/bot/ack',dependencies=[Depends(admin)])
    def bot_ack(body:dict):return manager.reviews.ack(body.get('actor_id'),body.get('id'),body.get('message_id'))

    @app.post('/v1/node-reviews/bot/callback',dependencies=[Depends(admin)])
    def bot_callback(body:dict):return manager.reviews.callback(body.get('actor_id'),str(body.get('data',''))[:64])

    @app.post('/v1/node-billing/bot/claim',dependencies=[Depends(admin)])
    def billing_claim(body:dict):return {'ok':True,'items':manager.billing.claim(body.get('actor_id'))}

    @app.post('/v1/node-billing/bot/ack',dependencies=[Depends(admin)])
    def billing_ack(body:dict):return manager.billing.ack(body.get('actor_id'),body.get('id'),body.get('message_id'))

    @app.post('/v1/node-billing/bot/callback',dependencies=[Depends(admin)])
    def billing_callback(body:dict):return manager.billing.callback(body.get('actor_id'),str(body.get('data',''))[:64])

    @app.get('/v1/standard', dependencies=[Depends(admin)])
    def standard():
        return manager.standard_public()

    @app.get('/v1/vpn/config', dependencies=[Depends(admin)])
    def vpn_config(profile: str | None = None):
        return manager.vpn_config.catalog(profile)

    @app.get('/v1/vpn/change', dependencies=[Depends(admin)])
    def vpn_change(id: str):
        return {'ok': True, 'change': manager.vpn_config.public(id)}

    @app.post('/v1/vpn/validate', dependencies=[Depends(admin)], status_code=202)
    def vpn_validate(body: VPNDraft):
        return {'ok': True, 'change': manager.vpn_config.submit(body.model_dump(mode='json'))}

    @app.post('/v1/vpn/apply', dependencies=[Depends(admin)], status_code=202)
    def vpn_apply(body: VPNApply):
        return {'ok': True, 'change': manager.vpn_config.commit(body.id)}

    @app.post('/v1/vpn/rollback', dependencies=[Depends(admin)], status_code=202)
    def vpn_rollback(body: VPNRollback):
        return {'ok': True, 'change': manager.vpn_config.rollback(body.id, body.revision)}

    @app.get('/v1/nodes', dependencies=[Depends(admin)])
    def nodes():
        inventory = manager.network.enrich(manager.store.nodes())
        presented = []
        from .ru_egress import RUEgress
        ru=RUEgress(manager.root,manager.control,manager.ssh_factory)
        ru_state=json.loads(ru.path.read_text()) if ru.path.exists() else {}
        for node in inventory:
            node={**node,'ru_enabled':ru.selected(node,ru_state)}
            reason = ('node_not_managed_by_backend' if not (node.get('job_id') or node.get('managed_ssh')) else
                      'cannot_remove_last_node' if not any(
                          n['uuid'] != node['uuid'] and n.get('provisioned', True) and not n.get('maintenance', False)
                          for n in inventory) else None)
            if reason is None:
                try: manager.network.removal_guard(node['uuid'])
                except SafeError as exc: reason=str(exc)
            presented.append({**node, 'removable': reason is None, 'removal_block_reason': reason})
        result = {'nodes': presented,'ru_gateway':ru.public()}
        status = manager.control / 'smart-dns-status.json'
        if status.exists():
            result['routing'] = json.loads(status.read_text())
        return result

    @app.get('/v1/node-network', dependencies=[Depends(admin)])
    def node_network(): return manager.network.catalog()

    @app.post('/v1/node-network/connection', dependencies=[Depends(admin)], status_code=202)
    def node_connection(body:NodeConnection): return {'ok':True,'change':manager.network.submit('connection',body.model_dump(mode='json'))}

    @app.post('/v1/node-network/routes', dependencies=[Depends(admin)], status_code=202)
    def node_routes(body:NodeRoutes): return {'ok':True,'change':manager.network.submit('routes',body.model_dump(mode='json'))}

    @app.post('/v1/node-network/order', dependencies=[Depends(admin)], status_code=202)
    def node_order(body:NodeOrder): return {'ok':True,'change':manager.network.submit('order',body.model_dump(mode='json'))}

    @app.post('/v1/node-network/rollback', dependencies=[Depends(admin)], status_code=202)
    def node_rollback(body:VPNRollback): return {'ok':True,'change':manager.network.rollback(body.id,body.revision)}

    @app.post('/v1/nodes/preflight', dependencies=[Depends(admin)])
    def preflight(body: AddNode):
        return manager.preflight(body)

    @app.post('/v1/nodes', status_code=202, dependencies=[Depends(admin)])
    def add(body: AddNode, idempotency_key: Annotated[str, Header(min_length=8, max_length=128)]):
        return manager.submit(body, idempotency_key)

    @app.patch('/v1/nodes/{node_uuid}', dependencies=[Depends(admin)])
    def capacity(node_uuid: str, body: Capacity):
        return manager.capacity(node_uuid, body)

    @app.delete('/v1/nodes/{node_uuid}', dependencies=[Depends(admin)])
    def remove(node_uuid: str, body: RemoveNode):
        return manager.remove(node_uuid, body)

    @app.get('/v1/removals/{node_uuid}', dependencies=[Depends(admin)])
    def removal_status(node_uuid: str):
        result = manager.store.removal(node_uuid)
        if result is None:
            raise KeyError(node_uuid)
        return result

    @app.get('/v1/jobs', dependencies=[Depends(admin)])
    def jobs():
        with manager.store.db() as db:
            return {'jobs': [manager.store.public_job(row) for row in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]}

    @app.get('/v1/jobs/{job}', dependencies=[Depends(admin)])
    def job_status(job: str):
        row = manager.store.get(job)
        with manager.store.db() as db:
            events = [dict(e) for e in db.execute('SELECT at,phase FROM events WHERE job=? ORDER BY id', (job,))]
        return {**manager.store.public_job(row), 'events': events,
                'management_ip': row['context'].get('management_ip'),
                'host_key_sha256': row['context'].get('host_key_sha256'),
                'verification': row['context'].get('verification'),
                'review': manager.onboarding.public(job)}

    @app.post('/v1/installation-action', dependencies=[Depends(admin)])
    def installation_action(body: InstallationAction):
        return manager.onboarding.action(body)

    @app.post('/v1/jobs/{job}/retry', status_code=202, dependencies=[Depends(admin)])
    def retry(job: str, body: Credentials):
        manager.store.get(job)
        manager.store.retry(job, body)
        return manager.store.public_job(manager.store.get(job))

    @app.get('/v1/probes/targets')
    def targets(probe=Depends(probe_auth)):
        return {'probe_id': probe['id'], 'region': probe['region'], 'interval_seconds': 60,
                'targets': [{'node_uuid': n['uuid'], 'ip': n['public_ip'], 'port': 443,
                             'name': n['name']} for n in manager.store.nodes() if n.get('provisioned', True)],
                'tunnel_credentials': 'Provision a dedicated test user on the Raspberry Pi before reporting vpn_ok.'}

    @app.post('/v1/probes/reports')
    def report(body: ProbeReport, probe=Depends(probe_auth)):
        if body.node_uuid not in {n['uuid'] for n in manager.store.nodes()}:
            raise HTTPException(404, 'unknown_node')
        with manager.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM observations WHERE probe=? AND node=?',
                             (probe['id'], body.node_uuid)).fetchone()
            if old and body.sequence <= old['sequence']:
                raise HTTPException(409, 'stale_sequence')
            fresh_old = old and time.time() - old['received'] <= 300
            failures = 0 if body.vpn_ok else (old['failures'] + 1 if fresh_old else 1)
            db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(probe,node) DO UPDATE SET sequence=excluded.sequence,received=excluded.received,'
                       'ok=excluded.ok,failures=excluded.failures,latency=excluded.latency,error=excluded.error',
                       (probe['id'], body.node_uuid, body.sequence, time.time(), body.vpn_ok,
                        failures, body.latency_ms, body.error))
        with manager.lock:
            with manager.store.db() as db:
                observations = [dict(r) for r in db.execute(
                    'SELECT observations.*,probes.region,probes.required FROM observations JOIN probes ON probes.id=observations.probe')]
            atomic_json(manager.control / 'regional-probes.json', {'observations': observations})
        return {'accepted': True}

    return app
