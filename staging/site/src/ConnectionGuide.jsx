import { useState } from 'react'
import clients from '../../bot/client_apps.json'
const {devices,apps}=clients
export default function ConnectionGuide({subscription, copyText, onAccess}) {
  const [device, setDevice] = useState(() => {const explicit=new URLSearchParams(location.search).get('device');return devices.some(d=>d.id===explicit) ? explicit : /iPhone|iPad/i.test(navigator.userAgent) ? 'ios' : /Android/i.test(navigator.userAgent) ? 'android' : /Macintosh/i.test(navigator.userAgent) ? 'mac' : /Windows/i.test(navigator.userAgent) ? 'windows' : 'ios'})
  const [client,setClient]=useState(()=>new URLSearchParams(location.search).get('client'))
  const [step, setStep] = useState(0)
  const [copied, setCopied] = useState(false)
  const [copyFailed,setCopyFailed]=useState(false)
  const current = devices.find(d => d.id === device)
  const appId=current.apps.includes(client) ? client : current.apps[0]
  const app=apps[appId]
  const originalUrl=subscription?.active && subscription?.subscriptionUrl
  const url = originalUrl
  const copy = async () => { if (url) { const success=await copyText(url, 'Ссылка скопирована. Добавьте её в приложение.');setCopied(success!==false);setCopyFailed(success===false) } }
  return <section className="instructions connection-guide" id="setup-guide">
    <div className="guide-intro"><div><span className="eyebrow">ПОДКЛЮЧЕНИЕ</span><h2>Три шага — и вы в сети</h2><p>Одна подписка — до 3 устройств. После продления обновите её в приложении.</p></div><a className="secondary-button" href="/support">Служба поддержки</a></div>
    <div className="device-picker" role="group" aria-label="Ваше устройство">{devices.map(d => <button type="button" key={d.id} aria-pressed={device === d.id} onClick={() => {setDevice(d.id);setClient(null);setStep(0);setCopied(false);setCopyFailed(false)}}>{d.label}</button>)}</div>
    <div className="liquid-card guide-wizard">
      <nav className="guide-step-nav" aria-label="Шаги подключения">{['Установить','Добавить','Включить'].map((label,i) => <button key={label} aria-current={step === i ? 'step' : undefined} onClick={() => setStep(i)}><span>{i+1}</span>{label}</button>)}</nav>
      <div className="guide-scene" key={`${device}-${appId}-${step}`}>
        <span className="guide-big-number" aria-hidden="true">0{step+1}</span>
        <div className="guide-scene-copy">
          {step === 0 && <><h3>Установите приложение</h3><p>{appId==='v2rayn' ? 'Установите v2rayN и вернитесь к инструкции.' : current.apps.includes('v2raytun') ? 'Нет v2RayTun в вашем регионе? Установите Happ. Оба приложения поддерживают вашу подписку.' : 'Установите Happ — оно поддерживает подписку и учёт устройств.'}</p><div className="device-picker guide-app-picker" role="group" aria-label="Приложение для VPN">{current.apps.map(id=><button type="button" key={id} aria-pressed={appId===id} onClick={()=>setClient(id)}>{apps[id].name}</button>)}</div><p className="guide-app-hint">{device==='tv' ? `Найдите ${app.name} в Google Play на телевизоре.` : appId==='happ' && ['ios','mac'].includes(device) ? 'На сайте Happ выберите своё устройство и регион App Store.' : 'Установите приложение и вернитесь сюда.'}</p><a className="secondary-button" href={app.downloads[device]} target="_blank" rel="noreferrer">Скачать {app.name} ↗</a>{appId==='happ' && device==='android' && <a className="text-button" href="https://www.happ.su/main/ru" target="_blank" rel="noreferrer">Нет в Google Play? Скачать APK у разработчика ↗</a>}<button className="primary-button" onClick={() => setStep(1)}>Установлено, дальше →</button></>}
          {step === 1 && <><h3>Добавьте подписку в {app.name}</h3><p>{device === 'tv' ? 'Скопируйте ссылку. На телевизоре откройте добавление подписки и следуйте подсказкам для QR-кода или введите ссылку вручную.' : `Скопируйте свою ссылку. В ${app.name}: ${app.import}`}</p>{url ? <><button className="primary-button" onClick={copy}>{copied ? '✓ Скопировано · ещё раз' : 'Скопировать мою ссылку'}</button>{copyFailed && <label className="guide-manual-copy">Выделите и скопируйте ссылку вручную<textarea readOnly rows={3} value={url} onFocus={event=>event.target.select()}/></label>}{appId === 'v2raytun' && device !== 'tv' && <a className="secondary-button" href={`v2raytun://import/${url}`}>Добавить сразу в v2RayTun ↗</a>}</> : <><p className="guide-note">Сначала активируйте доступ в кабинете — здесь появится ваша ссылка.</p><button className="primary-button" onClick={onAccess}>Перейти к доступу</button></>}<button className="text-button" onClick={() => setStep(2)}>Подписка добавлена →</button></>}
          {step === 2 && <><h3>Включите VPN</h3><p>{appId==='v2rayn' ? 'Выберите «Умный выбор» активным сервером и включите режим TUN, чтобы VPN работал для всего компьютера. При запросе разрешите запуск с правами администратора.' : 'Выберите «Умный выбор» и нажмите кнопку подключения. Разрешите добавление VPN, если устройство попросит.'}</p><div className="guide-success"><span aria-hidden="true">✓</span><div><strong>Готово — можно пользоваться</strong><p>Страну можно сменить прямо в приложении.</p></div></div><details className="guide-trouble"><summary>Не подключается?</summary><ol><li>Проверьте срок доступа в кабинете.</li><li>Обновите подписку в приложении.</li><li>Выберите другую страну и подключитесь снова.</li>{appId==='v2rayn'&&<li>Если появилась ошибка REALITY, обновите ядро Xray в v2rayN и синхронизируйте дату и время компьютера.</li>}</ol><a href="/support">Служба поддержки →</a></details></>}
        </div>
      </div>
      <div className="guide-footer"><span>Шаг {step+1} из 3 · {current.label} · {app.name}</span>{step > 0 && <button className="text-button" onClick={() => setStep(step-1)}>← Назад</button>}</div>
    </div>
  </section>
}
