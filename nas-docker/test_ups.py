import os
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import api


class UpsTests(unittest.TestCase):
    def read(self, payload):
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
                            time.sleep(.3)
                        else:
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
                    self.assertLess(time.monotonic() - start, .28)
                thread.join()
                self.assertEqual(commands, [b'DUMPALL\n'])
                return result

    def test_driver_readings_and_unavailable_values(self):
        base = b'SETINFO device.model "W120"\nSETINFO device.mfr "WL"\nSETINFO output.voltage "12.19"\nSETINFO output.current "1.160"\nSETINFO ups.alarm "No battery installed!"\nDATAOK\nDUMPDONE\n'
        value = self.read(base)
        self.assertEqual(value['watts'], 14.1)
        self.assertEqual(value['source'], 'dc_voltage_current')
        self.assertEqual(value['alarm'], 'No battery installed!')
        for raw, expected in ((b'0', 0), (b'2.871', 35.0), (b'4', 48.8)):
            self.assertEqual(self.read(base.replace(b'1.160', raw))['watts'], expected)
        for payload in (base.replace(b'DATAOK', b'DATASTALE'), base.replace(b'1.160', b'nan'),
                        base.replace(b'1.160', b'-1'), base.replace(b'W120', b'AC-UPS'),
                        base.replace(b'DUMPDONE\n', b''), b'x' * 32768, None):
            self.assertFalse(self.read(payload)['valid'])
        self.assertEqual(self.read(b'SETINFO ups.realpower "42.5"\nDATAOK\nDUMPDONE\n')['watts'], 42.5)

    def test_two_second_cache_clears_failed_read(self):
        metrics = api.Metrics(Mock())
        with patch.object(api, 'ups_status', side_effect=[{'watts': 14.1}, {'watts': None}]) as read:
            with patch.object(api.time, 'monotonic', return_value=100):
                self.assertEqual(metrics.snapshot()['ups']['watts'], 14.1)
            with patch.object(api.time, 'monotonic', return_value=101):
                self.assertEqual(metrics.snapshot()['ups']['watts'], 14.1)
            with patch.object(api.time, 'monotonic', return_value=102):
                self.assertIsNone(metrics.snapshot()['ups']['watts'])
            self.assertEqual(read.call_count, 2)
