// Derive visible access from the server verdict and its actual expiry, even in an open tab.
export function effectiveSubscription(value, now = Date.now()) {
  if (!value) return null
  const expiry = Date.parse(value.expiresAt || '')
  return { ...value, active: Boolean(value.active && !value.blocked && Number.isFinite(expiry) && expiry > now) }
}
