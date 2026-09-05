"""Isolated, unprivileged fnOS capacity collector. Never opens user files."""
import json
import os
import re
import time
from pathlib import Path


def volumes(mountinfo, prefix=""):
    result = {}
    for line in mountinfo.splitlines():
        fields = line.split()
        try:
            separator = fields.index('-')
            path = fields[4]
            if prefix:
                if not path.startswith(prefix + '/'):
                    continue
                path = path[len(prefix):]
            filesystem, source = fields[separator + 1:separator + 3]
            if re.fullmatch(r'/vol[0-9]+', path) and filesystem in ('btrfs', 'ext4', 'xfs', 'zfs'):
                # btrfs subvolumes may have distinct device numbers on one filesystem.
                identity = (filesystem, source if filesystem in ('btrfs', 'zfs') else fields[2])
                result[path] = {'path': path, 'filesystem': filesystem, 'source': source,
                                'identity': identity, 'device': fields[2], 'mount_root': fields[3]}
        except (ValueError, IndexError):
            continue
    return sorted(result.values(), key=lambda row: int(row['path'][4:]))


def collect(root='/hostfs', mountinfo='/proc/self/mountinfo'):
    result = {'total': None, 'used': None, 'percent': None, 'valid': False,
              'filesystems': 0, 'volumes': [], 'mode': 'auto', 'reason': ''}
    try:
        rows = volumes(Path(root + '/proc/1/mountinfo').read_text())
        mounted = {row['path']: row for row in volumes(Path(mountinfo).read_text(), root)}
    except OSError:
        return {**result, 'reason': '宿主机挂载信息不可读'}
    seen = set()
    total = used = 0
    missing = False
    for row in rows:
        row = dict(row)
        identity = tuple(row.pop('identity'))
        row['valid'] = False
        try:
            path = root + row['path']
            actual = mounted.get(row['path'], {})
            # fnOS btrfs stat.st_dev can differ from mountinfo even on the host.
            # Compare mount identities in both namespaces instead of inode device IDs.
            if any(actual.get(key) != row[key] for key in ('device', 'source', 'filesystem', 'mount_root')):
                raise OSError('mount not propagated')
            stats = os.statvfs(path)
            size = stats.f_frsize or stats.f_bsize
            capacity = stats.f_blocks * size
            if capacity <= 0 or not 0 <= stats.f_bfree <= stats.f_blocks:
                raise OSError('invalid filesystem counters')
            consumed = (stats.f_blocks - stats.f_bfree) * size
            row.update(total=capacity, used=consumed, valid=True, included=identity not in seen)
            if identity not in seen:
                total += capacity
                used += consumed
                seen.add(identity)
        except OSError:
            row['reason'] = '卷不可读或挂载尚未传播'
            missing = True
        result['volumes'].append(row)
    valid = bool(total and not missing)
    result.update(total=total if valid else None, used=used if valid else None,
                  percent=round(used * 100 / total, 1) if valid else None,
                  valid=valid, filesystems=len(seen),
                  reason='存在不可读数据卷，停止输出不完整合计' if missing else '' if rows else '未发现已挂载的 fnOS 数据卷')
    return result


def read_snapshot(filename):
    invalid = {'total': None, 'used': None, 'percent': None, 'valid': False,
               'filesystems': 0, 'volumes': [], 'mode': 'auto', 'reason': '自动发现采集器未就绪或数据过期'}
    try:
        with open(filename) as source:
            value = json.loads(source.read(262145))
        if not isinstance(value, dict) or not 0 <= time.time() - value['sampled_at'] <= 90:
            return invalid
        if not isinstance(value['volumes'], list) or len(value['volumes']) > 256:
            return invalid
        if value['valid']:
            total, used = value['total'], value['used']
            if type(total) is not int or type(used) is not int or not 0 <= used <= total or total <= 0:
                return invalid
            value['percent'] = round(used * 100 / total, 1)
        else:
            value.update(total=None, used=None, percent=None)
        return value
    except (OSError, ValueError, TypeError, KeyError):
        return invalid


if __name__ == '__main__':
    output = Path(os.environ.get('NAS_STATUS_STORAGE_SNAPSHOT', '/discovery/storage.json'))
    while True:
        value = {**collect(), 'sampled_at': time.time()}
        temporary = output.with_suffix('.next')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(output)
        time.sleep(30)
