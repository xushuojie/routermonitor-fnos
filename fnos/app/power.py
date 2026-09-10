"""Independent power readings and explicit selection; UPS stays UPS-only."""
import json
import math
import os
from pathlib import Path
import time

SNAPSHOT = os.environ.get('NAS_STATUS_POWER_SNAPSHOT', '/host/monitor/power.json')
LABELS = {'ups_output': 'UPS 输出功率', 'platform': '平台功率', 'cpu_package': 'CPU 封装功率'}


def unavailable(reason, scope='unavailable'):
    return {'watts': None, 'valid': False, 'source': 'unavailable', 'scope': scope,
            'label': LABELS.get(scope, '功率不可用'), 'reason': reason}


def valid_watts(row):
    return (row.get('valid') is True and type(row.get('watts')) in (int, float)
            and math.isfinite(row['watts']) and 0 <= row['watts'] <= 128000)


def normalize(row, scope, age):
    if not isinstance(row, dict):
        return unavailable('来源没有提供读数', scope)
    if not valid_watts(row):
        return unavailable(str(row.get('reason') or '功率传感器读数无效')[:500], scope)
    if row.get('source') != 'powercap' or row.get('scope') != scope:
        return unavailable('功率来源或范围无效', scope)
    return {'watts': row['watts'], 'valid': True, 'source': 'powercap', 'scope': scope,
            'label': LABELS[scope], 'age_seconds': round(age, 1),
            'reason': 'CPU 封装区间平均功率，非整机功率' if scope == 'cpu_package'
            else '平台区间平均功率，范围由硬件定义，非插座功率'}


def host_power():
    try:
        path = Path(SNAPSHOT)
        info = path.stat()
        now = time.time()
        if info.st_size > 16384 or not 0 <= now-info.st_mtime <= 15:
            return unavailable('宿主机功率采集缓存过期或过大')
        data = json.loads(path.read_text(encoding='utf-8'))
        age = now-data['sampled_at']
        if data.get('schema') != 1 or not 0 <= age <= 15:
            return unavailable('宿主机功率采集时间无效或过期')
        row = data['power']
        # Accept the previous single-source cache during an in-place upgrade.
        raw = row.get('sources', {row.get('scope'): row})
        sources = {scope: normalize(raw.get(scope), scope, age)
                   for scope in ('platform', 'cpu_package')}
        chosen = next((r for r in sources.values() if r['valid']),
                      unavailable(str(row.get('reason') or '平台与 CPU 功率均不可用')[:500]))
        return {**chosen, 'sources': sources, 'age_seconds': round(age, 1)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return unavailable('未发现可用的宿主机功率缓存，请检查采集进程及内核 powercap 支持')


def select(ups, fallback, mode='auto'):
    ups_row = ({**ups, 'scope': 'ups_output', 'label': LABELS['ups_output'],
                'reason': 'UPS 输出端功率；若连接多个设备，不等于 NAS 单机功率'}
               if valid_watts(ups) else unavailable(str(ups.get('reason') or 'UPS 功率不可用')[:500], 'ups_output'))
    raw = fallback.get('sources', {fallback.get('scope'): fallback})
    sources = {'ups_output': ups_row}
    for scope in ('platform', 'cpu_package'):
        row = raw.get(scope)
        sources[scope] = (dict(row) if isinstance(row, dict) else
                          unavailable(fallback.get('reason') or '该来源没有可用读数', scope))
    if mode == 'auto':
        chosen = next((row for row in sources.values() if valid_watts(row)),
                      unavailable('UPS、平台与 CPU 封装功率均不可用'))
    else:
        chosen = sources.get(mode, unavailable('功率来源设置无效'))
    return {**chosen, 'mode': mode, 'sources': sources}
