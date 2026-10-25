import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import './staging.css'
import './experience.css'
import './auth-legacy.css'
import './admin-theme.css'
import './subscription-access.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Bounded diagnostics: classify failures without URLs, form values or user stacks.
let reportedErrors = 0
for (const kind of ['error', 'unhandledrejection']) {
  window.addEventListener(kind, (event) => {
    if (++reportedErrors > 5) return
    fetch('/api/client-error', { method: 'POST', headers: {'Content-Type':'application/json','X-Kingline-Request':'fetch'}, body:JSON.stringify({kind, route:location.pathname === '/admin' ? 'admin' : location.pathname === '/support' ? 'support' : 'account', asset:String(event.filename || '').split('/').pop()?.split('?')[0].slice(0,100) || '', error: /^(TypeError|ReferenceError|SyntaxError|RangeError)$/.test(event.error?.name || event.reason?.name) ? event.error?.name || event.reason?.name : 'ClientError'}), keepalive:true }).catch(() => {})
  })
}
