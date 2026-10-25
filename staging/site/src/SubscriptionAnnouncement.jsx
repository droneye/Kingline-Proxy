import { useEffect, useState } from 'react'

const endpoint = '/api/admin/messages/announcement'

export default function SubscriptionAnnouncement({ getJson, postJson }) {
  const [saved, setSaved] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let active = true
    getJson(endpoint).then((data) => {
      if (!active) return
      setSaved(data.announcement)
      setText(data.announcement.text)
      setError('')
    }).catch(() => {
      if (active) setError('Не удалось загрузить объявление. Попробуйте ещё раз.')
    })
    return () => { active = false }
  }, [getJson, reload])

  const save = async (value) => {
    if (!saved || busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const data = await postJson(endpoint, { text: value, revision: saved.revision })
      setSaved(data.announcement)
      setText(data.announcement.text)
      setNotice(data.announcement.text
        ? 'Сохранено. Текст появится после обновления подписки в приложении.'
        : 'Объявление убрано. Изменение придёт при обновлении подписки.')
    } catch (failure) {
      setError(failure.code === 'announcement_changed'
        ? 'Другой администратор изменил текст. Загрузите актуальную версию перед сохранением.'
        : failure.message || 'Не удалось сохранить. Попробуйте ещё раз.')
    } finally {
      setBusy(false)
    }
  }

  return <section className="admin-card liquid-card subscription-announcement">
    <div className="panel-heading compact-heading">
      <span className="eyebrow">В VPN-приложении</span>
      <h2>Текст над профилями</h2>
      <p>Общее объявление для пользователей v2rayTun.</p>
    </div>
    {error && <div className="infra-notice error" role="alert">
      <p>{error}</p>
      <button className="secondary-button fit-button" disabled={busy} onClick={() => setReload((n) => n + 1)} type="button">Загрузить заново</button>
    </div>}
    {!saved ? <p className="announcement-hint" role="status">{error ? 'Сохранение недоступно до загрузки текущего текста.' : 'Загружаем объявление…'}</p> : <form onSubmit={(event) => { event.preventDefault(); save(text) }}>
      <label className="field-label">
        Объявление
        <textarea className="glass-input" disabled={busy} maxLength={saved.maxLength} onChange={(event) => { setText(event.target.value); setNotice('') }}
          placeholder="Например: Добавили новую локацию. Обновите подписку, чтобы она появилась."
          rows={4} value={text} />
      </label>
      <div className="announcement-meta"><span>Видно после обновления подписки</span><span>{Array.from(text).length} / {saved.maxLength}</span></div>
      <div className="announcement-preview" aria-label="Предпросмотр объявления">
        <span>Предпросмотр · v2rayTun</span>
        <strong>Kingline</strong>
        {text.trim() && <p>{text.trim()}</p>}
        <div>⚡ Умный выбор</div>
        <small>Ваши локации</small>
      </div>
      <div className="announcement-actions">
        <button className="primary-button fit-button" disabled={busy || text.trim() === saved.text} type="submit">{busy ? 'Сохраняем…' : 'Сохранить в приложении'}</button>
        {saved.text && <button className="secondary-button fit-button" disabled={busy} onClick={() => save('')} type="button">Убрать объявление</button>}
      </div>
      <p className="announcement-hint">Одна надпись у всех. В других VPN-приложениях показ зависит от поддержки объявлений.</p>
    </form>}
    {notice && <p className="infra-notice success" role="status">{notice}</p>}
  </section>
}
