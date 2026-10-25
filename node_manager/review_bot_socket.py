"""Narrow bot-only Unix socket. No node credentials, generic proxy or delete endpoint."""
import os
from fastapi import FastAPI,Request,HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from .models import StrictModel
from .site_admin import ManagerClient
from pathlib import Path

class Actor(StrictModel):actor_id:int=Field(gt=0)
class Acknowledgement(Actor):
    id:int=Field(gt=0)
    message_id:int=Field(gt=0)
class Callback(Actor):data:str=Field(pattern=r'^kr:[A-Za-z0-9_-]{24}$')
class BillingCallback(Actor):data:str=Field(pattern=r'^kb:[A-Za-z0-9_-]{24}$')

def create_app(client=None):
    client=client or ManagerClient(Path(os.environ.get('KINGLINE_MANAGER_ROOT','/opt/kingline-manager/data')))
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.middleware('http')
    async def bounded(request:Request,call_next):
        size=request.headers.get('content-length','')
        if request.method!='POST' or not size.isdigit() or int(size)>2048:return JSONResponse({'ok':False,'error':'invalid_request'},413)
        result=await call_next(request);result.headers['Cache-Control']='no-store';return result
    @app.exception_handler(RequestValidationError)
    async def invalid(request,error):return JSONResponse({'ok':False,'error':'invalid_request'},422)
    @app.exception_handler(HTTPException)
    async def known(request,error):return JSONResponse({'ok':False,'error':error.detail},error.status_code)
    @app.post('/claim')
    def claim(body:Actor):return client.call('POST','/v1/node-reviews/bot/claim',body.model_dump())
    @app.post('/ack')
    def ack(body:Acknowledgement):return client.call('POST','/v1/node-reviews/bot/ack',body.model_dump())
    @app.post('/callback')
    def callback(body:Callback):return client.call('POST','/v1/node-reviews/bot/callback',body.model_dump())
    @app.post('/billing/claim')
    def billing_claim(body:Actor):return client.call('POST','/v1/node-billing/bot/claim',body.model_dump())
    @app.post('/billing/ack')
    def billing_ack(body:Acknowledgement):return client.call('POST','/v1/node-billing/bot/ack',body.model_dump())
    @app.post('/billing/callback')
    def billing_callback(body:BillingCallback):return client.call('POST','/v1/node-billing/bot/callback',body.model_dump())
    return app
if __name__=='__main__':
    import uvicorn
    uvicorn.run(create_app(),uds='/run/kingline-node-review/bot.sock',log_level='warning',access_log=False)
