"""Exercise GUI access control and persistent source selection through real HTTP."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import api
from network_sources import Sources, SelectionError, intersect
from web import WebApp


def interface(name, index, kind='', master=None, parent=None, rx=1000, tx=2000):
    return {'name': name, 'ifindex': index, 'kind': kind or 'physical', 'master_index': master, 'parent_index': parent,
            'mac': '00:00:00:00:00:%02x' % index, 'permanent_mac': '', 'up': True, 'carrier': True,
            'counters': [rx, tx], 'errors': [0, 0], 'drops': [0, 0]}


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('eth0', 'eth1'):
            (self.root / 'devices' / name).mkdir(parents=True)
            base = self.root / 'sys/class/net' / name
            base.mkdir(parents=True)
            (base / 'device').symlink_to(self.root / 'devices' / name)
        (self.root / 'proc/sys/kernel/random').mkdir(parents=True)
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('boot')
        self.patches = [patch.object(api, 'SYS_ROOT', str(self.root)), patch.object(api, 'PROC_ROOT', str(self.root)),
                        patch.object(api, 'CONFIGURED_IFACE', 'physical')]
        for item in self.patches:
            item.start()
        self.source = Sources(api, self.root / 'data')
        self.source.host_network = True
        self.raw = [interface('eth0', 1), interface('eth1', 2, master=3), interface('bond0', 3, 'bond'),
                    interface('vlan20', 4, 'vlan', parent=1), interface('br0', 5, 'bridge')]
        self.collect()
        physical = [row for row in self.source.rows.values() if row['physical']]
        self.source.settings = {'revision': 1, 'mode': 'sum', 'members': [{'id': r['id'], 'name': r['name']} for r in physical], 'aliases': {}}

    def tearDown(self):
        self.source.close()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def collect(self):
        with patch('network_sources.read_links', side_effect=lambda: [dict(row) for row in self.raw]), patch('network_sources.read_addresses', return_value={}):
            self.source.collect()

    def choice(self, *names):
        return {'revision': self.source.settings['revision'], 'mode': 'single' if len(names) == 1 else 'sum',
                'members': [{'id': row['id'], 'name': row['name']} for row in self.source.rows.values() if row['name'] in names], 'aliases': {}}

    def test_auto_members_follow_hotplug_manual_selection_does_not(self):
        value = self.choice('eth0')
        value['mode'] = 'auto'
        self.source.save(value)
        self.assertEqual(set(self.source.selected()[1]), {'eth0', 'eth1'})
        self.raw = [row for row in self.raw if row['name'] != 'eth1']
        self.collect()
        self.assertEqual(self.source.selected()[1], ['eth0'])
        self.raw.append(interface('eth1', 2, master=3))
        self.collect()
        self.assertEqual(set(self.source.selected()[1]), {'eth0', 'eth1'})
        self.source.save(self.choice('eth0'))
        self.collect()
        self.assertEqual(self.source.selected()[1], ['eth0'])
        self.assertEqual(json.loads(self.source.path.read_text())['mode'], 'single')

    def test_topology_missing_identity_and_atomic_revision(self):
        for names in [('eth1', 'bond0'), ('eth0', 'vlan20')]:
            with self.assertRaises(SelectionError):
                self.source.preview(self.choice(*names))
        self.source.settings['legacy_scope'] = 'physical:eth0,eth1'
        first = self.choice('eth0')
        result = self.source.save(first)
        self.assertEqual(result['revision'], 2)
        self.assertEqual(self.source.legacy_snapshot()['iface'], 'physical:eth0,eth1')
        self.assertEqual(self.source.legacy_snapshot()['rx_bytes'], 2000)
        with self.assertRaises(SelectionError):
            self.source.save(first)
        restored = self.source.save(self.choice('eth0', 'eth1'))
        self.assertEqual(restored['legacy_scope'], 'physical:eth0,eth1')
        self.source.save(self.choice('eth0'))
        previous = self.source.settings
        with patch('network_sources.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.source.save(self.choice('eth1'))
        self.assertEqual(self.source.settings, previous)
        self.assertEqual(json.loads(self.source.path.read_text()), previous)
        self.raw[0]['name'] = 'renamed'
        (self.root / 'sys/class/net/eth0').rename(self.root / 'sys/class/net/renamed')
        self.collect()
        self.assertEqual(self.source.selected()[1], ['renamed'])
        self.raw = self.raw[1:]
        self.collect()
        self.assertIsNone(self.source.counters([]))
        self.assertTrue(any(row['kind'] == 'missing' for row in self.source.interfaces()['interfaces']))

    def test_history_idle_compression_common_coverage_and_counter_reset(self):
        self.source.settings = self.choice('eth0', 'eth1')
        self.source.settings['revision'] = 1
        for step in range(4):
            with patch('network_sources.time.time', return_value=100 + step * 5):
                self.source.record_history()
        self.assertEqual(self.source.db.execute('SELECT COUNT(*) FROM samples').fetchone()[0], len(self.raw))
        self.assertEqual(self.source.history_selected[2]['coverage_seconds'], 15)
        for row in self.raw[:2]:
            row['counters'] = [row['counters'][0] + 100, row['counters'][1] + 50]
        self.collect()
        with patch('network_sources.time.time', return_value=120):
            self.source.record_history()
        self.assertEqual(self.source.history_selected[2]['rx_bytes'], 200)
        self.raw[0]['counters'] = [0, 0]
        self.collect()
        self.raw[0]['counters'] = [5000, 5000]
        self.collect()
        with patch('network_sources.time.time', return_value=125):
            self.source.record_history()
        self.assertEqual(self.source.history_selected[2]['rx_bytes'], 200)
        self.assertEqual(self.source.history_selected[2]['coverage_seconds'], 20)
        self.assertEqual(intersect([(0, 5), (8, 15)], [(2, 10)]), [(2, 5), (8, 10)])

    def test_admin_session_csrf_readonly_token_and_restart_persistence(self):
        metrics = Mock()
        app = WebApp(self.source, metrics, self.root / 'data')
        password = (self.root / 'data/initial-admin-password.txt').read_text().strip()
        server = api.ThreadingHTTPServer(('127.0.0.1', 0), api.handler_factory(metrics, 'device-token', web=app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        cookie = csrf = ''
        def request(path, method='GET', value=None, origin_value=origin, token=False, use_csrf=True):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            headers = {'Origin': origin_value, 'Cookie': cookie}
            if token:
                headers['Authorization'] = 'Bearer device-token'
            if use_csrf:
                headers['X-CSRF-Token'] = csrf
            if value is not None:
                headers['Content-Type'] = 'application/json'
            connection.request(method, path, json.dumps(value) if value is not None else None, headers)
            response = connection.getresponse()
            data = response.read()
            result = response.status, data, response.getheader('Set-Cookie')
            connection.close()
            return result
        try:
            self.assertEqual(request('/')[0], 200)
            self.assertEqual(request('/api/settings', token=True)[0], 401)
            self.assertEqual(request('/api/login', 'POST', {'password': password}, origin_value='http://evil.test')[0], 403)
            status, body, set_cookie = request('/api/login', 'POST', {'password': password})
            self.assertEqual(status, 200)
            self.assertIn('HttpOnly', set_cookie)
            self.assertIn('SameSite=Strict', set_cookie)
            cookie = set_cookie.split(';')[0]
            csrf = json.loads(body)['csrf']
            self.assertEqual(request('/api/settings', 'PUT', self.choice('eth0'), use_csrf=False)[0], 403)
            self.assertEqual(request('/api/settings', 'PUT', self.choice('eth0'))[0], 200)
            settings = json.loads(request('/api/settings')[1])
            self.assertEqual([m['name'] for m in settings['members']], ['eth0'])
            self.assertEqual(json.loads(self.source.path.read_text()), settings)
            self.assertEqual(request('/api/settings', 'PUT', {**settings, 'revision': 1})[0], 409)
            self.assertEqual(request('/api/logout', 'POST', {})[0], 200)
            self.assertEqual(request('/api/settings')[0], 401)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
