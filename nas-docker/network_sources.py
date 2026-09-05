"""Host interface discovery, validated selection and per-interface rolling history."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import threading
import time


def attrs(data):
    result = {}
    while len(data) >= 4:
        length, kind = struct.unpack_from('HH', data)
        if length < 4 or length > len(data):
            break
        result[kind & 0x3fff] = data[4:length]
        data = data[(length + 3) & ~3:]
    return result


def dump(kind):
    """Read-only rtnetlink dump; bounded independently of interface count."""
    result = []
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0) as client:
        client.settimeout(.3)
        client.bind((0, 0))
        client.send(struct.pack('IHHII', 32, kind, 0x301, 1, 0) + bytes(16))
        deadline = time.monotonic() + .5
        size = 0
        while time.monotonic() < deadline:
            packet = client.recv(262144)
            size += len(packet)
            if size > 4 * 1024 * 1024:
                raise OSError('interface dump too large')
            while len(packet) >= 16:
                length, message, flags, seq, pid = struct.unpack_from('IHHII', packet)
                if length < 16 or length > len(packet):
                    raise OSError('invalid netlink response')
                if message == 3:
                    if flags & 0x10:
                        raise OSError('interface inventory changed during dump')
                    return result
                if message == 2:
                    raise OSError('netlink error')
                result.append(packet[16:length])
                packet = packet[(length + 3) & ~3:]
        raise OSError('interface dump timed out')


def read_links():
    result = []
    for data in dump(18):  # RTM_GETLINK
        family, pad, device_type, index, flags, change = struct.unpack_from('BBHiII', data)
        values = attrs(data[16:])
        if 3 not in values:
            continue
        info = attrs(values.get(18, b''))
        kind = info.get(1, b'').rstrip(b'\0').decode('utf-8', 'replace')
        stats = values.get(23, values.get(7, b''))
        counts = struct.unpack_from('8Q' if 23 in values else '8I', stats) if len(stats) >= (64 if 23 in values else 32) else None
        number = lambda key: struct.unpack_from('I', values[key])[0] if key in values else None
        mac = lambda key: ':'.join('%02x' % byte for byte in values.get(key, b''))
        result.append({'name': values[3].rstrip(b'\0').decode('utf-8', 'replace'), 'ifindex': index,
                       'kind': kind or ('loopback' if device_type == 772 else 'unknown'),
                       'master_index': number(10), 'parent_index': number(5) if kind != 'veth' else None,
                       'mac': mac(1), 'permanent_mac': mac(54), 'up': bool(flags & 1),
                       'carrier': bool(flags & 0x10000),
                       'counters': list(counts[2:4]) if counts else None,
                       'errors': list(counts[4:6]) if counts else None,
                       'drops': list(counts[6:8]) if counts else None})
    return result


def read_addresses():
    result = {}
    for data in dump(22):  # RTM_GETADDR
        family, prefix, flags, scope, index = struct.unpack_from('BBBBI', data)
        values = attrs(data[8:])
        value = values.get(2, values.get(1))
        if value and family in (socket.AF_INET, socket.AF_INET6):
            result.setdefault(index, []).append(socket.inet_ntop(family, value) + '/' + str(prefix))
    return result


def atomic_json(path, value):
    temporary = path.with_suffix('.next')
    with open(temporary, 'w', encoding='utf-8', opener=lambda name, flags: os.open(name, flags, 0o600)) as out:
        json.dump(value, out, ensure_ascii=False, separators=(',', ':'))
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SelectionError(ValueError):
    pass


class Sources:
    def __init__(self, api, data_dir):
        self.api = api
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'settings.json'
        self.lock = threading.RLock()
        self.rows = {}
        self.previous = {}
        self.rates = {}
        self.sampled_at = self.details_at = self.rate_at = 0
        self.boot = api.read_text(api.host_path(api.PROC_ROOT, '/proc/sys/kernel/random/boot_id'))
        self.legacy_epoch = time.time_ns()
        self.error = ''
        self.settings = json.loads(self.path.read_text()) if self.path.exists() else None
        self.host_network = False
        try:
            self.host_network = os.stat('/proc/self/ns/net').st_ino == os.stat(api.host_path(api.PROC_ROOT, '/proc/1/ns/net')).st_ino
        except OSError:
            pass
        if not self.host_network:
            # PID 1 namespace links may require ptrace permission. Cross-check host hardware instead.
            try:
                links = {row['name']: row for row in read_links()}
                physical = api.physical_interface_names()
                self.host_network = bool(physical) and all(
                    name in links and str(links[name]['ifindex']) == api.read_text(api.host_path(api.SYS_ROOT, f'/sys/class/net/{name}/ifindex'))
                    and links[name]['mac'] == api.read_text(api.host_path(api.SYS_ROOT, f'/sys/class/net/{name}/address'))
                    for name in physical)
            except (OSError, AttributeError):
                pass
        self.discontinuous = set()
        self.history_at = 0
        self.history_values = {}
        self.history_selected = None
        self.db = sqlite3.connect(self.root / 'interfaces.sqlite3', check_same_thread=False)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS samples(id TEXT,start REAL,end REAL,rx INTEGER,tx INTEGER);
            CREATE INDEX IF NOT EXISTS by_id_end ON samples(id,end);
            CREATE INDEX IF NOT EXISTS by_end ON samples(end);
            CREATE TABLE IF NOT EXISTS baseline(id TEXT PRIMARY KEY,stamp REAL,rx INTEGER,tx INTEGER,boot TEXT);
        ''')
        self.baselines = {row[0]: row[1:] for row in self.db.execute('SELECT * FROM baseline')}
        self.collect()
        if self.settings is None:
            with self.lock:
                names = api.selected_network_interfaces()[1]
                members = [{'id': row['id'], 'name': row['name']} for row in self.rows.values() if row['name'] in names]
                mode = 'auto' if api.CONFIGURED_IFACE == 'physical' else 'single' if len(members) == 1 else 'sum'
                self.settings = {'revision': 1, 'mode': mode, 'members': members, 'aliases': {},
                                 'legacy_scope': api.selected_network_interfaces()[0]}
                self.settings['legacy_source'] = {'scope': self.settings['legacy_scope'], 'ids': [m['id'] for m in members]}
                atomic_json(self.path, self.settings)

    def collect(self):
        now = time.monotonic()
        if not self.host_network:
            # Sysfs remains a host source in bridge deployments; addresses/topology cannot be asserted.
            raw = []
            for name in self.api.interface_names():
                counters = self.api.network_counters([name])
                raw.append({'name': name, 'ifindex': self.api.read_text(self.api.host_path(self.api.SYS_ROOT, f'/sys/class/net/{name}/ifindex')),
                            'kind': 'unknown', 'mac': self.api.read_text(self.api.host_path(self.api.SYS_ROOT, f'/sys/class/net/{name}/address')), 'permanent_mac': '', 'up': None, 'carrier': None,
                            'master_index': None, 'parent_index': None, 'counters': list(counters[name]) if counters else None,
                            'errors': None, 'drops': None})
        else:
            raw = read_links()
        detail_due = now - self.details_at >= 5 or not self.rows
        addresses = read_addresses() if detail_due and self.host_network else {}
        by_index = {row['ifindex']: row['name'] for row in raw}
        updated = {}
        old_by_name = {row['name']: row for row in self.rows.values()}
        for row in raw:
            name = row['name']
            base = Path(self.api.host_path(self.api.SYS_ROOT, '/sys/class/net')) / name
            old = old_by_name.get(name, {})
            if detail_due or not old:
                physical = (base / 'device').exists()
                hardware = os.path.realpath(base / 'device') if physical else ''
                row.update(physical=physical, hardware=hardware,
                           driver=Path(os.path.realpath(base / 'device/driver')).name if (base / 'device/driver').exists() else '',
                           addresses=addresses.get(row['ifindex'], []),
                           speed_mbps=self.api.read_text(str(base / 'speed')) or None)
                if physical:
                    row['kind'] = 'wireless' if (base / 'wireless').exists() else 'physical'
            else:
                row.update({key: old[key] for key in ('physical', 'hardware', 'driver', 'addresses', 'speed_mbps')})
                if row['physical']:
                    row['kind'] = old['kind']
            identity = [row['hardware'], row['permanent_mac'] or row['mac'], row['kind']] if row['physical'] else [self.boot, row['ifindex'], row['mac'], row['kind']]
            row['id'] = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]
            row['master'] = by_index.get(row['master_index'])
            row['parent'] = by_index.get(row['parent_index'])
            row['valid'] = row['counters'] is not None
            row['rx_speed'] = row['tx_speed'] = None
            updated[row['id']] = row
        with self.lock:
            for identity, previous in self.rows.items():
                current = updated.get(identity, {}).get('counters')
                old = previous.get('counters')
                if current is None or (old and any(a < b for a, b in zip(current, old))):
                    self.discontinuous.add(identity)
                    if self.settings and identity in self.legacy_source().get('ids', []):
                        self.legacy_epoch += 1
                    self.rates.pop(identity, None)
                    self.previous.pop(identity, None)
            if now - self.rate_at >= 1:
                span = now - self.rate_at
                self.rates = {}
                for identity, row in updated.items():
                    before = self.previous.get(identity)
                    values = row['counters']
                    if before and values and 0 < span <= 2 and all(a >= b for a, b in zip(values, before)):
                        self.rates[identity] = [round((a - b) / span) for a, b in zip(values, before)]
                self.previous = {identity: row['counters'] for identity, row in updated.items()}
                self.rate_at = now
            for identity, row in updated.items():
                rates = self.rates.get(identity, [None, None])
                row['rx_speed'], row['tx_speed'] = rates if row['valid'] else (None, None)
            self.rows = updated
            self.sampled_at = now
            if detail_due:
                self.details_at = now
            self.error = ''
            if self.settings and self.settings['mode'] == 'auto':
                members = [{'id': row['id'], 'name': row['name']} for row in updated.values() if row['physical']]
                if {m['id'] for m in members} != {m['id'] for m in self.settings['members']}:
                    candidate = {**self.settings, 'members': members, 'revision': self.settings['revision'] + 1}
                    candidate.pop('legacy_scope', None)
                    if {m['id'] for m in members} == set(self.legacy_source().get('ids', [])):
                        candidate['legacy_scope'] = self.legacy_source()['scope']
                    atomic_json(self.path, candidate)
                    self.settings = candidate
                    self.history_selected = None

    def legacy_source(self):
        if self.settings.get('legacy_source'):
            return self.settings['legacy_source']
        if self.settings.get('legacy_scope'):
            return {'scope': self.settings['legacy_scope'], 'ids': [m['id'] for m in self.settings['members']]}
        return {}

    def legacy_snapshot(self):
        # Keep the pre-GUI combined history running even when the screen selects a subset.
        with self.lock:
            legacy = self.legacy_source()
            if not legacy or time.monotonic() - self.sampled_at > 1:
                return None
            rows = [self.rows.get(identity) for identity in legacy['ids']]
            if not rows or any(row is None or not row['valid'] for row in rows):
                return None
            counters = {row['name']: tuple(row['counters']) for row in rows}
            return {'iface': legacy['scope'], 'rx_bytes': sum(v[0] for v in counters.values()),
                    'tx_bytes': sum(v[1] for v in counters.values()),
                    'counters': {'epoch': str(self.legacy_epoch), 'values': counters}}

    def selected(self):
        with self.lock:
            members = self.settings['members']
            names = [self.rows.get(member['id'], member)['name'] for member in members]
            return self.settings.get('legacy_scope') or 'selection:' + hashlib.sha256(','.join(sorted(m['id'] for m in members)).encode()).hexdigest()[:16], names

    def counters(self, names):
        with self.lock:
            if time.monotonic() - self.sampled_at > 1:
                return None
            selected = {m['id'] for m in self.settings['members']}
            rows = [self.rows.get(identity) for identity in selected]
            if not rows or any(row is None or not row['valid'] for row in rows):
                return None
            return {row['name']: tuple(row['counters']) for row in rows}

    def validate(self, value):
        if not isinstance(value, dict) or value.get('mode') not in ('auto', 'recommended', 'single', 'sum'):
            raise SelectionError('请选择统计方式')
        members = ([{'id': row['id']} for row in self.rows.values() if row['physical']]
                   if value['mode'] == 'auto' else value.get('members'))
        if not isinstance(members, list) or not 1 <= len(members) <= 256 or any(not isinstance(m, dict) or not isinstance(m.get('id'), str) for m in members):
            raise SelectionError('至少选择一个接口，最多 256 个')
        ids = [member['id'] for member in members]
        if len(set(ids)) != len(ids) or (value['mode'] == 'single' and len(ids) != 1):
            raise SelectionError('单接口模式只能选择一个接口；不能重复选择')
        rows = [self.rows.get(identity) for identity in ids]
        if time.monotonic() - self.sampled_at > 1 or any(row is None or not row['valid'] for row in rows):
            raise SelectionError('接口已消失、身份变化或计数不可读，请刷新后重新选择')
        if not self.host_network and len(rows) > 1 and any(not row['physical'] for row in rows):
            raise SelectionError('当前网络命名空间无法核对虚拟接口拓扑，请单独选择或改用 host 网络')
        names = {row['name'] for row in rows}
        all_names = {row['name']: row for row in self.rows.values()}
        for row in rows:
            pending = [row.get('master'), row.get('parent')]
            seen = {row['name']}
            while pending:
                parent = pending.pop()
                if not parent or parent in seen:
                    continue
                if parent in names:
                    raise SelectionError(f"{row['name']} 与上层接口 {parent} 不能合并，否则可能重复统计")
                seen.add(parent)
                ancestor = all_names.get(parent, {})
                pending.extend([ancestor.get('master'), ancestor.get('parent')])
        if len(rows) > 1 and any(row['kind'] == 'openvswitch' for row in rows):
            raise SelectionError('Open vSwitch 的完整转发关系无法验证，请单独选择该接口或选择物理端口层')
        aliases = value.get('aliases', {})
        if not isinstance(aliases, dict) or len(aliases) > 256 or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 40 or any(ord(c) < 32 for c in v) for k, v in aliases.items()):
            raise SelectionError('接口别名最多 40 个字符')
        if value['mode'] in ('auto', 'recommended') and any(not row['physical'] for row in rows):
            raise SelectionError('推荐物理端口模式只允许选择物理接口')
        return rows

    def preview(self, value):
        with self.lock:
            rows = self.validate(value)
            valid = all(row['rx_speed'] is not None for row in rows)
            return {'members': [row['name'] for row in rows], 'valid': valid,
                    'rx_speed': sum(row['rx_speed'] for row in rows) if valid else None,
                    'tx_speed': sum(row['tx_speed'] for row in rows) if valid else None,
                    'warnings': (['包含虚拟接口：合计不是逐包去重后的 NAS 对外总流量'] if any(not row['physical'] for row in rows) else []) + (['网桥自身计数不代表全部桥接转发流量'] if any(row['kind'] == 'bridge' for row in rows) else []),
                    'message': '未发现已知上下层重复；隧道或转发的重复无法仅凭接口关系完全排除'}

    def save(self, value):
        with self.lock:
            if value.get('revision') != self.settings['revision']:
                raise SelectionError('配置已被其他页面修改，请重新载入后再保存')
            rows = self.validate(value)
            unchanged = {r['id'] for r in rows} == {m['id'] for m in self.settings['members']}
            candidate = {'revision': self.settings['revision'] + 1, 'mode': value['mode'],
                         'members': [{'id': row['id'], 'name': row['name']} for row in rows],
                         'aliases': value.get('aliases', {})}
            legacy = self.legacy_source()
            if legacy:
                candidate['legacy_source'] = legacy
                if {r['id'] for r in rows} == set(legacy['ids']):
                    candidate['legacy_scope'] = legacy['scope']
            atomic_json(self.path, candidate)
            self.settings = candidate
            if not unchanged:
                self.history_selected = None
            elif self.history_selected:
                self.history_selected = (candidate["revision"], *self.history_selected[1:])
            return candidate

    def interfaces(self):
        with self.lock:
            stale = time.monotonic() - self.sampled_at > 1
            rows = [{**row, 'valid': row['valid'] and not stale,
                     'rx_speed': None if stale else row['rx_speed'], 'tx_speed': None if stale else row['tx_speed'],
                     'history': {**self.history_values.get(identity, {}), 'valid': bool(self.history_values.get(identity, {}).get('valid') and time.time() - self.history_at <= 10 and row['valid'] and not stale)}} for identity, row in self.rows.items()]
            existing = set(self.rows)
            rows.extend({**member, 'kind': 'missing', 'physical': False, 'valid': False,
                         'rx_speed': None, 'tx_speed': None, 'addresses': []}
                        for member in self.settings['members'] if member['id'] not in existing)
            return {'interfaces': sorted(rows, key=lambda row: (not row['physical'], row['name'])),
                    'settings': self.settings, 'host_network': self.host_network, 'age': round(time.monotonic() - self.sampled_at, 2),
                    'error': self.error, 'recommended': [row['id'] for row in rows if row['physical'] and row['valid']]}

    def record_history(self):
        now = time.time()
        with self.lock:
            values = {key: row['counters'] for key, row in self.rows.items() if row['valid']} if time.monotonic() - self.sampled_at <= 1 else {}
            settings = self.settings
            discontinuous = self.discontinuous
            self.discontinuous = set()
        with self.db:
            if any(now < before[0] for before in self.baselines.values()):
                self.db.execute("DELETE FROM samples")
                self.baselines = {}
            self.db.execute('DELETE FROM samples WHERE end <= ?', (now - 86400,))
            self.db.execute('DELETE FROM baseline')
            for identity, value in values.items():
                before = self.baselines.get(identity)
                if before and identity not in discontinuous:
                    stamp, rx, tx, boot = before
                    if boot == self.boot and 0 < now - stamp <= 10 and value[0] >= rx and value[1] >= tx:
                        delta = value[0] - rx, value[1] - tx
                        # Compress consecutive idle intervals; coverage is retained independently of bytes.
                        updated = self.db.execute('UPDATE samples SET end=? WHERE id=? AND end=? AND rx=0 AND tx=0',
                                                  (now, identity, stamp)) if delta == (0, 0) else None
                        if updated is None or not updated.rowcount:
                            self.db.execute('INSERT INTO samples VALUES (?,?,?,?,?)', (identity, stamp, now, *delta))
                self.db.execute('INSERT INTO baseline VALUES (?,?,?,?,?)', (identity, now, *value, self.boot))
            self.baselines = {key: (now, *value, self.boot) for key, value in values.items()}
        history = {}
        for identity in values:
            row = self.db.execute('SELECT SUM(rx*(end-MAX(start,?))/(end-start)), SUM(tx*(end-MAX(start,?))/(end-start)), SUM(end-MAX(start,?)) FROM samples WHERE id=? AND end>?',
                                  (now - 86400, now - 86400, now - 86400, identity, now - 86400)).fetchone()
            history[identity] = {'rx_bytes': round(row[0] or 0), 'tx_bytes': round(row[1] or 0),
                                 'coverage_seconds': min(86400, int(row[2] or 0)), 'valid': bool(row[2])}
        selected = [member['id'] for member in settings['members']]
        series = [[tuple(row) for row in self.db.execute('SELECT start,end,rx,tx FROM samples WHERE id=? AND end>? ORDER BY start', (identity, now - 86400))] for identity in selected]
        ranges = [(now - 86400, now)]
        for rows in series:
            ranges = intersect(ranges, [(max(now - 86400, a), b) for a, b, rx, tx in rows])
        total = [0., 0.]
        for rows in series:
            cursor = 0
            for a, b, rx, tx in rows:
                while cursor < len(ranges) and ranges[cursor][1] <= a:
                    cursor += 1
                for index in range(cursor, len(ranges)):
                    start, end = ranges[index]
                    if start >= b:
                        break
                    share = max(0, min(b, end) - max(a, start)) / (b - a)
                    total[0] += rx * share
                    total[1] += tx * share
        coverage = int(sum(b - a for a, b in ranges)) if selected else 0
        combined = {'rx_bytes': round(total[0]), 'tx_bytes': round(total[1]), 'coverage_seconds': coverage,
                    'valid': bool(coverage and all(identity in values for identity in selected)), 'window_seconds': 86400,
                    'iface': self.selected()[0], 'basis': 'common_coverage', 'sample_age_seconds': 0}
        with self.lock:
            self.history_at = now
            self.history_values = history
            self.history_selected = (settings['revision'], now, combined)

    def history(self, legacy):
        with self.lock:
            stored = self.history_selected
            if time.monotonic() - self.sampled_at > 1 or any(not self.rows.get(m['id'], {}).get('valid') for m in self.settings['members']):
                return {'rx_bytes': None, 'tx_bytes': None, 'coverage_seconds': 0, 'valid': False, 'window_seconds': 86400}
            if self.settings.get('legacy_scope') == legacy.get('iface') and legacy.get('valid'):
                if stored is None or legacy['coverage_seconds'] > stored[2]['coverage_seconds']:
                    return {**legacy, 'basis': 'legacy_group'}
            if stored and stored[0] == self.settings['revision'] and 0 <= time.time() - stored[1] <= 10:
                return {**stored[2], 'sample_age_seconds': round(time.time() - stored[1], 1)}
            return {'rx_bytes': None, 'tx_bytes': None, 'coverage_seconds': 0, 'valid': False, 'window_seconds': 86400}

    def close(self):
        self.db.close()


def intersect(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        start, end = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if start < end:
            result.append((start, end))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return result
