#!/usr/bin/env python3
"""Small, dependency-free host metrics API for Router Monitor."""

import argparse
from contextlib import nullcontext
import sys
import hashlib
from collections import deque
import glob
import math
import shlex
import socket
import stat
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from traffic_history import TrafficHistory

PROC_ROOT = os.environ.get("PROC_ROOT", "/host")
SYS_ROOT = os.environ.get("SYS_ROOT", "/host")
DEBUGFS_ROOT = os.environ.get("DEBUGFS_ROOT", "/host/debug")
CONFIGURED_IFACE = os.environ.get("NAS_STATUS_IFACE", "physical").strip()
STORAGE_PATHS = tuple(path.strip() for path in
                      os.environ.get("NAS_STATUS_STORAGE_PATHS", "/vol1,/vol2").split(",")
                      if path.strip())
SECTOR_BYTES = 512
DEFAULT_HISTORY_DB = os.path.join(os.path.dirname(__file__), "data", "traffic.sqlite3")
SMART_ROOT = os.environ.get("SMART_ROOT", "/host/smartmontools")
SMART_MAX_AGE_SECONDS = int(os.environ.get("NAS_STATUS_SMART_MAX_AGE_SECONDS", "7200"))
SMART_TAIL_BYTES = 65536
_smart_temperature_cache = {}
NETWORK_SOURCES = None
_network_lock = threading.Lock()
_network_previous = {}
_network_epoch = time.time_ns()


def host_path(root, path):
    return root.rstrip("/") + path


def read_text(path, default=""):
    try:
        with open(path, encoding="utf-8") as source:
            return source.read().strip()
    except (OSError, UnicodeError):
        return default


def cpu_sample():
    match = re.search(r"^cpu\s+(.+)$", read_text(host_path(PROC_ROOT, "/proc/stat")), re.M)
    if not match:
        return None
    return [int(value) for value in match.group(1).split()]


def memory_status():
    values = {}
    for line in read_text(host_path(PROC_ROOT, "/proc/meminfo")).splitlines():
        match = re.match(r"^(\w+):\s+(\d+)", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(0, total - available)
    return {
        "total": total,
        "used": used,
        "available": available,
        "percent": round(used * 100 / total, 1) if total else 0.0,
        "valid": total > 0 and "MemAvailable" in values,
    }


def uptime_seconds():
    try:
        return int(float(read_text(host_path(PROC_ROOT, "/proc/uptime")).split()[0]))
    except (IndexError, ValueError):
        return None


def valid_temperature(raw):
    """Convert common sysfs millidegree values and reject bogus sensors."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(value) > 1000:
        value /= 1000.0
    return round(value, 1) if -20.0 <= value <= 150.0 else None


def sensor_kind(chip, label):
    identity = f"{chip} {label}".lower()
    drive_markers = ("drivetemp", "nvme", "composite", "drive", "disk")
    cpu_markers = (
        "x86_pkg_temp", "coretemp", "k10temp", "zenpower", "cpu_thermal",
        "soc_thermal", "cpu", "package id", "tctl", "tdie",
    )
    if any(marker in identity for marker in drive_markers):
        return "disk"
    if any(marker in identity for marker in cpu_markers):
        return "cpu"
    return "other"


def smart_temperature_from_line(line):
    fields = [field.strip() for field in line.split(";")]
    values = []
    for index in range(1, len(fields) - 2, 3):
        if fields[index] not in ("190", "194"):
            continue
        try:
            raw = int(fields[index + 2], 10)
        except ValueError:
            continue
        value = raw & 0xff
        if raw >= 0 and 0 < value < 100:
            values.append(value)
    return max(values) if values else None


def read_smart_temperature(path, size):
    try:
        with open(path, "rb") as source:
            offset = max(0, size - SMART_TAIL_BYTES)
            source.seek(offset)
            if offset:
                source.readline()
            lines = source.read().decode("ascii", "ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        value = smart_temperature_from_line(line)
        if value is not None:
            return value
    return None


def smart_disk_temperatures():
    now = time.time()
    try:
        names = sorted(name for name in os.listdir(SMART_ROOT)
                       if name.startswith("attrlog.") and name.endswith(".ata.csv"))
    except OSError:
        return []
    values = []
    for name in names:
        path = os.path.join(SMART_ROOT, name)
        try:
            status = os.stat(path)
        except OSError:
            continue
        age = now - status.st_mtime
        if not 0 <= age <= SMART_MAX_AGE_SECONDS:
            continue
        cache_key = (status.st_mtime_ns, status.st_size)
        cached = _smart_temperature_cache.get(path)
        if not cached or cached[:2] != cache_key:
            cached = (*cache_key, read_smart_temperature(path, status.st_size))
            _smart_temperature_cache[path] = cached
        if cached[2] is not None:
            values.append(cached[2])
    return values


def temperatures():
    sensors = []
    thermal_root = host_path(SYS_ROOT, "/sys/class/thermal")
    try:
        names = sorted(os.listdir(thermal_root))
    except OSError:
        names = []
    for name in names:
        if not name.startswith("thermal_zone"):
            continue
        directory = os.path.join(thermal_root, name)
        sensor_type = read_text(os.path.join(directory, "type"), "unknown")
        value = valid_temperature(read_text(os.path.join(directory, "temp")))
        if value is None:
            continue
        sensors.append({
            "zone": name.removeprefix("thermal_zone"),
            "type": sensor_type,
            "temp": value,
            "kind": sensor_kind(sensor_type, sensor_type),
        })

    # hwmon covers Intel/AMD CPUs, SATA drivetemp, NVMe and many ARM boards.
    hwmon_root = host_path(SYS_ROOT, "/sys/class/hwmon")
    try:
        hwmons = sorted(os.listdir(hwmon_root))
    except OSError:
        hwmons = []
    for hwmon in hwmons:
        directory = os.path.join(hwmon_root, hwmon)
        chip = read_text(os.path.join(directory, "name"), hwmon)
        try:
            files = sorted(os.listdir(directory))
        except OSError:
            continue
        for filename in files:
            match = re.fullmatch(r"temp(\d+)_input", filename)
            if not match:
                continue
            value = valid_temperature(read_text(os.path.join(directory, filename)))
            if value is None:
                continue
            label = read_text(os.path.join(directory, f"temp{match.group(1)}_label"), chip)
            sensors.append({
                "zone": hwmon,
                "type": f"{chip}:{label}",
                "temp": value,
                "kind": sensor_kind(chip, label),
            })

    for index, value in enumerate(smart_disk_temperatures()):
        sensors.append({"zone": f"smart{index}", "type": "smart", "temp": value, "kind": "disk"})

    # De-duplicate thermal/hwmon aliases, then put the hottest CPU and disk at
    # the front with stable generic names understood by every firmware build.
    unique = []
    seen = set()
    for sensor in sensors:
        key = (sensor["type"].lower(), sensor["temp"])
        if key not in seen:
            seen.add(key)
            unique.append(sensor)
    output = []
    for kind in ("cpu", "disk"):
        matches = [sensor for sensor in unique if sensor["kind"] == kind]
        if matches:
            hottest = max(matches, key=lambda sensor: sensor["temp"])
            output.append({"zone": hottest["zone"], "type": kind, "temp": hottest["temp"]})
    for sensor in unique:
        output.append({key: sensor[key] for key in ("zone", "type", "temp")})
    return output


def interface_names():
    root = host_path(SYS_ROOT, "/sys/class/net")
    try:
        return [name for name in os.listdir(root) if name != "lo"]
    except OSError:
        return []


def physical_interface_names():
    root = host_path(SYS_ROOT, "/sys/class/net")
    return sorted(name for name in interface_names()
                  if os.path.exists(os.path.join(root, name, "device")))


def detect_network_interface():
    if CONFIGURED_IFACE and CONFIGURED_IFACE.lower() not in ("auto", "physical"):
        return CONFIGURED_IFACE

    available = set(interface_names())
    candidates = []
    lines = read_text(host_path(PROC_ROOT, "/proc/net/route")).splitlines()[1:]
    for line in lines:
        fields = line.split()
        if len(fields) < 8 or fields[0] not in available or fields[1] != "00000000":
            continue
        try:
            flags, metric = int(fields[3], 16), int(fields[6])
        except ValueError:
            continue
        if flags & 0x1:
            candidates.append((metric, fields[0]))
    if candidates:
        return min(candidates)[1]

    # Fallback for bridges, bonds and systems without a conventional route.
    virtual_prefixes = ("docker", "veth", "br-", "virbr", "tailscale", "wg")
    usable = []
    for name in available:
        if name.startswith(virtual_prefixes):
            continue
        base = host_path(SYS_ROOT, f"/sys/class/net/{name}")
        state = read_text(os.path.join(base, "operstate"))
        carrier = read_text(os.path.join(base, "carrier"))
        if state == "up" or carrier == "1":
            usable.append(name)
    return sorted(usable or available)[0] if (usable or available) else ""


def selected_network_interfaces():
    if NETWORK_SOURCES is not None:
        return NETWORK_SOURCES.selected()
    if (CONFIGURED_IFACE.lower() or "physical") == "physical":
        interfaces = physical_interface_names()
        return "physical:" + ",".join(interfaces), interfaces
    interface = detect_network_interface()
    return interface, [interface] if interface else []


def network_counters(interfaces):
    if isinstance(interfaces, str):
        interfaces = [interfaces]
    if not interfaces:
        return None
    counters = {}
    for interface in interfaces:
        base = host_path(SYS_ROOT, f"/sys/class/net/{interface}/statistics")
        try:
            rx = int(read_text(os.path.join(base, "rx_bytes")))
            tx = int(read_text(os.path.join(base, "tx_bytes")))
        except ValueError:
            return None
        if min(rx, tx) < 0:
            return None
        counters[interface] = (rx, tx)
    return counters


def network_snapshot(include_counters=False):
    global _network_previous, _network_epoch
    with _network_lock, (NETWORK_SOURCES.lock if NETWORK_SOURCES is not None else nullcontext()):
        interface, interfaces = selected_network_interfaces()
        counters = NETWORK_SOURCES.counters(interfaces) if NETWORK_SOURCES is not None else network_counters(interfaces)
        if counters is None:
            _network_previous = {}
            _network_epoch += 1
            return None
        if _network_previous and (counters.keys() != _network_previous.keys() or any(
                value < old for name, values in counters.items()
                for value, old in zip(values, _network_previous[name]))):
            _network_epoch += 1
        _network_previous = counters
        result = {
            "sample_time": time.monotonic(),
            "iface": interface,
            "rx_bytes": sum(values[0] for values in counters.values()),
            "tx_bytes": sum(values[1] for values in counters.values()),
            "counter_epoch": str(_network_epoch),
        }
        if include_counters:
            result["counters"] = {"epoch": result["counter_epoch"], "values": counters}
        return result


def traffic_sample(provider=network_snapshot):
    if NETWORK_SOURCES is not None:
        NETWORK_SOURCES.record_history()
    snapshot = NETWORK_SOURCES.legacy_snapshot() if NETWORK_SOURCES is not None else provider(include_counters=True)
    boot_id = read_text(host_path(PROC_ROOT, "/proc/sys/kernel/random/boot_id"))
    if snapshot is None or not boot_id:
        return None
    return snapshot["iface"], snapshot["rx_bytes"], snapshot["tx_bytes"], boot_id, snapshot["counters"]


class NetworkMonitor:
    """One 5 Hz sampler for all clients; at most four missed points per reply."""
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.samples = deque(maxlen=50)
        self.current = None
        self.sequence = 0
        self.epoch = str(time.time_ns())
        self.average = [None, None]
        self.baseline = None

    def sample(self):
        if NETWORK_SOURCES is not None:
            NETWORK_SOURCES.collect()
        current = network_snapshot(include_counters=True)
        with self.lock:
            previous = self.current
            self.current = current
            if current is None:
                self.samples.clear()
                self.baseline = None
                self.average = [None, None]
                return
            same = previous and all(current[key] == previous[key]
                                    for key in ("iface", "counter_epoch"))
            elapsed = current["sample_time"] - previous["sample_time"] if same else 0
            if not same or not 0 < elapsed <= 1:
                self.epoch = str(time.time_ns())
                self.samples.clear()
                self.baseline = current
                self.average = [None, None]
                rates = [None, None]
            else:
                rates = [round((current[key] - previous[key]) / elapsed)
                         for key in ("rx_bytes", "tx_bytes")]
            self.sequence += 1
            self.samples.append([self.sequence, round(current["sample_time"], 3), *rates])
            span = current["sample_time"] - self.baseline["sample_time"]
            if span >= 1:
                self.average = [round((current[key] - self.baseline[key]) / span)
                                for key in ("rx_bytes", "tx_bytes")]
                self.baseline = current

    def latest(self, include_counters=False):
        with self.lock:
            if self.current is None or time.monotonic() - self.current["sample_time"] > 1:
                return None
            return {key: value for key, value in self.current.items()
                    if include_counters or key != "counters"}

    def response(self, since=0, epoch="", limit=4):
        with self.lock:
            if self.current is None:
                return None
            age = time.monotonic() - self.current["sample_time"]
            if age > 1:
                return None
            same = epoch == self.epoch and 0 <= since <= self.sequence
            pending = [point for point in self.samples if point[0] > since] if same else list(self.samples)[-(limit if limit > 4 else 1):]
            gap = not same or (pending and pending[0][0] != since + 1) or len(pending) > limit
            return {"v": 2, "source": hashlib.sha256(self.current["iface"].encode()).hexdigest()[:16],
                    "epoch": self.epoch, "seq": self.sequence, "age": round(age, 3),
                    "rate": self.average, "points": pending[-limit:], "gap": bool(gap)}

    def run(self):
        if self.stop.wait(.2):
            return
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.sample()
            except Exception as error:
                print(f"Network sample failed: {type(error).__name__}", flush=True)
            self.stop.wait(max(0, .2 - (time.monotonic() - started)))

    def start(self):
        self.sample()
        self.thread = threading.Thread(target=self.run, daemon=True, name="network-sampler")
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


def physical_block_names():
    root = host_path(SYS_ROOT, "/sys/class/block")
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return sorted(name for name in names
                  if not os.path.exists(os.path.join(root, name, "partition"))
                  and os.path.exists(os.path.join(root, name, "device")))


def disk_counters():
    names = physical_block_names()
    if not names:
        return None
    read_sectors = write_sectors = 0
    for name in names:
        fields = read_text(host_path(SYS_ROOT, f"/sys/class/block/{name}/stat")).split()
        try:
            reads, writes = int(fields[2]), int(fields[6])
        except (IndexError, ValueError):
            return None
        if min(reads, writes) < 0:
            return None
        read_sectors += reads
        write_sectors += writes
    # Linux block statistics always report sectors in 512-byte units.
    return "physical:" + ",".join(names), read_sectors * SECTOR_BYTES, write_sectors * SECTOR_BYTES


def storage_status():
    seen = set()
    total = used = 0
    for path in STORAGE_PATHS:
        try:
            device = os.stat(path).st_dev
            stats = os.statvfs(path)
        except OSError:
            continue
        block_size = stats.f_frsize or stats.f_bsize
        filesystem_total = stats.f_blocks * block_size
        if device in seen or filesystem_total <= 0:
            continue
        seen.add(device)
        filesystem_free = stats.f_bfree * block_size
        total += filesystem_total
        used += max(0, min(filesystem_total, filesystem_total - filesystem_free))
    valid = bool(seen and total)
    return {
        "total": total if valid else None,
        "used": used if valid else None,
        "percent": round(used * 100 / total, 1) if valid else None,
        "valid": valid,
        "filesystems": len(seen),
    }


def gpu_sample():
    """Return (backend, value, cumulative). AMD is instant; Intel cumulative."""
    drm_root = host_path(SYS_ROOT, "/sys/class/drm")
    try:
        cards = sorted(name for name in os.listdir(drm_root) if re.fullmatch(r"card\d+", name))
    except OSError:
        cards = []
    for card in cards:
        value = read_text(os.path.join(drm_root, card, "device", "gpu_busy_percent"))
        try:
            return "amdgpu", float(value), False
        except ValueError:
            pass

    debug_root = host_path(DEBUGFS_ROOT, "/dri")
    try:
        dri_entries = sorted(os.listdir(debug_root))
    except OSError:
        dri_entries = []
    for entry in dri_entries:
        data = read_text(os.path.join(debug_root, entry, "i915_engine_info"))
        # Kernel versions use rcs0, render or Render/3D for the render engine.
        matches = re.findall(r"^(?:rcs\d+|render[^\n]*|Render/3D[^\n]*).*?Runtime:\s*(\d+)ms", data, re.M | re.S)
        if matches:
            return "i915", float(max(int(value) for value in matches)), True
    return "unavailable", 0.0, False


def ups_status():
    """Read the existing NUT driver cache only; never claim USB or send controls."""
    invalid = {"watts": None, "valid": False, "source": "unavailable"}
    configured = os.environ.get("NAS_STATUS_UPS_SOCKET", "")
    try:
        paths = [configured] if configured else [path for path in glob.glob("/host/nut/usbhid-ups-*")
                                                if stat.S_ISSOCK(os.stat(path).st_mode)]
    except OSError:
        return invalid
    if len(paths) != 1:
        return invalid
    try:
        deadline = time.monotonic() + 1.0
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(paths[0])
            client.sendall(b"DUMPALL\n")
            data = b""
            while b"DUMPDONE\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {**invalid, "reason": "timeout"}
                if len(data) >= 32768:
                    return invalid
                client.settimeout(remaining)
                chunk = client.recv(min(4096, 32768 - len(data)))
                if not chunk:
                    return invalid
                data += chunk
        lines = data.decode("utf-8").splitlines()
        if "DATAOK" not in lines or "DATASTALE" in lines:
            return invalid
        values = {}
        for line in lines:
            if line.startswith("SETINFO "):
                parts = shlex.split(line)
                if len(parts) == 3:
                    values[parts[1]] = parts[2]
        watts = values.get("ups.realpower", values.get("output.realpower"))
        source = "reported"
        if watts is None:
            # Only this verified DC model may use V*A as watts; AC needs power factor.
            if values.get("device.model") != "W120" or values.get("device.mfr") != "WL":
                return invalid
            voltage, current = float(values["output.voltage"]), float(values["output.current"])
            if not (0 < voltage <= 60 and 0 <= current <= 100):
                return invalid
            watts, source = voltage * current, "dc_voltage_current"
        watts = float(watts)
        if not math.isfinite(watts) or not 0 <= watts <= 999:
            return invalid
        return {"watts": round(watts, 1), "valid": True, "source": source,
                "status": values.get("ups.status", ""), "alarm": values.get("ups.alarm", "")}
    except socket.timeout:
        return {**invalid, "reason": "timeout"}
    except (OSError, ValueError, KeyError, UnicodeError):
        return invalid


class UpsMonitor:
    """Keep USB-driver waits outside HTTP; expire briefly retained samples."""
    def __init__(self):
        self.value = {"watts": None, "valid": False, "source": "unavailable"}
        self.sampled_at = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None

    def sample(self):
        value = ups_status()
        with self.lock:
            if value["valid"]:
                self.value, self.sampled_at = value, time.monotonic()
            elif value.get("reason") != "timeout":
                self.value = value

    def snapshot(self, now):
        with self.lock:
            age = max(0, now - self.sampled_at)
            if self.value["valid"] and age >= 6:
                return {"watts": None, "valid": False, "source": "unavailable", "reason": "expired"}
            return {**self.value, "age_seconds": round(age, 1) if self.value["valid"] else None}

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            self.sample()
            self.stop.wait(max(0, 2 - (time.monotonic() - started)))

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="ups-sampler")
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


class Metrics:
    def __init__(self, traffic_history, network=None):
        self.traffic_history = traffic_history
        self.monitor = network
        self.network = network.latest if network is not None else network_snapshot
        self.published = None
        self.stop = threading.Event()
        self.thread = None
        self.sequence = 0
        self.previous_cpu = None
        self.previous_network = None
        self.previous_gpu = None
        self.previous_disk = None
        self.interface = ""
        self.disk_devices = ""
        self.cached = None
        self.sampled_at = self.temperatures_at = self.storage_at = 0
        self.cached_temperatures = self.cached_storage = None
        self.ups = UpsMonitor()

    def snapshot(self, force=False):
        now = time.monotonic()
        if not force and self.cached is not None and 0 <= now - self.sampled_at < 1:
            return {**self.cached, "traffic_24h": self.history_snapshot(), "ups": self.ups.snapshot(now)}
        current_cpu = cpu_sample()
        cpu_percent = 0.0
        cpu_valid = False
        if current_cpu and self.previous_cpu:
            old = self.previous_cpu[0]
            # guest and guest_nice are already included in user and nice.
            total = sum(current_cpu[:8]) - sum(old[:8])
            idle_now = current_cpu[3] + (current_cpu[4] if len(current_cpu) > 4 else 0)
            idle_old = old[3] + (old[4] if len(old) > 4 else 0)
            idle = idle_now - idle_old
            if total > 0:
                cpu_valid = True
                cpu_percent = round(max(0.0, min(100.0, (1 - idle / total) * 100)), 1)
        if current_cpu:
            self.previous_cpu = (current_cpu, now)

        current_network = self.network()
        rx_speed = tx_speed = 0
        if current_network and self.previous_network:
            elapsed = current_network["sample_time"] - self.previous_network["sample_time"]
            if (elapsed > 0 and current_network["iface"] == self.previous_network["iface"]
                    and current_network["counter_epoch"] == self.previous_network["counter_epoch"]):
                rx_speed = max(0, int((current_network["rx_bytes"] - self.previous_network["rx_bytes"]) / elapsed))
                tx_speed = max(0, int((current_network["tx_bytes"] - self.previous_network["tx_bytes"]) / elapsed))
        if current_network:
            self.interface = current_network["iface"]
        self.previous_network = current_network

        gpu_backend, gpu_value, gpu_is_cumulative = gpu_sample()
        gpu_percent = 0.0
        gpu_valid = gpu_backend != "unavailable" and math.isfinite(gpu_value) and not gpu_is_cumulative
        if not gpu_is_cumulative:
            gpu_percent = round(max(0.0, min(100.0, gpu_value)), 1)
            self.previous_gpu = None
        elif self.previous_gpu and self.previous_gpu[0] == gpu_backend:
            elapsed = now - self.previous_gpu[1]
            if elapsed > 0:
                delta = gpu_value - self.previous_gpu[2]
                gpu_valid = delta >= 0 and math.isfinite(delta)
                gpu_percent = round(max(0.0, min(100.0, delta / (elapsed * 10))), 1)
        if gpu_is_cumulative:
            self.previous_gpu = (gpu_backend, now, gpu_value)

        disk_read_speed = disk_write_speed = 0
        disk_valid = False
        current_disk = disk_counters()
        if current_disk:
            devices, read_bytes, write_bytes = current_disk
            if devices != self.disk_devices:
                self.disk_devices = devices
                self.previous_disk = None
            if self.previous_disk:
                old_read, old_write, old_time = self.previous_disk
                elapsed = now - old_time
                if elapsed > 0 and read_bytes >= old_read and write_bytes >= old_write:
                    disk_valid = True
                    disk_read_speed = int((read_bytes - old_read) / elapsed)
                    disk_write_speed = int((write_bytes - old_write) / elapsed)
            self.previous_disk = (read_bytes, write_bytes, now)
        else:
            self.disk_devices = ""
            self.previous_disk = None

        if self.cached_temperatures is None or now - self.temperatures_at >= 5:
            self.cached_temperatures = temperatures()
            self.temperatures_at = now
        if self.cached_storage is None or now - self.storage_at >= 30:
            self.cached_storage = storage_status()
            self.storage_at = now
        summary = {kind: next((sensor["temp"] for sensor in self.cached_temperatures
                               if sensor["type"] == kind), None) for kind in ("cpu", "disk")}
        self.cached = {
            "time": int(time.time()),
            "cpu": {"percent": cpu_percent, "valid": cpu_valid},
            "gpu": {"utilization": gpu_percent, "backend": gpu_backend, "valid": gpu_valid},
            "memory": memory_status(),
            "temp": self.cached_temperatures,
            "temperature_summary": summary,
            "net": {
                "iface": self.interface,
                "rx_speed": rx_speed,
                "tx_speed": tx_speed,
            },
            "disk_io": {
                "devices": self.disk_devices,
                "read_speed": disk_read_speed,
                "write_speed": disk_write_speed,
                "valid": disk_valid,
            },
            "storage": self.cached_storage,
            "ups": self.ups.snapshot(now),
            "uptime": uptime_seconds(),
            "traffic_24h": self.history_snapshot(),
        }
        self.sampled_at = now
        self.sequence += 1
        self.published = (self.cached, now, self.sequence, self.temperatures_at, self.storage_at)
        return self.cached


    def history_snapshot(self):
        legacy = self.traffic_history.snapshot()
        return NETWORK_SOURCES.history(legacy) if NETWORK_SOURCES is not None else legacy

    def read_snapshot(self):
        # Readers never trigger hardware I/O. Assignment publishes a complete snapshot.
        published = self.published
        if published is None:
            return None
        cached, sampled_at, sequence, temperatures_at, storage_at = published
        now = time.monotonic()
        result = {**cached, "traffic_24h": self.history_snapshot(),
                  "ups": self.ups.snapshot(now), "v": 2, "seq": sequence,
                  "age": round(max(0, now - sampled_at), 3),
                  "metric_age": {"temperature": round(max(0, now - temperatures_at), 1),
                                 "storage": round(max(0, now - storage_at), 1)}}
        if self.monitor is not None:
            network = self.monitor.response()
            result["net"] = {**cached["net"], "rx_speed": network["rate"][0] if network else None,
                             "tx_speed": network["rate"][1] if network else None,
                             "source": network["source"] if network else None,
                             "valid": bool(network and network["rate"][0] is not None)}
        return result

    def run(self):
        if self.stop.wait(1):
            return
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.snapshot(force=True)
            except Exception as error:
                print(f"Metrics sample failed: {type(error).__name__}", flush=True)
            self.stop.wait(max(.01, 1 - (time.monotonic() - started)))

    def start(self):
        self.snapshot()
        self.thread = threading.Thread(target=self.run, daemon=True, name="metrics-sampler")
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


def resolve_token():
    token_file = os.environ.get("NAS_STATUS_TOKEN_FILE", "/run/secrets/nas_status_token")
    token = read_text(token_file)
    return token or os.environ.get("NAS_STATUS_TOKEN", "").strip()


def handler_factory(metrics, token, network=None, web=None):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(5)
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def do_GET(self):
            if web is not None and web.handle(self):
                return
            request = urlsplit(self.path)
            path = request.path
            query = parse_qs(request.query)
            version2 = query.get("v") == ["2"]
            if path == "/health":
                return self.respond(200, {"status": "ok"})
            if path not in ("/", "/status", "/net"):
                return self.respond(404, {"error": "not found"})
            supplied = self.headers.get("Authorization", "")
            if not token or not hmac.compare_digest(supplied, "Bearer " + token):
                self.close_connection = True
                return self.respond(401, {"error": "unauthorized"})
            if web is not None:
                web.device_seen(self.client_address[0])
            if path == "/net":
                if version2 and network is not None:
                    try:
                        since = int(query.get("since", ["0"])[0])
                        if not 0 <= since <= 4294967295:
                            raise ValueError()
                    except ValueError:
                        return self.respond(400, {"error": "invalid sequence"})
                    snapshot = network.response(since, query.get("epoch", [""])[0])
                else:
                    snapshot = network.latest() if network is not None else network_snapshot()
                if snapshot is None:
                    return self.respond(503, {"error": "network counters unavailable"})
                return self.respond(200, snapshot)
            snapshot = metrics.read_snapshot()
            if snapshot is None or snapshot["age"] > 3:
                return self.respond(503, {"error": "metrics unavailable"})
            if not version2 and snapshot["uptime"] is None:
                snapshot["uptime"] = 0
            metadata = {key: snapshot[key] for key in ("v", "seq", "age", "metric_age")}
            if version2:
                for key, field in (("cpu", "percent"), ("gpu", "utilization"), ("memory", "percent")):
                    snapshot[key] = {**snapshot[key], field: snapshot[key][field] if snapshot[key]["valid"] else None}
            if query.get("display") == ["1"]:
                fields = {
                    "cpu": ("percent",), "gpu": ("utilization",), "memory": ("percent",),
                    "traffic_24h": ("rx_bytes", "tx_bytes", "coverage_seconds", "valid"),
                    "disk_io": ("read_speed", "write_speed", "valid"),
                    "storage": ("total", "used", "percent", "valid"),
                    "ups": ("watts",),
                }
                snapshot = {**{key: {field: snapshot[key][field] for field in names}
                               for key, names in fields.items()},
                            "uptime": snapshot["uptime"], "temperature_summary": snapshot["temperature_summary"]}
            if version2:
                snapshot.update(metadata)
            return self.respond(200, snapshot)

        def do_POST(self):
            if web is None or not web.handle(self):
                self.close_connection = True
                self.respond(404, {"error": "not found"})

        do_PUT = do_POST

        def respond(self, status, payload):
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, message, *args):
            if self.command == "GET" and self.path.split("?", 1)[0] in ("/net", "/status", "/api/overview", "/api/network/stream", "/api/network/interfaces", "/api/capabilities", "/api/session") and len(args) > 1 and str(args[1]) == "200":
                return
            print("%s - %s" % (self.address_string(), message % args), flush=True)

    return Handler


def main():
    global _network_previous, _network_epoch, NETWORK_SOURCES
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18199)
    parser.add_argument("--bind", default=os.environ.get("NAS_STATUS_BIND", "0.0.0.0"))
    args = parser.parse_args()
    token = resolve_token()
    if not token:
        raise SystemExit("NAS_STATUS_TOKEN or NAS_STATUS_TOKEN_FILE is required")
    data_dir = os.path.dirname(os.environ.get("NAS_STATUS_HISTORY_DB", DEFAULT_HISTORY_DB))
    from network_sources import Sources
    from web import WebApp
    NETWORK_SOURCES = Sources(sys.modules[__name__], data_dir)
    network = NetworkMonitor()
    history = TrafficHistory(os.environ.get("NAS_STATUS_HISTORY_DB", DEFAULT_HISTORY_DB),
                             lambda: traffic_sample(network.latest))
    if history.previous and history.previous[5]:
        _network_previous = history.previous[5]["values"]
        _network_epoch = int(history.previous[5]["epoch"])
        NETWORK_SOURCES.legacy_epoch = _network_epoch
    network.start()
    metrics = Metrics(history, network)
    web = WebApp(NETWORK_SOURCES, metrics, data_dir)
    server = ThreadingHTTPServer((args.bind, args.port), handler_factory(metrics, token, network, web))
    metrics.ups.start()
    history.start()
    metrics.start()
    print(f"NAS status API listening on :{args.port}, interface={CONFIGURED_IFACE}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        metrics.close()
        history.close()
        network.close()
        metrics.ups.close()
        NETWORK_SOURCES.close()
        NETWORK_SOURCES = None


if __name__ == "__main__":
    main()
