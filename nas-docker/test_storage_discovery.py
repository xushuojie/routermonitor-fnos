import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import storage_discovery as discovery


class StorageDiscoveryTests(unittest.TestCase):
    def test_mount_add_remove_duplicate_and_missing_never_silent(self):
        def line(n, path, source=None):
            return f'{n} 1 8:{n} / {path} rw - btrfs {source or "/dev/disk" + str(n)} rw\n'
        mounts = line(1, '/') + line(2, '/vol1') + line(3, '/vol2')
        mounts += line(4, '/vol2/docker') + line(5, '/vol4', '/dev/disk3')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'proc/1').mkdir(parents=True)
            info = root / 'proc/1/mountinfo'
            info.write_text(mounts)
            own = root / 'own-mountinfo'
            def write_mounts(text):
                info.write_text(text)
                own.write_text(text.replace(' /vol', ' ' + directory + '/vol'))
            write_mounts(mounts)
            stats = SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=100, f_bfree=40)
            with patch.object(discovery.os, 'statvfs', return_value=stats):
                value = discovery.collect(directory, own)
                self.assertTrue(value['valid'])
                self.assertEqual(value['total'], 819200)
                self.assertEqual(value['filesystems'], 2)
                self.assertFalse(value['volumes'][-1]['included'])
                write_mounts(mounts + line(7, '/vol7'))
                self.assertEqual(discovery.collect(directory, own)['filesystems'], 3)
                write_mounts(line(2, '/vol1'))
                self.assertEqual(discovery.collect(directory, own)['filesystems'], 1)
                own.write_text(line(99, directory + '/vol1'))  # Old/unrelated mount in container.
                value = discovery.collect(directory, own)
                self.assertFalse(value['valid'])
                self.assertIsNone(value['total'])

    def test_expired_or_malformed_snapshot_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'storage.json'
            value = {'sampled_at': time.time(), 'valid': True, 'volumes': [], 'total': 100, 'used': 40}
            filename.write_text(json.dumps(value))
            self.assertEqual(discovery.read_snapshot(filename)['percent'], 40)
            for bad in ([1], {**value, 'sampled_at': time.time() - 91}, {**value, 'used': 101}, {**value, 'total': '100'}):
                filename.write_text(json.dumps(bad))
                self.assertFalse(discovery.read_snapshot(filename)['valid'])
