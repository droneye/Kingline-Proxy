import TransportPicker from './TransportPicker'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ConnectionGuide from './ConnectionGuide.jsx'
import { AdminNodes, TechnicalLogs } from './AdminInfrastructure.jsx'
import AuthRunner from './AuthRunner.jsx'
import AdminDocumentation from './AdminDocumentation.jsx'
import SubscriptionProgressOverlay from './SubscriptionProgressOverlay.jsx'
import { effectiveSubscription } from './subscription-access.js'
import SubscriptionAnnouncement from './SubscriptionAnnouncement.jsx'

const accountModes = {
  login: {
    title: 'Вход',
    subtitle: 'Введите email и пароль. Без подтверждения почты вход закрыт.',
    button: 'Войти',
    footer: 'Нет аккаунта?',
    action: 'Создать',
  },
  register: {
    title: 'Регистрация',
    subtitle: 'Создайте аккаунт и подтвердите email перед первым входом.',
    button: 'Зарегистрироваться',
    footer: 'Уже есть аккаунт?',
    action: 'Войти',
  },
}

const authErrors = {
  invalid_announcement: 'Введите до 500 символов без служебных знаков.',
  announcement_changed: 'Объявление уже изменено. Загрузите актуальную версию.',
  bonus_claimed: 'Бонус за Telegram уже был выдан.',
  email_exists: 'Этот email уже зарегистрирован.',
  insufficient_balance: 'Недостаточно средств на балансе.',
  invalid_amount: 'Некорректная сумма пополнения.',
  invalid_credentials: 'Неверный email или пароль.',
  invalid_content_type: 'Некорректный тип запроса. Обновите страницу.',
  invalid_email: 'Введите корректный email.',
  invalid_name: 'Введите имя от 2 символов.',
  invalid_promo: 'Промокод недействителен или уже использован.',
  account_locked: 'Слишком много неверных паролей. Попробуйте позже или восстановите пароль.',
  admin_2fa_required: 'Подтвердите вход в админку кодом из Telegram.',
  admin_2fa_invalid: 'Неверный код из Telegram.',
  admin_2fa_expired: 'Код истек. Отправьте новый.',
  admin_2fa_too_many: 'Слишком много попыток ввода кода. Отправьте новый.',
  admin_2fa_unavailable: 'Для этого администратора не найден Telegram. Привяжите Telegram или добавьте админа по tg id.',
  email_not_verified: 'Почта не подтверждена. Мы отправили письмо повторно.',
  email_verify_expired: 'Ссылка подтверждения истекла. Войдите снова, чтобы получить новую.',
  email_verify_invalid: 'Ссылка подтверждения недействительна.',
  email_verify_used: 'Эта ссылка подтверждения уже использована.',
  email_link_expired: 'Код привязки почты истек. Отправьте письмо заново.',
  email_link_invalid: 'Неверный код привязки почты.',
  email_link_too_many: 'Слишком много попыток. Отправьте письмо заново.',
  email_link_used: 'Этот код уже был использован.',
  magic_link_expired: 'Ссылка входа истекла. Запросите новую.',
  magic_link_invalid: 'Ссылка входа недействительна. Запросите новую.',
  magic_link_used: 'Эта ссылка уже была использована. Запросите новую.',
  magic_link_disabled: 'Вход по magic link отключен. Используйте email и пароль.',
  mail_not_configured: 'Отправка почты пока не настроена на сервере.',
  email_send_failed: 'Не удалось отправить письмо. Попробуйте позже.',
  cannot_remove_self: 'Нельзя удалить собственный доступ администратора.',
  cannot_remove_owner: 'Главного администратора нельзя удалить.',
  cannot_edit_owner: 'Права главного администратора нельзя ограничить.',
  email_unavailable: 'У пользователя нет привязанной почты.',
  forbidden: 'Нет доступа к админской панели.',
  invalid_admin: 'Укажите email или Telegram ID администратора.',
  invalid_channel: 'Некорректный канал отправки.',
  invalid_user: 'Пользователь не найден.',
  message_required: 'Введите текст сообщения.',
  password_reset_expired: 'Ссылка восстановления истекла. Запросите новую.',
  password_reset_invalid: 'Ссылка восстановления недействительна.',
  password_reset_used: 'Эта ссылка восстановления уже использована.',
  payment_check_failed: 'Не удалось проверить платеж.',
  payment_not_found: 'Платеж не найден для этого аккаунта.',
  sandbox_payments_restricted: 'Пополнение пока недоступно. Обратитесь в поддержку.',
  payment_unavailable: 'Платежный модуль временно недоступен.',
  password_auth_disabled: 'Вход по паролю отключен. Используйте ссылку на почту.',
  permission_denied: 'Для этого действия нет доступа.',
  premium_active: 'Premium уже активен. Для продления используйте Premium.',
  premium_required: 'Выбор локации доступен только на Premium.',
  promo_exists: 'Такой промокод уже существует.',
  registration_verify_expired: 'Код истек. Зарегистрируйтесь еще раз.',
  registration_verify_invalid: 'Неверный код подтверждения.',
  registration_verify_too_many: 'Слишком много попыток. Зарегистрируйтесь еще раз.',
  registration_verify_used: 'Этот код уже был использован.',
  referral_already_used: 'Вы уже использовали реферальный код.',
  referral_empty: 'Введите реферальный код.',
  referral_mutual: 'Нельзя использовать код пользователя, которого вы уже приглашали.',
  referral_not_found: 'Такой реферальный код не найден.',
  referral_own_code: 'Нельзя использовать собственный реферальный код.',
  subscription_required: 'Сначала получите ссылку подписки.',
  too_many_attempts: 'Слишком много попыток. Попробуйте позже.',
  telegram_unavailable: 'У пользователя нет доступного Telegram ID.',
  telegram_auth_failed: 'Не удалось подтвердить Telegram Mini App. Откройте сайт из бота еще раз.',
  owner_required: 'Это действие доступно только главному администратору.',
  trial_used: 'Пробный доступ уже использован.',
  unauthorized: 'Войдите в аккаунт заново.',
  weak_password: 'Пароль должен быть не короче 8 символов.',
  csrf_failed: 'Запрос отклонен защитой сайта. Обновите страницу и повторите.',
  operation_failed: 'Не удалось выполнить операцию. Попробуйте позже.',
  job_not_found: 'Задача не найдена. Обновите страницу и попробуйте снова.',
}

const tabItems = [
  { id: 'access', label: 'Доступ' },
  { id: 'payment', label: 'Оплата' },
  { id: 'guide', label: 'Инструкция' },
]

const topupFallback = [300, 600, 900]

const appLinks = {
  android: 'https://play.google.com/store/apps/details?id=com.v2raytun.android',
  ios: 'https://apps.apple.com/us/app/v2raytun/id6476628951',
  telegramBot: 'https://t.me/stopRKNbot',
  happIos: 'https://apps.apple.com/us/app/happ-proxy-utility/id6504287215',
  happDesktop: 'https://github.com/Happ-proxy/happ-desktop/releases',
  v2rayN: 'https://github.com/2dust/v2rayN/releases',
  androidApk: '/downloads/v2raytun-android-arm64.xapk',
  androidTvApk: '/downloads/v2raytun-android-armeabi-v7a.xapk',
  happWindows: '/downloads/happ-windows-x64.exe',
}

const telegramStartLink = (code) =>
  `${appLinks.telegramBot}?start=link_${encodeURIComponent(code || '')}`

const wait = (ms) =>
  new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })

const isTransientNetworkError = (error) => {
  const message = String(error?.message || '').toLowerCase()
  return (
    error instanceof TypeError ||
    message.includes('load failed') ||
    message.includes('fetch failed') ||
    message.includes('failed to fetch') ||
    message.includes('networkerror') ||
    message.includes('network error') ||
    message.includes('network request failed')
  )
}

const postJson = async (path, payload = {}) => {
  const response = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Kingline-Request': 'fetch',
      ...deviceHeaders(),
    },
    credentials: 'include',
    body: JSON.stringify(payload),
  })
  const data = await response.json()

  if (!response.ok || !data.ok) {
    const error = new Error(authErrors[data.error] || 'Не удалось выполнить запрос.')
    error.code = typeof data.error === 'string' ? data.error : 'invalid_request'
    throw error
  }

  return data
}

const pendingGetRequests = new Map()

const getJson = (path) => {
  if (pendingGetRequests.has(path)) {
    return pendingGetRequests.get(path)
  }
  const request = (async () => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 25000)
    try {
      const response = await fetch(path, { credentials: 'include', signal: controller.signal })
      const data = await response.json()
      if (!response.ok || !data.ok) {
        const error = new Error(authErrors[data.error] || 'Не удалось выполнить запрос.')
        error.code = typeof data.error === 'string' ? data.error : 'invalid_request'
        throw error
      }
      return data
    } catch (error) {
      if (error.name === 'AbortError') {
        throw new Error('Сервер долго не отвечает. Попробуйте ещё раз.', { cause: error })
      }
      throw error
    } finally {
      window.clearTimeout(timer)
      pendingGetRequests.delete(path)
    }
  })()
  pendingGetRequests.set(path, request)
  return request
}

const startPolling = (refresh, interval, immediate = true) => {
  let stopped = false
  let timer
  const tick = async () => {
    try {
      if (!document.hidden) {
        await refresh()
      }
    } catch {
      // Refresh handlers show their own errors; keep polling after a lost connection.
    } finally {
      if (!stopped) {
        timer = window.setTimeout(tick, interval)
      }
    }
  }
  if (immediate) {
    tick()
  } else {
    timer = window.setTimeout(tick, interval)
  }
  return () => {
    stopped = true
    window.clearTimeout(timer)
  }
}

const fileToAttachment = (file) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () =>
      resolve({
        name: file.name,
        type: file.type || 'application/octet-stream',
        size: file.size,
        data: reader.result,
      })
    reader.onerror = () => reject(new Error('Не удалось прочитать файл.'))
    reader.readAsDataURL(file)
  })

const prepareAttachments = async (files) => {
  const list = Array.from(files || [])
  if (list.length > 4) throw new Error('Можно прикрепить до 4 файлов.')
  if (list.some(file => !/\.(png|jpe?g|webp|pdf)$/i.test(file.name))) throw new Error('Прикрепите скриншот PNG, JPG, WEBP или документ PDF.')
  const tooLarge = list.reduce((total, file) => total + file.size, 0) > 8 * 1024 * 1024
  if (tooLarge) {
    throw new Error('Вложения слишком большие. До 4 файлов и 8 МБ суммарно.')
  }
  return Promise.all(list.map(fileToAttachment))
}

const isImageAttachment = (file) => {
  const type = String(file?.type || '').toLowerCase()
  const name = String(file?.name || file?.url || '').toLowerCase()
  return type.startsWith('image/') || /\.(png|jpe?g|webp|gif)$/i.test(name)
}

const formatDate = (value) => {
  if (!value) {
    return 'Не активна'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
    timeZone: 'Europe/Moscow',
    year: 'numeric',
  })
}

const pluralDays = (value) => {
  const number = Math.abs(Number(value || 0))
  const mod10 = number % 10
  const mod100 = number % 100
  if (mod10 === 1 && mod100 !== 11) {
    return 'день'
  }
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
    return 'дня'
  }
  return 'дней'
}

const getExpiryStatus = (value) => {
  if (!value) {
    return {
      active: false,
      className: 'is-empty',
      label: 'не активна',
      title: 'Срок не задан',
    }
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return {
      active: false,
      className: 'is-empty',
      label: 'не активна',
      title: String(value),
    }
  }
  const diffMs = date.getTime() - Date.now()
  const days = Math.max(0, Math.ceil(Math.abs(diffMs) / 86_400_000))
  if (diffMs >= 0) {
    return {
      active: true,
      className: 'is-active',
      label: `через ${days} ${pluralDays(days)}`,
      title: formatDate(value),
    }
  }
  return {
    active: false,
    className: 'is-expired',
    label: `${days} ${pluralDays(days)} назад`,
    title: formatDate(value),
  }
}

const getUserSubscriptionStatus = (user) => {
  if (user?.blocked) {
    return {
      className: 'is-blocked',
      label: 'Заблокирован',
    }
  }
  const expiry = getExpiryStatus(user?.expiresAt)
  if (expiry.active) {
    return {
      className: 'is-active',
      label: 'Подписка активна',
    }
  }
  if (user?.expiresAt) {
    return {
      className: 'is-expired',
      label: 'Истекла',
    }
  }
  return {
    className: 'is-empty',
    label: 'Нет подписки',
  }
}

const formatBytes = (value) => {
  const bytes = Number(value || 0)
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 Б'
  }
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
  let size = bytes
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  const precision = unit >= 3 ? 1 : 0
  return `${size.toFixed(precision)} ${units[unit]}`
}

const formatRub = (value) =>
  `${new Intl.NumberFormat('ru-RU').format(Math.max(0, Math.round(Number(value || 0))))} ₽`

const formatNumber = (value) =>
  new Intl.NumberFormat('ru-RU').format(Math.max(0, Math.round(Number(value || 0))))

const formatDuration = (seconds) => {
  const total = Math.max(0, Number(seconds || 0))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (hours >= 24) {
    const days = Math.floor(hours / 24)
    const restHours = hours % 24
    return `${days} д ${restHours} ч`
  }
  if (hours > 0) {
    return `${hours} ч ${minutes} мин`
  }
  return `${minutes} мин`
}

const normalizeAdminUsername = (value) =>
  String(value || '').split('#')[0].replace('@', '').replaceAll(' ', '').trim().toLowerCase()

const getTariffLabel = (subscription) => {
  if (!subscription?.active || subscription?.blocked) {
    return 'Ваш кабинет'
  }
  return subscription.tariff === 'premium' ? 'Premium' : 'Пробный доступ'
}

const getInitialNotice = () => {
  const authError = new URLSearchParams(window.location.search).get('auth_error')
  return authError
    ? authErrors[authError] || 'Не удалось выполнить вход по ссылке.'
    : ''
}

const getInitialResetToken = () =>
  new URLSearchParams(window.location.search).get('reset_token') || ''

const getTelegramInitData = () =>
  window.Telegram?.WebApp?.initData || ''

function deviceHeaders() {
  const app = window.Telegram?.WebApp
  const miniapp = Boolean(app?.initData)
  const hint = miniapp && ['ios', 'android', 'macos'].includes(app.platform) ? app.platform
    : navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1 ? 'ios' : ''
  return { ...(hint ? { 'X-Kingline-Platform': hint } : {}), 'X-Kingline-Client': miniapp ? 'miniapp' : 'site' }
}

function BrandLogo() {
  const [src, setSrc] = useState('/brand/custom/logo.png')

  return (
    <img
      alt=""
      onError={() => setSrc('/brand/kingline-proxy-logo.svg')}
      src={src}
    />
  )
}

function App() {
  const [mode, setMode] = useState('login')
  const [authPanel, setAuthPanel] = useState(() =>
    getInitialResetToken() ? 'reset-confirm' : 'auth',
  )
  const [activeTab, setActiveTab] = useState(()=>new URLSearchParams(location.search).get('tab')==='guide' ? 'guide' : 'access')
  const [notice, setNotice] = useState(getInitialNotice)
  const [account, setAccount] = useState(null)
  const [subscriptionData, setSubscription] = useState(null)
  const [accessNow, setAccessNow] = useState(Date.now)
  const subscription = useMemo(() => effectiveSubscription(subscriptionData, accessNow), [subscriptionData, accessNow])
  const [pendingRegistration, setPendingRegistration] = useState(null)
  const [emailLinkPending, setEmailLinkPending] = useState(null)
  const [resetToken, setResetToken] = useState(getInitialResetToken)
  const [topupAmounts, setTopupAmounts] = useState(topupFallback)
  const [promoCode, setPromoCode] = useState('')
  const [referralCode, setReferralCode] = useState('')
  const [linkCode, setLinkCode] = useState(null)
  const [lastPaymentId, setLastPaymentId] = useState(() =>
    localStorage.getItem('kingline_payment_id') || '',
  )
  const [subscriptionProgressRun, setSubscriptionProgressRun] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const activeMode = accountModes[mode]
  const isRegister = mode === 'register'
  const hasSubscription = Boolean(subscription?.subscriptionUrl)
  const currentPath = window.location.pathname.replace(/\/+$/, '') || '/'
  const isAdminRoute = currentPath === '/admin'
  const isSupportRoute = currentPath === '/support'
  const isAuthRoute = currentPath === '/auth'

  useEffect(() => {
    const refresh = () => setAccessNow(Date.now())
    const expiry = Date.parse(subscriptionData?.expiresAt || '')
    const timer = subscriptionData?.active && Number.isFinite(expiry) && expiry > accessNow
      ? window.setTimeout(refresh, Math.min(2147483000, Math.max(0, expiry - Date.now()) + 30)) : null
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      if (timer !== null) window.clearTimeout(timer)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [subscriptionData?.active, subscriptionData?.expiresAt, accessNow])

  const statusTone = useMemo(() => {
    if (subscription?.blocked) return 'Приостановлен'
    if (subscription?.active) {
      return 'Активно'
    }
    if (subscription?.canUseTrial) {
      return 'Первые 24 часа'
    }
    return 'Доступ неактивен'
  }, [subscription])

  const applyPayload = (data) => {
    setAccount(data.account || null)
    setSubscription(data.subscription || null)
    if (data.topupAmounts) {
      setTopupAmounts(data.topupAmounts)
    }
    if (data.linkCode) {
      setLinkCode(data.linkCode)
    }
  }

  useEffect(() => {
    let alive = true
    const params = new URLSearchParams(window.location.search)
    const authError = params.get('auth_error')
    const incomingResetToken = params.get('reset_token')

    if (authError || incomingResetToken) {
      params.delete('auth_error')
      params.delete('reset_token')
      const cleanQuery = params.toString()
      window.history.replaceState(
        {},
        '',
        `${window.location.pathname}${cleanQuery ? `?${cleanQuery}` : ''}`,
      )
    }

    fetch('/api/auth/me', { credentials: 'include', headers: deviceHeaders() })
      .then((response) => response.json())
      .then(async (data) => {
        if (alive) {
          if (incomingResetToken) {
            setAccount(null)
            setSubscription(null)
            setLinkCode(null)
          } else {
            let nextData = data
            const initData = getTelegramInitData()
            if (!data.account && initData) {
              try {
                window.Telegram?.WebApp?.ready?.()
                window.Telegram?.WebApp?.expand?.()
                nextData = await postJson('/api/auth/telegram-webapp', { initData })
              } catch (error) {
                setNotice(error.message)
              }
            }
            applyPayload(nextData)
          }
        }
      })
      .catch(() => {
        if (alive) {
          setNotice('Не удалось связаться с сайтом. Обновите страницу через несколько секунд.')
        }
      })
      .finally(() => {
        if (alive) {
          setIsLoading(false)
        }
      })

    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!notice) {
      return undefined
    }
    const timer = window.setTimeout(() => setNotice(''), 5200)
    return () => window.clearTimeout(timer)
  }, [notice])

  useEffect(() => {
    if (!subscription?.pendingJobId) return
    let alive = true
    const timer = setInterval(async () => {
      try {
        const response = await fetch('/api/auth/me', { credentials: 'include', headers: deviceHeaders() })
        const data = await response.json()
        if (alive && data.ok && data.account) {
          setSubscription(data.subscription)
          setAccount(data.account)
        }
      } catch { /* Keep the saved operation visible while the network recovers. */ }
    }, 3000)
    return () => { alive = false; clearInterval(timer) }
  }, [subscription?.pendingJobId])

  const switchMode = (nextMode) => {
    setMode(nextMode)
    setAuthPanel('auth')
    setPendingRegistration(null)
    setEmailLinkPending(null)
    setResetToken('')
    setNotice('')
  }

  const runAction = async (action, successText) => {
    setNotice('')
    setIsSubmitting(true)
    try {
      const data = await action()
      applyPayload(data)
      setNotice(successText)
      return data
    } catch (error) {
      setNotice(error.message)
      return null
    } finally {
      setIsSubmitting(false)
    }
  }

  const triggerHaptic = (stage = 'tap') => {
    if (!('vibrate' in navigator)) {
      return
    }
    const patterns = {
      start: [10, 38, 18],
      success: [12, 34, 22, 48, 16],
      tap: [8],
    }
    navigator.vibrate(patterns[stage] || patterns.tap)
  }

  const startSubscriptionProgress = (kind) => {
    const id = `subscription-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2)}`
    setSubscriptionProgressRun({ id, kind, status: 'running' })
    return id
  }

  const finishSubscriptionProgress = (id, status = 'complete', result = {}) => {
    setSubscriptionProgressRun((current) =>
      current?.id === id ? { ...current, status, expiresAt: result.subscription?.expiresAt, error: result.error } : current,
    )
  }

  const dismissSubscriptionProgress = useCallback((id) => {
    setSubscriptionProgressRun((current) => (current?.id === id ? null : current))
  }, [])

  const pollAccountJob = async (jobId, successText) => {
    await wait(400)
    for (let attempt = 0; attempt < 90; attempt += 1) {
      let data
      try {
        data = await postJson('/api/account/job-status', { jobId })
        applyPayload(data)
      } catch (error) {
        if (isTransientNetworkError(error) && attempt < 89) {
          await wait(2500)
          continue
        }
        throw new Error(
          isTransientNetworkError(error)
            ? 'Связь с сайтом кратко прервалась. Обновите страницу через несколько секунд.'
            : error.message,
          { cause: error },
        )
      }

      setSubscriptionProgressRun(current => current ? { ...current, stage: data.job?.stage || 'checking' } : current)
      if (data.job?.status === 'succeeded') {
        setNotice(successText)
        return data
      }

      if (data.job?.status === 'failed') {
        throw new Error(authErrors[data.job.error] || 'Не удалось выполнить операцию.')
      }

      await wait(2000)
    }
    throw new Error('Операция еще выполняется. Обновите страницу через несколько секунд.')
  }

  const startSubscriptionJob = async (path, pendingText, successText) => {
    const progressId = startSubscriptionProgress(subscription?.active ? 'renew' : 'activate')
    triggerHaptic('start')
    setNotice(pendingText)
    setIsSubmitting(true)
    try {
      const data = await postJson(path)
      applyPayload(data)
      if (!data.job?.id) {
        setNotice(successText)
        finishSubscriptionProgress(progressId, 'complete', data)
        triggerHaptic('success')
        return data
      }
      const result = await pollAccountJob(data.job.id, successText)
      finishSubscriptionProgress(progressId, 'complete', result)
      triggerHaptic('success')
      return result
    } catch (error) {
      finishSubscriptionProgress(progressId, 'error', {error: error.message})
      setNotice(error.message)
      return null
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const payload = {
      email: form.get('email'),
      password: form.get('password'),
    }

    if (isRegister) {
      payload.name = form.get('name')
    }

    const data = await runAction(
      () =>
        postJson(isRegister ? '/api/auth/register' : '/api/auth/login', payload),
      isRegister
        ? 'Мы отправили код и ссылку на почту.'
        : 'Вход выполнен.',
    )

    if (data) {
      if (isRegister && data.registrationPending) {
        setPendingRegistration({
          email: data.email || payload.email,
          expiresAt: data.expiresAt,
        })
        setNotice('Мы отправили код и ссылку на почту. Введите код ниже или откройте ссылку из письма.')
        return
      }
      event.currentTarget.reset()
    }
  }

  const handleConfirmRegistration = async (event) => {
    event.preventDefault()
    if (!pendingRegistration?.email) {
      setNotice('Сначала заполните регистрацию.')
      return
    }
    const form = new FormData(event.currentTarget)
    const code = String(form.get('code') || '').trim()
    const data = await runAction(
      () =>
        postJson('/api/auth/confirm-registration', {
          email: pendingRegistration.email,
          code,
        }),
      'Почта подтверждена. Вход выполнен.',
    )
    if (data?.account) {
      setPendingRegistration(null)
      event.currentTarget.reset()
    }
  }

  const handleEmailLinkStart = async (event) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setNotice('')
    setIsSubmitting(true)
    try {
      const data = await postJson('/api/account/email-link/start', {
        name: form.get('name'),
        email: form.get('email'),
        password: form.get('password'),
      })
      setEmailLinkPending({
        email: data.email || form.get('email'),
        expiresAt: data.expiresAt,
      })
      setNotice('Мы отправили код и ссылку на почту.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleEmailLinkConfirm = async (event) => {
    event.preventDefault()
    if (!emailLinkPending?.email) {
      setNotice('Сначала укажите почту.')
      return
    }
    const form = new FormData(event.currentTarget)
    const code = String(form.get('code') || '').trim()
    const data = await runAction(
      () =>
        postJson('/api/account/email-link/confirm', {
          email: emailLinkPending.email,
          code,
        }),
      'Почта привязана. Теперь можно входить и по email.',
    )
    if (data?.account) {
      setEmailLinkPending(null)
      window.history.replaceState({}, '', '/')
      event.currentTarget.reset()
    }
  }

  const handlePasswordResetRequest = async (event) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setNotice('')
    setIsSubmitting(true)
    try {
      await postJson('/api/auth/password-reset', {
        email: form.get('email'),
      })
      setAuthPanel('reset-sent')
      setNotice('Если почта привязана к аккаунту, мы отправили ссылку для смены пароля.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handlePasswordResetConfirm = async (event) => {
    event.preventDefault()
    if (!resetToken) {
      setNotice('Ссылка восстановления недействительна.')
      return
    }
    const form = new FormData(event.currentTarget)
    const data = await runAction(
      () =>
        postJson('/api/auth/password-reset-confirm', {
          token: resetToken,
          password: form.get('password'),
        }),
      'Пароль обновлен. Вход выполнен.',
    )
    if (data?.account) {
      setResetToken('')
      setAuthPanel('auth')
      setMode('login')
      event.currentTarget.reset()
    }
  }

  const handleLogout = async () => {
    const data = await runAction(
      () => postJson('/api/auth/logout'),
      'Вы вышли из аккаунта.',
    )
    if (data) {
      setAccount(null)
      setSubscription(null)
      setLinkCode(null)
      setEmailLinkPending(null)
      setActiveTab('access')
    }
  }

  const confirmLogout = () => {
    if (window.confirm('Выйти из аккаунта?')) {
      handleLogout()
    }
  }

  const handleTrial = () =>
    startSubscriptionJob(
      '/api/account/trial',
      'Готовим пробный доступ. Через несколько секунд ссылка появится в кабинете.',
      'Пробный доступ готов. Скопируйте ссылку подписки в разделе «Доступ».',
    )

  const handlePremium = () => {
    return startSubscriptionJob(
      '/api/account/premium',
      'Готовим Premium. Через несколько секунд обновим вашу подписку.',
      subscription?.active && subscription?.tariff === 'premium'
        ? 'Premium продлен на 30 дней. Обновите подписку в приложении.'
        : 'Premium активирован на 30 дней. Ссылка готова в разделе «Доступ».',
    )
  }

  const handlePromo = (event) => {
    event.preventDefault()
    if (!promoCode.trim()) {
      setNotice('Введите промокод.')
      return
    }
    runAction(
      () => postJson('/api/account/promo', { code: promoCode }),
      'Промокод применен. Баланс обновлен.',
    ).then((data) => {
      if (data) {
        setPromoCode('')
      }
    })
  }

  const handleReferralCode = (event) => {
    event.preventDefault()
    if (!referralCode.trim()) {
      setNotice('Введите реферальный код.')
      return
    }
    runAction(
      () => postJson('/api/account/referral-code', { code: referralCode }),
      'Реферальный код применен. Бонус начислится другу после активации Premium.',
    ).then((data) => {
      if (data) {
        setReferralCode('')
      }
    })
  }

  const handleTopup = async (amount) => {
    setNotice('')
    setIsSubmitting(true)
    try {
      const data = await postJson('/api/account/topup', { amount })
      localStorage.setItem('kingline_payment_id', data.paymentId)
      setLastPaymentId(data.paymentId)
      window.location.assign(data.confirmationUrl)
    } catch (error) {
      setNotice(error.message)
      setIsSubmitting(false)
    }
  }

  const handleCheckPayment = () =>
    runAction(
      () => postJson('/api/account/check-payment', { paymentId: lastPaymentId }),
      'Статус платежа проверен. Если оплата прошла, баланс обновлен.',
    ).then((data) => {
      if (data?.paymentStatus === 'succeeded') {
        localStorage.removeItem('kingline_payment_id')
        setLastPaymentId('')
      }
    })

  const handleLinkCode = () =>
    runAction(
      () => postJson('/api/account/link-code'),
      'Кнопка для Telegram готова.',
    )

  const handleVpnMode = (transport) =>
    runAction(
      () => postJson('/api/account/vpn-mode', { transport }),
      'Сохранено. Обновите подписку в VPN-приложении — ссылка та же.',
    )

  const handleEmailReminders = (enabled) =>
    runAction(
      () => postJson('/api/account/email-reminders', { enabled }),
      enabled
        ? 'Напоминания на почту включены.'
        : 'Напоминания на почту выключены.',
    )

  const handleEmailAnnouncements = (enabled) =>
    runAction(
      () => postJson('/api/account/email-announcements', { enabled }),
      enabled
        ? 'Важные объявления на почту включены.'
        : 'Важные объявления на почту выключены.',
    )

  const copyText = async (text, successText = 'Скопировано.') => {
    if (!text) {
      return
    }
    try {
      await navigator.clipboard.writeText(text)
      setNotice(successText)
      return true
    } catch {
      setNotice('Не удалось скопировать автоматически. Выделите ссылку вручную.')
      return false
    }
  }

  if (isLoading && !account) {
    return <main className="site-shell"><div className="loading-card" role="status">Открываем кабинет…</div></main>
  }

  return (
    <main className="site-shell">
      <div className="noise-layer" aria-hidden="true" />
      <SubscriptionProgressOverlay
        key={subscriptionProgressRun?.id || 'subscription-progress'}
        onExited={dismissSubscriptionProgress}
        run={subscriptionProgressRun}
      />
      <div
        className={`app-frame ${account ? 'is-authed' : 'is-guest'} ${
          isAdminRoute ? 'is-admin-route' : ''
        } ${isSupportRoute ? 'is-support-route' : ''}`}
      >
        {account && (
        <header className="topbar">
          <button
            className="brand-button"
            onClick={() => {
              if (account) {
                if (window.location.pathname !== '/') {window.location.assign('/');return}
                setActiveTab('access')
              }
            }}
            type="button"
          >
            <span className="brand-mark" aria-hidden="true">
              <BrandLogo />
            </span>
            <span>
              <strong>Kingline VPN</strong>
              <small>личный кабинет</small>
            </span>
          </button>
          {account && (
            <div className="topbar-controls">
              {isSupportRoute && <a className="secondary-button support-shortcut" href="/">← Кабинет</a>}
              <button
                className="ghost-button topbar-action"
                disabled={isSubmitting}
                onClick={confirmLogout}
                aria-label="Выйти"
                type="button"
              >
                ×
              </button>
            </div>
          )}
        </header>
        )}

        {isAdminRoute ? (
          account ? (
            <AdminPanel
              account={account}
              setNotice={setNotice}
            />
          ) : (
            <UnauthedView
              authPanel={authPanel}
              activeMode={activeMode}
              handleConfirmRegistration={handleConfirmRegistration}
              handlePasswordResetConfirm={handlePasswordResetConfirm}
              handlePasswordResetRequest={handlePasswordResetRequest}
              handleSubmit={handleSubmit}
              isLoading={isLoading}
              isRegister={isRegister}
              isSubmitting={isSubmitting}
              mode={mode}
              pendingRegistration={pendingRegistration}
              setAuthPanel={setAuthPanel}
              switchMode={switchMode}
            />
          )
        ) : isSupportRoute ? (
          account ? (
            <SupportPage />
          ) : (
            <UnauthedView
              authPanel={authPanel}
              activeMode={activeMode}
              handleConfirmRegistration={handleConfirmRegistration}
              handlePasswordResetConfirm={handlePasswordResetConfirm}
              handlePasswordResetRequest={handlePasswordResetRequest}
              handleSubmit={handleSubmit}
              isLoading={isLoading}
              isRegister={isRegister}
              isSubmitting={isSubmitting}
              mode={mode}
              pendingRegistration={pendingRegistration}
              setAuthPanel={setAuthPanel}
              switchMode={switchMode}
            />
          )
        ) : isAuthRoute && account?.needsEmailLink ? (
          <EmailLinkView
            account={account}
            emailLinkPending={emailLinkPending}
            handleEmailLinkConfirm={handleEmailLinkConfirm}
            handleEmailLinkStart={handleEmailLinkStart}
            isLoading={isLoading}
            isSubmitting={isSubmitting}
            setEmailLinkPending={setEmailLinkPending}
          />
        ) : !account ? (
          <UnauthedView
            authPanel={authPanel}
            activeMode={activeMode}
            handleConfirmRegistration={handleConfirmRegistration}
            handlePasswordResetConfirm={handlePasswordResetConfirm}
            handlePasswordResetRequest={handlePasswordResetRequest}
            handleSubmit={handleSubmit}
            isLoading={isLoading}
            isRegister={isRegister}
            isSubmitting={isSubmitting}
            mode={mode}
            pendingRegistration={pendingRegistration}
            setAuthPanel={setAuthPanel}
            switchMode={switchMode}
          />
        ) : (
          <AuthedView
            account={account}
            activeTab={activeTab}
            copyText={copyText}
            handleCheckPayment={handleCheckPayment}
            handleEmailAnnouncements={handleEmailAnnouncements}
            handleLinkCode={handleLinkCode}
            handleEmailReminders={handleEmailReminders}
            handleVpnMode={handleVpnMode}
            handlePremium={handlePremium}
            handlePromo={handlePromo}
            handleReferralCode={handleReferralCode}
            handleTopup={handleTopup}
            handleTrial={handleTrial}
            hasSubscription={hasSubscription}
            isSubmitting={isSubmitting}
            lastPaymentId={lastPaymentId}
            linkCode={linkCode}
            promoCode={promoCode}
            referralCode={referralCode}
            setActiveTab={setActiveTab}
            setPromoCode={setPromoCode}
            setReferralCode={setReferralCode}
            statusTone={statusTone}
            subscription={subscription}
            topupAmounts={topupAmounts}
          />
        )}

        {notice && (
          <p aria-live="polite" className="notice-toast">
            {notice}
          </p>
        )}
      </div>
    </main>
  )
}

function HeroIntro({ showRunner = false }) {
  return (
    <section className={`hero-panel liquid-card ${showRunner ? 'has-runner' : ''}`}>
      <span className="eyebrow">Kingline VPN</span>
      <h1>Kingline VPN для <span className="mobile-title-break">защищённого доступа.</span></h1>
      <p>Одна подписка для телефона и компьютера. Подключайтесь автоматически или выбирайте локацию сами.</p>
      <div className="hero-seo-grid" aria-label="Преимущества Kingline VPN">
        <article>
          <strong>Защищённое соединение</strong>
          <span>Шифрование между вами и VPN-сервером.</span>
        </article>
        <article>
          <strong>Умный выбор</strong>
          <span>Германия и Дания в одной подписке.</span>
        </article>
        <article>
          <strong>Высокая скорость</strong>
          <span>Локации для стабильного доступа к сайтам и медиа.</span>
        </article>
        <article>
          <strong>Простое подключение</strong>
          <span>Android, iPhone, Windows, Mac и Android TV.</span>
        </article>
      </div>
      {showRunner && <AuthRunner active />}
    </section>
  )
}

function UnauthedView({
  authPanel,
  activeMode,
  handleConfirmRegistration,
  handlePasswordResetConfirm,
  handlePasswordResetRequest,
  handleSubmit,
  isLoading,
  isRegister,
  isSubmitting,
  mode,
  pendingRegistration,
  setAuthPanel,
  switchMode,
}) {
  const showConfirmation = Boolean(pendingRegistration)
  const showAuth = authPanel === 'auth'
  const showResetRequest = authPanel === 'reset-request'
  const showResetConfirm = authPanel === 'reset-confirm'
  const showResetSent = authPanel === 'reset-sent'

  return (
    <div className="guest-layout">
      <HeroIntro showRunner={showAuth && mode === 'register'} />

      <section className={`auth-panel liquid-card ${showConfirmation ? 'is-confirming' : ''}`}>
        {showAuth && (
          <div className="mode-control" data-mode={mode}>
            <span className="mode-indicator" aria-hidden="true" />
            <button
              aria-pressed={mode === 'login'}
              className="mode-button"
              onClick={() => switchMode('login')}
              type="button"
            >
              Вход
            </button>
            <button
              aria-pressed={mode === 'register'}
              className="mode-button"
              onClick={() => switchMode('register')}
              type="button"
            >
              Регистрация
            </button>
          </div>
        )}

        {showResetRequest && (
          <form className="auth-form" onSubmit={handlePasswordResetRequest}>
            <div className="panel-heading">
              <h2>Восстановление пароля</h2>
              <p>
                Введите почту аккаунта. Если она привязана, мы отправим ссылку
                для смены пароля.
              </p>
            </div>

            <label className="field-label">
              Email
              <input
                autoCapitalize="none"
                autoComplete="email"
                className="glass-input"
                inputMode="email"
                name="email"
                placeholder="name@example.com"
                required
                type="email"
              />
            </label>

            <button
              className="primary-button"
              disabled={isSubmitting || isLoading}
              type="submit"
            >
              {isSubmitting ? 'Отправляем...' : 'Получить ссылку'}
            </button>

            <div className="switch-row">
              <button
                className="text-button"
                onClick={() => setAuthPanel('auth')}
                type="button"
              >
                Вернуться ко входу
              </button>
            </div>
          </form>
        )}

        {showResetConfirm && (
          <form className="auth-form" onSubmit={handlePasswordResetConfirm}>
            <div className="panel-heading">
              <h2>Новый пароль</h2>
              <p>Введите новый пароль. После смены мы сразу откроем кабинет.</p>
            </div>

            <PasswordField
              autoComplete="new-password"
              label="Новый пароль"
              name="password"
              placeholder="Минимум 8 символов"
            />

            <button
              className="primary-button"
              disabled={isSubmitting || isLoading}
              type="submit"
            >
              {isSubmitting ? 'Сохраняем...' : 'Сменить пароль'}
            </button>

            <div className="switch-row">
              <button
                className="text-button"
                onClick={() => setAuthPanel('reset-request')}
                type="button"
              >
                Запросить новую ссылку
              </button>
            </div>
          </form>
        )}

        {showResetSent && (
          <div className="auth-form">
            <div className="panel-heading">
              <h2>Проверьте почту</h2>
              <p>
                Если email привязан к аккаунту, письмо уже отправлено. Откройте
                ссылку из письма и задайте новый пароль.
              </p>
            </div>
            <button
              className="secondary-button"
              onClick={() => setAuthPanel('auth')}
              type="button"
            >
              Вернуться ко входу
            </button>
          </div>
        )}

        {showAuth && showConfirmation && (
          <form className="auth-form auth-form-confirm" onSubmit={handleConfirmRegistration}>
            <div className="panel-heading">
              <h2>Подтвердите почту</h2>
              <p>
                Код отправлен на {pendingRegistration.email}. Можно ввести код
                здесь или открыть ссылку из письма.
              </p>
            </div>

            <label className="field-label">
              Код из письма
              <input
                autoComplete="one-time-code"
                className="glass-input"
                inputMode="numeric"
                maxLength="6"
                name="code"
                pattern="[0-9]{6}"
                placeholder="000000"
                required
                type="text"
              />
            </label>

            <button
              className="primary-button"
              disabled={isSubmitting || isLoading}
              type="submit"
            >
              {isSubmitting ? 'Проверяем...' : 'Подтвердить'}
            </button>

            <div className="switch-row">
              <span>Письмо не пришло?</span>
              <button
                className="text-button"
                onClick={() => switchMode('register')}
                type="button"
              >
                Отправить заново
              </button>
            </div>
          </form>
        )}

        {showAuth && !showConfirmation && (
          <form className="auth-form" onSubmit={handleSubmit}>
            <div className="panel-heading" key={`${mode}-title`}>
              <h2>{activeMode.title}</h2>
              <p>{activeMode.subtitle}</p>
            </div>

            <div className="form-grid" key={`${mode}-fields`}>
              {isRegister && (
                <label className="field-label">
                  Имя
                  <input
                    autoComplete="name"
                    className="glass-input"
                    name="name"
                    placeholder="Ваше имя"
                    required
                    type="text"
                  />
                </label>
              )}

              <label className="field-label">
                Email
                <input
                  autoCapitalize="none"
                  autoComplete="email"
                  className="glass-input"
                  inputMode="email"
                  name="email"
                  placeholder="name@example.com"
                  required
                  type="email"
                />
              </label>

              <PasswordField
                autoComplete={isRegister ? 'new-password' : 'current-password'}
                label="Пароль"
                name="password"
                placeholder={isRegister ? 'От 8 символов' : 'Введите пароль'}
              />
            </div>

            <button
              className="primary-button"
              disabled={isSubmitting || isLoading}
              type="submit"
            >
              {isSubmitting ? 'Проверяем...' : activeMode.button}
            </button>

            <div className="switch-row">
              <span>{activeMode.footer}</span>
              <button
                className="text-button"
                onClick={() => switchMode(isRegister ? 'login' : 'register')}
                type="button"
              >
                {activeMode.action}
              </button>
              {!isRegister && (
                <button
                  className="text-button"
                  onClick={() => setAuthPanel('reset-request')}
                  type="button"
                >
                  Забыли пароль?
                </button>
              )}
            </div>
          </form>
        )}
      </section>
    </div>
  )
}

function EmailLinkView({
  account,
  emailLinkPending,
  handleEmailLinkConfirm,
  handleEmailLinkStart,
  isLoading,
  isSubmitting,
  setEmailLinkPending,
}) {
  return (
    <div className="guest-layout email-link-layout">
      <HeroIntro />

      <section className={`auth-panel liquid-card ${emailLinkPending ? 'is-confirming' : ''}`}>
        {emailLinkPending ? (
          <form className="auth-form auth-form-confirm" onSubmit={handleEmailLinkConfirm}>
            <div className="panel-heading">
              <h2>Подтвердите почту</h2>
              <p>
                Код отправлен на {emailLinkPending.email}. Можно ввести код
                здесь или открыть ссылку из письма.
              </p>
            </div>

            <label className="field-label">
              Код из письма
              <input
                autoComplete="one-time-code"
                className="glass-input"
                inputMode="numeric"
                maxLength="6"
                name="code"
                pattern="[0-9]{6}"
                placeholder="000000"
                required
                type="text"
              />
            </label>

            <button
              className="primary-button"
              disabled={isSubmitting || isLoading}
              type="submit"
            >
              {isSubmitting ? 'Проверяем...' : 'Подтвердить'}
            </button>

            <div className="switch-row">
              <button
                className="text-button"
                onClick={() => setEmailLinkPending(null)}
                type="button"
              >
                Изменить email
              </button>
            </div>
          </form>
        ) : (
          <form className="auth-form" onSubmit={handleEmailLinkStart}>
            <div className="panel-heading">
              <h2>Привяжите почту</h2>
              <p>
                Сейчас кабинет открыт через Telegram. Добавьте email и пароль,
                чтобы входить с сайта напрямую.
              </p>
            </div>

            <div className="form-grid">
              <label className="field-label">
                Имя
                <input
                  autoComplete="name"
                  className="glass-input"
                  defaultValue={account?.name || ''}
                  name="name"
                  placeholder="Ваше имя"
                  required
                  type="text"
                />
              </label>

              <label className="field-label">
                Email
                <input
                  autoCapitalize="none"
                  autoComplete="email"
                  className="glass-input"
                  inputMode="email"
                  name="email"
                  placeholder="name@example.com"
                  required
                  type="email"
                />
              </label>

              <PasswordField
                autoComplete="new-password"
                label="Пароль"
                name="password"
                placeholder="Минимум 8 символов"
              />
            </div>

            <button
              className="primary-button"
              disabled={isSubmitting || isLoading}
              type="submit"
            >
              {isSubmitting ? 'Отправляем...' : 'Привязать почту'}
            </button>

            <div className="switch-row">
              <a className="text-button" href="/">
                Вернуться в кабинет
              </a>
            </div>
          </form>
        )}
      </section>
    </div>
  )
}

function PasswordField({
  autoComplete,
  label = 'Пароль',
  name = 'password',
  placeholder = 'Пароль',
}) {
  const [isVisible, setIsVisible] = useState(false)

  return (
    <label className="field-label">
      {label}
      <span className="password-field">
        <input
          autoComplete={autoComplete}
          className="glass-input"
          minLength="8"
          name={name}
          placeholder={placeholder}
          required
          type={isVisible ? 'text' : 'password'}
        />
        <button
          aria-label={isVisible ? 'Скрыть пароль' : 'Показать пароль'}
          className="password-toggle"
          onClick={() => setIsVisible((value) => !value)}
          type="button"
        >
          {isVisible ? 'Скрыть' : 'Показать'}
        </button>
      </span>
    </label>
  )
}

function AuthedView({
  account,
  activeTab,
  copyText,
  handleCheckPayment,
  handleEmailAnnouncements,
  handleEmailReminders,
  handleVpnMode,
  handleLinkCode,
  handlePremium,
  handlePromo,
  handleReferralCode,
  handleTopup,
  handleTrial,
  hasSubscription,
  isSubmitting,
  lastPaymentId,
  linkCode,
  promoCode,
  referralCode,
  setActiveTab,
  setPromoCode,
  setReferralCode,
  statusTone,
  subscription,
  topupAmounts,
}) {
  const accountLine = [account.name, account.email].filter(Boolean).join(' · ') || 'Telegram'

  return (
    <div className="dashboard-shell">
      <section className="status-card liquid-card">
        <div>
          <span className="eyebrow">{statusTone}</span>
          <h1>{getTariffLabel(subscription)}</h1>
          <p>{accountLine}</p>
        </div>
        <div className="status-side">
          <div className="balance-badge">
            <small>Баланс</small>
            <strong>{subscription?.balanceRub || 0} ₽</strong>
          </div>
          {account?.isAdmin && (
            <a className="admin-entry-button" href="/admin">
              Админка
            </a>
          )}
        </div>
      </section>

      <nav className="tab-switcher" aria-label="Разделы кабинета">
        {tabItems.map((item) => (
          <button
            aria-current={activeTab === item.id ? 'page' : undefined}
            className="tab-button"
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </nav>

      <section className="tab-panel" key={activeTab}>
        {activeTab === 'access' && (
          <AccessPanel
            copyText={copyText}
            account={account}
            handleEmailAnnouncements={handleEmailAnnouncements}
            handleEmailReminders={handleEmailReminders}
            handleVpnMode={handleVpnMode}
            handleLinkCode={handleLinkCode}
            handlePremium={handlePremium}
            handleReferralCode={handleReferralCode}
            handleTrial={handleTrial}
            hasSubscription={hasSubscription}
            isSubmitting={isSubmitting}
            linkCode={linkCode}
            referralCode={referralCode}
            setActiveTab={setActiveTab}
            setReferralCode={setReferralCode}
            subscription={subscription}
          />
        )}

        {activeTab === 'payment' && (
          <PaymentPanel
            handleCheckPayment={handleCheckPayment}
            handlePromo={handlePromo}
            handleTopup={handleTopup}
            isSubmitting={isSubmitting}
            lastPaymentId={lastPaymentId}
            promoCode={promoCode}
            setPromoCode={setPromoCode}
            subscription={subscription}
            topupAmounts={topupAmounts}
          />
        )}

        {activeTab === 'guide' && <ConnectionGuide subscription={subscription} copyText={copyText} onAccess={() => setActiveTab('access')} />}
      </section>
    </div>
  )
}

function AccessPanel({
  account,
  copyText,
  handleEmailAnnouncements,
  handleEmailReminders,
  handleVpnMode,
  handleLinkCode,
  handlePremium,
  handleReferralCode,
  handleTrial,
  hasSubscription,
  isSubmitting,
  linkCode,
  referralCode,
  setActiveTab,
  setReferralCode,
  subscription,
}) {
  const openTelegramBot = async () => {
    const data = await handleLinkCode()
    const currentCode = data?.linkCode || linkCode
    if (currentCode?.code) {
      window.location.assign(telegramStartLink(currentCode.code))
    }
  }

  return (
    <div className="panel-stack">
      {subscription?.pendingJobId && (
        <section className="liquid-card slim-card" role="status" aria-live="polite">
          <h3>{subscription.provisioningStage === 'retrying' ? 'Восстанавливаем связь с VPN-панелью' : 'Доступ создаётся'}</h3>
          <p>Операция сохранена. Повторим автоматически, без повторного списания. Статус обновится здесь.</p>
        </section>
      )}
      <section className={`liquid-card focus-card${hasSubscription && !subscription?.active && !subscription?.blocked ? ' access-expired-card' : ''}`}>
        <div className="panel-heading">
          <span className="eyebrow">{hasSubscription && !subscription?.active && !subscription?.blocked ? 'Доступ закончился' : 'Ваш доступ'}</span>
          <h2>
            {subscription?.blocked ? 'Доступ приостановлен' : subscription?.active
              ? 'Всё готово к подключению'
              : hasSubscription ? 'Продлите доступ' : 'Подключитесь за пару минут'}
          </h2>
          <p>
            {subscription?.blocked ? 'Напишите в поддержку — поможем разобраться.' : subscription?.active
              ? `Доступ действует до ${formatDate(subscription?.expiresAt)}`
              : hasSubscription
                ? 'Продлите доступ — прежняя подписка снова заработает. Менять ссылку в приложении не нужно.'
                : 'Первые 24 часа бесплатно. Затем — Premium с оплатой с баланса.'}
          </p>
        </div>

        {subscription?.blocked ? (
          <a className="primary-button" href="/support">Связаться с поддержкой</a>
        ) : !subscription?.active ? (
          <div className="action-stack">
            {!hasSubscription && subscription?.canUseTrial && <button
              className="primary-button"
              disabled={!subscription?.canUseTrial || isSubmitting}
              onClick={handleTrial}
              type="button"
            >
              Получить 24 часа бесплатно
            </button>}
            <button
              className={hasSubscription ? 'primary-button renew-access-button' : subscription?.canUseTrial ? 'secondary-button' : 'primary-button'}
              disabled={isSubmitting}
              onClick={handlePremium}
              type="button"
            >
              {hasSubscription ? 'Продлить за' : 'Активировать Premium за'} {subscription?.premiumPrice || 300} ₽
            </button>
            {hasSubscription && <div className="subscription-link-paused"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M10 13a5 5 0 007 .1l3-3a5 5 0 00-7-7l-2 2m3 6a5 5 0 00-7-.1l-3 3a5 5 0 007 7l2-2" /></svg><div><strong>Ваша ссылка сохранена</strong><small>Копирование станет доступно после продления</small></div></div>}
            <button
              className="text-button centered"
              onClick={() => setActiveTab('payment')}
              type="button"
            >
              Пополнить баланс
            </button>
          </div>
        ) : (
          <div className="action-stack">
            <button
              className="primary-button"
              onClick={() =>
                copyText(
                  subscription.subscriptionUrl,
                  'Ссылка подписки скопирована.',
                )
              }
              type="button"
            >
              Скопировать ссылку подписки
            </button>
            <div className="link-preview">
              <code>{subscription.subscriptionUrl.replace(/(\/sub\/).+$/, '$1••••••••')}</code>
            </div>

          </div>
        )}
      </section>

      {subscription?.active && (
      <section className="liquid-card slim-card">
        <div className="row-between">
          <div>
            <h3>Premium</h3>
            <p>30 дней, выбор локации и продление текущей ссылки.</p>
          </div>
          <button
            className="secondary-button fit-button"
            disabled={isSubmitting}
            onClick={handlePremium}
            type="button"
          >
            {subscription?.tariff === 'premium' ? 'Продлить' : 'Включить'}
          </button>
        </div>
      </section>

      )}

      {!subscription?.blocked && <TransportPicker subscription={subscription} onChange={handleVpnMode} disabled={isSubmitting} />}

      {account?.hasRealEmail && (
        <section className="liquid-card slim-card notification-card">
          <div className="panel-heading compact-heading">
            <h3>Уведомления на почту</h3>
            <p>Отдельно управляйте напоминаниями и важными объявлениями.</p>
          </div>
          <div className="notification-list">
            <div className="row-between reminder-row notification-row">
              <div>
                <h3>Напоминания</h3>
                <p>За 24 часа и за 1 час до окончания подписки.</p>
              </div>
              <label className="ios-toggle">
                <input
                  checked={account?.emailRemindersEnabled !== false}
                  disabled={isSubmitting}
                  onChange={(event) => handleEmailReminders(event.target.checked)}
                  type="checkbox"
                />
                <span aria-hidden="true" />
              </label>
            </div>
            <div className="row-between reminder-row notification-row">
              <div>
                <h3>Важные объявления</h3>
                <p>Админские рассылки и сервисные новости.</p>
              </div>
              <label className="ios-toggle">
                <input
                  checked={account?.emailAnnouncementsEnabled !== false}
                  disabled={isSubmitting}
                  onChange={(event) => handleEmailAnnouncements(event.target.checked)}
                  type="checkbox"
                />
                <span aria-hidden="true" />
              </label>
            </div>
          </div>
        </section>
      )}

      <ReferralPanel
        copyText={copyText}
        handleReferralCode={handleReferralCode}
        isSubmitting={isSubmitting}
        referralCode={referralCode}
        referrals={subscription?.referrals}
        setReferralCode={setReferralCode}
      />

      {account?.needsEmailLink ? (
        <section className="bonus-card liquid-card">
          <div className="panel-heading compact-heading">
            <span className="eyebrow">Почта</span>
            <h3>Привяжите email к кабинету</h3>
            <p>
              Вы вошли через Telegram Mini App. Добавьте почту и пароль, чтобы
              получать письма и входить на сайте без Telegram.
            </p>
          </div>
          <a className="primary-button action-link" href="/auth">
            Привязать почту
          </a>
        </section>
      ) : hasSubscription && !subscription?.telegramBonusClaimed && (
        <section className="bonus-card liquid-card">
          <div className="panel-heading compact-heading">
            <span className="eyebrow">Бонус доступен</span>
            <h3>+3 дня за привязку Telegram</h3>
            <p>
              Привязка объединит сайт и Telegram в одну подписку. Управлять
              доступом можно будет в обоих местах, а срок увеличится на 3 дня.
              Нажмите кнопку ниже: бот откроется сразу с кодом привязки.
            </p>
          </div>
          <button
            className="primary-button"
            disabled={isSubmitting}
            onClick={openTelegramBot}
            type="button"
          >
            Открыть Telegram-бота
          </button>
          {linkCode && (
            <div className="link-preview compact">
              <span>
                Если Telegram не открылся автоматически, нажмите кнопку еще раз
                или скопируйте команду вручную:
              </span>
              <a
                className="secondary-button action-link"
                href={telegramStartLink(linkCode.code)}
                rel="noreferrer"
                target="_blank"
              >
                Открыть бота
              </a>
              <button
                className="code-copy-button"
                onClick={() =>
                  copyText(
                    `/link ${linkCode.code}`,
                    'Команда для Telegram скопирована.',
                  )
                }
                type="button"
              >
                /link {linkCode.code}
              </button>
              <small>
                Код действует 30 минут. После привязки добавится{' '}
                {linkCode.bonusDays} дня.
              </small>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

function ReferralPanel({
  copyText,
  handleReferralCode,
  isSubmitting,
  referralCode,
  referrals,
  setReferralCode,
}) {
  const invited = referrals?.invited || []
  const code = referrals?.ownCode || ''
  const invitedBy = referrals?.invitedBy

  return (
    <section className="liquid-card slim-card referral-card">
      <div className="panel-heading compact-heading">
        <h3>Рефералы</h3>
        <p>Код такой же, как в Telegram-боте. За Premium приглашенного начисляется бонус.</p>
      </div>
      <div className="referral-code-row">
        <code>{code || 'Код создается...'}</code>
        <button
          className="secondary-button fit-button"
          disabled={!code}
          onClick={() => copyText(code, 'Реферальный код скопирован.')}
          type="button"
        >
          Скопировать
        </button>
      </div>
      {invitedBy ? (
        <p className="referral-applied">
          Пригласивший: {invitedBy.name || invitedBy.email || invitedBy.tgId}
        </p>
      ) : (
        <form className="referral-apply-row" onSubmit={handleReferralCode}>
          <label className="field-label">
            Код пригласившего
            <span className="referral-apply-control">
              <input
                className="glass-input"
                disabled={isSubmitting}
                maxLength={32}
                onChange={(event) => setReferralCode(event.target.value)}
                placeholder="Введите код друга"
                value={referralCode}
              />
              <button
                className="primary-button fit-button"
                disabled={isSubmitting}
                type="submit"
              >
                Применить
              </button>
            </span>
          </label>
        </form>
      )}
      {invited.length ? (
        <div className="referral-list">
          {invited.map((item) => (
            <div className="referral-item" key={item.tgId}>
              <strong>{item.name || item.email || item.tgId}</strong>
              <span>{referralStatus[item.rewarded] || 'Ожидает Premium'}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="support-empty">Пока никого нет.</p>
      )}
    </section>
  )
}

function PaymentPanel({
  handleCheckPayment,
  handlePromo,
  handleTopup,
  isSubmitting,
  lastPaymentId,
  promoCode,
  setPromoCode,
  subscription,
  topupAmounts,
}) {
  return (
    <div className="panel-stack">
      <section className="liquid-card focus-card">
        <div className="panel-heading">
          <span className="eyebrow">Баланс</span>
          <h2>{subscription?.balanceRub || 0} ₽</h2>
          <p>
            Premium стоит {subscription?.premiumPrice || 300} ₽ за 30 дней. После
            пополнения вернитесь в «Доступ» и активируйте тариф.
          </p>
        </div>
        <div className="amount-grid">
          {topupAmounts.map((amount) => (
            <button
              className="amount-button"
              disabled={isSubmitting}
              key={amount}
              onClick={() => handleTopup(amount)}
              type="button"
            >
              {amount} ₽
            </button>
          ))}
        </div>
        {lastPaymentId && (
          <button
            className="secondary-button"
            disabled={isSubmitting}
            onClick={handleCheckPayment}
            type="button"
          >
            Проверить последнюю оплату
          </button>
        )}
      </section>

      <form className="liquid-card slim-card promo-card" onSubmit={handlePromo}>
        <div className="panel-heading compact-heading">
          <h3>Промокод</h3>
          <p>Введите код, если он у вас есть. Баланс обновится автоматически.</p>
        </div>
        <label className="field-label">
          Код
          <input
            className="glass-input"
            onChange={(event) => setPromoCode(event.target.value)}
            placeholder="KL-XXXX"
            value={promoCode}
          />
        </label>
        <button
          className="secondary-button"
          disabled={isSubmitting}
          type="submit"
        >
          Применить
        </button>
      </form>
    </div>
  )
}

const adminTabs = [
  { id: 'analytics', label: 'Аналитика', permission: 'analytics' },
  { id: 'users', label: 'Пользователи' },
  { id: 'support', label: 'Поддержка', permission: 'support' },
  { id: 'promos', label: 'Промокоды', permission: 'promos' },
  { id: 'payments', label: 'Оплаты', permission: 'payments' },
  { id: 'nodes', label: 'Ноды', permission: 'nodes' },
  { id: 'technical', label: 'Технические логи', permission: 'technical' },
  { id: 'documentation', label: 'Документация', permission: 'documentation' },
  { id: 'broadcast', label: 'Сообщения', permission: 'broadcast' },
  { id: 'admins', label: 'Админы', ownerOnly: true },
  { id: 'audit', label: 'Журнал действий', ownerOnly: true, hiddenForNonOwner: true },
]

const referralStatus = {
  0: 'Ожидает Premium',
  1: 'Бонус выдан',
  2: 'В обработке',
}

function AdminTwoFactor({
  code,
  hint,
  isAvailable,
  isSubmitting,
  onCode,
  onSend,
  onSubmit,
  sent,
}) {
  return (
    <section className="admin-shell liquid-card admin-2fa-card">
      <span className="eyebrow">Защита админки</span>
      <h1>Код из Telegram</h1>
      <p>
        {isAvailable
          ? `Введите 6-значный код, отправленный в ${hint}.`
          : 'Для этого администратора не найден Telegram. Привяжите Telegram к аккаунту или добавьте админа по tg id.'}
      </p>
      {isAvailable && (
        <form className="admin-2fa-form" onSubmit={onSubmit}>
          <input
            autoComplete="one-time-code"
            className="glass-input"
            inputMode="numeric"
            maxLength={6}
            onChange={(event) => onCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
            placeholder="000000"
            value={code}
          />
          <button className="primary-button fit-button" disabled={isSubmitting} type="submit">
            Подтвердить
          </button>
        </form>
      )}
      <button
        className="secondary-button fit-button"
        disabled={isSubmitting || !isAvailable}
        onClick={onSend}
        type="button"
      >
        {sent ? 'Отправить заново' : 'Отправить код'}
      </button>
    </section>
  )
}

function AdminPanel({ account, setNotice }) {
  const [activeAdminTab, setActiveAdminTab] = useState(() => {const section=new URLSearchParams(window.location.search).get('section');return section==='vpn'?'nodes':adminTabs.some(tab=>tab.id===section) ? section : 'analytics'})
  const [adminData, setAdminData] = useState(null)
  const [selectedUser, setSelectedUser] = useState(null)
  const [analyticsData, setAnalyticsData] = useState(null)
  const [analyticsLiveSites, setAnalyticsLiveSites] = useState(null)
  const [auditLogs, setAuditLogs] = useState(null)
  const [analyticsRange, setAnalyticsRange] = useState('ten')
  const [analyticsUser, setAnalyticsUser] = useState('all')
  const [analyticsUserSort, setAnalyticsUserSort] = useState('traffic')
  const [analyticsChartMode, setAnalyticsChartMode] = useState('bars')
  const [analyticsCustomStart, setAnalyticsCustomStart] = useState('')
  const [analyticsCustomEnd, setAnalyticsCustomEnd] = useState('')
  const [referrals, setReferrals] = useState(null)
  const [balanceAmount, setBalanceAmount] = useState('')
  const [messageText, setMessageText] = useState('')
  const [messageSubject, setMessageSubject] = useState('Сообщение')
  const [messageChannel, setMessageChannel] = useState('telegram')
  const [broadcastChannel, setBroadcastChannel] = useState('both')
  const [broadcastSubject, setBroadcastSubject] = useState('')
  const [broadcastText, setBroadcastText] = useState('')
  const [activityHistory, setActivityHistory] = useState({})
  const [userInsights, setUserInsights] = useState({})
  const [loadingHistoryUser, setLoadingHistoryUser] = useState('')
  const [loadingInsightsUser, setLoadingInsightsUser] = useState('')
  const [adminEmail, setAdminEmail] = useState('')
  const [adminTgId, setAdminTgId] = useState('')
  const [promoCodeAdmin, setPromoCodeAdmin] = useState('')
  const [promoAmountAdmin, setPromoAmountAdmin] = useState('')
  const [supportAccount, setSupportAccount] = useState(null)
  const [supportMessages, setSupportMessages] = useState([])
  const [supportText, setSupportText] = useState('')
  const [supportFiles, setSupportFiles] = useState([])
  const [onlineData, setOnlineData] = useState(null)
  const [onlineDisplayMode, setOnlineDisplayMode] = useState('name')
  const [admin2fa, setAdmin2fa] = useState(null)
  const [admin2faCode, setAdmin2faCode] = useState('')
  const [isAdminLoading, setIsAdminLoading] = useState(true)
  const [isAdminSubmitting, setIsAdminSubmitting] = useState(false)
  const admin2faAutoSentRef = useRef(false)
  const adminOpenLoggedRef = useRef(false)

  const loadAdmin = useCallback(() => getJson('/api/admin/bootstrap'), [])
  const loadAdminOnline = useCallback(() => getJson('/api/admin/online'), [])
  const loadAdminLiveSites = useCallback(() => getJson('/api/admin/analytics/live-sites'), [])
  const loadAdminAudit = useCallback(() => getJson('/api/admin/audit'), [])
  const analyticsSortStorageKey = useMemo(() => {
    const accountId = adminData?.admin?.accountId
    return accountId ? `kingline_admin_analytics_sort_${accountId}` : ''
  }, [adminData?.admin?.accountId])
  const loadAdminAnalytics = useCallback(() => {
    const params = new URLSearchParams({
      range: analyticsRange,
      user: analyticsUser,
    })
    if (analyticsRange === 'custom') {
      if (analyticsCustomStart) {
        params.set('start', new Date(analyticsCustomStart).toISOString())
      }
      if (analyticsCustomEnd) {
        params.set('end', new Date(analyticsCustomEnd).toISOString())
      }
    }
    return getJson(`/api/admin/analytics?${params.toString()}`)
  }, [analyticsCustomEnd, analyticsCustomStart, analyticsRange, analyticsUser])

  useEffect(() => {
    if (!analyticsSortStorageKey) {
      return
    }
    try {
      const stored = window.localStorage.getItem(analyticsSortStorageKey)
      if (analyticsUserSortOptions.some(([id]) => id === stored)) {
        setAnalyticsUserSort(stored)
      }
    } catch {
      // localStorage can be unavailable in private or restricted browser modes.
    }
  }, [analyticsSortStorageKey])

  const handleAnalyticsUserSort = useCallback((value) => {
    const sortValue = analyticsUserSortOptions.some(([id]) => id === value) ? value : 'traffic'
    setAnalyticsUserSort(sortValue)
    if (!analyticsSortStorageKey) {
      return
    }
    try {
      window.localStorage.setItem(analyticsSortStorageKey, sortValue)
    } catch {
      // Sorting still works for the current session if storage is blocked.
    }
  }, [analyticsSortStorageKey])

  const applyLoadedAdmin = useCallback((data) => {
    if (data.admin2faRequired) {
      setAdmin2fa({
        hint: data.telegramHint || 'Telegram',
        available: Boolean(data.telegramAvailable),
        sent: Boolean(data.sent),
      })
      setAdminData(null)
      return false
    }
    setAdmin2fa(null)
    setAdmin2faCode('')
    setAdminData(data)
    return true
  }, [])

  const sendAdmin2faCode = useCallback(async () => {
    setIsAdminSubmitting(true)
    try {
      const data = await postJson('/api/admin/2fa/send')
      if (data.admin2faRequired) {
        setAdmin2fa({
          hint: data.telegramHint || 'Telegram',
          available: true,
          sent: true,
        })
        setNotice('Код отправлен в Telegram.')
      } else {
        applyLoadedAdmin(data)
      }
    } catch (error) {
      setNotice(error.message)
    } finally {
      setIsAdminSubmitting(false)
    }
  }, [applyLoadedAdmin, setNotice])

  const verifyAdmin2faCode = async (event) => {
    event.preventDefault()
    if (!admin2faCode.trim()) {
      setNotice('Введите код из Telegram.')
      return
    }
    setIsAdminSubmitting(true)
    try {
      const data = await postJson('/api/admin/2fa/verify', { code: admin2faCode })
      applyLoadedAdmin(data)
      setNotice('Вход в админку подтвержден.')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setIsAdminSubmitting(false)
    }
  }

  useEffect(() => {
    let alive = true
    loadAdmin()
      .then((data) => {
        if (alive) {
          const ready = applyLoadedAdmin(data)
          if (!ready && data.telegramAvailable && !admin2faAutoSentRef.current) {
            admin2faAutoSentRef.current = true
            sendAdmin2faCode()
          }
        }
      })
      .catch((error) => {
        if (alive) {
          setNotice(error.message)
        }
      })
      .finally(() => {
        if (alive) {
          setIsAdminLoading(false)
        }
      })
    return () => {
      alive = false
    }
  }, [applyLoadedAdmin, loadAdmin, sendAdmin2faCode, setNotice])

  useEffect(() => {
    if (!adminData) {
      return undefined
    }
    let alive = true
    const refreshOnline = async () => {
      try {
        const data = await loadAdminOnline()
        if (alive) {
          setOnlineData(data)
        }
      } catch (error) {
        if (alive) {
          setOnlineData((current) => current || { ok: false, error: error.message, users: [], panels: [] })
        }
      }
    }
    const stopPolling = startPolling(refreshOnline, 5000)
    return () => {
      alive = false
      stopPolling()
    }
  }, [adminData, loadAdminOnline])

  useEffect(() => {
    if (!adminData || activeAdminTab !== 'analytics') {
      return undefined
    }
    let alive = true
    const refreshAnalytics = async () => {
      try {
        const data = await loadAdminAnalytics()
        if (alive) {
          setAnalyticsData(data)
        }
      } catch (error) {
        if (alive) {
          setNotice(error.message)
        }
      }
    }
    const stopPolling = startPolling(refreshAnalytics, 60000)
    return () => {
      alive = false
      stopPolling()
    }
  }, [activeAdminTab, adminData, loadAdminAnalytics, setNotice])

  useEffect(() => {
    const canReadAnalytics = Boolean(
      adminData?.admin?.isOwner || adminData?.admin?.permissions?.analytics,
    )
    if (!adminData || activeAdminTab !== 'analytics' || !canReadAnalytics) {
      setAnalyticsLiveSites(null)
      return undefined
    }
    let alive = true
    const refreshLiveSites = async () => {
      try {
        const data = await loadAdminLiveSites()
        if (alive) {
          setAnalyticsLiveSites(data)
        }
      } catch {
        if (alive) {
          setAnalyticsLiveSites(null)
        }
      }
    }
    const stopPolling = startPolling(refreshLiveSites, 5000)
    return () => {
      alive = false
      stopPolling()
    }
  }, [activeAdminTab, adminData, loadAdminLiveSites])

  const applyAdminData = (data) => {
    setAdminData(data)
    if (selectedUser) {
      const updated = data.users?.find((user) => user.tgId === selectedUser.tgId)
      setSelectedUser(updated || null)
    }
  }

  const trackAdminEvent = useCallback((action, target = '', summary = {}) => {
    postJson('/api/admin/audit/event', { action, target, summary }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!adminData || adminOpenLoggedRef.current) {
      return
    }
    adminOpenLoggedRef.current = true
    trackAdminEvent('admin_panel_open', 'admin', { tab: activeAdminTab })
  }, [activeAdminTab, adminData, trackAdminEvent])

  useEffect(() => {
    if (!adminData?.admin?.isOwner || activeAdminTab !== 'audit') {
      return undefined
    }
    let alive = true
    loadAdminAudit()
      .then((data) => {
        if (alive) {
          setAuditLogs(data)
        }
      })
      .catch((error) => {
        if (alive) {
          setNotice(error.message)
        }
      })
    return () => {
      alive = false
    }
  }, [activeAdminTab, adminData, loadAdminAudit, setNotice])

  const runAdminAction = async (action, successText) => {
    setIsAdminSubmitting(true)
    try {
      const data = await action()
      applyAdminData(data)
      setNotice(successText)
      return data
    } catch (error) {
      setNotice(error.message)
      return null
    } finally {
      setIsAdminSubmitting(false)
    }
  }

  const refreshAdmin = () => {
    loadAdminOnline().then(setOnlineData).catch(() => {})
    if (activeAdminTab === 'analytics') {
      loadAdminAnalytics().then(setAnalyticsData).catch(() => {})
    }
    if (activeAdminTab === 'audit' && adminData?.admin?.isOwner) {
      loadAdminAudit().then(setAuditLogs).catch(() => {})
    }
    return runAdminAction(loadAdmin, 'Данные обновлены.')
  }

  const openUser = (user) => {
    setSelectedUser(user)
    setActiveAdminTab('user')
    trackAdminEvent('user_open', `tg:${user.tgId || ''}`, {
      email: user.email || '',
      username: user.username || '',
      name: user.name || '',
    })
    setReferrals(null)
    setBalanceAmount('')
    setMessageText('')
    setMessageSubject('Сообщение')
    setMessageChannel(user.canMessageTelegram ? 'telegram' : 'email')
    window.setTimeout(() => {
      document.getElementById('admin-user-detail')?.scrollIntoView({ block: 'start' })
    }, 0)
  }

  const openOnlineUser = (onlineUser) => {
    const users = adminData?.users || []
    const match = users.find((user) => {
      if (onlineUser.tgId && Number(user.tgId) === Number(onlineUser.tgId)) {
        return true
      }
      return normalizeAdminUsername(user.username) === normalizeAdminUsername(onlineUser.username)
    })
    if (match) {
      openUser(match)
    } else {
      setNotice('Пользователь не найден в таблице.')
    }
  }

  const openAnalyticsUser = (analyticsUserItem) => {
    const users = adminData?.users || []
    const match = users.find((user) => {
      if (analyticsUserItem.tgId && Number(user.tgId) === Number(analyticsUserItem.tgId)) {
        return true
      }
      return normalizeAdminUsername(user.username) === normalizeAdminUsername(analyticsUserItem.username)
    })
    if (match) {
      trackAdminEvent('analytics_user_open', `user:${analyticsUserItem.username || ''}`, {
        username: analyticsUserItem.username || '',
        name: analyticsUserItem.name || '',
      })
      openUser(match)
    }
  }

  const changeBalance = (sign) => {
    const amount = Number(balanceAmount)
    if (!Number.isFinite(amount) || amount <= 0 || !selectedUser?.tgId) {
      setNotice('Введите сумму и выберите пользователя.')
      return
    }
    runAdminAction(
      () =>
        postJson('/api/admin/user/balance', {
          tgId: selectedUser.tgId,
          delta: sign * Math.round(amount),
        }),
      'Баланс обновлен.',
    ).then((data) => {
      if (data) {
        setBalanceAmount('')
      }
    })
  }

  const toggleBlocked = () => {
    if (!selectedUser?.tgId) {
      return
    }
    runAdminAction(
      () =>
        postJson('/api/admin/user/block', {
          tgId: selectedUser.tgId,
          blocked: !selectedUser.blocked,
        }),
      selectedUser.blocked ? 'Пользователь разблокирован.' : 'Пользователь заблокирован.',
    )
  }

  const sendPersonalMessage = () => {
    if (!selectedUser || !messageText.trim()) {
      setNotice('Введите текст сообщения.')
      return
    }
    runAdminAction(
      () =>
        postJson('/api/admin/user/message', {
          tgId: selectedUser.tgId,
          email: selectedUser.email,
          channel: messageChannel,
          subject: messageSubject,
          text: messageText,
        }),
      'Сообщение отправлено.',
    ).then((data) => {
      if (data) {
        setMessageText('')
      }
    })
  }

  const loadReferrals = async () => {
    if (!selectedUser?.tgId) {
      return
    }
    setIsAdminSubmitting(true)
    try {
      const data = await getJson(`/api/admin/referrals?tg_id=${selectedUser.tgId}`)
      setReferrals(data.referrals)
    } catch (error) {
      setNotice(error.message)
    } finally {
      setIsAdminSubmitting(false)
    }
  }

  const makeSelectedAdmin = () => {
    if (!selectedUser) {
      return
    }
    runAdminAction(
      () =>
        postJson('/api/admin/admins/add', {
          email: selectedUser.email,
          tgId: selectedUser.tgId,
        }),
      'Админ добавлен.',
    )
  }

  const deleteSelectedUser = () => {
    if (!selectedUser) {
      return
    }
    const label = selectedUser.email || selectedUser.username || selectedUser.tgId || 'пользователя'
    if (!window.confirm(`Удалить ${label} из базы? Действие нельзя отменить.`)) {
      return
    }
    runAdminAction(
      () =>
        postJson('/api/admin/user/delete', {
          tgId: selectedUser.tgId,
          webAccountId: selectedUser.webAccountId,
        }),
      'Пользователь удален.',
    ).then((data) => {
      if (data) {
        setSelectedUser(null)
        setReferrals(null)
        setActiveAdminTab('users')
      }
    })
  }

  const addAdmin = (event) => {
    event.preventDefault()
    runAdminAction(
      () =>
        postJson('/api/admin/admins/add', {
          email: adminEmail,
          tgId: adminTgId,
        }),
      'Админ добавлен.',
    ).then((data) => {
      if (data) {
        setAdminEmail('')
        setAdminTgId('')
      }
    })
  }

  const removeAdmin = (adminId) =>
    runAdminAction(
      () => postJson('/api/admin/admins/remove', { id: adminId }),
      'Админ удален.',
    )

  const createPromo = (event) => {
    event.preventDefault()
    runAdminAction(
      () =>
        postJson('/api/admin/promos/create', {
          code: promoCodeAdmin,
          amount: promoAmountAdmin,
        }),
      'Промокод создан.',
    ).then((data) => {
      if (data) {
        setPromoCodeAdmin('')
        setPromoAmountAdmin('')
      }
    })
  }

  const deletePromo = (code) =>
    runAdminAction(
      () => postJson('/api/admin/promos/delete', { code }),
      'Промокод удален.',
    )

  const updateAdminPermissions = (adminId, permissions) =>
    runAdminAction(
      () => postJson('/api/admin/admins/permissions', { id: adminId, permissions }),
      'Права обновлены.',
    )

  const changeSelectedUserTransport = (transport) => {
    if (!selectedUser?.tgId) {
      setNotice('Сначала выберите пользователя.')
      return
    }
    runAdminAction(
      () =>
        postJson('/api/admin/user/vpn-mode', {
          tgId: selectedUser.tgId,
          transport,
        }),
      'Протокол сохранён. Пользователю нужно обновить подписку в приложении.',
    )
  }

  const loadActivityHistory = async (user) => {
    const username = normalizeAdminUsername(user?.username)
    if (!username) {
      setNotice('У пользователя нет username для истории.')
      return
    }
    if (activityHistory[username]) {
      setActivityHistory((current) => {
        const next = { ...current }
        delete next[username]
        return next
      })
      return
    }
    setLoadingHistoryUser(username)
    try {
      const data = await getJson(
        `/api/admin/analytics/sessions?username=${encodeURIComponent(username)}`,
      )
      trackAdminEvent('analytics_sessions_open', `user:${username}`, {
        username,
        opened: !activityHistory[username],
      })
      setActivityHistory((current) => ({ ...current, [username]: data }))
    } catch (error) {
      setNotice(error.message)
    } finally {
      setLoadingHistoryUser('')
    }
  }

  const loadUserInsights = async (user) => {
    const username = normalizeAdminUsername(user?.username)
    if (!username) {
      setNotice('У пользователя нет username для расширенной аналитики.')
      return
    }
    if (userInsights[username]) {
      setUserInsights((current) => {
        const next = { ...current }
        delete next[username]
        return next
      })
      return
    }
    setLoadingInsightsUser(username)
    try {
      const data = await getJson(
        `/api/admin/analytics/user-insights?username=${encodeURIComponent(username)}`,
      )
      trackAdminEvent('analytics_insights_open', `user:${username}`, {
        username,
        opened: !userInsights[username],
      })
      setUserInsights((current) => ({ ...current, [username]: data }))
    } catch (error) {
      setNotice(error.message)
    } finally {
      setLoadingInsightsUser('')
    }
  }

  const sendBroadcast = (event) => {
    event.preventDefault()
    if (!broadcastText.trim()) {
      setNotice('Введите текст рассылки.')
      return
    }
    runAdminAction(
      () =>
        postJson('/api/admin/broadcast/send', {
          channel: broadcastChannel,
          subject: broadcastSubject || 'Сообщение',
          text: broadcastText,
        }),
      'Рассылка отправлена.',
    ).then((data) => {
      if (data) {
        setBroadcastText('')
        setBroadcastSubject('')
        const result = data.broadcast
        if (result) {
          setNotice(
            `Отправлено: email ${result.emailSent}, Telegram ${result.telegramSent}. Ошибки: email ${result.emailFailed}, Telegram ${result.telegramFailed}.`,
          )
        }
      }
    })
  }

  const openSupportConversation = async (conversation) => {
    setSupportAccount(conversation)
    setSupportText('')
    setSupportFiles([])
    setActiveAdminTab('support')
    trackAdminEvent('support_conversation_open', `account:${conversation.accountId}`, {
      email: conversation.email || '',
      name: conversation.name || '',
      tgId: conversation.tgId || '',
    })
    setIsAdminSubmitting(true)
    try {
      const data = await getJson(
        `/api/admin/support/messages?account_id=${conversation.accountId}`,
      )
      setSupportMessages(data.messages || [])
    } catch (error) {
      setNotice(error.message)
    } finally {
      setIsAdminSubmitting(false)
    }
  }

  const sendAdminSupport = async (event) => {
    event.preventDefault()
    if (!supportAccount) {
      return
    }
    setIsAdminSubmitting(true)
    try {
      const attachments = await prepareAttachments(supportFiles)
      const data = await postJson('/api/admin/support/send', {
        accountId: supportAccount.accountId,
        text: supportText,
        attachments,
      })
      setSupportMessages(data.messages || [])
      setSupportText('')
      setSupportFiles([])
      setAdminData((current) => ({ ...current, support: data.support || current.support }))
    } catch (error) {
      setNotice(error.message)
    } finally {
      setIsAdminSubmitting(false)
    }
  }

  useEffect(() => {
    if (activeAdminTab !== 'support' || !supportAccount?.accountId) {
      return undefined
    }

    let alive = true
    const refreshSupport = async () => {
      try {
          const data = await getJson(
            `/api/admin/support/messages?account_id=${supportAccount.accountId}`,
          )
          if (alive) {
            setSupportMessages(data.messages || [])
          }
      } catch (error) {
        if (alive) {
          setNotice(error.message)
        }
      }
    }

    const stopPolling = startPolling(refreshSupport, 3000, false)
    return () => {
      alive = false
      stopPolling()
    }
  }, [activeAdminTab, supportAccount?.accountId, setNotice])

  if (isAdminLoading) {
    return (
      <section className="admin-shell liquid-card">
        <span className="eyebrow">Админ-панель</span>
        <h1>Загрузка данных...</h1>
      </section>
    )
  }

  if (admin2fa) {
    return (
      <AdminTwoFactor
        code={admin2faCode}
        hint={admin2fa.hint}
        isAvailable={admin2fa.available}
        isSubmitting={isAdminSubmitting}
        onCode={setAdmin2faCode}
        onSend={sendAdmin2faCode}
        onSubmit={verifyAdmin2faCode}
        sent={admin2fa.sent}
      />
    )
  }

  if (!adminData) {
    return (
      <section className="admin-shell liquid-card">
        <span className="eyebrow">Админ-панель</span>
        <h1>Нет доступа</h1>
        <p>Войдите под аккаунтом администратора.</p>
      </section>
    )
  }

  const adminMeta = adminData.admin || {}
  const canManageAdmins = Boolean(adminMeta.canManageAdmins)
  const canUse = (permission) =>
    !permission || adminMeta.isOwner || Boolean(adminMeta.permissions?.[permission])
  const canOpenTab = (tab) => {
    if (tab.ownerOnly && !canManageAdmins) {
      return false
    }
    return canUse(tab.permission)
  }

  return (
    <div className="admin-layout">
      <section className="admin-hero liquid-card">
        <div>
          <span className="eyebrow">Админ-панель</span>
          <h1>Панель управления</h1>
          <p>{account.email}</p>
        </div>
        <button
          aria-label="Обновить"
          className="icon-button"
          disabled={isAdminSubmitting}
          onClick={refreshAdmin}
          type="button"
        >
          ↻
        </button>
      </section>

      <AdminOnlineCard
        displayMode={onlineDisplayMode}
        onOpenUser={openOnlineUser}
        online={onlineData}
        setDisplayMode={setOnlineDisplayMode}
      />

      <nav className="admin-tabs" aria-label="Разделы админки">
        {adminTabs.map((tab) => {
          if (tab.hiddenForNonOwner && !adminMeta.isOwner) {
            return null
          }
          return (
          <button
            aria-current={activeAdminTab === tab.id ? 'page' : undefined}
            className="tab-button"
            disabled={!canOpenTab(tab)}
            key={tab.id}
            onClick={() => {
              if (canOpenTab(tab)) {
                setActiveAdminTab(tab.id)
                window.history.replaceState(null,'',`/admin?section=${tab.id}`)
                trackAdminEvent('admin_tab_open', `tab:${tab.id}`, { label: tab.label })
              }
            }}
            type="button"
          >
            {tab.label}
          </button>
          )
        })}
      </nav>

      {activeAdminTab === 'analytics' && (
        canUse('analytics') ? (
          <AdminAnalytics
            activityHistory={activityHistory}
            data={analyticsData}
            isHistoryLoading={loadingHistoryUser}
            isInsightsLoading={loadingInsightsUser}
            liveSites={analyticsLiveSites}
            loadActivityHistory={loadActivityHistory}
            loadUserInsights={loadUserInsights}
            onOpenUser={openAnalyticsUser}
            onlineDisplayMode={onlineDisplayMode}
            userInsights={userInsights}
            chartMode={analyticsChartMode}
            range={analyticsRange}
            selectedUser={analyticsUser}
            setChartMode={setAnalyticsChartMode}
            setCustomEnd={setAnalyticsCustomEnd}
            setCustomStart={setAnalyticsCustomStart}
            setRange={setAnalyticsRange}
            setSelectedUser={setAnalyticsUser}
            setUserSort={handleAnalyticsUserSort}
            userSort={analyticsUserSort}
            users={adminData.users || []}
            customEnd={analyticsCustomEnd}
            customStart={analyticsCustomStart}
          />
        ) : (
          <AdminLocked title="Аналитика" />
        )
      )}

      {activeAdminTab === 'users' && (
        <AdminUsersTable
          onOpenUser={openUser}
          users={adminData.users || []}
        />
      )}

      {activeAdminTab === 'user' && selectedUser && (
        <AdminUserDetail
          balanceAmount={balanceAmount}
          canBalance={canUse('balance')}
          canBlock={canUse('block')}
          canTransport={canUse('user_transport')}
          canDelete={canUse('delete_users')}
          canManageAdmins={canManageAdmins}
          canMessage={canUse('message')}
          changeBalance={changeBalance}
          changeTransport={changeSelectedUserTransport}
          deleteUser={deleteSelectedUser}
          isSubmitting={isAdminSubmitting}
          loadReferrals={loadReferrals}
          makeSelectedAdmin={makeSelectedAdmin}
          messageChannel={messageChannel}
          messageSubject={messageSubject}
          messageText={messageText}
          onBack={() => setActiveAdminTab('users')}
          referrals={referrals}
          selectedUser={selectedUser}
          sendPersonalMessage={sendPersonalMessage}
          setBalanceAmount={setBalanceAmount}
          setMessageChannel={setMessageChannel}
          setMessageSubject={setMessageSubject}
          setMessageText={setMessageText}
          toggleBlocked={toggleBlocked}
        />
      )}

      {activeAdminTab === 'support' && (
        canUse('support') ? (
          <AdminSupport
            conversations={adminData.support || []}
            filesCount={supportFiles.length}
            isSubmitting={isAdminSubmitting}
            messages={supportMessages}
            onFileChange={setSupportFiles}
            onOpenConversation={openSupportConversation}
            selected={supportAccount}
            sendSupport={sendAdminSupport}
            setText={setSupportText}
            text={supportText}
          />
        ) : (
          <AdminLocked title="Поддержка" />
        )
      )}

      {activeAdminTab === 'promos' && (
        canUse('promos') ? (
          <AdminPromos
            createPromo={createPromo}
            deletePromo={deletePromo}
            isSubmitting={isAdminSubmitting}
            promoAmount={promoAmountAdmin}
            promoCode={promoCodeAdmin}
            promos={adminData.promos || []}
            setPromoAmount={setPromoAmountAdmin}
            setPromoCode={setPromoCodeAdmin}
          />
        ) : (
          <AdminLocked title="Промокоды" />
        )
      )}

      {activeAdminTab === 'payments' && (
        canUse('payments') ? (
          <AdminPayments payments={adminData.payments || []} />
        ) : (
          <AdminLocked title="Оплаты" />
        )
      )}

      {activeAdminTab === 'nodes' && canUse('nodes') && <AdminNodes getJson={getJson} postJson={postJson} canViewCredentials={canUse('node_credentials')} canManageNetwork={canUse('vpn_config')} />}
      {activeAdminTab === 'technical' && canUse('technical') && <TechnicalLogs getJson={getJson} />}
      {activeAdminTab === 'documentation' && canUse('documentation') && <AdminDocumentation getJson={getJson} />}

      {activeAdminTab === 'broadcast' && (
        canUse('broadcast') ? (
          <AdminBroadcast
            channel={broadcastChannel}
            isSubmitting={isAdminSubmitting}
            onSubmit={sendBroadcast}
            setChannel={setBroadcastChannel}
            setSubject={setBroadcastSubject}
            setText={setBroadcastText}
            subject={broadcastSubject}
            text={broadcastText}
          />
        ) : (
          <AdminLocked title="Сообщения" />
        )
      )}

      {activeAdminTab === 'admins' && (
        canManageAdmins ? (
          <AdminAdmins
            addAdmin={addAdmin}
            adminEmail={adminEmail}
            adminTgId={adminTgId}
            admins={adminData.admins || []}
            isSubmitting={isAdminSubmitting}
            removeAdmin={removeAdmin}
            setAdminEmail={setAdminEmail}
            setAdminTgId={setAdminTgId}
            updatePermissions={updateAdminPermissions}
          />
        ) : (
          <AdminLocked title="Админы" />
        )
      )}

      {activeAdminTab === 'audit' && (
        adminMeta.isOwner ? (
          <AdminAuditLogs
            items={auditLogs?.items || []}
            isLoading={!auditLogs}
            refresh={() => loadAdminAudit().then(setAuditLogs).catch((error) => setNotice(error.message))}
          />
        ) : (
          <AdminLocked title="Логи" />
        )
      )}
    </div>
  )
}

const adminAuditActionLabels = {
  admin_2fa_verified: '2FA подтвержден',
  admin_panel_open: 'Открыл админку',
  admin_tab_open: 'Открыл раздел',
  user_open: 'Открыл профиль пользователя',
  analytics_user_open: 'Открыл пользователя из аналитики',
  analytics_sessions_open: 'Открыл историю активности',
  analytics_insights_open: 'Открыл категории и устройства',
  support_conversation_open: 'Открыл чат поддержки',
  balance_change: 'Изменил баланс',
  user_block: 'Блокировка пользователя',
  user_message: 'Личное сообщение',
  user_delete: 'Удалил пользователя',
  dns_location: 'Сменил локацию',
  user_transport_change: 'Сменил протокол пользователя',
  support_send: 'Ответил в поддержку',
  broadcast_send: 'Массовая рассылка',
  subscription_announcement_change: 'Изменил объявление в приложении',
  admin_add: 'Добавил админа',
  admin_remove: 'Удалил админа',
  admin_permissions: 'Изменил права админа',
  promo_create: 'Создал промокод',
  promo_delete: 'Удалил промокод',
}

const formatAuditSummary = (summary) => {
  if (!summary || typeof summary !== 'object') {
    return ''
  }
  const pairs = Object.entries(summary)
    .filter(([, value]) => value !== '' && value !== null && value !== undefined)
    .slice(0, 6)
  if (!pairs.length) {
    return ''
  }
  return pairs
    .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join(' · ')
}

function AdminAuditLogs({ isLoading, items, refresh }) {
  return (
    <section className="admin-card liquid-card admin-audit-card">
      <div className="admin-audit-head">
        <div>
          <h2>Журнал действий</h2>
          <p>Действия и открытия разделов администраторами.</p>
        </div>
        <button className="icon-button" onClick={refresh} type="button" aria-label="Обновить логи">
          ↻
        </button>
      </div>

      <div className="admin-audit-list">
        {isLoading ? (
          <span className="analytics-live-site-empty">Загружаю...</span>
        ) : items.length ? (
          items.map((item) => {
            const summary = formatAuditSummary(item.summary)
            return (
              <article className="admin-audit-item" key={item.id}>
                <div className="admin-audit-main">
                  <strong>{adminAuditActionLabels[item.action] || item.action}</strong>
                  <span>{item.adminEmail}</span>
                </div>
                <div className="admin-audit-meta">
                  <span>{formatDate(item.createdAt)}</span>
                  {item.target ? <span>{item.target}</span> : null}
                  {item.ip ? <span>{item.ip}</span> : null}
                </div>
                {summary ? <p>{summary}</p> : null}
              </article>
            )
          })
        ) : (
          <span className="analytics-live-site-empty">Логов пока нет.</span>
        )}
      </div>
    </section>
  )
}

function AdminOnlineCard({ displayMode, onOpenUser, online, setDisplayMode }) {
  const users = online?.users || []
  const count = Number(online?.count || 0)
  const panelCount = online?.panels?.length || 0
  const availablePanels = online?.panels?.filter((panel) => panel.ok).length || 0
  const loading = !online || online.loading
  const visibleUsers = users.slice(0, 18)

  return (
    <section className="admin-online-card liquid-card">
      <div className="admin-online-head">
        <div title={panelCount ? `Панелей доступно: ${availablePanels} из ${panelCount}` : undefined}>
          <h2>
            {!loading && !online.error && <span className="online-status-dot" aria-hidden="true" />}
            {loading ? 'Загрузка…' : online.error ? 'Онлайн недоступен' : 'Онлайн'}
            {!loading && !online.error && <span className="online-total-count">{count}</span>}
          </h2>
          <p>
            Активность за 3 минуты
            {online?.stale && !loading ? ' · обновляем' : ''}
          </p>
        </div>
        <div className="online-mode-toggle" aria-label="Отображение онлайн">
          <button
            aria-current={displayMode === 'username' ? 'true' : undefined}
            onClick={() => setDisplayMode('username')}
            type="button"
          >
            Логин
          </button>
          <button
            aria-current={displayMode === 'name' ? 'true' : undefined}
            onClick={() => setDisplayMode('name')}
            type="button"
          >
            Имя
          </button>
        </div>
      </div>
      <div className="online-user-list" aria-live="polite">
        {visibleUsers.length ? (
          visibleUsers.map((user) => {
            const name = displayMode === 'name' ? user.name || user.username : user.username
            const locations = user.locations?.length
              ? user.locations.map((item) => item.code)
              : (user.panels || []).filter((panel) => panel !== 'REMNA')
            const locationDetails = user.locations?.length
              ? user.locations.map((item) => item.activeIps != null
                ? `${item.code}: ${item.activeIps} IP за 3 минуты`
                : `${item.code}: через ноду`).join('; ')
              : locations.join(', ') || 'Локация уточняется'
            const deviceDetails = user.registeredDevices != null
              ? `Устройств с подпиской: ${user.registeredDevices} из ${user.deviceLimit || 3}. Это не число одновременных подключений.`
              : ''
            const details = [name, locationDetails, deviceDetails].filter(Boolean).join('\n')
            return (
            <button
              className="online-user-chip"
              key={user.username}
              onClick={() => onOpenUser(user)}
              title={details}
              aria-label={`Открыть пользователя. ${details}`}
              type="button"
            >
              <span className="online-user-name">{name}</span>
              <small className="online-user-locations">{[...new Set(locations)].join(' · ') || '…'}</small>
              {user.registeredDevices != null && <span className="online-count-badge" title={deviceDetails}>{user.registeredDevices}/{user.deviceLimit || 3}</span>}
            </button>
            )
          })
        ) : (
          <p className="support-empty">
            {loading ? 'Получаем данные...' : online?.error ? 'Не удалось получить онлайн.' : 'Сейчас никого нет.'}
          </p>
        )}
        {users.length > visibleUsers.length && (
          <span className="online-user-chip is-muted">+{users.length - visibleUsers.length}</span>
        )}
      </div>
    </section>
  )
}

const analyticsRanges = [
  ['ten', '10 мин'],
  ['hour', 'Час'],
  ['day', 'День'],
  ['week', 'Неделя'],
  ['month', 'Месяц'],
  ['year', 'Год'],
  ['all', 'Все'],
  ['custom', 'Период'],
]

const analyticsUserSortOptions = [
  ['traffic', 'Трафик ↓'],
  ['name', 'Имя'],
  ['activity', 'Активность ↓'],
  ['requests', 'Запросы ↓'],
  ['recent_hour', 'Были за час'],
]

const paymentRanges = [
  ['week', '7 дней'],
  ['month', 'Месяц'],
  ['year', 'Год'],
  ['custom', 'Период'],
]

function ChartYAxis({ labels }) {
  return (
    <div className="analytics-y-axis" aria-hidden="true">
      {labels.map((label, index) => (
        <span key={`axis-${index}`}>{label}</span>
      ))}
    </div>
  )
}

function ChartGridLines() {
  return (
    <div className="analytics-grid-lines" aria-hidden="true">
      <span />
      <span />
      <span />
    </div>
  )
}

function chartPath(coords) {
  return coords.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
}

function smoothChartPath(coords, height, minY = 0) {
  if (coords.length < 3) {
    return chartPath(coords)
  }

  const clampY = (value) => Math.max(minY, Math.min(height, value))
  let path = `M${coords[0][0].toFixed(1)} ${coords[0][1].toFixed(1)}`

  for (let index = 0; index < coords.length - 1; index += 1) {
    const prev = coords[index - 1] || coords[index]
    const current = coords[index]
    const next = coords[index + 1]
    const after = coords[index + 2] || next
    const control1X = current[0] + (next[0] - prev[0]) / 6
    const control1Y = clampY(current[1] + (next[1] - prev[1]) / 6)
    const control2X = next[0] - (after[0] - current[0]) / 6
    const control2Y = clampY(next[1] - (after[1] - current[1]) / 6)

    path += ` C${control1X.toFixed(1)} ${control1Y.toFixed(1)}, ${control2X.toFixed(1)} ${control2Y.toFixed(1)}, ${next[0].toFixed(1)} ${next[1].toFixed(1)}`
  }

  return path
}

function AnalyticsTrafficChart({ mode = 'bars', series = [] }) {
  const lineWidth = 720
  const lineHeight = 160
  const linePaddingX = 8
  const lineTopInset = 8
  const plotWidth = lineWidth - linePaddingX * 2
  const plotHeight = lineHeight - lineTopInset
  const rawMaxValue = Math.max(
    0,
    ...series.map((point) => Number(point.totalBytes || 0)),
  )
  const maxValue = rawMaxValue || 1
  const axisLabels = rawMaxValue > 0 ? [formatBytes(rawMaxValue), formatBytes(rawMaxValue / 2), '0'] : ['0', '', '']
  const middleIndex = Math.max(0, Math.floor((series.length - 1) / 2))

  const lineCoordsFor = (key) =>
    series.map((point, index) => {
      const x = series.length <= 1
        ? lineWidth / 2
        : linePaddingX + (plotWidth * index) / (series.length - 1)
      const value = Number(point[key] || 0)
      const y = lineTopInset + plotHeight - (plotHeight * value) / maxValue
      return [x, y]
    })

  const areaFor = (coords) => {
    if (!coords.length) {
      return ''
    }
    const baseY = lineHeight
    const first = coords[0]
    const last = coords[coords.length - 1]
    return `${smoothChartPath(coords, lineHeight, lineTopInset)} L${last[0].toFixed(1)} ${baseY} L${first[0].toFixed(1)} ${baseY} Z`
  }

  const totalCoords = lineCoordsFor('totalBytes')

  if (mode === 'bars') {
    return (
      <div className="analytics-traffic-bars" aria-label="График трафика столбиками">
        <ChartYAxis labels={axisLabels} />
        <ChartGridLines />
        <div className="analytics-bars-grid">
          {series.map((point, index) => {
            const totalHeight = Math.max(4, (Number(point.totalBytes || 0) / maxValue) * 100)
            return (
              <div className="analytics-bar-column" key={`${point.label}-${index}`}>
                <div className="analytics-steam-bar">
                  <span
                    className="analytics-steam-bar-fill"
                    style={{ height: `${totalHeight}%` }}
                    title={`${point.label}: ${formatBytes(point.totalBytes)}`}
                  />
                </div>
              </div>
            )
          })}
        </div>
        <div className="analytics-chart-axis">
          <span>{series[0]?.label || ''}</span>
          <span>{series[Math.max(0, Math.floor((series.length - 1) / 2))]?.label || ''}</span>
          <span>{series[series.length - 1]?.label || ''}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="analytics-traffic-chart analytics-line-chart" aria-label="График трафика">
      <ChartYAxis labels={axisLabels} />
      <ChartGridLines />
      <svg role="img" viewBox={`0 0 ${lineWidth} ${lineHeight}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="trafficFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgba(255, 131, 151, 0.28)" />
            <stop offset="100%" stopColor="rgba(255, 131, 151, 0.02)" />
          </linearGradient>
        </defs>
        <path className="analytics-area" d={areaFor(totalCoords)} />
        <path className="analytics-line" d={smoothChartPath(totalCoords, lineHeight, lineTopInset)} />
      </svg>
      <div className="analytics-chart-axis">
        <span>{series[0]?.label || ''}</span>
        <span>{series[middleIndex]?.label || ''}</span>
        <span>{series[series.length - 1]?.label || ''}</span>
      </div>
    </div>
  )
}

function OnlineActivityChart({ mode = 'bars', series = [] }) {
  const lineWidth = 720
  const lineHeight = 160
  const linePaddingX = 8
  const lineTopInset = 8
  const plotWidth = lineWidth - linePaddingX * 2
  const plotHeight = lineHeight - lineTopInset
  const rawMaxActive = Math.max(0, ...series.map((point) => Number(point.activeUsers || 0)))
  const maxActive = rawMaxActive || 1
  const activeMidLabel = rawMaxActive > 1 ? String(Math.round(rawMaxActive / 2)) : ''
  const axisLabels = rawMaxActive > 0 ? [String(rawMaxActive), activeMidLabel, '0'] : ['0', '', '']
  const middleIndex = Math.max(0, Math.floor((series.length - 1) / 2))
  const coords = series.map((point, index) => {
    const x = series.length <= 1
      ? lineWidth / 2
      : linePaddingX + (plotWidth * index) / (series.length - 1)
    const value = Number(point.activeUsers || 0)
    const y = lineTopInset + plotHeight - (plotHeight * value) / maxActive
    return [x, y]
  })
  const path = smoothChartPath(coords, lineHeight, lineTopInset)
  const area = coords.length
    ? `${path} L${coords[coords.length - 1][0].toFixed(1)} ${lineHeight} L${coords[0][0].toFixed(1)} ${lineHeight} Z`
    : ''

  if (mode === 'line') {
    return (
      <div className="analytics-activity-line analytics-line-chart" aria-label="Активность онлайн линией">
        <ChartYAxis labels={axisLabels} />
        <ChartGridLines />
        <svg role="img" viewBox={`0 0 ${lineWidth} ${lineHeight}`} preserveAspectRatio="none">
          <defs>
            <linearGradient id="activityFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="rgba(121, 199, 178, 0.26)" />
              <stop offset="100%" stopColor="rgba(121, 199, 178, 0.02)" />
            </linearGradient>
          </defs>
          <path className="analytics-area is-activity" d={area} />
          <path className="analytics-line is-activity" d={path} />
        </svg>
        <div className="analytics-chart-axis">
          <span>{series[0]?.label || ''}</span>
          <span>{series[middleIndex]?.label || ''}</span>
          <span>{series[series.length - 1]?.label || ''}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="analytics-activity" aria-label="Активность онлайн">
      <ChartYAxis labels={axisLabels} />
      <ChartGridLines />
      <div className="analytics-activity-bars">
        {series.map((point, index) => {
          const activeUsers = Number(point.activeUsers || 0)
          const height = activeUsers ? Math.max(8, (activeUsers / maxActive) * 100) : 0
          return (
            <div
              className="analytics-activity-column"
              key={`${point.label}-activity-${index}`}
              title={`${point.label}: ${activeUsers ? `${activeUsers} онлайн` : 'нет активности'}`}
            >
              <span
                className={activeUsers ? 'is-active' : undefined}
                style={{ height: `${height}%` }}
              />
            </div>
          )
        })}
      </div>
      <div className="analytics-chart-axis">
        <span>{series[0]?.label || ''}</span>
        <span>{series[Math.max(0, Math.floor((series.length - 1) / 2))]?.label || ''}</span>
        <span>{series[series.length - 1]?.label || ''}</span>
      </div>
    </div>
  )
}

function AdminLiveSites({ data, displayMode }) {
  const users = (data?.users || []).filter((user) => user?.username)
  if (!users.length) {
    return null
  }

  return (
    <div className="analytics-live-sites" aria-live="polite">
      <div className="analytics-live-sites-head">
        <div>
          <strong>Недавние подключения</strong>
          <span>{data?.updatedAt ? formatDate(data.updatedAt) : 'обновляем в реальном времени'}</span>
        </div>
        <small>{users.length} онлайн</small>
      </div>
      <div className="analytics-live-sites-list">
        {users.map((user) => {
          const sites = user.sites || []
          const displayName = displayMode === 'username' ? user.username : user.name || user.username
          const visibleSites = sites.slice(0, 8)
          const trackSites = visibleSites
          return (
            <div className="analytics-live-site-row" key={user.username}>
              <div className="analytics-live-site-user">
                <span className="analytics-online-dot is-online" aria-hidden="true" />
                <strong>{displayName}</strong>
              </div>
              {sites.length ? (
                <div className="analytics-live-site-marquee">
                  <div
                    className="analytics-live-site-track"
                    style={{ animationDuration: `${Math.max(22, visibleSites.length * 8)}s` }}
                  >
                    {trackSites.map((site, index) => (
                      <span className="analytics-live-site-chip" key={`${user.username}-${site.name}-${index}`} title={site.domain || site.name}>
                        {site.name}
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                <span className="analytics-live-site-empty">пока только служебные запросы</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function AdminAnalytics({
  activityHistory,
  chartMode,
  customEnd,
  customStart,
  data,
  isHistoryLoading,
  isInsightsLoading,
  liveSites,
  loadActivityHistory,
  loadUserInsights,
  onOpenUser,
  onlineDisplayMode,
  range,
  selectedUser,
  setCustomEnd,
  setCustomStart,
  setChartMode,
  setRange,
  setSelectedUser,
  setUserSort,
  userInsights,
  userSort,
  users,
}) {
  const totals = data?.totals || {}
  const analyticsUsers = useMemo(() => data?.users || [], [data?.users])
  const analyticsNow = useMemo(() => {
    const value = Date.parse(data?.end || '')
    return Number.isFinite(value) ? value : 0
  }, [data?.end])
  const selectableUsers = users.filter((user) => user.username)
  const peakActive = Math.max(0, ...(data?.series || []).map((point) => Number(point.activeUsers || 0)))
  const lastSeen = analyticsUsers
    .map((user) => user.lastSeenAt)
    .filter(Boolean)
    .sort()
    .at(-1)
  const sortedAnalyticsUsers = useMemo(() => {
    const tieByName = (left, right) =>
      String(left.name || left.username || '').localeCompare(String(right.name || right.username || ''), 'ru', {
        sensitivity: 'base',
      })
    const timestamp = (user) => {
      const value = Date.parse(user.lastSeenAt || '')
      return Number.isFinite(value) ? value : 0
    }
    const wasOnlineLastHour = (user) => {
      const value = timestamp(user)
      return value > 0 && analyticsNow > 0 && analyticsNow - value <= 60 * 60 * 1000
    }
    return [...analyticsUsers].sort((left, right) => {
      if (userSort === 'name') {
        return tieByName(left, right)
      }
      if (userSort === 'activity') {
        return (Number(right.onlineSeconds || 0) - Number(left.onlineSeconds || 0)) || tieByName(left, right)
      }
      if (userSort === 'requests') {
        return (Number(right.requestCount || 0) - Number(left.requestCount || 0)) || tieByName(left, right)
      }
      if (userSort === 'recent_hour') {
        const leftRecent = wasOnlineLastHour(left)
        const rightRecent = wasOnlineLastHour(right)
        if (leftRecent !== rightRecent) {
          return leftRecent ? -1 : 1
        }
        return (timestamp(right) - timestamp(left)) || tieByName(left, right)
      }
      return (Number(right.totalBytes || 0) - Number(left.totalBytes || 0)) || tieByName(left, right)
    })
  }, [analyticsNow, analyticsUsers, userSort])

  return (
    <section className="admin-card liquid-card analytics-section">
      <div className="panel-heading compact-heading">
        <h2>Аналитика</h2>
        <p>Трафик и активность поступают с VPN-нод. Подключения — за 7 дней, суточные счётчики — за 90 дней.</p>
      </div>

      <div className="analytics-controls">
        <div className="analytics-range">
          {analyticsRanges.map(([id, label]) => (
            <button
              aria-current={range === id ? 'true' : undefined}
              key={id}
              onClick={() => setRange(id)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
        <select
          className="admin-mini-select"
          onChange={(event) => setSelectedUser(event.target.value)}
          value={selectedUser}
        >
          <option value="all">Все пользователи</option>
          {selectableUsers.map((user) => (
            <option key={user.username || user.tgId} value={normalizeAdminUsername(user.username)}>
              {user.name || user.username}
            </option>
          ))}
        </select>
        <div className="analytics-chart-mode" aria-label="Вид графиков">
          <button
            aria-current={chartMode === 'bars' ? 'true' : undefined}
            onClick={() => setChartMode('bars')}
            type="button"
          >
            Столбики
          </button>
          <button
            aria-current={chartMode === 'line' ? 'true' : undefined}
            onClick={() => setChartMode('line')}
            type="button"
          >
            Линия
          </button>
        </div>
      </div>

      {range === 'custom' && (
        <div className="analytics-custom-row">
          <input
            className="glass-input"
            onChange={(event) => setCustomStart(event.target.value)}
            type="datetime-local"
            value={customStart}
          />
          <input
            className="glass-input"
            onChange={(event) => setCustomEnd(event.target.value)}
            type="datetime-local"
            value={customEnd}
          />
        </div>
      )}

      <div className="analytics-kpis">
        <div>
          <span>Трафик</span>
          <strong>{formatBytes(totals.totalBytes)}</strong>
        </div>
        <div>
          <span>Среднее в день</span>
          <strong>{formatBytes(totals.avgBytesPerDay)}</strong>
        </div>
        <div>
          <span>Онлайн</span>
          <strong>{formatDuration(totals.onlineSeconds)}</strong>
        </div>
        <div>
          <span>Пик онлайн</span>
          <strong>{peakActive || 'нет'}</strong>
        </div>
      </div>

      <div className="analytics-chart-grid">
        <div className="analytics-panel">
          <div className="analytics-panel-head">
            <h3>Трафик</h3>
            <small>{range === 'ten' ? 'последние 10 минут' : 'за выбранный период'}</small>
          </div>
          <AnalyticsTrafficChart mode={chartMode} series={data?.series || []} />
        </div>
        <div className="analytics-panel">
          <div className="analytics-panel-head">
            <h3>Активность</h3>
            <small>{lastSeen ? `последняя ${formatDate(lastSeen)}` : 'короткие разрывы сглаживаются'}</small>
          </div>
          <OnlineActivityChart mode={chartMode} series={data?.series || []} />
        </div>
      </div>

      <AdminLiveSites data={liveSites} displayMode={onlineDisplayMode} />

      <div className="analytics-user-list-head">
        <div>
          <strong>Пользователи</strong>
          <span>{sortedAnalyticsUsers.length ? `${sortedAnalyticsUsers.length} в выборке` : 'данные собираются'}</span>
        </div>
        <select
          aria-label="Сортировка пользователей аналитики"
          className="admin-mini-select analytics-sort-select"
          onChange={(event) => setUserSort(event.target.value)}
          value={userSort}
        >
          {analyticsUserSortOptions.map(([id, label]) => (
            <option key={id} value={id}>{label}</option>
          ))}
        </select>
      </div>

      <div className="analytics-user-list">
        {sortedAnalyticsUsers.length ? (
          sortedAnalyticsUsers.map((user) => {
            const username = normalizeAdminUsername(user.username)
            const history = activityHistory?.[username]
            const insights = userInsights?.[username]
            return (
              <article className="analytics-user-row" key={user.username}>
                <div className="analytics-user-main">
                  <button className="analytics-user-open" onClick={() => onOpenUser(user)} type="button">
                    <strong>
                      <span
                        className={user.onlineNow ? 'analytics-online-dot is-online' : 'analytics-online-dot'}
                        title={user.onlineNow ? 'Сейчас в сети' : 'Сейчас не в сети'}
                      />
                      <span className="analytics-user-name-text">{user.name || user.username}</span>
                    </strong>
                    <span>@{user.username}</span>
                  </button>
                  <div className="analytics-user-buttons">
                    <button
                      aria-label={`Показать историю активности ${user.name || user.username}`}
                      className="analytics-user-history-button"
                      disabled={isHistoryLoading === username}
                      onClick={() => loadActivityHistory(user)}
                      title="История входов в сеть"
                      type="button"
                    >
                      {history ? '×' : '≡'}
                    </button>
                    <button
                      aria-label={`Показать аналитику ${user.name || user.username}`}
                      className="analytics-user-chart-button"
                      onClick={() => {
                        setSelectedUser(username)
                        window.setTimeout(() => {
                          document.querySelector('.analytics-chart-grid')?.scrollIntoView({ block: 'start' })
                        }, 0)
                      }}
                      title="Показать графики по этому пользователю"
                      type="button"
                    >
                      <span aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                    </button>
                    <button
                      aria-label={`Показать профиль запросов ${user.name || user.username}`}
                      className={`analytics-user-insights-button ${insights ? 'is-active' : ''}`}
                      disabled={isInsightsLoading === username}
                      onClick={() => loadUserInsights(user)}
                      title="Устройство, IP, сайты и категории"
                      type="button"
                    >
                      <span className="analytics-user-insights-icon" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                        <i />
                      </span>
                    </button>
                  </div>
                </div>
                <span
                  data-label="Период"
                  title="Трафик за выбранный период"
                >
                  {formatBytes(user.totalBytes)}
                </span>
                <span
                  data-label="Весь трафик"
                  title="Суммарный трафик клиента за все время по данным Remnawave"
                >
                  {formatBytes(user.allTimeBytes)}
                </span>
                <span
                  data-label="Среднее потребление"
                  title="Средний расход трафика в день за выбранный период"
                >
                  {formatBytes(user.avgBytesPerDay)}/день
                </span>
                <span
                  data-label="Онлайн"
                  title="Сколько времени пользователь был онлайн за выбранный период"
                >
                  {formatDuration(user.onlineSeconds)}
                </span>
                <span
                  data-label="Запросы"
                  title="Количество запросов за выбранный период"
                >
                  {formatNumber(user.requestCount)}
                </span>
                <span
                  data-label="Последний раз в сети"
                  title="Когда пользователь последний раз был онлайн"
                >
                  {user.lastSeenAt ? formatDate(user.lastSeenAt) : 'не был в сети'}
                </span>
                {history && <AdminActivityHistory history={history} />}
                {insights && <AdminUserInsights insights={insights} />}
              </article>
            )
          })
        ) : (
          <p className="support-empty">Данные еще собираются. Первый снимок появится в течение минуты.</p>
        )}
      </div>
    </section>
  )
}

function AdminActivityHistory({ history }) {
  const items = history?.items || []
  return (
    <div className="analytics-history">
      <div className="analytics-history-head">
        <strong>История активности</strong>
        <span>{history?.total || 0} входов</span>
      </div>
      {items.length ? (
        <div className="analytics-history-list">
          {items.map((item) => (
            <div className="analytics-history-item" key={`${item.startedAt}-${item.endedAt}`}>
              <span>{formatDate(item.startedAt)}</span>
              <span>{formatDuration(item.durationSeconds)}</span>
              <span>{item.maxClients || 1} кл.</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="support-empty">Входов в сеть пока не зафиксировано.</p>
      )}
    </div>
  )
}

function AdminUserInsights({ insights }) {
  const [selectedCategory, setSelectedCategory] = useState('')
  const [categoryMode, setCategoryMode] = useState('sites')
  const [categoryHistory, setCategoryHistory] = useState(null)
  const [categoryHistoryLoading, setCategoryHistoryLoading] = useState(false)
  const [categoryHistoryError, setCategoryHistoryError] = useState('')
  const [siteSearch, setSiteSearch] = useState('')
  const [siteHistory, setSiteHistory] = useState(null)
  const [siteHistoryTitle, setSiteHistoryTitle] = useState('')
  const [siteHistoryLoading, setSiteHistoryLoading] = useState(false)
  const [siteHistoryError, setSiteHistoryError] = useState('')
  const categories = insights?.categories || []
  const activeCategory = categories.find((item) => item.name === selectedCategory)
  const maxTopCount = Math.max(1, ...(insights?.topSites || []).map((site) => Number(site.count || 0)))
  const accountDevices = insights?.accountDevices
  const observedDevices = accountDevices?.devices || []
  const devices = observedDevices.length ? observedDevices : insights?.device?.devices?.length ? insights.device.devices : (insights?.device ? [insights.device] : [])
  const networkKind = insights?.network?.kind === 'mobile'
    ? 'мобильная сеть'
    : insights?.network?.kind === 'home'
      ? 'фиксированный интернет · по оператору'
      : 'тип подключения не раскрывается оператором'
  const username = insights?.username || ''
  const loadSiteHistory = useCallback(async ({ query = '', domain = '', title = '' }) => {
    if (!username) {
      return
    }
    const normalizedQuery = query.trim()
    const normalizedDomain = domain.trim()
    if (!normalizedQuery && !normalizedDomain) {
      setSiteHistory(null)
      setSiteHistoryTitle('')
      return
    }
    setSiteHistoryLoading(true)
    setSiteHistoryError('')
    try {
      const params = new URLSearchParams({ username })
      if (normalizedDomain) {
        params.set('domain', normalizedDomain)
      } else {
        params.set('q', normalizedQuery)
      }
      const data = await getJson(`/api/admin/analytics/site-history?${params.toString()}`)
      setSiteHistory(data)
      setSiteHistoryTitle(title || normalizedQuery || normalizedDomain)
    } catch (error) {
      setSiteHistoryError(error.message || 'Не удалось загрузить историю сайта.')
    } finally {
      setSiteHistoryLoading(false)
    }
  }, [username])
  const loadCategoryHistory = useCallback(async (category) => {
    if (!username || !category) {
      setCategoryHistory(null)
      return
    }
    setCategoryHistoryLoading(true)
    setCategoryHistoryError('')
    try {
      const params = new URLSearchParams({ username, category })
      const data = await getJson(`/api/admin/analytics/site-history?${params.toString()}`)
      setCategoryHistory(data)
    } catch (error) {
      setCategoryHistoryError(error.message || 'Не удалось загрузить время категории.')
    } finally {
      setCategoryHistoryLoading(false)
    }
  }, [username])

  useEffect(() => {
    const query = siteSearch.trim()
    if (query.length < 2) {
      return undefined
    }
    const timer = window.setTimeout(() => {
      loadSiteHistory({ query, title: query })
    }, 350)
    return () => window.clearTimeout(timer)
  }, [siteSearch, loadSiteHistory])

  const activeCategoryName = activeCategory?.name || ''

  useEffect(() => {
    if (categoryMode !== 'time' || !activeCategoryName) {
      return
    }
    const timer = window.setTimeout(() => {
      loadCategoryHistory(activeCategoryName)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [activeCategoryName, categoryMode, loadCategoryHistory])

  return (
    <div className="analytics-insights">
      <div className="analytics-insights-grid">
        <div className="analytics-insight-card">
          <span>Устройства</span>
          <div className={`analytics-device-list${observedDevices.length ? ' has-observed-devices' : ''}`}>
            {devices.length ? (
              devices.map((device) => (
                <div className="analytics-device-item" key={`${device.family}-${device.label}`}>
                  <strong>{device.label || 'Не определено'}</strong>
                  <small>{observedDevices.length ? (device.source === 'site_and_miniapp' ? 'Сайт и Telegram Mini App' : device.source === 'miniapp' ? 'Telegram Mini App' : 'Вход на сайт') : device.confidence ? `${device.confidence}%` : 'мало сигналов'}</small>
                </div>
              ))
            ) : (
              <strong>Не определено</strong>
            )}
          </div>
          {observedDevices.length ? <small>Последний вход: {formatDate(observedDevices[0].lastSeenAt)}</small> : devices[0]?.signals?.length ? <small>{devices[0].signals.slice(0, 3).join(' · ')}</small> : <small>Тип устройства появится после входа в кабинет</small>}
          {accountDevices && <small>{accountDevices.transport === 'xhttp' ? 'XHTTP' : 'TCP/RAW'} · {accountDevices.manual ? 'ручной выбор · автоматика отключена' : accountDevices.autoDueAt ? `автосмена ${formatDate(accountDevices.autoDueAt)} МСК` : accountDevices.selectionSource === 'auto' ? 'выбрано автоматически' : 'по умолчанию'}</small>}
        </div>
        <div className="analytics-insight-card">
          <span>Оператор</span>
          <strong>{insights?.network?.isp || insights?.network?.org || 'Уточняем оператора'}</strong>
          <small>
            {[insights?.network?.city, insights?.network?.country].filter(Boolean).join(', ') || insights?.network?.ip || 'нет IP'}
          </small>
          <small>{networkKind}{insights?.network?.asn ? ` · AS${String(insights.network.asn).replace(/^AS/i,'')}` : ''}</small>
        </div>
      </div>

      <details className="analytics-insight-details">
        <summary>История IP</summary>
        <div className="analytics-ip-list">
          {(insights?.ipHistory || []).map((item) => (
            <div className="analytics-ip-item" key={`${item.ip}-${item.changedAt}`}>
              <strong>{item.ip}</strong>
              <span>{[item.geo?.city, item.geo?.country].filter(Boolean).join(', ') || 'гео не найдено'}</span>
              <span>{item.geo?.isp || item.geo?.org || 'оператор не найден'}</span>
              <small>{formatDate(item.changedAt)}</small>
            </div>
          ))}
        </div>
      </details>

      <div className="analytics-top-sites">
        <div className="analytics-insights-head">
          <strong>Топ сайтов</strong>
          <span>{insights?.totalEvents || 0} запросов</span>
        </div>
        {(insights?.topSites || []).slice(0, 12).map((site, index) => (
          <div className="analytics-top-site" key={`${site.name}-${site.domain}`}>
            <i>{index + 1}</i>
            <span>{site.name}</span>
            <strong>{site.count}</strong>
            <em style={{ width: `${Math.max(8, (Number(site.count || 0) / maxTopCount) * 100)}%` }} />
          </div>
        ))}
      </div>

      <div className="analytics-category-block">
        <div className="analytics-insights-head">
          <strong>Категории</strong>
          <span>нажмите, чтобы раскрыть сайты</span>
        </div>
        <AnalyticsCategoryDonut categories={categories} />
        <div className="analytics-category-list">
          {categories.map((category) => (
            <button
              aria-current={selectedCategory === category.name ? 'true' : undefined}
              key={category.name}
              onClick={() => {
                setSelectedCategory(selectedCategory === category.name ? '' : category.name)
                setCategoryHistory(null)
                setCategoryHistoryError('')
              }}
              type="button"
            >
              <span>{category.name}</span>
              <strong>{category.count}</strong>
            </button>
          ))}
        </div>
        {activeCategory && (
          <div className="analytics-category-details">
            <div className="analytics-category-mode" aria-label="Режим просмотра категории">
              <button
                aria-current={categoryMode === 'sites' ? 'true' : undefined}
                onClick={() => setCategoryMode('sites')}
                type="button"
              >
                Сайты
              </button>
              <button
                aria-current={categoryMode === 'time' ? 'true' : undefined}
                onClick={() => setCategoryMode('time')}
                type="button"
              >
                Время
              </button>
            </div>
            {categoryMode === 'sites' ? (
              <div className="analytics-category-sites">
                {activeCategory.sites.slice(0, 24).map((site) => (
                  <button
                    key={`${activeCategory.name}-${site.name}`}
                    onClick={() => loadSiteHistory({ domain: site.domain, title: site.name })}
                    type="button"
                  >
                    {site.name}
                    <strong>{site.count}</strong>
                  </button>
                ))}
              </div>
            ) : (
              <div className="analytics-category-time">
                {categoryHistoryLoading && <span className="analytics-live-site-empty">Собираю периоды...</span>}
                {categoryHistoryError && <span className="analytics-live-site-empty">{categoryHistoryError}</span>}
                {!categoryHistoryLoading && !categoryHistoryError && categoryHistory?.sessions?.length ? (
                  categoryHistory.sessions.map((session) => (
                    <div className="analytics-category-session" key={`${activeCategory.name}-${session.startAt}-${session.endAt}`}>
                      <strong>{formatDate(session.startAt)}</strong>
                      <span>
                        {session.endAt && session.endAt !== session.startAt ? `до ${formatDate(session.endAt)}` : 'один запрос'} · {session.count} запросов
                      </span>
                      <small>{(session.sites || []).slice(0, 5).map((site) => site.name).join(' · ')}</small>
                    </div>
                  ))
                ) : null}
                {!categoryHistoryLoading && !categoryHistoryError && categoryHistory && !categoryHistory.sessions?.length && (
                  <span className="analytics-live-site-empty">Периодов по этой категории нет.</span>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="analytics-timeline">
        <div className="analytics-insights-head">
          <strong>Поиск по сайтам</strong>
          <span>{siteHistory?.total ? `${siteHistory.total} совпадений за все время` : 'по всей истории'}</span>
        </div>
        <div className="analytics-site-search">
          <input
            className="glass-input"
            onChange={(event) => {
              const value = event.target.value
              setSiteSearch(value)
              if (!value.trim()) {
                setSiteHistory(null)
                setSiteHistoryTitle('')
                setSiteHistoryError('')
              }
            }}
            placeholder="Введите сайт, например github"
            type="search"
            value={siteSearch}
          />
        </div>
        <div className="analytics-search-results">
          {siteHistoryLoading && <span className="analytics-live-site-empty">Ищу по всей истории...</span>}
          {siteHistoryError && <span className="analytics-live-site-empty">{siteHistoryError}</span>}
          {!siteHistoryLoading && !siteHistoryError && siteHistory?.items?.length ? (
            <>
              <div className="analytics-search-summary">
                <strong>{siteHistoryTitle}</strong>
                <span>показаны последние {siteHistory.items.length} записей</span>
              </div>
              {siteHistory.items.map((item, index) => (
                <div className="analytics-search-result" key={`${item.time}-${item.domain}-${item.ip}-${index}`}>
                  <strong>{item.site}</strong>
                  <span>{item.domain || 'домен не найден'}</span>
                  <small>{formatDate(item.time)} · {item.ip || 'IP не найден'}</small>
                </div>
              ))}
            </>
          ) : null}
          {!siteHistoryLoading && !siteHistoryError && siteHistory && !siteHistory.items?.length && (
            <span className="analytics-live-site-empty">Ничего не найдено.</span>
          )}
        </div>
      </div>
    </div>
  )
}

function AnalyticsCategoryDonut({ categories }) {
  const total = categories.reduce((sum, category) => sum + Number(category.count || 0), 0)
  if (!total) {
    return null
  }
  const colors = [
    '#ff8397',
    '#8ba8e8',
    '#79c7b2',
    '#d9b184',
    '#b7a0df',
    '#7dc4d5',
    '#c9c17a',
    '#d58e9c',
    '#92bac7',
    '#a2c296',
    '#c59ac7',
    '#999dde',
  ]
  const segments = categories.slice(0, 10).reduce((acc, category, index) => {
    const start = acc.cursor
    const size = (Number(category.count || 0) / total) * 100
    const end = start + size
    return {
      cursor: end,
      values: [...acc.values, `${colors[index % colors.length]} ${start}% ${end}%`],
    }
  }, { cursor: 0, values: [] })
  const gradient = segments.values.join(', ')
  return (
    <div className="analytics-category-donut-row">
      <div className="analytics-category-donut" style={{ background: `conic-gradient(${gradient})` }}>
        <span>{total}</span>
      </div>
      <div className="analytics-category-donut-legend">
        {categories.slice(0, 8).map((category, index) => (
          <span key={`donut-${category.name}`}>
            <i style={{ backgroundColor: colors[index % colors.length] }} />
            {category.name}
            <strong>{Math.round((Number(category.count || 0) / total) * 100)}%</strong>
          </span>
        ))}
      </div>
    </div>
  )
}

function AdminUsersTable({ users, onOpenUser }) {
  const activeSubscriptions = users.reduce(
    (count, user) => count + (getExpiryStatus(user.expiresAt).active ? 1 : 0),
    0,
  )

  return (
    <section className="admin-card liquid-card">
      <div className="panel-heading compact-heading">
        <h2>Пользователи</h2>
        <p>Данные из Telegram-бота и web-аккаунтов без UUID клиентов.</p>
      </div>
      <div className="admin-users-summary" aria-label="Активные подписки">
        <span>Активных подписок</span>
        <strong>{formatNumber(activeSubscriptions)}</strong>
      </div>
      <div className="admin-table-wrap">
        <table className="admin-table admin-users-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Имя</th>
              <th>Username</th>
              <th>TG ID</th>
              <th>Почта</th>
              <th>Тариф</th>
              <th>Начало</th>
              <th>Дата окончания</th>
              <th>Баланс</th>
              <th>Статус</th>
              <th aria-label="Действия" />
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const expiry = getExpiryStatus(user.expiresAt)
              const subscriptionStatus = getUserSubscriptionStatus(user)
              return (
                <tr
                  className={`admin-click-row admin-subscription-row ${subscriptionStatus.className}`}
                  key={`${user.tgId}-${user.email}-${user.number}`}
                  onClick={() => onOpenUser(user)}
                >
                  <td>{user.number}</td>
                  <td>{user.name || '—'}</td>
                  <td>{user.username ? `@${user.username}` : '—'}</td>
                  <td>{user.tgId || '—'}</td>
                  <td className="admin-email-cell">{user.email || '—'}</td>
                  <td>{user.tariff}</td>
                  <td>{formatDate(user.startedAt)}</td>
                  <td>
                    <span
                      className={`admin-expiry-badge ${expiry.className}`}
                      title={expiry.title}
                    >
                      {expiry.label}
                    </span>
                  </td>
                  <td>{user.balanceRub} ₽</td>
                  <td>
                    <span className={`admin-subscription-status ${subscriptionStatus.className}`}>
                      {subscriptionStatus.label}
                    </span>
                  </td>
                  <td>
                    <button
                      className="admin-dot-button"
                      onClick={(event) => {
                        event.stopPropagation()
                        onOpenUser(user)
                      }}
                      aria-label="Открыть пользователя"
                      type="button"
                    >
                      ⋮
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function AdminUserDetail({
  balanceAmount,
  canBalance,
  canBlock,
  canTransport,
  canDelete,
  canManageAdmins,
  canMessage,
  changeBalance,
  changeTransport,
  deleteUser,
  isSubmitting,
  loadReferrals,
  makeSelectedAdmin,
  messageChannel,
  messageSubject,
  messageText,
  onBack,
  referrals,
  selectedUser,
  sendPersonalMessage,
  setBalanceAmount,
  setMessageChannel,
  setMessageSubject,
  setMessageText,
  toggleBlocked,
}) {
  return (
    <section className="admin-user-detail liquid-card" id="admin-user-detail">
      <div className="admin-detail-head">
        <button
          aria-label="Назад"
          className="icon-button"
          onClick={onBack}
          type="button"
        >
          ‹
        </button>
        <div>
          <span className="eyebrow">Пользователь</span>
          <h3>{selectedUser.name || selectedUser.email || selectedUser.tgId}</h3>
          <p>{selectedUser.email || 'Почта не привязана'}</p>
        </div>
      </div>

      <div className="admin-user-facts">
        <span>TG ID: {selectedUser.tgId || 'нет'}</span>
        <span>Username: {selectedUser.username ? `@${selectedUser.username}` : 'нет'}</span>
        <span>Тариф: {selectedUser.tariff}</span>
        <span>Начало: {formatDate(selectedUser.startedAt)}</span>
        <span>Конец: {formatDate(selectedUser.expiresAt)}</span>
        <span>Баланс: {selectedUser.balanceRub} ₽</span>
        <span>Статус: {selectedUser.blocked ? 'заблокирован' : 'активен'}</span>
        <span>Протокол: {selectedUser.transport === 'xhttp' ? 'VLESS · XHTTP' : 'VLESS · TCP/RAW'}</span>
      </div>

      <div className={`admin-action-grid admin-user-transport ${canTransport ? '' : 'is-disabled'}`}>
        <span className="field-label">Способ подключения</span>
        <div className="admin-user-transport-options">
          {[
            ['raw', 'TCP/RAW'],
            ['xhttp', 'XHTTP'],
          ].map(([value, label]) => (
            <button
              aria-current={(selectedUser.transport || 'raw') === value ? 'true' : undefined}
              className="admin-location-button"
              disabled={isSubmitting || !canTransport || !selectedUser.tgId || (value === 'xhttp' && !selectedUser.xhttpAvailable)}
              key={value}
              onClick={() => changeTransport(value)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
        <small>{selectedUser.transportManual ? 'Ручной выбор · автоматика отключена' : selectedUser.transportAutoCompleted ? 'Автовыбор уже выполнен · повторов не будет' : 'По умолчанию · возможен один автоматический выбор'}</small>
        <small>{canTransport ? 'Выбор относится ко всем устройствам пользователя и отключает автоматическую смену. После сохранения нужно обновить подписку в приложении.' : 'Нужно право «Протоколы пользователей». Его выдаёт владелец в разделе «Админы».'}</small>
      </div>

      <div className={`admin-action-grid ${canBalance ? '' : 'is-disabled'}`}>
        <label className="field-label">
          Сумма
          <span className="admin-balance-row">
            <button
              aria-label="Отнять"
              className="icon-button"
              disabled={isSubmitting || !canBalance}
              onClick={() => changeBalance(-1)}
              type="button"
            >
              −
            </button>
            <input
              className="glass-input"
              disabled={!canBalance}
              inputMode="numeric"
              onChange={(event) => setBalanceAmount(event.target.value)}
              placeholder="300"
              value={balanceAmount}
            />
            <button
              aria-label="Добавить"
              className="icon-button"
              disabled={isSubmitting || !canBalance}
              onClick={() => changeBalance(1)}
              type="button"
            >
              +
            </button>
          </span>
        </label>
      </div>

      <div className={`admin-action-grid ${canMessage ? '' : 'is-disabled'}`}>
        <label className="field-label">
          Канал
          <select
            className="glass-input"
            disabled={!canMessage}
            onChange={(event) => setMessageChannel(event.target.value)}
            value={messageChannel}
          >
            <option disabled={!selectedUser.canMessageTelegram} value="telegram">
              Telegram
            </option>
            <option disabled={!selectedUser.canMessageEmail} value="email">
              Почта
            </option>
          </select>
        </label>
        {messageChannel === 'email' && (
          <label className="field-label">
            Тема письма
            <input
              className="glass-input"
              disabled={!canMessage}
              onChange={(event) => setMessageSubject(event.target.value)}
              value={messageSubject}
            />
          </label>
        )}
        <label className="field-label">
          Сообщение
          <textarea
            className="glass-input support-input"
            disabled={!canMessage}
            onChange={(event) => setMessageText(event.target.value)}
            placeholder="Текст личного сообщения"
            value={messageText}
          />
        </label>
        <button
          className="primary-button"
          disabled={isSubmitting || !canMessage}
          onClick={sendPersonalMessage}
          type="button"
        >
          Отправить
        </button>
      </div>

      <div className="admin-inline-actions">
        <button
          className="secondary-button"
          disabled={isSubmitting || !canBlock}
          onClick={toggleBlocked}
          type="button"
        >
          {selectedUser.blocked ? 'Разблокировать' : 'Заблокировать'}
        </button>
        <button
          className="secondary-button"
          disabled={isSubmitting}
          onClick={loadReferrals}
          type="button"
        >
          Рефералы
        </button>
        <button
          className="secondary-button"
          disabled={
            isSubmitting ||
            !canManageAdmins ||
            (!selectedUser.email && !selectedUser.tgId)
          }
          onClick={makeSelectedAdmin}
          type="button"
        >
          Сделать админом
        </button>
        <button
          className="secondary-button danger-button"
          disabled={isSubmitting || !canDelete}
          onClick={deleteUser}
          type="button"
        >
          Удалить
        </button>
      </div>

      {referrals && <AdminReferralBox referrals={referrals} />}
    </section>
  )
}

function AdminReferralBox({ referrals }) {
  return (
    <div className="admin-referrals">
      <h3>Рефералы</h3>
      <p>Код: {referrals.ownCode || 'нет кода'}</p>
      {referrals.invitedBy && (
        <p>
          Приглашён: {referrals.invitedBy.name || referrals.invitedBy.tgId} ·{' '}
          {referrals.invitedBy.code}
        </p>
      )}
      {referrals.invited.length ? (
        <ul>
          {referrals.invited.map((item) => (
            <li key={item.tgId}>
              {item.name || item.tgId} · {item.username ? `@${item.username}` : 'без username'} ·{' '}
              {referralStatus[item.rewarded] || 'Неизвестно'}
            </li>
          ))}
        </ul>
      ) : (
        <p>Приглашённых нет.</p>
      )}
    </div>
  )
}

function AdminPromos({
  createPromo,
  deletePromo,
  isSubmitting,
  promoAmount,
  promoCode,
  promos,
  setPromoAmount,
  setPromoCode,
}) {
  return (
    <section className="admin-card liquid-card">
      <div className="panel-heading compact-heading">
        <h2>Промокоды</h2>
        <p>Создание и список текущих кодов из promos.db.</p>
      </div>
      <form className="admin-form-row" onSubmit={createPromo}>
        <input
          className="glass-input"
          onChange={(event) => setPromoCode(event.target.value)}
          placeholder="Код или пусто для авто"
          value={promoCode}
        />
        <input
          className="glass-input"
          inputMode="numeric"
          onChange={(event) => setPromoAmount(event.target.value)}
          placeholder="Сумма"
          value={promoAmount}
        />
        <button className="primary-button" disabled={isSubmitting} type="submit">
          Создать
        </button>
      </form>
      <AdminSimpleTable
        columns={['Код', 'Сумма', 'Создан', 'Использован', '']}
        rows={promos.map((promo) => [
          promo.code,
          `${promo.amount} ₽`,
          formatDate(promo.createdAt),
          promo.usedAt ? formatDate(promo.usedAt) : 'нет',
          <button
            className="admin-link-button"
            disabled={isSubmitting}
            key={promo.code}
            onClick={() => deletePromo(promo.code)}
            type="button"
          >
            Удалить
          </button>,
        ])}
      />
    </section>
  )
}

function AdminPayments({ payments }) {
  const [paymentRange, setPaymentRange] = useState('week')
  const [paymentCustomStart, setPaymentCustomStart] = useState('')
  const [paymentCustomEnd, setPaymentCustomEnd] = useState('')
  const [paymentAnalytics, setPaymentAnalytics] = useState(null)
  const [paymentError, setPaymentError] = useState('')

  useEffect(() => {
    let alive = true
    const params = new URLSearchParams({ range: paymentRange })
    if (paymentRange === 'custom') {
      if (paymentCustomStart) {
        params.set('start', new Date(paymentCustomStart).toISOString())
      }
      if (paymentCustomEnd) {
        params.set('end', new Date(paymentCustomEnd).toISOString())
      }
    }
    getJson(`/api/admin/payments/analytics?${params.toString()}`)
      .then((data) => {
        if (alive) {
          setPaymentAnalytics(data)
          setPaymentError('')
        }
      })
      .catch((error) => {
        if (alive) {
          setPaymentError(error.message)
        }
      })
    return () => {
      alive = false
    }
  }, [paymentCustomEnd, paymentCustomStart, paymentRange])

  return (
    <section className="admin-card liquid-card">
      <div className="panel-heading compact-heading">
        <h2>Оплаты</h2>
        <p>Успешные платежи и последние записи из основной БД.</p>
      </div>
      <div className="payment-analytics">
        <div className="payment-analytics-head">
          <div>
            <span>Оплачено за период</span>
            <strong>{formatRub(paymentAnalytics?.totalAmount)}</strong>
            <small>{paymentAnalytics?.totalCount || 0} успешных платежей</small>
          </div>
          <div className="analytics-range payment-range" aria-label="Период оплат">
            {paymentRanges.map(([id, label]) => (
              <button
                aria-current={paymentRange === id ? 'true' : undefined}
                key={id}
                onClick={() => setPaymentRange(id)}
                type="button"
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {paymentRange === 'custom' && (
          <div className="analytics-custom-row">
            <input
              className="glass-input"
              onChange={(event) => setPaymentCustomStart(event.target.value)}
              type="datetime-local"
              value={paymentCustomStart}
            />
            <input
              className="glass-input"
              onChange={(event) => setPaymentCustomEnd(event.target.value)}
              type="datetime-local"
              value={paymentCustomEnd}
            />
          </div>
        )}
        <PaymentRevenueChart series={paymentAnalytics?.series || []} />
        {paymentError && <p className="support-error">{paymentError}</p>}
      </div>
      <AdminSimpleTable
        columns={['ID платежа', 'TG ID', 'Пользователь', 'Почта', 'Сумма', 'Статус', 'Создан']}
        rows={payments.map((payment) => [
          payment.paymentId,
          payment.tgId || '—',
          payment.name || (payment.username ? `@${payment.username}` : '—'),
          payment.email || '—',
          `${payment.amount} ₽`,
          payment.status || '—',
          formatDate(payment.createdAt),
        ])}
      />
    </section>
  )
}

function PaymentRevenueChart({ series = [] }) {
  const rawMaxValue = Math.max(0, ...series.map((point) => Number(point.amount || 0)))
  const maxValue = rawMaxValue || 1
  const axisLabels = rawMaxValue > 0 ? [formatRub(rawMaxValue), formatRub(rawMaxValue / 2), '0'] : ['0', '', '']
  const middleIndex = Math.max(0, Math.floor((series.length - 1) / 2))

  return (
    <div className="analytics-traffic-bars payment-chart" aria-label="График успешных оплат по дням">
      <ChartYAxis labels={axisLabels} />
      <ChartGridLines />
      <div className="analytics-bars-grid">
        {series.map((point, index) => {
          const height = Math.max(4, (Number(point.amount || 0) / maxValue) * 100)
          return (
            <div className="analytics-bar-column" key={`${point.label}-${index}`}>
              <div className="analytics-steam-bar">
                <span
                  className="analytics-steam-bar-fill"
                  style={{ height: `${height}%` }}
                  title={`${point.label}: ${formatRub(point.amount)}, платежей: ${point.count || 0}`}
                />
              </div>
            </div>
          )
        })}
      </div>
      <div className="analytics-chart-axis">
        <span>{series[0]?.label || ''}</span>
        <span>{series[middleIndex]?.label || ''}</span>
        <span>{series[series.length - 1]?.label || ''}</span>
      </div>
    </div>
  )
}

function AdminSupport({
  conversations,
  filesCount = 0,
  isSubmitting,
  messages,
  onFileChange,
  onOpenConversation,
  selected,
  sendSupport,
  setText,
  text,
}) {
  return (
    <section className="admin-card liquid-card">
      <div className="panel-heading compact-heading">
        <h2>Поддержка</h2>
        <p>Чаты пользователей сайта.</p>
      </div>
      <div className="admin-support-layout">
        <div className="admin-support-list">
          {conversations.length ? (
            conversations.map((conversation) => (
              <button
                className={`admin-support-item ${
                  selected?.accountId === conversation.accountId ? 'is-active' : ''
                }`}
                key={conversation.accountId}
                onClick={() => onOpenConversation(conversation)}
                type="button"
              >
                <strong>{conversation.name || conversation.email}</strong>
                <span>{conversation.lastText || 'Файл'}</span>
              </button>
            ))
          ) : (
            <p className="support-empty">Диалогов пока нет.</p>
          )}
        </div>

        <div className="admin-support-chat">
          {selected ? (
            <>
              <div className="admin-support-title">
                <strong>{selected.name || selected.email}</strong>
                <span>{selected.email}</span>
              </div>
              <SupportMessageList
                adminLabel="Вы"
                conversationKey={selected.accountId}
                userLabel={selected.name || selected.email || 'Пользователь'}
                messages={messages}
              />
              <SupportComposer
                buttonLabel="Отправить"
                filesCount={filesCount}
                isSending={isSubmitting}
                onFiles={onFileChange}
                onSubmit={sendSupport}
                placeholder="Ответ пользователю"
                setText={setText}
                text={text}
              />
            </>
          ) : (
            <p className="support-empty">Выберите чат слева.</p>
          )}
        </div>
      </div>
    </section>
  )
}

function AdminLocked({ title }) {
  return (
    <section className="admin-card liquid-card is-disabled">
      <div className="panel-heading compact-heading">
        <h2>{title}</h2>
        <p>Доступ к этому разделу выключен главным администратором.</p>
      </div>
    </section>
  )
}

function AdminBroadcast({
  channel,
  isSubmitting,
  onSubmit,
  setChannel,
  setSubject,
  setText,
  subject,
  text,
}) {
  return (
    <div className="admin-messages">
    <SubscriptionAnnouncement getJson={getJson} postJson={postJson} />
    <section className="admin-card liquid-card">
      <div className="panel-heading compact-heading">
        <h2>Отправить сообщение</h2>
        <p>Рассылка в Telegram и на почту.</p>
      </div>
      <form className="admin-broadcast-form" onSubmit={onSubmit}>
        <div className="admin-broadcast-top">
          <label className="field-label">
            Канал
            <select
              className="glass-input"
              onChange={(event) => setChannel(event.target.value)}
              value={channel}
            >
              <option value="both">Почта и Telegram</option>
              <option value="email">Только почта</option>
              <option value="telegram">Только Telegram</option>
            </select>
          </label>
          {channel !== 'telegram' && (
            <label className="field-label">
              Тема
              <input
                className="glass-input"
                onChange={(event) => setSubject(event.target.value)}
                placeholder="Сообщение"
                value={subject}
              />
            </label>
          )}
        </div>
        <label className="field-label">
          Сообщение
          <textarea
            className="glass-input support-input"
            onChange={(event) => setText(event.target.value)}
            placeholder="Текст рассылки"
            value={text}
          />
        </label>
        <button className="primary-button fit-button" disabled={isSubmitting} type="submit">
          Отправить
        </button>
      </form>
    </section>
    </div>
  )
}

function AdminAdmins({
  addAdmin,
  adminEmail,
  adminTgId,
  admins,
  isSubmitting,
  removeAdmin,
  setAdminEmail,
  setAdminTgId,
  updatePermissions,
}) {
  const permissionLabels = {
    analytics: 'Аналитика',
    balance: 'Баланс',
    message: 'Сообщения',
    block: 'Блокировка',
    promos: 'Промокоды',
    support: 'Поддержка',
    payments: 'Оплаты',
    broadcast: 'Общие сообщения и объявления',
    delete_users: 'Удаление пользователей',
    nodes: 'Ноды: просмотр и управление',
    node_credentials: 'Пароли и SSH-ключи нод',
    technical: 'Технические логи',
    vpn_config: 'SNI и маршруты в разделе «Ноды»',
    documentation: 'Документация сервиса',
    user_transport: 'Протоколы пользователей',
  }

  return (
    <section className="admin-card liquid-card admin-members">
      <div className="panel-heading compact-heading">
        <h2>Администраторы</h2>
        <p>Добавьте администратора по email или Telegram ID и выберите его права. Доступ к SNI, маршрутам и SSH-данным требует также права на ноды.</p>
      </div>
      <form className="admin-form-row" onSubmit={addAdmin}>
        <input
          className="glass-input"
          onChange={(event) => setAdminEmail(event.target.value)}
          placeholder="email"
          value={adminEmail}
        />
        <input
          className="glass-input"
          inputMode="numeric"
          onChange={(event) => setAdminTgId(event.target.value)}
          placeholder="Telegram ID"
          value={adminTgId}
        />
        <button className="primary-button" disabled={isSubmitting} type="submit">
          Добавить
        </button>
      </form>
      <div className="admin-members-list">
        {admins.map(admin=><article className="admin-member" key={admin.id}>
          <header className="admin-member-head">
            <span className="admin-member-avatar" aria-hidden="true">{admin.isOwner ? '♔' : (admin.email || 'A').slice(0,1).toUpperCase()}</span>
            <div className="admin-member-identity"><strong>{admin.email || 'Администратор Telegram'}</strong><small>{admin.tgId ? `Telegram ID: ${admin.tgId}` : 'Telegram не привязан'}</small></div>
            <span className={`infra-status ${admin.isOwner ? 'good' : 'neutral'}`}>{admin.isOwner ? 'Владелец' : 'Администратор'}</span>
          </header>
          {admin.isOwner ? <p className="admin-member-owner">Полный доступ ко всем разделам и управлению администраторами.</p> : <>
            <div className="admin-member-permissions">
              {[
                ['Пользователи и сервис',['analytics','user_transport','balance','message','block','delete_users','support']],
                ['Оплаты и продвижение',['payments','promos','broadcast']],
                ['Серверы и диагностика',['nodes','vpn_config','node_credentials','technical','documentation']],
              ].map(([title,keys])=><fieldset key={title}><legend>{title}</legend>{keys.map(key=><label className="admin-permission-option" key={key}>
                <input checked={Boolean(admin.permissions?.[key])} disabled={isSubmitting} onChange={event=>updatePermissions(admin.id,{...(admin.permissions || {}),[key]:event.target.checked})} type="checkbox" />
                <span>{permissionLabels[key]}</span>
              </label>)}</fieldset>)}
            </div>
            <footer className="admin-member-footer"><small>Изменения прав сохраняются сразу</small><button className="admin-link-button danger-text" disabled={isSubmitting} onClick={()=>removeAdmin(admin.id)} type="button">Убрать администратора</button></footer>
          </>}
        </article>)}
        {!admins.length && <p className="infra-empty">Администраторов пока нет.</p>}
      </div>
    </section>
  )
}

function AdminSimpleTable({ columns, rows }) {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row, index) => (
              <tr key={index}>
                {row.map((cell, cellIndex) => (
                  <td key={`${index}-${cellIndex}`}>{cell}</td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={columns.length}>Нет данных.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function SupportMessageList({
  adminLabel = 'Поддержка',
  messagesEndRef,
  conversationKey,
  onScroll,
  messages,
  userLabel = 'Вы',
}) {
  const internalLog=useRef(null), nearTail=useRef(true)
  useEffect(()=>{nearTail.current=true},[conversationKey])
  useEffect(()=>{if(!messagesEndRef && nearTail.current && internalLog.current)internalLog.current.scrollTo({top:internalLog.current.scrollHeight,behavior:'instant'})},[messages.length,messagesEndRef,conversationKey])
  return (
    <div ref={internalLog} className="support-log" aria-live="polite" onScroll={event=>{const log=event.currentTarget;nearTail.current=log.scrollHeight-log.scrollTop-log.clientHeight<90;onScroll?.(event)}} tabIndex={0} aria-label="История сообщений">
      {messages.length ? (
        messages.map((message) => (
          <div
            className={`support-message is-${message.direction}`}
            key={message.id}
          >
            <span>
              {message.direction === 'admin'
                ? adminLabel
                : userLabel}
            </span>
            {message.text && <p>{message.text}</p>}
            {message.attachments?.length ? (
              <div className="support-attachments">
                {message.attachments.map((file) => (
                  isImageAttachment(file) ? (
                    <a
                      className="support-image-link"
                      href={file.url}
                      key={file.url}
                      rel="noreferrer"
                      target="_blank"
                    >
                      <img
                        alt={file.name || 'Вложение'}
                        loading="lazy"
                        src={file.url}
                      />
                    </a>
                  ) : (
                    <a
                      className="support-document-link"
                      href={file.url}
                      key={file.url}
                      rel="noreferrer"
                      target="_blank"
                    >
                      <span aria-hidden="true">📎</span>
                      {file.name}
                    </a>
                  )
                ))}
              </div>
            ) : null}
            <div className="support-meta-row">
              <small>{formatDate(message.createdAt)}</small>
            </div>
          </div>
        ))
      ) : (
        <p className="support-empty">
          Напишите, что случилось. Здесь появится ответ поддержки.
        </p>
      )}
      <span className="support-scroll-anchor" ref={messagesEndRef} />
    </div>
  )
}

function SupportComposer({
  buttonLabel,
  filesCount,
  isSending,
  onFiles,
  onSubmit,
  placeholder,
  setText,
  text,
}) {
  const [inputId] = useState(() => `support-file-${Math.random().toString(36).slice(2)}`)
  const [isDragOver, setIsDragOver] = useState(false)

  const handleDrop = (event) => {
    event.preventDefault()
    setIsDragOver(false)
    if (event.dataTransfer.files?.length) {
      onFiles(event.dataTransfer.files)
    }
  }

  return (
    <form
      className={`support-form ${isDragOver ? 'is-dragover' : ''}`}
      onDragEnter={(event) => {
        event.preventDefault()
        setIsDragOver(true)
      }}
      onDragLeave={(event) => {
        if (
          event.relatedTarget instanceof Node &&
          event.currentTarget.contains(event.relatedTarget)
        ) {
          return
        }
        setIsDragOver(false)
      }}
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
      onSubmit={onSubmit}
    >
      <textarea
        className="glass-input support-input"
        aria-label={placeholder}
        maxLength="4000"
        onChange={(event) => {
          setText(event.target.value)
        }}
        placeholder={placeholder}
        rows="2"
        value={text}
      />
      <div className="support-composer-actions">
        <label
          aria-label="Прикрепить файл"
          className="support-attach-button"
          htmlFor={inputId}
          title="Прикрепить файл"
        >
          📎
        </label>
        <input
          className="support-file-input"
          id={inputId}
          key={filesCount ? 'support-files-selected' : 'support-files-empty'}
          multiple
          accept=".png,.jpg,.jpeg,.webp,.pdf"
          onChange={(event) => onFiles(event.target.files)}
          type="file"
        />
        {filesCount > 0 && (
          <button type="button" className="support-file-count" onClick={() => onFiles([])} aria-label="Убрать вложения">{filesCount} файл(а) ×</button>
        )}
        <button
          className="primary-button support-send-button"
          disabled={isSending || (!text.trim() && !filesCount)}
          type="submit"
        >
          {isSending ? 'Отправляем...' : buttonLabel}
        </button>
      </div>
    </form>
  )
}

function SupportPage() {
  useEffect(() => {
    const viewport=window.visualViewport
    const update=()=>document.documentElement.style.setProperty('--chat-height', `${Math.round(viewport?.height || window.innerHeight)}px`)
    update();viewport?.addEventListener('resize', update)
    return()=>{viewport?.removeEventListener('resize',update);document.documentElement.style.removeProperty('--chat-height')}
  }, [])
  return (
    <div className="support-page">
      <SupportChat alwaysOpen page />
    </div>
  )
}

function SupportChat({ alwaysOpen = false, page = false } = {}) {
  const [isOpen, setIsOpen] = useState(alwaysOpen)
  const [messages, setMessages] = useState([])
  const [supportText, setSupportText] = useState('')
  const [supportFiles, setSupportFiles] = useState([])
  const [supportError, setSupportError] = useState('')
  const [isSending, setIsSending] = useState(false)
  const nearBottom = useRef(true)
  const messagesRef = useRef([])
  const messagesEndRef = useRef(null)

  useEffect(() => {
    messagesRef.current = messages
  }, [messages])

  const scrollToLatest = useCallback(() => {
    window.requestAnimationFrame(() => {
      const log = messagesEndRef.current?.parentElement
      if (log && nearBottom.current) log.scrollTo({top:log.scrollHeight, behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'})
    })
  }, [])

  const loadMessages = useCallback(async () => {
    const lastId = messagesRef.current.reduce((maxId, message) => {
      const numericId = Number(message.id)
      return Number.isInteger(numericId) ? Math.max(maxId, numericId) : maxId
    }, 0)
    const data = await getJson(`/api/support/messages?after_id=${lastId}`)
    if (lastId && data.messages?.length) {
      setMessages((current) => [...current, ...data.messages])
    } else if (!lastId) {
      setMessages(data.messages || [])
    }
  }, [])

  useEffect(() => {
    if (isOpen) {
      scrollToLatest()
    }
  }, [isOpen, messages.length, scrollToLatest])

  useEffect(() => {
    if (!isOpen) {
      return undefined
    }
    let alive = true
    const refresh = async () => {
      try {
        await loadMessages()
        if (alive) {
          setSupportError('')
        }
      } catch (error) {
        if (alive) {
          setSupportError(error.message)
        }
      }
    }
    const stopPolling = startPolling(refresh, 3000)
    return () => {
      alive = false
      stopPolling()
    }
  }, [isOpen, loadMessages])

  const submitSupport = async (event) => {
    event.preventDefault()
    const text = supportText.trim()
    if (!text && !supportFiles.length) {
      setSupportError('Введите сообщение для поддержки.')
      return
    }
    setSupportError('')
    nearBottom.current = true
    setIsSending(true)
    const filesSnapshot = supportFiles
    let pendingId = ''
    try {
      const attachments = await prepareAttachments(supportFiles)
      pendingId = `pending-${Date.now()}`
      const pendingMessage = {
        id: pendingId,
        direction: 'user',
        text,
        attachments: attachments.map((file) => ({
          ...file,
          url: file.data,
        })),
        createdAt: new Date().toISOString(),
      }
      setMessages((current) => [...current, pendingMessage])
      setSupportText('')
      setSupportFiles([])
      const data = await postJson('/api/support/send', { text, attachments })
      setMessages(data.messages || [])
    } catch (error) {
      if (pendingId) {
        setMessages((current) => current.filter((message) => message.id !== pendingId))
      }
      setSupportText(text)
      setSupportFiles(filesSnapshot)
      setSupportError(error.message)
    } finally {
      setIsSending(false)
    }
  }

  return (
    <div className={`support-card liquid-card ${page ? 'is-page' : ''}`}>
      <div className="panel-heading compact-heading">
        <span className="eyebrow">Служба поддержки</span>
        <h3>Чат поддержки</h3>
        <p>
          Напишите нам. Ответ появится здесь — историю можно открыть в любой момент.
        </p>
      </div>

      {!isOpen ? (
        <button
          className="primary-button"
          onClick={() => setIsOpen(true)}
          type="button"
        >
          Открыть чат поддержки
        </button>
      ) : (
        <div className="support-chat">
          <SupportMessageList
            onScroll={event => {const log=event.currentTarget;nearBottom.current = log.scrollHeight-log.scrollTop-log.clientHeight < 90}}
            messagesEndRef={messagesEndRef}
            messages={messages}
          />

          <SupportComposer
            buttonLabel="Отправить"
            filesCount={supportFiles.length}
            isSending={isSending}
            onFiles={setSupportFiles}
            onSubmit={submitSupport}
            placeholder="Опишите, что не работает или какой вопрос нужно решить"
            setText={setSupportText}
            text={supportText}
          />
          {supportError && <p className="support-error">{supportError}</p>}
        </div>
      )}
    </div>
  )
}

export default App
