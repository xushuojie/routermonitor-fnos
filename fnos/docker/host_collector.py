"""Read-only host GPU bridge; no Docker socket or driver libraries in the web container."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

def pci_id(value):
    match = re.fullmatch(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])', value)
    return 'pci:%04x:%s:%s.%s' % (int(match[1], 16), match[2].lower(), match[3].lower(), match[4]) if match else None

def nvidia_rows():
    executable = shutil.which('nvidia-smi')
    if not executable:
        return []
    try:
        result = subprocess.run([executable, '--query-gpu=pci.bus_id,name,utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3, check=True)
        rows = []
        for fields in csv.reader(io.StringIO(result.stdout[:131072])):
            if len(fields) != 3:
                continue
            device, name, usage = (v.strip() for v in fields); identity = pci_id(device)
            if not identity:
                continue
            try: percent = float(usage)
            except ValueError: percent = None
            valid = percent is not None and math.isfinite(percent) and 0 <= percent <= 100
            rows.append({'id': identity, 'label': name, 'backend': 'nvidia', 'utilization': percent if valid else None,
                         'valid': valid, 'reason': '' if valid else '驱动未提供 GPU 利用率'})
        return rows
    except (OSError, subprocess.SubprocessError):
        return []

def intel_percent(payload):
    """Decode complete records even if the tool was interrupted before closing its array."""
    decoder = json.JSONDecoder(); samples = []; cursor = 0
    while cursor < len(payload):
        cursor = payload.find('{', cursor)
        if cursor < 0: break
        try:
            value, consumed = decoder.raw_decode(payload[cursor:])
            cursor += consumed
            if isinstance(value, dict) and isinstance(value.get('engines'), dict): samples.append(value)
        except ValueError:
            break
    # The first record can be the initial baseline, so require a second interval.
    if len(samples) < 2: return None
    busy = [value.get('busy') for value in samples[-1]['engines'].values() if isinstance(value, dict)]
    busy = [v for v in busy if type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 100]
    return max(busy) if busy else None

def intel_row(card):
    device = card / 'device'
    identity = pci_id(device.resolve().name)
    if not identity: return None
    backend = (device / 'driver').resolve().name
    row = {'id': identity, 'label': 'Intel ' + card.name, 'backend': backend, 'utilization': None, 'valid': False,
           'reason': '宿主机未安装 intel_gpu_top，或工具不支持此驱动'}
    executable = shutil.which('intel_gpu_top')
    if not executable: return row
    process = None
    try:
        process = subprocess.Popen([executable, '-J', '-s', '500', '-d', 'drm:/dev/dri/' + card.name, '-o', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try: output = process.communicate(timeout=2.2)[0]
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGINT)
            try: output = process.communicate(timeout=.5)[0]
            except subprocess.TimeoutExpired:
                process.kill(); output = process.communicate()[0]
        percent = intel_percent(output[:262144])
        if percent is not None:
            row.update(utilization=percent, valid=True, reason='')
        else:
            row['reason'] = 'Intel 工具未提供有效引擎计数，请检查驱动及 PMU 支持'
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        if process is not None and process.poll() is None:
            process.kill(); process.wait()
    return row

def snapshot():
    rows = nvidia_rows()
    cards = []
    for card in Path('/sys/class/drm').glob('card[0-9]*'):
        if re.fullmatch(r'card\d+', card.name) and (card / 'device/driver').resolve().name in ('i915', 'xe'):
            cards.append(card)
    if cards:
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows.extend(row for row in pool.map(intel_row, cards[:16]) if row)
    return {'schema': 1, 'sampled_at': time.time(), 'gpus': rows}

def run(output):
    import fcntl
    output.mkdir(parents=True, exist_ok=True)
    with open(output / 'collector.lock', 'a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        pid = output / 'collector.pid'; pid.write_text(str(os.getpid()))
        stop = False
        def terminate(*_):
            nonlocal stop
            stop = True
        signal.signal(signal.SIGTERM, terminate); signal.signal(signal.SIGINT, terminate)
        try:
            while not stop:
                started = time.monotonic()
                try:
                    value = snapshot(); temporary = output / 'gpu.next'
                    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                    os.replace(temporary, output / 'gpu.json')
                except OSError:
                    pass
                while not stop and time.monotonic() - started < 5:
                    time.sleep(.2)
        finally:
            pid.unlink(missing_ok=True)

def control(action, output):
    output = output.resolve()
    if action == 'run': return run(output)
    pid_file = output / 'collector.pid'
    def running(pid):
        try:
            return Path('/proc/%d/stat' % pid).read_text().rsplit(') ', 1)[1].split()[0] != 'Z'
        except (OSError, IndexError):
            return False
    if action == 'stop':
        try:
            pid = int(pid_file.read_text())
            argv = Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\0')
            if str(Path(__file__).resolve()).encode() in argv and str(output).encode() in argv and b'run' in argv:
                os.kill(pid, signal.SIGTERM)
                for _ in range(75):
                    if not running(pid): break
                    time.sleep(.2)
                else:
                    # Recheck identity before stopping an unresponsive worker and its tools.
                    argv = Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\0')
                    if str(Path(__file__).resolve()).encode() in argv and str(output).encode() in argv and b'run' in argv:
                        if os.getpgid(pid) == pid:
                            os.killpg(pid, signal.SIGKILL)
                        else:
                            os.kill(pid, signal.SIGKILL)
                        for _ in range(20):
                            if not running(pid): break
                            time.sleep(.1)
        except (OSError, ValueError): pass
        return
    if action == 'start':
        output.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'run', str(output)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['start', 'stop', 'run']); parser.add_argument('output', type=Path)
    args = parser.parse_args(); control(args.action, args.output)
