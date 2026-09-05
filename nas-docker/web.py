"""Same-origin GUI with separate administrator sessions; ESP tokens stay read-only."""
from collections import deque
import hashlib
import hmac
import ipaddress
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit, parse_qs

from network_sources import atomic_json, SelectionError


class WebApp:
    def __init__(self, sources, metrics, data_dir):
        self.sources, self.metrics = sources, metrics
        self.root = Path(data_dir)
        self.password_path = self.root / 'admin.json'
        self.assets = {name: (Path(__file__).parent / 'web' / name).read_bytes()
                       for name in ('index.html', 'app.js', 'style.css')}
        self.lock = threading.Lock()
        self.sessions = {}
        self.failures = {}
        self.auth_attempts = deque(maxlen=30)
        self.auth_lock = threading.Lock()
        self.public_origin = os.environ.get('NAS_STATUS_PUBLIC_ORIGIN', '').rstrip('/')
        self.trusted_proxies = {ipaddress.ip_address(value.strip()) for value in
                                os.environ.get('NAS_STATUS_TRUSTED_PROXIES', '').split(',') if value.strip()}
        if self.public_origin:
            origin = urlsplit(self.public_origin)
            origin.port  # Validate the configured port before opening the server.
            if origin.scheme not in ('http', 'https') or not origin.hostname or origin.username or origin.password or origin.path or origin.query or origin.fragment or not self.trusted_proxies:
                raise ValueError('Public GUI requires an HTTP(S) origin and exact trusted proxy IP addresses')
        self.last_device_at = 0
        self.device_address = ''
        if not self.password_path.exists():
            password = os.environ.get('NAS_STATUS_ADMIN_PASSWORD') or secrets.token_urlsafe(18)
            self.set_password(password)
            if not os.environ.get('NAS_STATUS_ADMIN_PASSWORD'):
                path = self.root / 'initial-admin-password.txt'
                with open(path, 'w', opener=lambda name, flags: os.open(name, flags, 0o600)) as out:
                    out.write(password + '\n')
                print('GUI admin password created in data/initial-admin-password.txt', flush=True)
        self.password = json.loads(self.password_path.read_text())

    def set_password(self, password):
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise SelectionError('管理员密码需为 12–128 个字符')
        salt = secrets.token_hex(16)
        value = {'salt': salt, 'hash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 300000).hex()}
        atomic_json(self.password_path, value)
        self.password = value

    def valid_password(self, password):
        if not isinstance(password, str) or len(password) > 128:
            return False
        record = self.password
        value = hashlib.pbkdf2_hmac('sha256', password.encode(), record['salt'].encode(), 300000).hex()
        return hmac.compare_digest(value, record['hash'])

    def allowed_transport(self, handler):
        """Only a configured TLS proxy may serve the public GUI. Ignore XFF."""
        try:
            peer = ipaddress.ip_address(handler.client_address[0])
            host = handler.headers.get('Host', '')
            if self.public_origin:
                origin = urlsplit(self.public_origin)
                forwarded = handler.headers.get_all('X-Forwarded-Proto') or []
                return (peer in self.trusted_proxies and host == origin.netloc and
                        (forwarded == [origin.scheme] if origin.scheme == 'https' else not forwarded))
            address = urlsplit('http://' + host)
            if address.username or address.password or address.path or address.query or address.fragment:
                return False
            address.port
            name = address.hostname
            return peer.is_private and (name == 'localhost' or ipaddress.ip_address(name).is_private)
        except ValueError:
            return False

    def cookie(self, sid='', age=0):
        return ('nas_admin=' + sid + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=' + str(age)
                + ('; Secure' if self.public_origin.startswith('https://') else ''))

    def device_seen(self, address):
        self.last_device_at, self.device_address = time.monotonic(), address

    def session(self, handler):
        try:
            cookie = SimpleCookie(handler.headers.get('Cookie', ''))
            sid = cookie['nas_admin'].value if 'nas_admin' in cookie else ''
        except Exception:
            return '', None
        with self.lock:
            value = self.sessions.get(sid)
            if value and value['expires'] > time.monotonic():
                return sid, value
        return '', None

    def same_origin(self, handler):
        supplied = handler.headers.get('Origin', '')
        if self.public_origin:
            return supplied == self.public_origin
        origin = urlsplit(supplied)
        return origin.scheme == 'http' and origin.netloc == handler.headers.get('Host') and not (origin.path or origin.username or origin.query or origin.fragment)

    def respond(self, handler, status, value, cookie=None):
        body = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        self.send(handler, status, body, 'application/json; charset=utf-8', cookie)

    def send(self, handler, status, body, content_type, cookie=None):
        handler.send_response(status)
        handler.send_header('Content-Type', content_type)
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.send_header('X-Frame-Options', 'DENY')
        if self.public_origin.startswith('https://'):
            handler.send_header('Strict-Transport-Security', 'max-age=31536000')
        handler.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            handler.send_header('Set-Cookie', cookie)
        if handler.close_connection:
            handler.send_header('Connection', 'close')
        handler.end_headers()
        handler.wfile.write(body)

    def overview(self):
        value = self.metrics.read_snapshot()
        if value is None:
            return {'available': False}
        value['available'] = value['age'] <= 3
        for key, field in (('cpu', 'percent'), ('gpu', 'utilization'), ('memory', 'percent')):
            value[key] = {**value[key], field: value[key][field] if value[key]['valid'] and value['available'] else None}
        value['sources'] = {'network': self.sources.selected()[1], 'cpu': '/host/proc/stat',
                            'memory': '/host/proc/meminfo', 'disk_io': value['disk_io'].get('devices'),
                            'storage_paths': list(self.sources.api.STORAGE_PATHS), 'gpu': value['gpu'].get('backend'),
                            'ups': value['ups'].get('source')}
        return value

    def handle(self, handler):
        path = urlsplit(handler.path).path
        if (path.startswith('/api/') or path in ('/', '/index.html', '/app.js', '/style.css')) and not self.allowed_transport(handler):
            handler.close_connection = True
            self.respond(handler, 403, {'error': '请通过已配置的 HTTPS 入口访问；未配置公网入口时仅允许局域网 IP 访问'})
            return True
        if path in ('/', '/index.html', '/app.js', '/style.css') and handler.command == 'GET':
            name = 'index.html' if path in ('/', '/index.html') else path[1:]
            types = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'style.css': 'text/css; charset=utf-8'}
            self.send(handler, 200, self.assets[name], types[name])
            return True
        if not path.startswith('/api/'):
            return False
        sid, session = self.session(handler)
        try:
            body = None
            if handler.command != 'GET':
                if not self.same_origin(handler):
                    raise PermissionError('请求来源不匹配，请从当前网页重新打开')
                if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json' or handler.headers.get('Transfer-Encoding'):
                    raise SelectionError('请求必须为 JSON')
                length = int(handler.headers.get('Content-Length', '0'))
                if not 0 < length <= 32768:
                    raise SelectionError('配置内容过大或为空')
                body = json.loads(handler.rfile.read(length))
                if not isinstance(body, dict):
                    raise SelectionError('无效的配置格式')
                if path != '/api/login' and (session is None or not hmac.compare_digest(handler.headers.get('X-CSRF-Token', '').encode(), session['csrf'].encode())):
                    raise PermissionError('会话已过期，请重新登录')
            if path == '/api/login' and handler.command == 'POST':
                peer = handler.client_address[0]
                with self.lock:
                    now = time.monotonic()
                    while self.auth_attempts and now - self.auth_attempts[0] >= 60:
                        self.auth_attempts.popleft()
                    self.failures = {key: value for key, value in self.failures.items() if value and now - value[-1] < 60}
                    failures = self.failures.setdefault(peer, deque(maxlen=8))
                    if len(failures) >= 8 or len(self.failures) > 256 or len(self.auth_attempts) >= 30:
                        self.respond(handler, 429, {'error': '尝试次数过多，请一分钟后再试'})
                        return True
                    failures.append(now)
                    self.auth_attempts.append(now)
                if not self.auth_lock.acquire(blocking=False):
                    self.respond(handler, 429, {'error': '登录繁忙，请稍后重试'})
                    return True
                try:
                    if not self.valid_password(body.get('password')):
                        self.respond(handler, 401, {'error': '管理员密码不正确'})
                        return True
                    with self.lock:
                        self.failures.pop(peer, None)
                        self.sessions = {key: value for key, value in self.sessions.items() if value['expires'] > now}
                        if len(self.sessions) >= 32:
                            self.sessions.pop(next(iter(self.sessions)))
                        sid = secrets.token_urlsafe(32)
                        session = {'csrf': secrets.token_urlsafe(24), 'expires': now + 28800}
                        self.sessions[sid] = session
                finally:
                    self.auth_lock.release()
                self.respond(handler, 200, {'authenticated': True, 'csrf': session['csrf']},
                             self.cookie(sid, 28800))
                return True
            if path == '/api/session' and handler.command == 'GET':
                self.respond(handler, 200, {'authenticated': session is not None, 'csrf': session['csrf'] if session else None})
                return True
            if session is None:
                self.respond(handler, 401, {'error': '请先登录管理员账户'})
                return True
            result = None
            if handler.command == 'GET':
                if path == '/api/overview':
                    result = self.overview()
                elif path == '/api/network/interfaces':
                    result = self.sources.interfaces()
                elif path == '/api/network/stream':
                    query = parse_qs(urlsplit(handler.path).query)
                    result = self.metrics.monitor.response(int(query.get("since", ["0"])[0]), query.get("epoch", [""])[0], limit=50)
                elif path == '/api/device-access':
                    result = {'token': self.sources.api.resolve_token()}
                elif path == '/api/settings':
                    result = self.sources.settings
                elif path == '/api/capabilities':
                    result = {'host_network': self.sources.host_network,
                              'network_reason': '' if self.sources.host_network else '未使用 host 网络；IP 和拓扑信息无法完整核对，请按 Compose 模板部署',
                              'interface_count': len(self.sources.rows), 'protocol': 2,
                              'device': {'online': time.monotonic() - self.last_device_at < 5,
                                         'address': self.device_address, 'age': round(time.monotonic() - self.last_device_at, 1) if self.last_device_at else None},
                              'intervals': {'network_ms': 200, 'status_ms': 1000, 'history_ms': 5000},
                              'supported': {'gpu': ['AMD busy_percent', 'Intel i915 render engine'],
                                            'ups': ['本机 NUT 已有驱动缓存；有功功率或已验证的直流 V×A']}}
            elif handler.command == 'POST' and path == '/api/network/preview':
                result = self.sources.preview(body)
            elif handler.command == 'PUT' and path == '/api/settings':
                result = self.sources.save(body)
            elif handler.command == 'POST' and path == '/api/logout':
                with self.lock:
                    self.sessions.pop(sid, None)
                self.respond(handler, 200, {'ok': True}, self.cookie())
                return True
            elif handler.command == 'PUT' and path == '/api/password':
                if not self.auth_lock.acquire(blocking=False):
                    self.respond(handler, 429, {'error': '密码校验繁忙，请稍后重试'})
                    return True
                try:
                    if not self.valid_password(body.get('current')):
                        raise PermissionError('当前管理员密码不正确')
                    with self.lock:
                        self.set_password(body.get('password'))
                        self.sessions.clear()
                        (self.root / 'initial-admin-password.txt').unlink(missing_ok=True)
                finally:
                    self.auth_lock.release()
                result = {'ok': True}
            if result is None:
                self.respond(handler, 404, {'error': '接口不存在或数据尚未就绪'})
            else:
                self.respond(handler, 200, result)
        except PermissionError as error:
            handler.close_connection = True
            self.respond(handler, 403, {'error': str(error)})
        except (SelectionError, ValueError, TypeError, RecursionError) as error:
            handler.close_connection = True
            message = str(error) if isinstance(error, SelectionError) else '无效的请求参数或 JSON'
            self.respond(handler, 409 if '其他页面' in message else 400, {'error': message})
        except (OSError, sqlite3.Error) as error:
            handler.close_connection = True
            self.respond(handler, 503, {'error': '暂时无法保存或读取，请重新载入确认配置后重试'})
        return True
