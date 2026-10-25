import { useEffect, useMemo, useState } from 'react'
import './documentation.css'

function inline(text) {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^)]+\))/g).map((part,i)=>{
    if(part.startsWith('`'))return <code key={i}>{part.slice(1,-1)}</code>
    if(part.startsWith('**'))return <strong key={i}>{part.slice(2,-2)}</strong>
    const link=part.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/)
    return link?<a key={i} href={link[2]} target="_blank" rel="noreferrer">{link[1]} ↗</a>:part
  })
}
function Markdown({text}) {
  const lines=text.split('\n'),elements=[]
  for(let i=0;i<lines.length;i++){
    let line=lines[i]
    if(!line.trim())continue
    const key=i
    if(line.startsWith('```')){const code=[];while(++i<lines.length&&!lines[i].startsWith('```'))code.push(lines[i]);elements.push(<pre key={key}><code>{code.join('\n')}</code></pre>);continue}
    const heading=line.match(/^(#{1,5})\s+(.+)$/)
    if(heading){const Tag='h'+Math.min(4,heading[1].length+1);elements.push(<Tag key={key}>{inline(heading[2])}</Tag>);continue}
    if(line.startsWith('|')){const rows=[];while(i<lines.length&&lines[i].startsWith('|')){if(!/^\|[\s:|-]+\|$/.test(lines[i]))rows.push(lines[i].split('|').slice(1,-1).map(c=>c.trim()));i++}i--;elements.push(<div className="docs-table-scroll" key={key}><table><thead><tr>{rows[0]?.map((c,n)=><th key={n}>{inline(c)}</th>)}</tr></thead><tbody>{rows.slice(1).map((row,n)=><tr key={n}>{row.map((c,m)=><td key={m}>{inline(c)}</td>)}</tr>)}</tbody></table></div>);continue}
    if(/^[-*] /.test(line)||/^\d+\. /.test(line)){const ordered=/^\d/.test(line),items=[];while(i<lines.length&&(ordered?/^\d+\. /:/^[-*] /).test(lines[i]))items.push(lines[i++].replace(ordered?/^\d+\. /:/^[-*] /,''));i--;const List=ordered?'ol':'ul';elements.push(<List key={key}>{items.map((item,n)=><li key={n}>{inline(item)}</li>)}</List>);continue}
    if(line.startsWith('> ')){elements.push(<blockquote key={key}>{inline(line.slice(2))}</blockquote>);continue}
    while(i+1<lines.length&&lines[i+1].trim()&&!/^(#|```|\||[-*] |\d+\. |> )/.test(lines[i+1]))line+=' '+lines[++i]
    elements.push(<p key={key}>{inline(line)}</p>)
  }
  return elements
}

export default function AdminDocumentation({getJson}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[search,setSearch]=useState(''),[selected,setSelected]=useState('overview'),[copied,setCopied]=useState(false)
  useEffect(()=>{let cancel=false;getJson('/api/admin/documentation').then(next=>{if(!cancel)setData(next)}).catch(()=>{if(!cancel)setError('Не удалось загрузить документацию. Обновите раздел.')});return()=>{cancel=true}},[getJson])
  const sections=useMemo(()=>{const parts=(data?.content||'').split(/^## /m);return parts.map((part,i)=>({id:i?'section-'+i:'overview',title:i?part.split('\n')[0]:'Обзор',text:i?part.slice(part.indexOf('\n')+1):part}))},[data])
  const matches=sections.filter(section=>(section.title+' '+section.text).toLowerCase().includes(search.trim().toLowerCase()))
  const visible=search?matches:sections.filter(section=>section.id===selected)
  const copy=async()=>{try{await navigator.clipboard.writeText(data.exportContent);setCopied(true)}catch{setError('Не удалось скопировать. Скачайте файл документации.')}}
  return <section className="admin-card liquid-card documentation-panel"><div className="infra-section-heading"><div><span className="eyebrow">БАЗА ЗНАНИЙ</span><h2>Документация сервиса</h2><p>Устройство сервиса, управление и восстановление. Без паролей и VPN-ключей.</p></div><div className="infra-actions"><a className="secondary-button" href="/api/admin/documentation/export" download>Скачать .md</a><button className="primary-button" disabled={!data} onClick={copy}>{copied?'✓ Скопировано':'Скопировать для ИИ'}</button></div></div>
    {error&&<p className="infra-notice error" role="alert">{error}</p>}{data?<><p className="docs-version">Версия {data.version} · обновлено {new Date(data.updatedAt).toLocaleDateString('ru-RU')}<span>Выгрузка включает текущее состояние нод.</span></p><label className="docs-search">Найти в документации<input type="search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Например: оплата, Raspberry, восстановление…"/></label><div className="docs-layout"><nav className="docs-navigation" aria-label="Разделы документации">{sections.map(section=><button type="button" key={section.id} aria-current={selected===section.id&&!search?'page':undefined} onClick={()=>{setSelected(section.id);setSearch('')}}>{section.title}</button>)}<button type="button" aria-current={selected==='runtime'?'page':undefined} onClick={()=>{setSelected('runtime');setSearch('')}}>Текущее состояние</button></nav><article className="docs-content">{selected==='runtime'&&!search?<><h3>Текущее состояние</h3><p>Снимок на момент открытия раздела. Для свежих измерений откройте «Ноды».</p><pre><code>{JSON.stringify(data.context,null,2)}</code></pre></>:visible.length?visible.map(section=><section key={section.id}><h3>{section.title}</h3><Markdown text={section.text}/></section>):<p>По этому запросу ничего не найдено.</p>}</article></div></>:!error&&<p>Загружаем документацию…</p>}</section>
}
