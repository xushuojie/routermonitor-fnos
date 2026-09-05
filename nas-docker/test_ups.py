import os
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import api


class UpsTests(unittest.TestCase):
    def read(self, payload, delay=0):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/ups'
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen(1)
                commands = []
                def respond():
                    with server.accept()[0] as client:
                        commands.append(client.recv(128))
                        if payload is None:
                            time.sleep(1.1)
                        else:
                            time.sleep(delay)
                            try:
                                client.sendall(payload)
                            except BrokenPipeError:
                                pass
                thread = threading.Thread(target=respond)
                thread.start()
                with open(path + '.pid', 'w') as pid:
                    pid.write('123')
                with patch.dict(os.environ, NAS_STATUS_UPS_SOCKET=''), patch.object(
                        api.glob, 'glob', return_value=[path, path + '.pid']):
                    start = time.monotonic()
                    result = api.ups_status()
                    self.assertLess(time.monotonic() - start, 1.08)
                thread.join()
                self.assertEqual(commands, [b'DUMPALL\n'])
                return result

    def test_driver_readings_and_unavailable_values(self):
        base = b'SETINFO device.model "W120"\nSETINFO device.mfr "WL"\nSETINFO output.voltage "12.19"\nSETINFO output.current "1.160"\nSETINFO ups.alarm "No battery installed!"\nDATAOK\nDUMPDONE\n'
        value = self.read(base)
        self.assertEqual(value['watts'], 14.1)
        self.assertEqual(self.read(base, delay=.4)['watts'], 14.1)
        self.assertEqual(value['source'], 'dc_voltage_current')
        self.assertEqual(value['alarm'], 'No battery installed!')
        for raw, expected in ((b'0', 0), (b'2.871', 35.0), (b'4', 48.8)):
            self.assertEqual(self.read(base.replace(b'1.160', raw))['watts'], expected)
        for payload in (base.replace(b'DATAOK', b'DATASTALE'), base.replace(b'1.160', b'nan'),
                        base.replace(b'1.160', b'-1'), base.replace(b'W120', b'AC-UPS'),
                        base.replace(b'DUMPDONE\n', b''), b'x' * 32768, None):
            self.assertFalse(self.read(payload)['valid'])
        self.assertEqual(self.read(b'SETINFO ups.realpower "42.5"\nDATAOK\nDUMPDONE\n')['watts'], 42.5)

    def test_timeout_retention_expiry_and_explicit_failure(self):
        monitor = api.UpsMonitor()
        good = {"watts": 14.1, "valid": True}
        timeout = {"watts": None, "valid": False, "reason": "timeout"}
        stale = {"watts": None, "valid": False}
        with patch.object(api, 'ups_status', return_value=good), patch.object(api.time, 'monotonic', return_value=100):
            monitor.sample()
        with patch.object(api, 'ups_status', return_value=timeout):
            monitor.sample()
        self.assertEqual(monitor.snapshot(105.9)['watts'], 14.1)
        self.assertIsNone(monitor.snapshot(106)['watts'])
        with patch.object(api, 'ups_status', return_value=good), patch.object(api.time, 'monotonic', return_value=107):
            monitor.sample()
        self.assertEqual(monitor.snapshot(107)['watts'], 14.1)
        with patch.object(api, 'ups_status', return_value=stale):
            monitor.sample()
        self.assertIsNone(monitor.snapshot(107.1)['watts'])

    def test_blocked_driver_does_not_block_http_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        def slow_read():
            entered.set()
            release.wait(1)
            return {"watts": 14.1, "valid": True}
        metrics = api.Metrics(Mock())
        with patch.object(api, 'ups_status', side_effect=slow_read):
            metrics.ups.start()
            try:
                self.assertTrue(entered.wait(1))
                started = time.monotonic()
                self.assertIsNone(metrics.snapshot()['ups']['watts'])
                self.assertLess(time.monotonic() - started, .1)
            finally:
                release.set()
                metrics.ups.close()
