import './transport-picker.css'

export default function TransportPicker({ subscription, onChange, disabled }) {
  const mode = subscription?.transport || 'raw'
  return (
    <section className="liquid-card slim-card transport-card">
      <fieldset className="transport-picker" disabled={disabled}>
        <legend>Способ подключения</legend>
        <p className="transport-intro">Выберите вариант для своего устройства.</p>
        <div className="transport-options">
          <label className={`transport-option ${mode === 'raw' ? 'is-selected' : ''}`}>
            <input type="radio" name="vpn-transport" value="raw" checked={mode === 'raw'} onChange={() => onChange('raw')} />
            <span><strong>VLESS · TCP/RAW</strong><small>Рекомендуем для iPhone и iPad</small></span>
            <span className="transport-check" aria-hidden="true">{mode === 'raw' ? '✓' : ''}</span>
          </label>
          <label className={`transport-option ${mode === 'xhttp' ? 'is-selected' : ''} ${!subscription?.xhttpAvailable ? 'is-unavailable' : ''}`}>
            <input type="radio" name="vpn-transport" value="xhttp" checked={mode === 'xhttp'} disabled={!subscription?.xhttpAvailable} onChange={() => onChange('xhttp')} />
            <span><strong>VLESS · XHTTP</strong><small>{subscription?.xhttpAvailable ? 'Рекомендуем для Android · HTTPS' : 'Скоро появится'}</small></span>
            <span className="transport-check" aria-hidden="true">{mode === 'xhttp' ? '✓' : ''}</span>
          </label>
        </div>
        <p className="transport-note">Оба варианта зашифрованы. При обрывах попробуйте TCP/RAW.</p>
        <p className="transport-refresh">Режим общий для всех ваших устройств. После смены обновите подписку в приложении. Ссылка та же.</p>
      </fieldset>
    </section>
  )
}
