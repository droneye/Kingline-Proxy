# Kingline Proxy Site

Vite + React frontend and Python API for the Kingline Proxy VPN website.

## Email Verification

Users register or log in with email and password. A new account cannot enter the
cabinet until the email is confirmed by a one-time verification link.

The production service uses local Postfix for send-only SMTP:

```env
SMTP_HOST=127.0.0.1
SMTP_PORT=25
SMTP_TLS=0
SMTP_SSL=0
SMTP_FROM=Kingline Proxy <no-reply@kingline.online>
KINGLINE_SITE_URL=https://kingline.online
```

For an external SMTP provider, configure these variables in a systemd drop-in:

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_TLS=1
SMTP_SSL=0
SMTP_USER=login@example.com
SMTP_PASSWORD=secret
SMTP_FROM=Kingline Proxy <login@example.com>
KINGLINE_SITE_URL=https://kingline.online
```

After changing env values:

```bash
systemctl restart kingline-site-api.service
```

For better delivery to Gmail/Yandex/etc. the Cloudflare zone should also have:

```dns
@      TXT  "v=spf1 ip4:198.51.100.20 -all"
_dmarc TXT  "v=DMARC1; p=none"
mail._domainkey TXT "v=DKIM1; h=sha256; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwFIuwQaM/R9lOHzrGpYGiOT8OpTp6LpDPnkmW2lQLYgI89jgsTio96V63qhFmpz3zAfcocIN2GIgTjCCD5xG7yDGoeXcRoZc3JVnzlmfmWGN6k5H8klPQ6agdv6wDEXNodqNaJymCgv47ixZVxQbecJVFTNq9b3zoFU+yC6pwpMbnGL1AGuP2HkkUgQsviDoIZfAgHfsxpFsGY9+Kf4NdcC0mojOjM5jtQsU/pXPAK5ENxg200DJWEYJ8wMlRJIcSYbIRMxUsQbwxA6jobMzVABpZJFmgw/bCz418TKf8xJ5aICUh2qZQPlmT0LK6IGIz45ar8WRMgF1IMq9GydNvQIDAQAB"
```

## Commands

```bash
npm run lint
npm run build
```
