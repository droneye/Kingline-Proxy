from node_manager.ru_policy import build,RU_TAG
def test_ru_route_preserves_private_block_and_does_not_affect_non_ru():
    config={'inbounds':[{'tag':'public','port':443}], 'outbounds':[{'tag':'direct','protocol':'freedom'},{'tag':'blocked','protocol':'blackhole'}], 'routing':{'rules':[{'type':'field','ip':['geoip:private'],'outboundTag':'blocked'}]}}
    result=build(config,'private-service-uuid',['domain:ru','domain:yandex.net'])
    assert result['routing']['rules'][0]['outboundTag']=='blocked'
    assert result['routing']['rules'][1]['balancerTag']=='kingline-ru'
    assert result['routing']['balancers'][0]['fallbackTag']=='direct'
    assert result['inbounds']==config['inbounds']
    assert len(config['outbounds'])==2
    assert build(result,'private-service-uuid',['domain:ru','domain:yandex.net'])==result
    assert [o['tag'] for o in result['outbounds']].count(RU_TAG)==1
