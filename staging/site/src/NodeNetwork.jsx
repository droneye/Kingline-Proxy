import { useCallback, useEffect, useRef, useState } from 'react'
import './node-network.css'

const base='/api/admin/infrastructure/'
const active=new Set(['queued_check','checking','queued_apply','applying'])
const messages={network_route_cycle:'Получился замкнутый маршрут. Выберите другую входную ноду.',network_nodes_changed:'Список серверов изменился. Обновите данные.',network_keys_changed:'Список ключей изменился. Обновите порядок.',network_route_too_long:'В одной цепочке допускается до восьми нод.',network_tunnel_check_failed:'Проверка VPN через новый маршрут не прошла. Возвращаем прежние настройки.',network_node_has_dependents:'Через эту ноду подключаются другие локации. Сначала измените их маршруты.',network_disconnect_before_remove:'Перед удалением выберите для этой локации прямое подключение.',vpn_revision_changed:'Настройки уже изменились. Обновите данные и сохраните ещё раз.',vpn_operation_busy:'Дождитесь завершения текущего изменения.',vpn_installation_busy:'Дождитесь завершения установки сервера.',vpn_xray_validation_failed:'Xray не принял настройки или сервер недоступен.',vpn_rollback_revision_changed:'После этой операции были другие изменения. Автоматическое восстановление заблокировано.',ssh_connection_failed:'Не удалось связаться с сервером по служебному ключу.',remote_operation_failed:'На сервере не удалось применить изменения. Прежние настройки восстанавливаются.',manager_unavailable:'Служба нод временно недоступна.',infrastructure_unavailable:'Служба нод временно недоступна.',invalid_request:'Проверьте SNI и target. Для target нужен адрес с портом, например example.org:443.'}
const explain=e=>messages[e?.code||e?.message||e]||'Не удалось выполнить изменение. Подробности — в технических логах.'
const states={queued_check:'Изменения в очереди',checking:'Проверяем настройки',queued_apply:'Готовим восстановление',applying:'Применяем изменения',ready:'Изменения сохранены',noop:'Настройки уже такие',failed:'Не удалось выполнить',rolled_back:'Прежние настройки возвращены',rollback_failed:'Восстановление не завершено'}
const phases={queued:'Ожидаем запуска',checking:'Проверяем конфигурацию и связь с серверами',checked:'Проверка завершена',configuring:'Готовим подключение',relays:'Настраиваем передачу между нодами',verifying:'Проверяем интернет и DNS через VPN',publishing:'Обновляем ключи в подписках',rolling_back:'Возвращаем прежние настройки',ready:'Обновите подписку в приложении',unchanged:'Повторное применение не требуется'}

export function NodeConnectionDialog({node,getJson,postJson,onClose,onAccepted,Dialog}) {
  const [data,setData]=useState(null),[sni,setSni]=useState(''),[target,setTarget]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('')
  useEffect(()=>{let cancel=false;getJson(base+'node-network').then(next=>{if(cancel)return;const row=next.nodes.find(n=>n.uuid===node.uuid);if(!row){setError(messages.network_nodes_changed);return}setData(next);setSni(row.sni);setTarget(row.target)}).catch(e=>{if(!cancel)setError(explain(e))});return()=>{cancel=true}},[getJson,node.uuid])
  const submit=async e=>{e.preventDefault();setBusy(true);setError('');try{const result=await postJson(base+'node-connection',{uuid:node.uuid,sni:sni.trim(),target:target.trim(),revision:data.revision,request_id:crypto.randomUUID()});onAccepted(result.change);onClose()}catch(e){setError(explain(e))}finally{setBusy(false)}}
  return <Dialog title={'SNI и target · '+(node.label||node.display_name)} onClose={()=>!busy&&onClose()}><form className="infra-form" onSubmit={submit}>{!data&&!error&&<p>Загружаем текущие настройки…</p>}<label>SNI<input autoCapitalize="none" autoCorrect="off" spellCheck={false} value={sni} onChange={e=>setSni(e.target.value)} required disabled={!data||busy} maxLength="253" placeholder="example.org"/><small>Имя сайта в TLS-подключении.</small></label><label>Target<input autoCapitalize="none" autoCorrect="off" spellCheck={false} value={target} onChange={e=>setTarget(e.target.value)} required disabled={!data||busy} maxLength="260" placeholder="example.org:443"/><small>Адрес и порт цели REALITY. Цель должна поддерживать выбранное SNI.</small></label>{data&&sni!==data.common_sni&&<p className="infra-notice">SNI отличается от общего. Эта локация будет доступна отдельным ключом; DNS-«Умный выбор» временно её исключит.</p>}<p className="infra-footnote">Изменение касается этой ноды. После проверки настройки сохранятся автоматически. Затем обновите подписку в приложении.</p>{error&&<p className="infra-notice error" role="alert">{error}</p>}<footer className="infra-dialog-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy||!data}>{busy?'Отправляем…':'Сохранить и проверить'}</button></footer></form></Dialog>
}

function chain(via,id) {
  const result=[],seen=new Set()
  for(let current=id;current;current=via[current]){if(seen.has(current))return null;seen.add(current);result.unshift(current)}
  return result
}
function allowedParent(via,id,parent) {
  const path=chain(via,parent)
  return path&&!path.includes(id)
}

function RouteTree({nodes,via,selected,onSelect,ru}) {
  const positions={},columns={}
  for(const n of nodes){const depth=chain(via,n.uuid)?.length||1;(columns[depth] ||= []).push(n)}
  const highest=Math.max(1,...Object.values(columns).map(list=>list.length)),height=Math.max(360,highest*112+100),depth=Math.max(1,...Object.keys(columns).map(Number))
  positions.device={x:24,y:height/2-40}
  for(const [level,list] of Object.entries(columns))for(const [i,node] of list.entries())positions[node.uuid]={x:Number(level)*290+24,y:(height-list.length*112)/2+i*112+14}
  const exit=positions[selected],chosen=nodes.find(n=>n.uuid===selected),viaRU=chosen?.ru_enabled!==false&&ru?.configured
  const endX=(depth+1)*290+24,ruY=height/2+25,width=(depth+2)*290+24
  const selectedPath=new Set(chain(via,selected)||[])
  const branch=(russian)=>{
    const x=exit.x+216,y=exit.y+40,toY=russian?ruY+40:height/2-65
    if(exit.x+290===endX)return `M${x},${y} C${x+38},${y} ${endX-40},${toY} ${endX-8},${toY}`
    // Keep both branches outside unrelated node cards in deeper columns.
    const lane=russian?height-22:22,edge=russian?ruY+88:height/2-113
    return `M${x},${y} H${x+24} V${lane} H${endX+108} V${edge}`
  }
  return <div className="node-tree-scroll" aria-label="Схема маршрутов"><div className="node-tree-stage" style={{width,height}}>
    <svg width={width} height={height} role="img" aria-label="Путь до выбранной ноды; обычный интернет и отдельная ветка российских сайтов">
      <defs><marker id="node-route-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="currentColor"/></marker></defs>
      {nodes.map(n=>{const parent=via[n.uuid]||'device',a=positions[parent]||positions.device,b=positions[n.uuid],x=a.x+216,y=a.y+40;return <path key={n.uuid} className={selectedPath.has(n.uuid)?'is-highlighted':''} d={`M${x},${y} C${x+40},${y} ${b.x-40},${b.y+40} ${b.x-8},${b.y+40}`} markerEnd="url(#node-route-arrow)"/>})}
      {exit&&<><path className="internet-branch" d={branch(false)} markerEnd="url(#node-route-arrow)"/><path className="ru-branch" d={branch(true)} markerEnd="url(#node-route-arrow)"/></>}
    </svg>
    <div className="node-tree-device" style={{left:positions.device.x,top:positions.device.y}}><span>▣</span><div><strong>Ваше устройство</strong><small>Начало подключения</small></div></div>
    {nodes.map(n=><button type="button" key={n.uuid} className={'node-tree-server '+(selected===n.uuid?'is-selected':'')} style={{left:positions[n.uuid].x,top:positions[n.uuid].y}} aria-pressed={selected===n.uuid} onClick={()=>onSelect(n.uuid)}><strong>{n.label}</strong><small>{via[n.uuid]?'Через '+(nodes.find(p=>p.uuid===via[n.uuid])?.label||'ноду'):'Прямое подключение'}</small><span className="mono">{n.ip}</span></button>)}
    <div className="node-tree-ru node-tree-internet" style={{left:endX,top:height/2-105}}><strong>Обычный интернет</strong><small>IP выбранной локации</small></div>
    <div className={'node-tree-ru '+(!viaRU?'node-tree-internet':'')} style={{left:endX,top:ruY}}><strong>{viaRU?'🇷🇺 Российский выход':'Российские сайты'}</strong><small>{viaRU?(ru.name||'РФ-сервер'):'Напрямую с этой локации'}</small><small>{viaRU?'Российские сайты · IP РФ':'IP выбранной локации'}</small></div>
  </div></div>
}

export default function NodeTools({getJson,postJson,canManageNetwork,view,onViewChange,job,onJobChange,onData,onRefresh,Dialog,nodeSignature}) {
  const [data,setData]=useState(null),[order,setOrder]=useState([]),[via,setVia]=useState({}),[selected,setSelected]=useState('')
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[dragging,setDragging]=useState(null),[confirm,setConfirm]=useState(null)
  const sequence=useRef(0),handled=useRef('')
  const jobId=job?.id,jobState=job?.state
  const refresh=useCallback(async()=>{
    const call=++sequence.current
    try{const next=await getJson(base+(canManageNetwork?'node-network':'node-order'));if(call!==sequence.current)return;setData(next);setOrder(next.keys.map(k=>k.uuid));setVia(Object.fromEntries((next.nodes||[]).map(n=>[n.uuid,n.via||null])));setSelected(old=>next.nodes?.some(n=>n.uuid===old)?old:next.nodes?.find(n=>n.uuid===new URLSearchParams(location.search).get('routeNode'))?.uuid||next.nodes?.[0]?.uuid||'');onData(next);setError('');const running=next.jobs?.find(j=>active.has(j.state)||j.state==='rollback_failed');if(running)onJobChange(running)}catch(e){if(call===sequence.current)setError(explain(e))}
  },[getJson,canManageNetwork,onData,onJobChange])
  useEffect(()=>{const timer=setTimeout(refresh,0);return()=>clearTimeout(timer)},[refresh,nodeSignature])
  useEffect(()=>{
    if(!jobId||!active.has(jobState))return
    let canceled=false
    const poll=async()=>{try{const next=await getJson(base+'node-job?id='+jobId);if(!canceled)onJobChange(next.change)}catch(e){if(!canceled)setError(explain(e))}}
    const timer=setInterval(()=>{if(!document.hidden)poll()},2000),first=setTimeout(poll,100)
    return()=>{canceled=true;clearInterval(timer);clearTimeout(first)}
  },[getJson,jobId,jobState,onJobChange])
  useEffect(()=>{
    if(!job||active.has(job.state)||handled.current===job.id+job.state)return
    handled.current=job.id+job.state
    const timer=setTimeout(()=>{refresh();onRefresh()},0)
    return()=>clearTimeout(timer)
  },[job,refresh,onRefresh])
  const locked=busy||active.has(job?.state)
  const nodes=data?.nodes||[],byId=Object.fromEntries(nodes.map(n=>[n.uuid,n]))
  const oldVia=Object.fromEntries(nodes.map(n=>[n.uuid,n.via||null]))
  const changedOrder=data&&order.join(',')!==data.keys.map(k=>k.uuid).join(',')
  const changedRoutes=data&&JSON.stringify(via)!==JSON.stringify(oldVia)
  const selectedPath=chain(via,selected)||[]
  const reorder=(id,to)=>{setOrder(old=>{const next=[...old],index=next.indexOf(id);if(to<0||to>=next.length||index<0)return old;next.splice(index,1);next.splice(to,0,id);return next})}
  const send=async action=>{
    setBusy(true);setError('')
    try{const body={revision:data.revision,request_id:crypto.randomUUID(),...(action==='order'?{hosts:order}:{via})};const result=await postJson(base+'node-'+action,body);onJobChange(result.change);setConfirm(null)}catch(e){setError(explain(e));setConfirm(null)}finally{setBusy(false)}
  }
  const restore=async()=>{setBusy(true);try{const latest=await getJson(base+'node-network');const result=await postJson(base+'node-rollback',{id:job.id,revision:latest.revision});onJobChange(result.change);setConfirm(null)}catch(e){setError(explain(e));setConfirm(null)}finally{setBusy(false)}}
  return <div className="node-tools"><nav className="node-view-tabs" aria-label="Управление нодами">{[['servers','Серверы'],['order','Порядок ключей'],...(canManageNetwork?[['routes','Маршруты']]:[])].map(([id,label])=><button type="button" key={id} aria-pressed={view===id} onClick={()=>onViewChange(id)}>{label}</button>)}</nav>{error&&<p className="infra-notice error" role="alert">{error}</p>}
    {job&&<section className={'node-network-job '+(['failed','rollback_failed'].includes(job.state)?'has-error':'')} aria-live="polite"><div><span className="eyebrow">{job.profile_name}</span><strong>{states[job.state]||job.state}</strong><small>{phases[job.phase]||''}</small></div>{active.has(job.state)&&<span className="node-job-spinner" aria-label="Выполняется"/>}{job.error&&<p>{explain(job.error)}</p>}{job.checks?.length>0&&<div className="node-network-checks">{job.checks.map((c,i)=><small key={i}>{c.ok?'✓':'×'} {c.node} · {c.check==='xray_test'?'Конфигурация Xray':'HTTPS и DNS'}</small>)}</div>}{!active.has(job.state)&&<div className="infra-actions">{canManageNetwork&&job.can_rollback&&<button className="secondary-button" disabled={busy} onClick={()=>setConfirm('restore')}>{job.state==='rollback_failed'?'Повторить восстановление':'Вернуть прежние настройки'}</button>}<button className="text-button" onClick={()=>onJobChange(null)}>Закрыть</button></div>}</section>}
    {view==='order'&&<section className="node-order-panel"><div className="node-tool-heading"><div><h3>Порядок ключей в подписке</h3><p>Расположите ключи так, как они должны идти в приложении.</p></div><button className="secondary-button" disabled={locked} onClick={refresh}>Обновить</button></div><ol className="node-key-list">{order.map((id,index)=>{const key=data?.keys.find(k=>k.uuid===id);if(!key)return null;return <li key={id} className={dragging===id?'is-dragging':''} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(dragging&&!locked)reorder(dragging,index);setDragging(null)}}><span className="node-key-number">{index+1}</span><button className="node-drag-handle" draggable={!locked} disabled={locked} onDragStart={e=>{e.dataTransfer.setData('text/plain',id);setDragging(id)}} onDragEnd={()=>setDragging(null)} aria-label={'Перетащить '+key.label}>⠿</button><div><strong>{key.label}</strong><small>{key.smart?'Автоматический выбор локации':'Отдельная локация'}</small></div><div className="node-order-arrows"><button type="button" disabled={locked||index===0} onClick={()=>reorder(id,index-1)} aria-label={'Поднять '+key.label}>↑</button><button type="button" disabled={locked||index===order.length-1} onClick={()=>reorder(id,index+1)} aria-label={'Опустить '+key.label}>↓</button></div></li>})}</ol>{!data&&<p>Загружаем ключи…</p>}<div className="node-tool-footer"><small>На компьютере можно перетаскивать. Стрелки работают и на телефоне. Новый порядок появится после обновления подписки; приложение может применить свою сортировку.</small><button className="primary-button" disabled={locked||!changedOrder} onClick={()=>send('order')}>Сохранить порядок</button></div></section>}
    {view==='routes'&&canManageNetwork&&<section className="node-route-panel"><div className="node-tool-heading"><div><h3>Кто через кого подключается</h3><p>Выберите локацию на схеме и назначьте ей входную ноду.</p></div><button className="secondary-button" disabled={locked} onClick={refresh}>Обновить</button></div>{data?<><RouteTree nodes={nodes} via={via} selected={selected} onSelect={setSelected} ru={data.ru_egress}/><p className="node-tree-mobile-hint">Схему можно листать вбок →</p><div className="node-route-inspector"><div><span className="eyebrow">ВЫХОДНАЯ ЛОКАЦИЯ</span><h3>{byId[selected]?.label}</h3><p>Обычные сайты увидят IP этой локации.</p><p className="node-route-ru-note">Российские сайты: {byId[selected]?.ru_enabled!==false&&data.ru_egress?.configured ? 'через российский выход' : 'напрямую с этой локации'}. Настройка меняется в карточке ноды.</p><div className="node-route-path"><span>Устройство</span>{selectedPath.map(id=><span key={id}>→ {byId[id]?.label}</span>)}</div></div><label>Подключать через<select value={via[selected]||''} disabled={locked||!selected} onChange={e=>setVia({...via,[selected]:e.target.value||null})}><option value="">Напрямую</option>{nodes.filter(n=>n.uuid!==selected).map(n=><option key={n.uuid} value={n.uuid} disabled={!allowedParent(via,selected,n.uuid)}>{n.label}</option>)}</select><small>Петли и подключение ноды через себя запрещены.</small></label></div><p className="node-route-hint">Локация с цепочкой остаётся отдельным ключом. DNS-«Умный выбор» использует прямые подключения. Чем длиннее цепочка, тем выше задержка и расход трафика на промежуточных серверах.</p><div className="node-tool-footer"><small>Сначала проверим новый маршрут. При ошибке вернём прежние настройки. Служебный сервер в Литве в передаче VPN-трафика не участвует.</small><button className="primary-button" disabled={locked||!changedRoutes} onClick={()=>setConfirm('routes')}>Сохранить маршруты</button></div></>:<p>Загружаем схему…</p>}</section>}
    {confirm&&<Dialog title={confirm==='restore'?'Вернуть прежние настройки?':'Сохранить новые маршруты?'} onClose={()=>!busy&&setConfirm(null)}><div className="infra-form">{confirm==='routes'?<><p>Ключи изменённых локаций будут подключаться через выбранные входные ноды.</p><p>После завершения обновите подписку в приложении. Уже открытые соединения могут сохранять прежний путь до переподключения.</p></>:<p>Будут восстановлены параметры перед этой операцией. Более свежие изменения backend не перезапишет.</p>}<footer className="infra-dialog-actions"><button className="secondary-button" disabled={busy} onClick={()=>setConfirm(null)}>Отмена</button><button className="primary-button" disabled={busy} onClick={()=>confirm==='restore'?restore():send('routes')}>{busy?'Отправляем…':'Подтвердить'}</button></footer></div></Dialog>}
  </div>
}
