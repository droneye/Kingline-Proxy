# Service Diagnostics

Main log: `/var/log/kingline/diagnostics.jsonl` (UTC, one JSON object per line).
Archives: `diagnostics.jsonl.1` through `.10`, newest first.
Rotation: 20 MiB per file, approximately 220 MiB total including the active log.
Access: directory 0750, files 0640. The log is not served by the website.

The bot and site use a bounded background queue. A process-shared file lock
protects writes and rotation. A logging outage cannot block request handlers;
`diagnostics_loss` reports dropped events or write errors after logging recovers.
Abrupt process termination can lose the last queued events.

## Events

- `application_log`: Python warnings/errors and exception stack locations.
- `http_exception`, `telegram_error`, `async_exception`, `thread_exception`:
  unexpected failures, with sanitized messages and stack locations.
- `slow_operation`, `operation_failed`: HTTP, XUI API calls, subscription backend,
  provisioning, lock waits, DNS, analytics and Telegram update timing.
- `latency_summary`: per-process, per-operation counts, errors, average, maximum,
  and an upper-bound estimate for p95, emitted every 60 seconds. A null p95 bound
  means the percentile exceeded 30 seconds. Final shutdown windows can be shorter.
- `event_loop_lag` in latency events: bot scheduling delay; warning at 200 ms.
- `http_probe`, `tcp_probe`, `icmp_probe`, `dns_probe`: minute-by-minute health
  and response times from this server. ICMP loss alone does not prove downtime;
  some hosts filter ping. These are not measurements from customers' devices.
- `host_resources`, `service_health`: disk, memory, load, service state and restart
  counts. `cpu_usage_ns` is cumulative; compare adjacent samples to calculate CPU.

Slow thresholds: HTTP/XUI/backend 1 second, DNS 100 ms, Telegram/analytics 2 seconds,
provisioning 3 seconds, snapshot 4 seconds, lock held 5 seconds.
Normal timings are aggregated; slow/failed operations are recorded individually.
Request bodies, cookies, query strings and subscription IDs are not recorded.
Known environment secrets, credentials in log messages, URLs and emails are masked.
`request_id` correlates events within a handled HTTP request.

## Operations

```sh
tail -f /var/log/kingline/diagnostics.jsonl
systemctl status kingline-diagnostics.timer
systemctl start kingline-diagnostics.service
```

Filter with `jq`:

```sh
jq -c 'select(.level == "ERROR" or .level == "WARNING")' /var/log/kingline/diagnostics.jsonl
jq -c 'select(.event == "latency_summary")' /var/log/kingline/diagnostics.jsonl
```

The timer runs `scripts/diagnostics_monitor.py` without changing subscriptions,
balances, account data or panel configuration. Probe failures are diagnostic events.
Override `KINGLINE_DIAGNOSTICS_PATH` to use a separate log in tests or another deployment.
This captures observable failures and latency; silent logical bugs still require
reproduction and tests. Browser JavaScript errors are not collected by this server log.
