"""Same-origin GUI with separate administrator sessions; ESP tokens stay read-only."""
from collections import deque
import hashlib
import hmac
import ipaddress
import re
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
import hardware_settings
from device_tokens import DeviceTokens


class WebApp:
    def __init__(self, sources, metrics, data_dir):
        self.sources, self.metrics = sources, metrics
        self.root = Path(data_dir)
        self.device_tokens = DeviceTokens(self.root, self.sources.api.resolve_token())
        self.password_path = self.root / 'admin.json'
        self.assets = {name: (Path(__file__).parent / 'web' / name).read_bytes()
                       for name in ('index.html', 'app.js', 'style.css')}
        self.lock = threading.Lock()
        self.sessions = {}
        self.failures = {}
        self.auth_attempts = deque(maxlen=30)
        self.auth_lock = threading.Lock()
        self.hardware_lock = threading.Lock()
        self.hardware_cache = None
        self.hardware_at = 0
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
        if not isinstance(password, str) or not password:
            raise SelectionError('管理员密码不能为空')
        salt = secrets.token_hex(16)
        value = {'salt': salt, 'hash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 300000).hex()}
        atomic_json(self.password_path, value)
        self.password = value

    def valid_password(self, password):
        if not isinstance(password, str) or not password:
            return False
        record = self.password
        value = hashlib.pbkdf2_hmac('sha256', password.encode(), record['salt'].encode(), 300000).hex()
        return hmac.compare_digest(value, record['hash'])

    def cookie(self, sid='', age=0):
        return 'nas_admin=' + sid + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=' + str(age)

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
        origin = urlsplit(supplied)
        return origin.scheme in ('http', 'https') and origin.netloc == handler.headers.get('Host') and not (origin.path or origin.username or origin.query or origin.fragment)

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
                            'storage_paths': [row['path'] for row in value['storage'].get('volumes', [])] or value['storage'].get('paths', []), 'gpu': value['gpu'].get('backend'),
                            'ups': value['ups'].get('source')}
        return value

    def handle(self, handler):
        path = urlsplit(handler.path).path
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
                    result = {'token': self.device_tokens.current()}
                elif path == '/api/settings':
                    result = self.sources.settings
                elif path == '/api/hardware-settings':
                    result = hardware_settings.load_settings(self.root)
                elif path == '/api/hardware':
                    result = self.hardware_inventory()
                elif path == '/api/diagnostics':
                    result = self.diagnostics()
                elif path == '/api/capabilities':
                    result = {'host_network': self.sources.host_network,
                              'lan_access': self.lan_access(handler.server.server_port),
                              'network_reason': '' if self.sources.host_network else '未使用 host 网络；IP 和拓扑信息无法完整核对，请按 Compose 模板部署',
                              'interface_count': len(self.sources.rows), 'protocol': 2,
                              'device': {'online': time.monotonic() - self.last_device_at < 5,
                                         'address': self.device_address, 'age': round(time.monotonic() - self.last_device_at, 1) if self.last_device_at else None},
                              'intervals': {'network_ms': round(hardware_settings.intervals()['network'] * 1000),
                                            'status_ms': round(hardware_settings.intervals()['status'] * 1000), 'history_ms': 5000},
                              'supported': {'gpu': ['AMD busy_percent', 'Intel i915 引擎计数', '宿主机 Intel / NVIDIA 只读采集'],
                                            'ups': ['本机 NUT socket 或远程 NUT；有功功率或已验证的直流 V×A']}}
            elif handler.command == 'POST' and path == '/api/network/preview':
                result = self.sources.preview(body)
            elif handler.command == 'PUT' and path == '/api/settings':
                result = self.sources.save(body)
            elif handler.command == 'PUT' and path == '/api/device-access':
                if body.get('token') != body.get('confirm'):
                    raise SelectionError('两次输入的 Token 不一致')
                self.device_tokens.save(body.get('token'))
                result = {'ok': True}
            elif handler.command == 'PUT' and path == '/api/hardware-settings':
                body = hardware_settings.validate(body)
                if body.get('storage_mode') == 'selected' and isinstance(body.get('storage_paths'), list):
                    # Bind chosen mount paths to filesystem identities across restarts.
                    from storage_discovery import read_snapshot
                    snapshot = read_snapshot(os.environ.get('NAS_STATUS_STORAGE_SNAPSHOT', '/tmp/nas-monitor/storage.json'))
                    identities = {}
                    current = hardware_settings.load_settings(self.root).get('storage_volume_ids', {})
                    for selected_path in body['storage_paths']:
                        if not isinstance(selected_path, str):
                            continue
                        if selected_path in current:
                            # Saving another option must not rebind an unplugged volume to '/'.
                            identities[selected_path] = current[selected_path]
                            continue
                        candidates = []
                        for row in snapshot.get('volumes', []):
                            for mount in row.get('mounts', [row.get('path', '')]):
                                mount = mount.get('path', '') if isinstance(mount, dict) else mount
                                if mount and (selected_path == mount or selected_path.startswith(mount.rstrip('/') + '/')):
                                    candidates.append((len(mount), row))
                        if candidates:
                            row = max(candidates, key=lambda pair: pair[0])[1]
                            identities[selected_path] = row['id']
                    body['storage_volume_ids'] = identities
                result = hardware_settings.save_settings(body, self.root)
                self.hardware_at = 0
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

    def hardware_inventory(self):
        import hardware
        from storage_discovery import read_snapshot
        selected = hardware_settings.load_settings(self.root)
        with self.hardware_lock:
            if self.hardware_cache is None or time.monotonic() - self.hardware_at >= 5:
                self.hardware_cache = hardware.inventory()
                self.hardware_at = time.monotonic()
            inventory = dict(self.hardware_cache)
        storage = read_snapshot(os.environ.get('NAS_STATUS_STORAGE_SNAPSHOT', '/tmp/nas-monitor/storage.json'))
        diagnostics = list(inventory.get('diagnostics', []))
        error = hardware_settings.settings_error(self.root)
        if error:
            diagnostics.append({'component': '硬件设置', 'status': 'unavailable', 'detail': error})
        diagnostics.append({'component': '存储空间', 'status': 'ok' if storage.get('valid') else 'unavailable',
                            'detail': storage.get('reason') or '已发现 %d 个存储卷' % len(storage.get('volumes', []))})
        diagnostics.append({'component': '网络接口', 'status': 'ok' if self.sources.host_network else 'unavailable',
                            'detail': '已发现 %d 个接口；多端口合计可能包含同一流量的多次经过' % len(self.sources.rows) if self.sources.host_network else '无法核对宿主网络命名空间'})
        return {**inventory, 'settings': selected, 'storage': storage, 'diagnostics': diagnostics,
                'intervals': hardware_settings.intervals(selected)}

    def diagnostics(self):
        value = self.hardware_inventory()
        # Export capabilities and failure states, without credentials or host identifiers.
        settings = value['settings']
        private = [settings.get('ups_host', ''), settings.get('ups_name', ''), settings.get('ups_socket', '')]
        private += settings.get('storage_paths', [])
        def clean(text):
            text = str(text)
            for token in sorted((v for v in private if v and v != '/'), key=len, reverse=True):
                text = text.replace(token, '[已隐藏]')
            return text
        return {'app_version': '1.3.0-5', 'architecture': 'amd64',
                'profile': settings['profile'], 'intervals': value['intervals'],
                'storage': [{'index': i + 1, 'filesystem': row.get('filesystem'), 'valid': row.get('valid'),
                             'included': row.get('included'), 'reason': clean(row.get('reason', ''))}
                            for i, row in enumerate(value['storage'].get('volumes', []))],
                'gpu': [{'backend': row.get('backend'), 'valid': row.get('valid'), 'reason': clean(row.get('reason', ''))}
                        for row in value.get('gpus', [])],
                'sensor_count': len(value.get('sensors', [])),
                'diagnostics': [{**row, 'detail': clean(row.get('detail', ''))} for row in value['diagnostics']]}

    def lan_access(self, port):
        """Advertise host LAN IPv4 addresses and the listening port, never proxy headers."""
        result = {'port': port, 'addresses': [], 'reason': ''}
        if not self.sources.host_network:
            return {**result, 'reason': '无法确认宿主网络，请检查应用网络模式'}
        networks = tuple(ipaddress.ip_network(value) for value in ('192.168.0.0/16', '10.0.0.0/8', '172.16.0.0/12'))
        excluded = re.compile(r'^(?:lo$|docker|veth|br-[0-9a-f]+$|cni|flannel|virbr|lxcbr|podman|kube|tun|tap|tailscale|zt|wg)')
        seen = set()
        for row in list(self.sources.rows.values()):
            name = row.get('name', '')
            if excluded.match(name) or row.get('up') is False or row.get('carrier') is False:
                continue
            if not row.get('physical') and row.get('kind') not in ('physical', 'wireless', 'bond', 'team', 'bridge', 'vlan', 'openvswitch', 'unknown'):
                continue
            for value in row.get('addresses', []):
                try:
                    address = ipaddress.ip_interface(value).ip
                except (TypeError, ValueError):
                    continue
                if address.version != 4 or not any(address in network for network in networks) or address in seen:
                    continue
                seen.add(address)
                result['addresses'].append({'interface': name, 'ip': str(address), 'url': 'http://%s:%d' % (address, port)})
        result['addresses'].sort(key=lambda row: (not row['ip'].startswith('192.168.'), row['interface'], int(ipaddress.ip_address(row['ip']))))
        if not result['addresses']:
            result['reason'] = '尚未识别到 NAS 局域网 IPv4 地址，请检查网卡连接'
        return result
