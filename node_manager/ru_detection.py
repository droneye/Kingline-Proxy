"""Conservative failure classification, not attribution to an organisation."""
TITLES={'healthy':'Проверка пройдена','unexpected_exit':'Неожиданный IP выхода VPN','unstable':'Повторяющиеся обрывы','flow_stall_suspected':'Передача начинается и зависает','tls_interference_suspected':'Возможная фильтрация TLS/REALITY','regional_path_suspected':'Вход недоступен из домашней сети РФ','server_unavailable':'Сбой ноды или её сети','inconclusive':'Нужна повторная проверка','probe_source_problem':'Проблема проверяющего Raspberry'}

def classify(row,*,core_ok=None,source_ok=True,other_ru_ok=False,previous=None):
    attempts=row.get('attempts',[]);failed=[a for a in attempts if not a.get('ok')]
    unstable=bool(failed)
    if not source_ok:return {'code':'probe_source_problem','confirmed':False,'confidence':'low','unstable':unstable}
    if row.get('ok'):
        repeated=unstable and bool(previous and previous.get('unstable'))
        return {'code':'unstable' if repeated else 'healthy','confirmed':repeated,'confidence':'medium' if repeated else 'none','unstable':unstable}
    if len(failed)<2:return {'code':'inconclusive','confirmed':False,'confidence':'low','unstable':unstable}
    if all(a.get('error')=='unexpected_exit' for a in failed):return {'code':'unexpected_exit','confirmed':True,'confidence':'medium','unstable':True}
    if core_ok is False:return {'code':'server_unavailable','confirmed':True,'confidence':'medium','unstable':True}
    zeros=all(not a.get('tcp',{}).get('ok') for a in failed)
    plateaus=[a['large']['bytes'] for a in failed if (a.get('small') or {}).get('ok') and a.get('large') and 2048<=a['large'].get('bytes',0)<=131072]
    region=core_ok is True and other_ru_ok
    if region and len(plateaus)>=2 and max(plateaus)<=min(plateaus)*1.6:code='flow_stall_suspected'
    elif region and any((a.get('small') or {}).get('ok') for a in failed):code='unstable'
    elif region and not zeros:code='tls_interference_suspected'
    elif core_ok is True:code='regional_path_suspected'
    else:code='inconclusive'
    return {'code':code,'confirmed':True,'confidence':'medium' if core_ok is not None else 'low','unstable':True,'plateau_bytes':round(sum(plateaus)/len(plateaus)) if plateaus else None}
