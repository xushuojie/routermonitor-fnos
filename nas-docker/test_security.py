"""Security regressions use a loopback server, never the live NAS."""
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import api
from web import WebApp


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'NAS_STATUS_PUBLIC_ORIGIN': '', 'NAS_STATUS_TRUSTED_PROXIES': '',
                                     'NAS_STATUS_ADMIN_PASSWORD': 'test-password-only-123'}):
            self.app = WebApp(Mock(), Mock(), Path(self.tmp.name))
        self.server = api.LimitedHTTPServer(('127.0.0.1', 0), api.handler_factory(Mock(), 'test-device-token', web=self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, method='GET', body=None, **headers):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_private_host_and_public_proxy_boundary(self):
        self.assertEqual(self.request('/api/settings', Authorization='Bearer test-device-token')[0], 401)
        self.assertEqual(self.request('/', Host='attacker.example')[0], 403)
        self.app.public_origin = 'https://nas.example.com'
        self.app.trusted_proxies = {ipaddress.ip_address('127.0.0.1')}
        self.assertEqual(self.request('/', Host='nas.example.com')[0], 403)
        headers = {'Host': 'nas.example.com', 'X-Forwarded-Proto': 'https'}
        self.assertEqual(self.request('/', **headers)[0], 200)
        self.app.trusted_proxies.clear()
        self.assertEqual(self.request('/', **headers)[0], 403)
        self.app.trusted_proxies.add(ipaddress.ip_address('127.0.0.1'))
        headers.update({'Origin': self.app.public_origin, 'Content-Type': 'application/json'})
        status, response, body = self.request('/api/login', 'POST', json.dumps({'password': 'test-password-only-123'}), **headers)
        self.assertEqual(status, 200)
        self.assertIn('; Secure', response['Set-Cookie'])
        self.assertIn('max-age=', response['Strict-Transport-Security'])
        headers['Origin'] = 'http://nas.example.com'
        self.assertEqual(self.request('/api/login', 'POST', '{}', **headers)[0], 403)

    def test_login_budget_and_malformed_json(self):
        headers = {'Origin': self.origin, 'Content-Type': 'application/json'}
        self.assertEqual(self.request('/api/login', 'POST', '{"password":' + '[' * 1500 + '0' + ']' * 1500 + '}', **headers)[0], 400)
        with patch.object(self.app, 'valid_password', return_value=False) as verify:
            for _ in range(8):
                self.assertEqual(self.request('/api/login', 'POST', '{"password":"wrong"}', **headers)[0], 401)
            self.assertEqual(self.request('/api/login', 'POST', '{}', **headers)[0], 429)
            self.assertEqual(verify.call_count, 8)
            self.app.failures.clear()
            self.app.auth_attempts.extend([api.time.monotonic()] * 30)
            self.assertEqual(self.request('/api/login', 'POST', '{}', **headers)[0], 429)
            self.assertEqual(verify.call_count, 8)

    def test_ambiguous_http_framing_and_unicode_auth(self):
        for extra in [b'Content-Length: 0\r\nContent-Length: 0\r\n',
                      b'Transfer-Encoding: chunked\r\n', b'Content-Length: 1\r\n',
                      b'Host: duplicate\r\n']:
            with socket.create_connection(self.server.server_address, timeout=3) as client:
                client.sendall(b'GET /health HTTP/1.1\r\nHost: localhost\r\n' + extra + b'\r\n')
                self.assertIn(b' 400 ', client.recv(1024).split(b'\r\n')[0])
        self.assertEqual(self.request('/status', Authorization='Bearer \xe9')[0], 401)
        self.assertEqual(self.request('/.env')[0], 404)
        self.assertEqual(self.request('/health')[0], 200)

    def test_connection_capacity_releases_after_close(self):
        # Exhaust the bounded admission slots without creating a load test.
        for _ in range(32):
            self.assertTrue(self.server.slots.acquire(blocking=False))
        try:
            with socket.create_connection(self.server.server_address, timeout=3) as client:
                self.assertEqual(client.recv(1), b'')
        finally:
            for _ in range(32):
                self.server.slots.release()
        self.assertEqual(self.request('/health')[0], 200)


if __name__ == '__main__':
    unittest.main()
