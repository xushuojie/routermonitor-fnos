"""Persistent administrator hardware choices, separate from device API settings."""
import copy
import json
import os
from pathlib import Path, PurePosixPath
import threading

from network_sources import atomic_json, SelectionError

DEFAULTS = {'revision': 1, 'storage_mode': 'auto', 'storage_paths': [], 'storage_volume_ids': {},
            'cpu_sensor': 'auto', 'disk_sensor': 'auto', 'gpu': 'auto',
            'ups_mode': 'auto', 'ups_socket': '', 'ups_host': '127.0.0.1',
            'ups_port': 3493, 'ups_name': '', 'profile': 'realtime', 'power_mode': 'auto'}
PROFILES = {
    'realtime': {'network': .2, 'status': 1, 'storage': 30, 'sensors': 5, 'ups': 2},
    'standard': {'network': .5, 'status': 1, 'storage': 45, 'sensors': 10, 'ups': 5},
    'eco': {'network': 1, 'status': 2, 'storage': 60, 'sensors': 15, 'ups': 10},
}
_lock = threading.RLock()
_cache = {}
_errors = {}

def settings_path(data_dir=None):
    root = Path(data_dir) if data_dir is not None else Path(os.environ.get('NAS_STATUS_HISTORY_DB', '/data/traffic.sqlite3')).parent
    return root / 'hardware-settings.json'

def validate(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise SelectionError('硬件设置字段无效')
    result = {**copy.deepcopy(DEFAULTS), **value}
    if type(result['revision']) is not int or result['revision'] < 1:
        raise SelectionError('设置版本无效，请重新载入')
    for key, choices in [('storage_mode', ('auto', 'selected')), ('ups_mode', ('auto', 'local', 'remote', 'off')), ('profile', tuple(PROFILES)), ('power_mode', ('auto', 'ups_output', 'platform', 'cpu_package'))]:
        if result[key] not in choices:
            raise SelectionError('请选择有效的采集方式')
    paths = result['storage_paths']
    if not isinstance(paths, list) or len(paths) > 256:
        raise SelectionError('存储路径列表无效')
    normalized = []
    for path in paths:
        if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or len(path) > 4096 or any(ord(c) < 32 for c in path) or '..' in PurePosixPath(path).parts:
            raise SelectionError('请填写宿主机绝对挂载路径，例如 /vol1')
        path = str(PurePosixPath(path))
        if path not in normalized:
            normalized.append(path)
    result['storage_paths'] = normalized
    identities = result['storage_volume_ids']
    if not isinstance(identities, dict) or len(identities) > 256 or any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 4096 or len(v) > 1024 for k, v in identities.items()):
        raise SelectionError('存储卷标识无效')
    result['storage_volume_ids'] = {k: v for k, v in identities.items() if k in normalized}
    if result['storage_mode'] == 'selected' and not normalized:
        raise SelectionError('请至少选择一个存储卷')
    for key in ('cpu_sensor', 'disk_sensor', 'gpu', 'ups_socket', 'ups_host', 'ups_name'):
        value = result[key]
        if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 for c in value):
            raise SelectionError('硬件来源设置无效')
    if not all(result[key] for key in ('cpu_sensor', 'disk_sensor', 'gpu')):
        raise SelectionError('请选择硬件来源或自动识别')
    if type(result['ups_port']) is not int or not 1 <= result['ups_port'] <= 65535:
        raise SelectionError('UPS 端口必须为 1–65535')
    if result['ups_mode'] == 'local' and not result['ups_socket'].startswith('/'):
        raise SelectionError('请选择本机 UPS socket')
    if result['ups_mode'] == 'remote':
        if not result['ups_host'] or any(c in result['ups_host'] for c in '/\\ \t') or not result['ups_name'] or any(c.isspace() or c in '\\"' for c in result['ups_name']):
            raise SelectionError('远程 UPS 需要主机地址及有效的设备名称')
    return result

def _read_settings(data_dir=None):
    path = settings_path(data_dir)
    with _lock:
        try:
            stat = path.stat(); stamp = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            return copy.deepcopy(DEFAULTS)
        cached = _cache.get(str(path))
        if cached and cached[0] == stamp:
            return copy.deepcopy(cached[1])
        if stat.st_size > 131072:
            raise SelectionError('硬件设置文件过大')
        value = validate(json.loads(path.read_text(encoding='utf-8')))
        _cache[str(path)] = (stamp, value)
        return copy.deepcopy(value)

def load_settings(data_dir=None):
    key = str(settings_path(data_dir))
    try:
        value = _read_settings(data_dir)
        _errors.pop(key, None)
        return value
    except (OSError, ValueError, TypeError):
        _errors[key] = '硬件设置文件无法读取或格式无效，暂用默认设置；请在设置页重新保存'
        return copy.deepcopy(DEFAULTS)

def settings_error(data_dir=None):
    load_settings(data_dir)
    return _errors.get(str(settings_path(data_dir)), '')

def save_settings(value, data_dir=None):
    candidate = validate(value)
    with _lock:
        current = load_settings(data_dir)
        if candidate['revision'] != current['revision']:
            raise SelectionError('其他页面已修改设置，请重新载入后再保存')
        candidate['revision'] += 1
        path = settings_path(data_dir); path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, candidate)
        _cache.pop(str(path), None)
        return candidate

def intervals(settings=None):
    selected = settings if settings is not None else load_settings()
    return dict(PROFILES.get(selected.get('profile'), PROFILES['realtime']))
