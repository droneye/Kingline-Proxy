Put a square PNG logo here as `logo.png`.

Recommended source path:

```bash
/root/kingline_site/public/brand/custom/logo.png
```

Rebuild and publish:

```bash
cd /root/kingline_site
npm run build
rsync -a --delete --exclude support_uploads/ dist/ /var/www/kingline_site/
systemctl reload nginx
```

The site and email templates use `/brand/custom/logo.png`.
