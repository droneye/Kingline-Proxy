import base64
from io import BytesIO
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import urlparse

import sub_proxy as proxy
from xui import XUIClient


class SubscriptionProxyTests(unittest.TestCase):
    def setUp(self):
        proxy.OPTIONAL_BACKEND_FAILURES.clear()

    def parse(self, body):
        return proxy._parse_subscription_body(
            body, sub_id="secret", original_host="vpn.test", base_domain="vpn.test", fixed=False,
        )

    def test_valid_plain_and_base64_keys_are_preserved(self):
        content = b"vless://uuid@vpn.test:443?security=tls#Mobile\n"
        for payload in [content, base64.b64encode(content)]:
            lines, _, _ = self.parse(payload)
            self.assertEqual(len(lines), 1)
            self.assertIn("secret.vpn.test", lines[0])

    def test_html_and_mixed_error_payloads_rejected(self):
        for payload in [b"<html>Login</html>", b"vless://uuid@vpn.test:443\n<html>Error</html>"]:
            with self.assertRaises(ValueError):
                self.parse(payload)

    def test_primary_404_is_preserved_not_translated_to_502(self):
        error = HTTPError("https://vpn.test/sub/secret", 404, "Not Found", {}, BytesIO(b"Not found"))
        with patch.object(proxy.urllib.request, "urlopen", side_effect=error):
            result = proxy._fetch_and_parse_subscription(
                backend_base="https://vpn.test", parsed_backend=urlparse("https://vpn.test"),
                ssl_ctx=None, path_and_query="/sub/secret", label="primary", method="GET",
                sub_id="secret", original_host="vpn.test", base_domain="vpn.test", fixed=False,
            )
        self.assertEqual(result[0], 404)
        self.assertEqual(result[2], b"Not found")

    def test_dns_stall_has_total_deadline_and_other_optional_keys_survive(self):
        release = threading.Event()
        exited = threading.Event()

        def fetch(**kwargs):
            if kwargs["label"] == "fr":
                try:
                    release.wait(2)
                finally:
                    exited.set()
                return []
            return ["vless://working-key"]

        with patch.multiple(
            proxy, XUI_SUB_BACKEND_FR="https://slow.test", BACKEND_FR_PARSED=urlparse("https://slow.test"),
            XUI_SUB_BACKEND_EXTRA="https://fast.test", BACKEND_EXTRA_PARSED=urlparse("https://fast.test"),
            SUB_OPTIONAL_BACKEND_TIMEOUT_SECONDS=0.05,
        ), patch.object(proxy, "_try_fetch_optional_subscription", side_effect=fetch):
            started = time.monotonic()
            try:
                lines = proxy._fetch_optional_lines("/sub/secret", "secret")
                self.assertLess(time.monotonic() - started, 0.3)
                self.assertEqual(lines, ["vless://working-key"])
                self.assertIn("https://slow.test", proxy.OPTIONAL_BACKEND_FAILURES)
            finally:
                release.set()
                exited.wait(1)

    def test_worker_exhaustion_skips_optional_without_waiting(self):
        with patch.object(proxy.OPTIONAL_FETCH_SLOTS, "acquire", return_value=False), patch.multiple(
            proxy, XUI_SUB_BACKEND_FR="https://slow.test", BACKEND_FR_PARSED=urlparse("https://slow.test"),
        ):
            self.assertEqual(proxy._fetch_optional_lines("/sub/secret", "secret"), [])


    def test_head_uses_get_only_backend_and_returns_metadata_without_body(self):
        handler = object.__new__(proxy.SubProxyHandler)
        handler.path = "/sub/secret"
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        upstream = (200, {"Subscription-Userinfo": "expire=123"}, b"vless://key", [], "plain", False)
        with patch.object(proxy, "_fetch_and_parse_subscription", return_value=upstream) as fetch, patch.object(
            proxy, "_fetch_optional_lines"
        ) as optional:
            handler._proxy_request("HEAD")
        self.assertEqual(fetch.call_args.kwargs["method"], "GET")
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Subscription-Userinfo", "expire=123")
        self.assertEqual(handler.wfile.getvalue(), b"")
        optional.assert_not_called()


class LoginValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_html_login_response_falls_back_to_valid_form(self):
        client = XUIClient("http://panel.test", "user", "pass")
        with patch.object(client, "_post_json", return_value=(200, {"raw": "<html>Login</html>"})), patch.object(
            client, "_post_form", return_value=(200, {"success": True})
        ) as form:
            await client.login()
        form.assert_awaited_once()

    async def test_existing_subscription_ownership_beats_email(self):
        self.assertFalse(XUIClient._client_matches_user(
            {"email": "reused #1", "subId": "another"}, "reused", "mine",
        ))
