import http.client
import threading
import time
import unittest
from urllib.parse import urlencode

from http.server import ThreadingHTTPServer

from tools.briefing_session_server import SessionHandler, safe_next, sign_session, valid_session


class BriefingSessionTests(unittest.TestCase):
    def test_signed_session_accepts_valid_and_rejects_tampered_or_expired(self):
        token = sign_session("secret", 200)
        self.assertTrue(valid_session("secret", token, now=100))
        self.assertFalse(valid_session("other", token, now=100))
        self.assertFalse(valid_session("secret", token, now=200))
        self.assertFalse(valid_session("secret", token + "x", now=100))

    def test_safe_next_only_accepts_local_absolute_paths(self):
        default = "/prototype/briefing/?batch=latest"
        self.assertEqual(safe_next("/local-data/briefing/latest/briefing.json"), "/local-data/briefing/latest/briefing.json")
        self.assertEqual(safe_next("https://evil.example"), default)
        self.assertEqual(safe_next("//evil.example/path"), default)
        self.assertEqual(safe_next(None), default)

    def test_default_session_duration_example_is_future(self):
        expires = int(time.time()) + 90 * 86400
        self.assertTrue(valid_session("secret", sign_session("secret", expires)))

    def test_http_login_cookie_verify_and_logout(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SessionHandler)
        server.settings = {
            "username": "briefing",
            "password": "correct-password",
            "secret": "session-secret",
            "session_days": 90,
        }
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request("GET", "/verify", headers={"X-Forwarded-Uri": "/private.json"})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 302)
            self.assertIn("next=%2Fprivate.json", response.getheader("Location"))

            wrong_body = urlencode({"username": "briefing", "password": "wrong", "next": "/private.json"})
            connection.request("POST", "/login", body=wrong_body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            response = connection.getresponse()
            wrong_page = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIsNone(response.getheader("Set-Cookie"))
            self.assertIn("账号或密码不正确", wrong_page)

            body = urlencode({"username": "briefing", "password": "correct-password", "next": "/private.json"})
            connection.request("POST", "/login", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 302)
            cookie_header = response.getheader("Set-Cookie")
            self.assertIn("Max-Age=7776000", cookie_header)
            self.assertIn("Secure", cookie_header)
            self.assertIn("HttpOnly", cookie_header)
            self.assertIn("SameSite=Lax", cookie_header)
            cookie = cookie_header.split(";", 1)[0]

            connection.request("GET", "/verify", headers={"Cookie": cookie})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 200)

            connection.request("GET", "/logout", headers={"Cookie": cookie})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 302)
            self.assertIn("Max-Age=0", response.getheader("Set-Cookie"))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
