"""Host filesystem capacity inventory. Only mount metadata and counters are read."""
import hashlib
import json
import os
import posixpath
import re
import stat
import time
from collections import defaultdict
from pathlib import Path


LOCAL_FILESYSTEMS = frozenset((
    'btrfs', 'ext2', 'ext3', 'ext4', 'xfs', 'zfs', 'bcachefs', 'f2fs', 'jfs',
    'reiserfs', 'nilfs2', 'ntfs', 'ntfs3', 'fuseblk', 'fuse.ntfs-3g',
    'exfat', 'fuse.exfat', 'vfat', 'msdos', 'hfs', 'hfsplus', 'udf', 'iso9660',
))


def _unescape(value):
    return re.sub(r'\\(040|011|012|134)', lambda match: chr(int(match[1], 8)), value)


def _source(value):
    # Some mountinfo exporters append a Btrfs subvolume to the block device.
    return re.sub(r'\[.*\]$', '', value)


def _identity(row):
    filesystem = row['filesystem']
    if filesystem in ('btrfs', 'zfs'):
        return filesystem, _source(row['source'])
    return filesystem, row['device']


def _identifier(identity):
    return 'fs-' + hashlib.sha256('\0'.join(identity).encode()).hexdigest()[:20]


def _device_source(root, source):
    """Resolve device aliases using symlink metadata; never open a block device."""
    source = _source(source)
    for _ in range(8):
        if not source.startswith('/dev/'):
            break
        try:
            target = os.readlink(root.rstrip('/') + source)
        except OSError:
            break
        source = posixpath.normpath(target if target.startswith('/') else posixpath.join(posixpath.dirname(source), target))
    return source


def _uuid_map(root):
    result = {}
    try:
        for path in Path(root.rstrip('/') + '/dev/disk/by-uuid').iterdir():
            source = _device_source(root, '/dev/disk/by-uuid/' + path.name)
            if source.startswith('/dev/') and not source.startswith('/dev/disk/by-uuid/'):
                result[source] = path.name
    except OSError:
        pass
    return result


def _under(path, directory):
    return directory == '/' or path == directory or path.startswith(directory.rstrip('/') + '/')


def _local(row):
    if row['filesystem'] not in LOCAL_FILESYSTEMS:
        return False
    if re.match(r'^/dev/(?:loop|ram)[0-9]+(?:p[0-9]+)?$', _source(row['source'])):
        return False  # Disk images consume space already counted on their backing filesystem.
    for path in (row['path'], row['mount_root']):
        if re.search(r'(?:^|/)(?:@docker|@containerd)(?:/|$)', path):
            return False
        if re.match(r'^/(?:var/lib|run)/(?:docker|containerd)(?:/|$)', path):
            return False
    return True


def _mounts(mountinfo, prefix=''):
    result = {}
    prefix = prefix.rstrip('/')
    for line in mountinfo.splitlines():
        fields = line.split()
        try:
            separator = fields.index('-')
            path = _unescape(fields[4])
            if prefix:
                if path == prefix:
                    path = '/'
                elif path.startswith(prefix + '/'):
                    path = path[len(prefix):]
                else:
                    continue
            if not path.startswith('/') or not re.fullmatch(r'[0-9]+:[0-9]+', fields[2]):
                continue
            filesystem, source = fields[separator + 1:separator + 3]
            row = {'path': path, 'filesystem': filesystem, 'source': _unescape(source),
                   'device': fields[2], 'mount_root': _unescape(fields[3])}
            row['identity'] = _identity(row)
            result[path] = row  # A later overmount hides an earlier entry at this path.
        except (ValueError, IndexError):
            continue
    return list(result.values())


def _order(row):
    path = row['path']
    match = re.fullmatch(r'/vol([0-9]+)', path)
    return (0 if match else 1 if path == '/' else 2,
            int(match[1]) if match else 0,
            row['mount_root'] != '/', path.count('/'), path)


def volumes(mountinfo, prefix=''):
    """Parse local mounts, including aliases; compatible with the old helper."""
    return sorted((row for row in _mounts(mountinfo, prefix) if _local(row)), key=_order)


def _empty(reason='', mode='auto'):
    return {'total': None, 'used': None, 'percent': None, 'valid': False,
            'filesystems': 0, 'volumes': [], 'mode': mode, 'reason': reason}


def _settings(data_dir=None):
    from hardware_settings import load_settings
    return load_settings(data_dir)


def _child_directory(root, path, mountpoint):
    """Validate descendants without following arbitrary symlinks or opening files."""
    if path == mountpoint:
        return True
    current = mountpoint.rstrip('/')
    for component in path[len(current):].strip('/').split('/'):
        current += '/' + component
        if not stat.S_ISDIR(os.lstat(root.rstrip('/') + current).st_mode):
            return False
    return True


def _select(path, mounts, root):
    if not isinstance(path, str) or not path.startswith('/') or '\0' in path:
        return None, '请选择宿主机绝对目录'
    normalized = posixpath.normpath(path)
    if '..' in path.split('/') or normalized.startswith('//'):
        return None, '目录不能包含上级路径或特殊路径前缀'
    candidates = [row for row in mounts if _under(normalized, row['path'])]
    if not candidates:
        return None, '未发现此目录所在的已挂载文件系统'
    row = max(candidates, key=lambda item: len(item['path']))
    if not _local(row):
        return None, '此目录属于网络共享、虚拟文件系统或容器内部挂载'
    try:
        if not _child_directory(root, normalized, row['path']):
            return None, '目录不存在、不是目录或包含符号链接'
    except OSError:
        return None, '目录不存在或无法读取目录元数据'
    return row, ''


def _sample(group, mounted, root, uuids):
    canonical = min(group, key=_order)
    identity = canonical['identity']
    row = {key: value for key, value in canonical.items() if key != 'identity'}
    sources = sorted({_device_source(root, item['source']) for item in group})
    filesystem_uuid = next((uuids[source] for source in sources if source in uuids), None)
    stable = (canonical['filesystem'], 'uuid:' + filesystem_uuid if filesystem_uuid else sources[0])
    row.update(id=_identifier(stable), uuid=filesystem_uuid, mounts=sorted(item['path'] for item in group),
               total=None, used=None, percent=None, valid=False, included=False,
               selected=False, selected_paths=[], reason='卷不可读或挂载尚未传播')
    for candidate in sorted(group, key=_order):
        actual = mounted.get(candidate['path'])
        if not actual or actual['identity'] != identity or actual['mount_root'] != candidate['mount_root']:
            continue
        try:
            counters = os.statvfs(root.rstrip('/') + candidate['path'])
            size = counters.f_frsize or counters.f_bsize
            capacity = counters.f_blocks * size
            if capacity <= 0 or not 0 <= counters.f_bfree <= counters.f_blocks:
                raise OSError('invalid filesystem counters')
            consumed = (counters.f_blocks - counters.f_bfree) * size
            row.update(total=capacity, used=consumed, percent=round(consumed * 100 / capacity, 1),
                       valid=True, reason='', sampled_path=candidate['path'])
            if row['filesystem'] == 'zfs':
                row['reason'] = '显示此 ZFS 数据集的文件系统可见容量，可能受配额影响'
            return row
        except OSError:
            continue
    return row


def collect(root='/hostfs', mountinfo='/proc/self/mountinfo', settings=None,
            data_dir=None, previous=None):
    """Return all local volumes and a complete, deduplicated selected/auto total.

    No selected path is passed to statvfs: only verified mountpoints are sampled.
    previous detects selected mounts falling back onto a parent after unmount.
    storage_volume_ids remembers the selected identity across process restarts.
    """
    settings = _settings(data_dir) if settings is None else settings
    mode = 'selected' if settings.get('storage_mode') == 'selected' else 'auto'
    result = _empty(mode=mode)
    try:
        host_mounts = _mounts(Path(root.rstrip('/') + '/proc/1/mountinfo').read_text())
        mounted = {row['path']: row for row in _mounts(Path(mountinfo).read_text(), root)}
    except (OSError, UnicodeError):
        return {**result, 'reason': '宿主机挂载信息不可读'}
    groups = defaultdict(list)
    for row in host_mounts:
        if _local(row):
            groups[row['identity']].append(row)
    uuids = _uuid_map(root)
    sampled = {identity: _sample(group, mounted, root, uuids) for identity, group in groups.items()}
    missing = []
    expected = dict(settings.get('storage_volume_ids') or {})
    if previous and previous.get('mode') == 'selected':
        for row in previous.get('volumes', []):
            for path in row.get('selected_paths', []):
                if row.get('id', '').startswith('fs-'):
                    expected.setdefault(path, row['id'])
    if mode == 'auto':
        for row in sampled.values():
            row['selected'] = True
    else:
        paths = settings.get('storage_paths') or []
        for path in dict.fromkeys(paths):
            candidate, reason = _select(path, host_mounts, root)
            if candidate:
                row = sampled[candidate['identity']]
                if path in expected and expected[path] != row['id']:
                    candidate, reason = None, '原选定卷已卸载或身份发生变化，请重新选择'
                else:
                    row['selected'] = True
                    row['selected_paths'].append(path)
            if not candidate:
                missing.append({'id': expected.get(path, 'missing-' + hashlib.sha256(str(path).encode()).hexdigest()[:20]),
                                'path': path, 'mounts': [], 'filesystem': '', 'source': '',
                                'device': '', 'mount_root': '', 'total': None, 'used': None,
                                'percent': None, 'valid': False, 'included': False,
                                'selected': True, 'selected_paths': [path], 'reason': reason})
    rows = sorted(sampled.values(), key=_order) + missing
    selected = [row for row in rows if row['selected']]
    zfs_pools = defaultdict(list)
    for row in selected:
        if row['filesystem'] == 'zfs':
            zfs_pools[_source(row['source']).split('/')[0]].append(row)
    ambiguous = {row['id'] for group in zfs_pools.values() if len(group) > 1 for row in group}
    for row in rows:
        row['included'] = row['selected'] and row['valid'] and row['id'] not in ambiguous
        if row['id'] in ambiguous:
            row['reason'] = '同一 ZFS 池的多个数据集共享空间且可能有不同配额，无法可靠相加'
        elif not row['selected'] and not row['reason']:
            row['reason'] = '未选入合计'
    included = [row for row in rows if row['included']]
    total = sum(row['total'] for row in included)
    used = sum(row['used'] for row in included)
    valid = bool(selected and total > 0 and all(row['valid'] for row in selected) and not ambiguous)
    reason = ''
    if ambiguous:
        reason = '选定的 ZFS 数据集共享存储池，保留逐卷容量；合计不可用，请每池只选择一个数据集'
    elif not selected:
        reason = '尚未选择存储卷' if mode == 'selected' else '未发现已挂载的本地磁盘文件系统'
    elif not valid:
        reason = '存在缺失或不可读的选定卷，停止输出不完整合计'
    result.update(total=total if valid else None, used=used if valid else None,
                  percent=round(used * 100 / total, 1) if valid else None, valid=valid,
                  filesystems=len(included), volumes=rows, reason=reason)
    return result


def inventory(root='/hostfs', mountinfo='/proc/self/mountinfo', settings=None, data_dir=None):
    """Return the full local inventory, including currently unselected rows."""
    return collect(root, mountinfo, settings, data_dir)['volumes']


def read_snapshot(filename):
    invalid = _empty('自动发现采集器未就绪或数据过期')
    try:
        from hardware_settings import intervals
        max_age = max(90, intervals()['storage'] * 3)
        with open(filename, encoding='utf-8') as source:
            value = json.loads(source.read(524289))
        if not isinstance(value, dict) or not 0 <= time.time() - value['sampled_at'] <= max_age:
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


def run_collector():
    from hardware_settings import intervals
    output = Path(os.environ.get('NAS_STATUS_STORAGE_SNAPSHOT', '/discovery/storage.json'))
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    previous_settings = None
    deadline = 0
    while True:
        settings = _settings()
        if settings != previous_settings or time.monotonic() >= deadline:
            previous = {**collect(settings=settings, previous=previous), 'sampled_at': time.time()}
            temporary = output.with_suffix('.next')
            temporary.write_text(json.dumps(previous, ensure_ascii=False), encoding='utf-8')
            temporary.replace(output)
            previous_settings = settings
            deadline = time.monotonic() + intervals(settings)['storage']
        time.sleep(1)  # Detect saved settings within one second, including atomic replacements.


if __name__ == '__main__':
    run_collector()
