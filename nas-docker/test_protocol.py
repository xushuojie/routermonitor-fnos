"""Shared sampling, bounded replay, stale sources and persistent HTTP contracts."""
import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch

import api


class ProtocolTests(unittest.TestCase):
    def test_sampling_replay_reset_and_expiry(self):
        monitor = api.NetworkMonitor()
        clock = [10.0]
        raw = {'sample_time': 10, 'iface': 'physical:' + ','.join('ethernet%d' % i for i in range(30)),
               'counter_epoch': '100', 'rx_bytes': 0, 'tx_bytes': 0, 'counters': {}}
        with patch.object(api, 'network_snapshot', side_effect=lambda **kw: dict(raw)) as read, \
             patch.object(api.time, 'monotonic', side_effect=lambda: clock[0]):
            monitor.sample()
            first = monitor.response()
            self.assertTrue(first['gap'])
            self.assertEqual(len(first['source']), 16)
            self.assertEqual(first['rate'], [None, None])
            for i in range(1, 7):
                clock[0] = raw['sample_time'] = 10 + i * .2
                raw['rx_bytes'], raw['tx_bytes'] = i * 200, i * 100
                monitor.sample()
            current = monitor.response(first['seq'], first['epoch'])
            self.assertEqual(current['rate'], [1000, 500])
            self.assertEqual(len(current['points']), 4)
            self.assertTrue(current['gap'])
            self.assertLess(len(json.dumps(current)), 1024)
            before = read.call_count
            for _ in range(10):
                duplicate = monitor.response(current['seq'], current['epoch'])
                self.assertEqual(duplicate['points'], [])
                self.assertFalse(duplicate['gap'])
                self.assertEqual(monitor.latest()['sample_time'], 11.2)
            self.assertEqual(read.call_count, before)
            replay = monitor.response(current['seq'] - 2, current['epoch'])
            self.assertEqual(len(replay['points']), 2)
            self.assertFalse(replay['gap'])
            raw['counter_epoch'] = '101'
            clock[0] = raw['sample_time'] = 11.4
            raw['rx_bytes'] = 1
            monitor.sample()
            reset = monitor.response(current['seq'], current['epoch'])
            self.assertNotEqual(reset['epoch'], current['epoch'])
            self.assertEqual(reset['rate'], [None, None])
            self.assertTrue(reset['gap'])
            clock[0] = 13
            self.assertIsNone(monitor.latest())
            self.assertIsNone(monitor.response())

    def test_http_shared_snapshots_versions_missing_gpu_and_staleness(self):
        history = Mock()
        history.snapshot.return_value = {'rx_bytes': 1, 'tx_bytes': 2, 'coverage_seconds': 5, 'valid': True}
        network = api.NetworkMonitor()
        with patch.object(api, 'network_snapshot', return_value={
                'sample_time': api.time.monotonic(), 'iface': 'eth0', 'rx_bytes': 100,
                'tx_bytes': 200, 'counter_epoch': '1', 'counters': {}}):
            network.sample()
        metrics = api.Metrics(history, network)
        metrics.snapshot()
        server = api.ThreadingHTTPServer(('127.0.0.1', 0), api.handler_factory(metrics, 'test', network))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        other = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        def get(connection, path, token='test'):
            connection.request('GET', path, headers={'Authorization': 'Bearer ' + token})
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(int(response.getheader('Content-Length')), len(body))
            return response.status, json.loads(body)
        try:
            with patch.object(api, 'network_snapshot', side_effect=AssertionError('HTTP sampled hardware')), \
                 patch.object(metrics, 'snapshot', side_effect=AssertionError('HTTP sampled metrics')):
                status, net = get(client, '/net?v=2')
                self.assertEqual(status, 200)
                sock = client.sock
                second = get(other, '/net?v=2')[1]
                self.assertEqual({k: v for k, v in second.items() if k != 'age'},
                                 {k: v for k, v in net.items() if k != 'age'})
                status, data = get(client, '/status?display=1&v=2')
                self.assertEqual(status, 200)
                self.assertIsNone(data['gpu']['utilization'])
                self.assertLess(len(json.dumps(data)), 1024)
                self.assertEqual(data['traffic_24h']['rx_bytes'], 1)
                self.assertEqual(get(client, '/status?display=1')[1]['gpu']['utilization'], 0)
                self.assertEqual(get(client, '/net')[1]['iface'], 'eth0')
                self.assertIs(client.sock, sock)
                self.assertEqual(get(client, '/net?v=2&since=-1')[0], 400)
                with patch.object(api.time, 'monotonic', return_value=metrics.sampled_at + 4):
                    self.assertEqual(get(client, '/status?display=1&v=2')[0], 503)
                    self.assertEqual(get(client, '/net?v=2')[0], 503)
                self.assertEqual(get(client, '/net', token='wrong')[0], 401)
                self.assertIsNone(client.sock)
        finally:
            client.close()
            other.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
