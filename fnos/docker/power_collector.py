"""Read Linux powercap energy counters without changing limits or permissions.

Values describe a platform domain or complete CPU packages, never wall power.
https://www.kernel.org/doc/html/latest/power/powercap/powercap.html
"""
from pathlib import Path
import math
import re
import time


def unavailable(reason):
    return {'watts': None, 'valid': False, 'source': 'unavailable',
            'scope': 'unavailable', 'label': '功率不可用', 'reason': reason}


class PowerReader:
    def __init__(self, root='/sys'):
        self.root = Path(root)
        self.previous = {}
        self.known_packages = set()

    def sample(self, now=None):
        now = time.monotonic() if now is None else now
        domains = {}
        # Class entries are symlink aliases on some kernels. Resolve and deduplicate.
        for base in (self.root/'devices/virtual/powercap', self.root/'class/powercap'):
            for pattern in ('*/energy_uj', '*/*/energy_uj', '*/*/*/energy_uj'):
                for energy in base.glob(pattern):
                    try:
                        directory = energy.parent.resolve()
                        name = (directory/'name').read_text().strip()
                        if name != 'psys' and not re.fullmatch(r'package-\d+', name):
                            continue  # cores/uncore/DRAM overlap package energy.
                        key = str(directory)
                        domains[key] = (directory, name)
                    except (OSError, UnicodeError):
                        continue
        current, readings = {}, {}
        self.known_packages.update(name for _, name in domains.values() if name.startswith('package-'))
        for key, (directory, name) in sorted(domains.items()):
            watts = None
            try:
                if (directory/'enabled').exists() and (directory/'enabled').read_text().strip() == '0':
                    raise ValueError('disabled domain')
                energy = int((directory/'energy_uj').read_text())
                maximum = int((directory/'max_energy_range_uj').read_text())
                if not 0 <= energy < maximum:
                    raise ValueError('invalid counter')
                identity = (directory/'energy_uj').stat().st_ino
                current[key] = (now, energy, maximum, identity)
                old = self.previous.get(key)
                if old and old[2:] == (maximum, identity):
                    dt = now - old[0]
                    delta = energy - old[1]
                    # A backwards counter may be a reset, not a wrap. Only accept
                    # an edge-to-edge transition, and never bridge a long gap.
                    if delta < 0 and old[1] >= maximum*.9 and energy <= maximum*.1:
                        delta += maximum
                    if .1 <= dt <= 15 and delta >= 0 and maximum / 1_000_000 > 2000 * dt:
                        candidate = delta / 1_000_000 / dt
                        if math.isfinite(candidate) and 0 <= candidate <= 2000:
                            watts = candidate
                readings.setdefault(name, []).append((key, watts))
            except (OSError, ValueError, UnicodeError):
                # Keep the domain in the topology, so an unreadable socket does
                # not turn a multi-socket total into a misleading partial value.
                readings.setdefault(name, []).append((key, None))
        topology_changed = set(current) != set(self.previous)
        self.previous = current
        if topology_changed:
            row = unavailable('能耗计数器预热或硬件范围变化，等待两个连续样本')
            return {**row, 'sources': {'platform': dict(row), 'cpu_package': dict(row)}}
        # Prefer one interface per named domain; MMIO/MSR aliases must not add.
        def choose(name):
            entries = readings[name]
            entries.sort(key=lambda row: ('mmio' in row[0], row[0]))
            return next((w for _, w in entries if w is not None), None)
        sources = {'platform': unavailable('未发现可读的平台能耗计数器，或计数器正在预热'),
                   'cpu_package': unavailable('未发现可读的 CPU 封装计数器，或样本重置、过期、部分缺失')}
        if 'psys' in readings:
            watts = choose('psys')
            if watts is not None:
                sources['platform'] = {'watts': round(watts, 1), 'valid': True, 'source': 'powercap',
                        'scope': 'platform', 'label': '平台功率',
                        'reason': '平台能耗计数器区间平均值；范围由硬件定义，不等于插座整机功率'}
        packages = [choose(name) if name in readings else None for name in sorted(self.known_packages)]
        if packages and all(w is not None for w in packages):
            sources['cpu_package'] = {'watts': round(sum(packages), 1), 'valid': True, 'source': 'powercap',
                    'scope': 'cpu_package', 'label': 'CPU 封装功率',
                    'reason': 'CPU 封装能耗计数器区间平均值；不含硬盘、独立显卡及电源损耗',
                    'packages': len(packages)}
        chosen = next((row for row in sources.values() if row['valid']),
                      unavailable('未发现可读的平台 / CPU 封装能耗计数器，或样本重置、过期、部分缺失'))
        return {**chosen, 'sources': sources}
