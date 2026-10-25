"""Server-side Russian-domain policy, with a native health-based direct fallback."""
import copy
RU_TAG='KINGLINE_RU'
BALANCER='kingline-ru'
def build(config,user_uuid,domains,address='10.78.0.1'):
    result=copy.deepcopy(config)
    direct=next(o['tag'] for o in result['outbounds'] if o.get('protocol')=='freedom')
    result['outbounds']=[o for o in result['outbounds'] if o.get('tag')!=RU_TAG]+[
        {'tag':RU_TAG,'protocol':'vless','settings':{'vnext':[{'address':address,'port':15443,'users':[{'id':user_uuid,'encryption':'none'}]}]},'streamSettings':{'network':'tcp','security':'none','sockopt':{'tcpKeepAliveIdle':60,'tcpKeepAliveInterval':30}}}]
    old=result.get('observatory') or {}
    if old and old.get('subjectSelector')!=[RU_TAG]:raise ValueError('existing_observatory_requires_review')
    if result.get('burstObservatory'):raise ValueError('existing_observatory_requires_review')
    result['observatory']={'subjectSelector':[RU_TAG],'probeUrl':'https://www.gstatic.com/generate_204','probeInterval':'30s','enableConcurrency':False}
    routing=result.setdefault('routing',{})
    routing['balancers']=[b for b in routing.get('balancers',[]) if b.get('tag')!=BALANCER]+[
        {'tag':BALANCER,'selector':[RU_TAG],'fallbackTag':direct,'strategy':{'type':'leastPing'}}]
    rules=[r for r in routing.get('rules',[]) if r.get('balancerTag')!=BALANCER]
    after=max((i for i,r in enumerate(rules) if 'geoip:private' in r.get('ip',[]) or r.get('inboundTag')==['api']),default=-1)+1
    rules.insert(after,{'type':'field','domain':list(domains),'network':'tcp,udp','balancerTag':BALANCER})
    routing['rules']=rules
    return result
