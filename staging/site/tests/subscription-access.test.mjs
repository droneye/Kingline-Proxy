import assert from 'node:assert/strict'
import test from 'node:test'
import { effectiveSubscription } from '../src/subscription-access.js'

const now = Date.parse('2026-10-24T12:00:00Z')
const active = { active: true, expiresAt: '2026-10-24T12:00:01Z', subscriptionUrl: 'https://example.invalid/sub/fixture', balanceRub: 300 }

test('an open tab loses access exactly at expiry while keeping its subscription identity', () => {
  assert.equal(effectiveSubscription(active, now).active, true)
  const expired = effectiveSubscription(active, now + 1000)
  assert.equal(expired.active, false)
  assert.equal(expired.subscriptionUrl, active.subscriptionUrl)
  assert.equal(expired.balanceRub, 300)
  assert.equal(active.active, true)
})
test('a future date cannot override a blocked or inactive server verdict', () => {
  assert.equal(effectiveSubscription({ ...active, blocked: true }, now).active, false)
  assert.equal(effectiveSubscription({ ...active, active: false }, now).active, false)
})
test('missing or malformed expiry never exposes a connect action', () => {
  assert.equal(effectiveSubscription(null, now), null)
  assert.equal(effectiveSubscription({ active: true }, now).active, false)
  assert.equal(effectiveSubscription({ active: true, expiresAt: 'invalid' }, now).active, false)
})
test('renewal restores the same subscription as soon as a valid new expiry arrives', () => {
  assert.equal(effectiveSubscription(active, now + 2000).active, false)
  assert.equal(effectiveSubscription({ ...active, expiresAt: '2026-11-24T12:00:00Z' }, now + 2000).active, true)
})
