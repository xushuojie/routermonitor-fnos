import copy, http.client, importlib.util, json, os, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import api, hardware_settings
from web import WebApp

SNAPSHOT = {'time': 1700000000, 'v': 2, 'seq': 1, 'age': .1, 'metric_age': {},
 'cpu': {'percent': 12, 'valid': True}, 'gpu': {'utilization': 0, 'backend': 'unavailable', 'valid': False},
 'memory': {'percent': 32, 'total': 16000000000, 'used': 5120000000, 'valid': True},
 'temp': [], 'temperature_summary': {'cpu': None, 'disk': None},
 'net': {'iface': 'eth0', 'rx_speed': 1200000, 'tx_speed': 800000},
 'disk_io': {'devices': 'physical:sda', 'read_speed': 1000000, 'write_speed': 500000, 'valid': True},
 'storage': {'total': 1000000000000, 'used': 500000000000, 'percent': 50, 'valid': True, 'filesystems': 1, 'volumes': [], 'mode': 'auto', 'reason': ''},
 'ups': {'watts': None, 'valid': False, 'source': 'unavailable'},
 'uptime': 12345, 'traffic_24h': {'rx_bytes': 20000000, 'tx_bytes': 12000000, 'coverage_seconds': 3600, 'valid': True}}

class Metrics:
 def read_snapshot(self): return copy.deepcopy(SNAPSHOT)
class Network:
 def latest(self): return {'iface': 'eth0', 'rx_bytes': 123, 'tx_bytes': 456, 'sample_time': 1, 'counter_epoch': 1}
 def response(self, since, epoch, **kwargs): return {'v': 2, 'seq': 3, 'epoch': 'test', 'points': [], 'since': since}
class Sources:
 host_network = True
 rows = {}
 settings = {'revision': 1, 'mode': 'auto', 'members': [], 'aliases': {}}
 api = type('TokenAPI', (), {'resolve_token': staticmethod(lambda: 'device-test-token')})
 def selected(self): return 'test', ['eth0']
 def interfaces(self): return {'interfaces': [], 'settings': self.settings, 'host_network': True, 'recommended': []}
 def preview(self, body): return {'valid': True, 'members': [], 'warnings': []}
 def save(self, body): self.settings = body; return body

class Server:
 def __init__(self, module, web=None):
  metrics = Metrics(); metrics.monitor = Network()
  self.http = module.LimitedHTTPServer(('127.0.0.1', 0), module.handler_factory(metrics, web.device_tokens.current if web else 'device-test-token', Network(), web))
  self.port = self.http.server_port
  self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
 def close(self): self.http.shutdown(); self.http.server_close(); self.thread.join()
 def request(self, path, method='GET', body=None, headers=None):
  conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
  data = None if body is None else json.dumps(body)
  h = dict(headers or {})
  if data is not None: h.setdefault('Content-Type', 'application/json')
  conn.request(method, path, data, h); response = conn.getresponse(); raw = response.read(); result = (response.status, dict(response.getheaders()), raw); conn.close()
  return result

class Integration(unittest.TestCase):
 def test_device_storage_does_not_gain_ui_metadata(self):
  sample = {'mode': 'selected', 'volumes': [{'id': 'private-id', 'uuid': 'private-uuid', 'selected_paths': ['/vol1'], 'path': '/vol1', 'valid': True, 'total': 100, 'used': 20, 'included': True}]}
  with patch('storage_discovery.read_snapshot', return_value=sample):
   value = api.storage_status()
   self.assertEqual(value['mode'], 'manual')
   self.assertEqual(value['volumes'][0], {'path': '/vol1', 'valid': True, 'total': 100, 'used': 20, 'included': True})
 def test_legacy_routes_identical(self):
  spec = importlib.util.spec_from_file_location('original_api', ROOT / 'tests/fixtures/legacy_api.py')
  old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
  servers = [Server(old), Server(api)]
  try:
   for path in ['/health', '/status', '/status?v=2', '/status?display=1', '/status?v=2&display=1', '/net', '/net?v=2', '/net?v=2&since=1', '/net?v=2&since=-1', '/missing']:
    for token in [None, 'Bearer wrong', 'Bearer device-test-token']:
     values = []
     for server in servers:
      status, headers, body = server.request(path, headers={'Authorization': token} if token else {})
      values.append((status, json.loads(body)))
     self.assertEqual(*values, msg=(path, token))
  finally:
   for server in servers: server.close()

 def test_settings_auth_password_and_persistence(self):
  with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'NAS_STATUS_HISTORY_DB': str(Path(folder)/'traffic.sqlite3'), 'NAS_STATUS_ADMIN_PASSWORD': '1'}):
   web = WebApp(Sources(), Metrics(), folder); server = Server(api, web)
   try:
    for path in ['/api/hardware', '/api/hardware-settings', '/api/diagnostics']:
     self.assertEqual(server.request(path)[0], 401)
    host = 'nas.example.com:18199'; base = {'Host': host, 'Origin': 'http://' + host}
    self.assertEqual(server.request('/api/login', 'POST', {'password': 'wrong'}, base)[0], 401)
    status, headers, body = server.request('/api/login', 'POST', {'password': '1'}, base)
    self.assertEqual(status, 200); value = json.loads(body)
    auth = {**base, 'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': value['csrf']}
    status, _, body = server.request('/api/capabilities', headers=auth)
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(body)['lan_access']['port'], server.port)
    status, _, body = server.request('/api/hardware-settings', headers=auth); self.assertEqual(status, 200)
    settings = json.loads(body); settings['profile'] = 'eco'; settings['power_mode'] = 'cpu_package'
    self.assertEqual(server.request('/api/hardware-settings', 'PUT', settings, base)[0], 403)
    status, _, body = server.request('/api/hardware-settings', 'PUT', settings, auth); self.assertEqual(status, 200)
    saved = json.loads(body); self.assertEqual(saved['profile'], 'eco'); self.assertEqual(saved['revision'], 2)
    self.assertEqual(hardware_settings.load_settings(folder)['profile'], 'eco')
    self.assertEqual(hardware_settings.load_settings(folder)['power_mode'], 'cpu_package')
    self.assertEqual(server.request('/api/hardware-settings','PUT',{**saved,'power_mode':'fake'},auth)[0],400)
    self.assertEqual(server.request('/api/hardware-settings', 'PUT', settings, auth)[0], 409)
    self.assertEqual(server.request('/api/hardware-settings', 'PUT', saved, {**auth, 'Origin': 'https://wrong.example'})[0], 403)
    with patch('storage_discovery.read_snapshot', return_value={'volumes': [{'id': 'root-id', 'path': '/', 'mounts': ['/']}, {'id': 'usb-uuid', 'path': '/mnt/usb', 'mounts': ['/mnt/usb']}] }):
     chosen = {**saved, 'storage_mode': 'selected', 'storage_paths': ['/mnt/usb/']}
     status, _, body = server.request('/api/hardware-settings', 'PUT', chosen, auth)
     self.assertEqual(status, 200); saved = json.loads(body)
     self.assertEqual(saved['storage_paths'], ['/mnt/usb'])
     self.assertEqual(saved['storage_volume_ids'], {'/mnt/usb': 'usb-uuid'})
    with patch('storage_discovery.read_snapshot', return_value={'volumes': [{'id': 'root-id', 'path': '/', 'mounts': ['/']}, {'id': 'usb-uuid', 'path': '/mnt/usb', 'mounts': [], 'valid': False}] }):
     chosen = {**saved, 'profile': 'standard'}
     status, _, body = server.request('/api/hardware-settings', 'PUT', chosen, auth)
     self.assertEqual(status, 200); saved = json.loads(body)
     self.assertEqual(saved['storage_volume_ids'], {'/mnt/usb': 'usb-uuid'})
    status, _, body = server.request('/api/diagnostics', headers=auth); self.assertEqual(status, 200)
    self.assertNotIn(b'device-test-token', body); self.assertNotIn(b'nas_admin', body)
    self.assertEqual(server.request('/api/password', 'PUT', {'current': '1', 'password': ''}, auth)[0], 400)
    self.assertEqual(server.request('/api/password', 'PUT', {'current': '1', 'password': 'x'}, auth)[0], 200)
    self.assertEqual(server.request('/api/hardware-settings', headers=auth)[0], 401)
    self.assertEqual(server.request('/api/login', 'POST', {'password': 'x'}, {**base, 'Origin': 'https://' + host})[0], 200)
    self.assertEqual(server.request('/api/login', 'POST', {'password': '1'}, base)[0], 401)
   finally: server.close()

 def test_corrupt_optional_settings_do_not_stop_collectors(self):
  with tempfile.TemporaryDirectory() as folder:
   Path(folder, 'hardware-settings.json').write_text('{broken')
   self.assertEqual(hardware_settings.load_settings(folder)['profile'], 'realtime')
   self.assertTrue(hardware_settings.settings_error(folder))

if __name__ == '__main__': unittest.main()
