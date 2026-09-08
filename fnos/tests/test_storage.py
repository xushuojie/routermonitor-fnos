"""Filesystem fixtures: no disks, mount changes, or user files are accessed."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import storage_discovery as sd


def mount(path, filesystem='ext4', source='/dev/sda1', device='8:1', mount_root='/'):
    return {'path': path, 'filesystem': filesystem, 'source': source,
            'device': device, 'mount_root': mount_root}


def encode(value):
    return value.replace('\\', '\\134').replace(' ', '\\040').replace('\t', '\\011')


def mountinfo(rows, prefix=''):
    output = []
    for index, row in enumerate(rows, 10):
        path = prefix + (row['path'] if row['path'] != '/' or not prefix else '')
        output.append(f"{index} 1 {row['device']} {encode(row['mount_root'])} {encode(path)} rw - {row['filesystem']} {encode(row['source'])} rw")
    return '\n'.join(output)


def counters(total, used):
    return SimpleNamespace(f_frsize=1, f_bsize=4096, f_blocks=total, f_bfree=total-used)


class StorageTests(unittest.TestCase):
    def collect(self, rows, settings=None, mounted=None, sizes=None, directories=None, previous=None, uuids=None):
        host = mountinfo(rows)
        container = mountinfo(rows if mounted is None else mounted, '/hostfs')
        observed = []
        sizes = sizes or {}
        directories = directories or set()

        def read(path, *args, **kwargs):
            name = str(path).replace('\\', '/')
            return host if name == '/hostfs/proc/1/mountinfo' else container

        def statvfs(path):
            observed.append(path)
            value = sizes.get(path, (100, 40))
            if isinstance(value, Exception):
                raise value
            return counters(*value)

        def lstat(path):
            if path not in directories:
                raise FileNotFoundError(path)
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)

        with patch.object(sd.Path, 'read_text', read), \
             patch.object(sd.os, 'statvfs', statvfs, create=True), \
             patch.object(sd.os, 'lstat', lstat), \
             patch.object(sd, '_uuid_map', return_value=uuids or {}), \
             patch.object(sd, '_device_source', side_effect=lambda root, source: sd._source(source)):
            result = sd.collect(settings=settings or {'storage_mode': 'auto'}, previous=previous)
        return result, observed

    def test_system_data_and_external_filesystems_are_discovered(self):
        rows = [mount('/'), mount('/vol1', 'btrfs', '/dev/sdb1', '0:31'),
                mount('/mnt/USB Disk', 'exfat', '/dev/sdc1', '8:33'),
                mount('/media/ntfs', 'ntfs3', '/dev/sdd1', '8:49'),
                mount('/boot/efi', 'vfat', '/dev/sda2', '8:2'),
                mount('/custom/storage', 'xfs', '/dev/md0', '9:0')]
        result, observed = self.collect(rows)
        self.assertTrue(result['valid'])
        self.assertEqual(result['filesystems'], 6)
        self.assertEqual((result['total'], result['used'], result['percent']), (600, 240, 40.0))
        self.assertIn('/hostfs/', observed)
        self.assertIn('/hostfs/mnt/USB Disk', observed)
        self.assertEqual(len({row['id'] for row in result['volumes']}), 6)
        self.assertTrue(all(row['included'] and row['valid'] for row in result['volumes']))

    def test_pseudo_network_container_and_disk_images_are_excluded(self):
        rows = [mount('/'), mount('/proc', 'proc', 'proc', '0:1'),
                mount('/mnt/share', 'nfs4', 'server:/share', '0:2'),
                mount('/mnt/smb', 'cifs', '//server/share', '0:3'),
                mount('/var/lib/docker/overlay2/x/merged', 'overlay', 'overlay', '0:4'),
                mount('/vol1/@docker/subvol', 'btrfs', '/dev/sdb1', '0:5'),
                mount('/image', 'ext4', '/dev/loop0', '7:0')]
        result, observed = self.collect(rows)
        self.assertEqual([row['path'] for row in result['volumes']], ['/'])
        self.assertEqual(observed, ['/hostfs/'])

    def test_btrfs_subvolumes_and_bind_mounts_count_once(self):
        rows = [mount('/vol1', 'btrfs', '/dev/sdb1[/@]', '0:31', '/@'),
                mount('/mnt/photos', 'btrfs', '/dev/sdb1[/@photos]', '0:32', '/@photos'),
                mount('/', 'ext4', '/dev/sda1', '8:1'),
                mount('/bind/root', 'ext4', '/dev/root', '8:1', '/')]
        result, _ = self.collect(rows)
        self.assertEqual(result['filesystems'], 2)
        self.assertEqual(result['total'], 200)
        self.assertEqual(result['volumes'][0]['mounts'], ['/mnt/photos', '/vol1'])

    def test_selected_alias_or_descendant_maps_to_longest_mount(self):
        rows = [mount('/'), mount('/vol1', 'btrfs', '/dev/sdb1', '0:31'),
                mount('/vol1/usb', 'exfat', '/dev/sdc1', '8:33')]
        settings = {'storage_mode': 'selected', 'storage_paths': ['/vol1/usb/photos', '/vol1/usb']}
        result, observed = self.collect(rows, settings, directories={'/hostfs/vol1/usb/photos'})
        self.assertTrue(result['valid'])
        self.assertEqual(result['total'], 100)
        self.assertEqual(len(result['volumes']), 3)
        selected = [row for row in result['volumes'] if row['selected']]
        self.assertEqual(selected[0]['path'], '/vol1/usb')
        self.assertNotIn('/hostfs/vol1/usb/photos', observed)

    def test_pseudo_descendant_cannot_fall_back_to_root(self):
        rows = [mount('/'), mount('/proc', 'proc', 'proc', '0:1')]
        result, observed = self.collect(rows, {'storage_mode': 'selected', 'storage_paths': ['/proc/1']})
        self.assertFalse(result['valid'])
        self.assertIsNone(result['total'])
        self.assertIn('虚拟文件系统', result['volumes'][-1]['reason'])
        self.assertNotIn('/hostfs/proc/1', observed)

    def test_nonexistent_or_traversal_path_is_missing_not_root(self):
        for path in ('/not-a-real-volume', '/vol1/../etc', 'relative'):
            with self.subTest(path=path):
                result, _ = self.collect([mount('/')], {'storage_mode': 'selected', 'storage_paths': [path]})
                self.assertFalse(result['valid'])
                self.assertFalse(result['volumes'][0]['selected'])

    def test_missing_propagation_prevents_incomplete_auto_total(self):
        rows = [mount('/'), mount('/vol1', 'btrfs', '/dev/sdb1', '0:31')]
        result, observed = self.collect(rows, mounted=rows[:1])
        self.assertFalse(result['valid'])
        self.assertIsNone(result['total'])
        root = next(row for row in result['volumes'] if row['path'] == '/')
        self.assertEqual(root['total'], 100)
        self.assertTrue(root['valid'])
        self.assertNotIn('/hostfs/vol1', observed)

    def test_unselected_unreadable_volume_does_not_break_selected_total(self):
        rows = [mount('/'), mount('/vol1', 'btrfs', '/dev/sdb1', '0:31')]
        result, _ = self.collect(rows, {'storage_mode': 'selected', 'storage_paths': ['/']}, mounted=rows[:1])
        self.assertTrue(result['valid'])
        self.assertEqual(len(result['volumes']), 2)
        self.assertFalse(result['volumes'][0]['valid'])

    def test_alias_fallback_when_canonical_mount_is_unreadable(self):
        rows = [mount('/vol1'), mount('/alias', mount_root='/')]
        result, observed = self.collect(rows, sizes={'/hostfs/vol1': PermissionError('denied')})
        self.assertTrue(result['valid'])
        self.assertEqual(result['volumes'][0]['sampled_path'], '/alias')
        self.assertEqual(observed, ['/hostfs/vol1', '/hostfs/alias'])

    def test_replaced_container_mount_is_not_sampled(self):
        rows = [mount('/vol1')]
        mounted = [mount('/vol1', source='/dev/sdz1', device='65:1')]
        result, observed = self.collect(rows, mounted=mounted)
        self.assertFalse(result['valid'])
        self.assertEqual(observed, [])

    def test_invalid_counters_do_not_produce_negative_or_zero_usage(self):
        for values in ((0, 0), (100, -1), (100, 101)):
            with self.subTest(values=values):
                result, _ = self.collect([mount('/')], sizes={'/hostfs/': values})
                self.assertFalse(result['valid'])
                self.assertIsNone(result['volumes'][0]['percent'])

    def test_uuid_identity_survives_device_renumbering(self):
        first, _ = self.collect([mount('/vol1', source='/dev/sdb1', device='8:17')], uuids={'/dev/sdb1': 'volume-uuid'})
        second, _ = self.collect([mount('/vol2', source='/dev/sdd1', device='8:49')], uuids={'/dev/sdd1': 'volume-uuid'})
        self.assertEqual(first['volumes'][0]['id'], second['volumes'][0]['id'])

    def test_disconnected_selected_volume_cannot_turn_into_root(self):
        rows = [mount('/'), mount('/mnt/usb', 'exfat', '/dev/sdb1', '8:17')]
        settings = {'storage_mode': 'selected', 'storage_paths': ['/mnt/usb']}
        first, _ = self.collect(rows, settings)
        second, _ = self.collect(rows[:1], settings, directories={'/hostfs/mnt', '/hostfs/mnt/usb'}, previous=first)
        self.assertFalse(second['valid'])
        self.assertFalse(second['volumes'][0]['selected'])
        self.assertIn('身份发生变化', second['volumes'][-1]['reason'])
        third, _ = self.collect(rows[:1], settings, directories={'/hostfs/mnt', '/hostfs/mnt/usb'}, previous=second)
        self.assertFalse(third['valid'])

    def test_persisted_identity_detects_replacement_after_restart(self):
        settings = {'storage_mode': 'selected', 'storage_paths': ['/vol1'], 'storage_volume_ids': {'/vol1': 'fs-prior-id'}}
        result, _ = self.collect([mount('/vol1')], settings)
        self.assertFalse(result['valid'])
        self.assertIsNone(result['total'])
        self.assertEqual(result['volumes'][-1]['id'], 'fs-prior-id')

    def test_zfs_shared_pool_keeps_per_dataset_values_without_double_total(self):
        rows = [mount('/zpool', 'zfs', 'tank', '0:31'), mount('/zpool/quota', 'zfs', 'tank/quota', '0:32')]
        result, _ = self.collect(rows, sizes={'/hostfs/zpool': (1000, 400), '/hostfs/zpool/quota': (100, 20)})
        self.assertFalse(result['valid'])
        self.assertIsNone(result['total'])
        self.assertTrue(all(row['valid'] for row in result['volumes']))
        self.assertEqual([row['total'] for row in result['volumes']], [1000, 100])
        self.assertIn('共享存储池', result['reason'])
        selected, _ = self.collect(rows, {'storage_mode': 'selected', 'storage_paths': ['/zpool/quota']})
        self.assertTrue(selected['valid'])
        self.assertIn('配额', next(row for row in selected['volumes'] if row['selected'])['reason'])

    def test_fuseblk_and_mount_escape_are_supported(self):
        result, _ = self.collect([mount('/USB My Disk', 'fuseblk', '/dev/sdb1', '0:33')])
        self.assertTrue(result['valid'])
        self.assertEqual(result['volumes'][0]['path'], '/USB My Disk')

    def test_read_snapshot_retains_invalid_per_volume_details_and_dynamic_age(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'storage.json'
            value = {'sampled_at': 850, 'valid': False, 'total': 10, 'used': 2, 'percent': 20,
                     'filesystems': 1, 'mode': 'selected', 'reason': 'missing', 'volumes': [{'total': 100, 'valid': True}]}
            filename.write_text(json.dumps(value), encoding='utf-8')
            with patch.object(sd.time, 'time', return_value=1000), patch('hardware_settings.intervals', return_value={'storage': 60}):
                result = sd.read_snapshot(filename)
                self.assertIsNone(result['total'])
                self.assertEqual(result['volumes'][0]['total'], 100)
            with patch.object(sd.time, 'time', return_value=1000), patch('hardware_settings.intervals', return_value={'storage': 30}):
                self.assertEqual(sd.read_snapshot(filename)['volumes'], [])

    def test_settings_change_triggers_sample_at_next_one_second_poll(self):
        initial = {'storage_mode': 'auto', 'profile': 'eco', 'revision': 1}
        changed = {'storage_mode': 'selected', 'storage_paths': ['/vol1'], 'profile': 'eco', 'revision': 2}
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'NAS_STATUS_STORAGE_SNAPSHOT': str(Path(directory) / 'storage.json')}), \
             patch.object(sd, '_settings', side_effect=[initial, initial, changed]), \
             patch.object(sd, 'collect', return_value=sd._empty()) as collect, \
             patch.object(sd.time, 'monotonic', side_effect=[0, 1, 2]), \
             patch.object(sd.time, 'sleep', side_effect=[None, None, KeyboardInterrupt]) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                sd.run_collector()
            self.assertEqual(collect.call_count, 2)
            self.assertEqual(collect.call_args.kwargs['settings'], changed)
            self.assertTrue(all(call.args == (1,) for call in sleep.call_args_list))


if __name__ == '__main__':
    unittest.main()
