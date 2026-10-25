import { createPortal } from 'react-dom'
import { useEffect, useRef, useState } from 'react'
import './SubscriptionProgressOverlay.css'
export default function SubscriptionProgressOverlay({ run, onExited }) {
  const card = useRef(null)
  const [visibleStep, setVisibleStep] = useState(0)
  const [closing, setClosing] = useState(false)
  const finished = run?.status === 'complete' || run?.status === 'error'
  const done = run?.status === 'complete'
  useEffect(() => {
    if (!run?.id) return
    const previous = document.activeElement
    const overflow = document.body.style.overflow
    const root=document.getElementById('root')
    if(root)root.inert=true
    document.body.style.overflow = 'hidden'
    card.current?.focus()
    return () => {document.body.style.overflow = overflow;if(root)root.inert=false; previous?.focus?.()}
  }, [run?.id])
  useEffect(() => {
    if (!run?.id) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const timers = [setTimeout(() => setVisibleStep(1), reduced ? 0 : 900), setTimeout(() => setVisibleStep(2), reduced ? 0 : 1800)]
    return () => timers.forEach(clearTimeout)
  }, [run?.id])
  if (!run?.id) return null
  const close = () => {setClosing(true);setTimeout(() => {onExited?.(run.id);setClosing(false);setVisibleStep(0)}, 350)}
  const handleKeys = event => {
    if (event.key === 'Escape' && finished) close()
    if (event.key === 'Tab') {const items=card.current.querySelectorAll('button,a[href]');if(!items.length){event.preventDefault();return}const first=items[0],last=items[items.length-1];if(event.shiftKey && (document.activeElement === first || document.activeElement === card.current)){event.preventDefault();last.focus()}else if(!event.shiftKey && document.activeElement === last){event.preventDefault();first.focus()}}
  }
  const error = run.status === 'error'
  const ready = done && visibleStep >= 2
  return createPortal(<div className={`subscription-progress-overlay is-visible ${closing ? 'is-exiting' : ''}`}>
    <div className="subscription-progress-counter" role="dialog" aria-modal="true" aria-labelledby="progress-title" tabIndex={-1} ref={card} onKeyDown={handleKeys}>
      <div className={`progress-orb ${ready ? 'is-ready' : ''} ${error ? 'is-error' : ''}`} aria-hidden="true">{error ? '!' : ready ? '✓' : <i />}</div>
      <span className="eyebrow">KINGLINE VPN</span>
      <h2 id="progress-title">{error ? 'Нужно немного внимания' : ready ? 'Подписка готова' : run.stage === 'retrying' ? 'Восстанавливаем связь' : run.kind === 'renew' ? 'Продлеваем доступ' : 'Готовим доступ'}</h2>
      <div aria-live="polite" className="progress-description"><p>{error ? run.error || 'Не удалось завершить действие. Проверьте доступ в кабинете перед повтором.' : ready ? run.kind === 'renew' ? 'Обновите подписку в приложении. Ваша ссылка остаётся прежней.' : 'Добавьте ссылку из кабинета в приложение. Три шага подключения — в инструкции.' : run.stage === 'retrying' ? 'Продолжим автоматически. Повторно платить не нужно.' : done ? 'Готово. Показываем результат вашей операции.' : 'Проверяем условия и сохраняем ваш доступ.'}</p></div>
      {!error && <ol className="provisioning-steps">{['Условия проверены','Доступ сохранён','Можно подключаться'].map((label,i) => {const completed = done ? visibleStep >= i : i === 0 && ['payment_confirmed','provisioning','retrying','ready'].includes(run.stage);return <li key={label} className={completed ? 'is-done' : 'is-waiting'}><span>{completed ? '✓' : i+1}</span>{label}</li>})}</ol>}
      {ready && run.expiresAt && <p className="progress-receipt">Доступ до <strong>{new Date(run.expiresAt).toLocaleDateString('ru-RU')}</strong></p>}
      {(ready || error) && <button className="primary-button" onClick={close}>Понятно</button>}
      {!finished && <small>Можно закрыть окно — операция продолжится.</small>}
      {!finished && <button className="text-button" onClick={close}>Вернуться в кабинет</button>}
    </div>
  </div>, document.body)
}
