"""Read-only, optional hardware collectors for x86 NAS installations."""
import glob
import json
import math
import os
import re
import shlex
import socket
import stat
import time

from hardware_settings import load_settings, settings_error

SYS_ROOT = os.environ.get("SYS_ROOT", "/host")
DEBUGFS_ROOT = os.environ.get("DEBUGFS_ROOT", "/host/debug")
SMART_ROOT = os.environ.get("SMART_ROOT", "/host/smartmontools")
NUT_ROOT = os.environ.get("NUT_ROOT", "/host/nut")
GPU_SNAPSHOT = os.environ.get("NAS_STATUS_GPU_SNAPSHOT", "/host/monitor/gpu.json")
SMART_MAX_AGE = int(os.environ.get("NAS_STATUS_SMART_MAX_AGE_SECONDS", "7200"))
LAST_GPU_ID = ""
_engine_samples = {}
_smart_cache = {}


def read_text(path, default=""):
    try:
        with open(path, encoding="utf-8") as source:
            return source.read(131073).strip()
    except (OSError, UnicodeError):
        return default


def number(value, low, high):
    try:
        result = float(value)
        return result if math.isfinite(result) and low <= result <= high else None
    except (ValueError, TypeError, OverflowError):
        return None


def temperature(raw):
    result = number(raw, -200000, 200000)
    if result is not None:
        if abs(result) > 1000:
            result /= 1000
        if -20 <= result <= 150:
            return round(result, 1)
    return None


def kind_for(chip, label):
    name = (chip + " " + label).lower()
    if any(word in name for word in ("drivetemp", "nvme", "composite", "drive", "disk")):
        return "disk"
    if any(word in name for word in ("x86_pkg_temp", "coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu", "package id", "tctl", "tdie")):
        return "cpu"
    return "other"


def stable_device(path):
    """Use PCI/platform identity, never the volatile hwmon/card enumeration."""
    actual = os.path.realpath(path).replace("\\", "/")
    pci = re.findall(r"(?:^|/)([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])(?=/|$)", actual)
    if pci:
        return "pci:" + pci[-1].lower()
    actual = actual.split("/sys/", 1)[-1]
    actual = re.sub(r"/hwmon/hwmon\d+.*$", "", actual)
    return "device:" + actual


def sensors():
    output = []
    thermal_counts = {}
    for directory in sorted(glob.glob(SYS_ROOT.rstrip("/") + "/sys/class/thermal/thermal_zone*")):
        zone = os.path.basename(directory).removeprefix("thermal_zone")
        chip = read_text(directory + "/type", "unknown")
        value = temperature(read_text(directory + "/temp"))
        if value is None:
            continue
        count = thermal_counts.get(chip, 0)
        thermal_counts[chip] = count + 1
        identity = "thermal:" + chip + (":" + str(count) if count else "")
        output.append({"id": identity, "label": chip, "kind": kind_for(chip, chip),
                       "temp": value, "zone": zone, "type": chip})
    for directory in sorted(glob.glob(SYS_ROOT.rstrip("/") + "/sys/class/hwmon/hwmon*")):
        chip = read_text(directory + "/name", os.path.basename(directory))
        device = stable_device(directory + "/device") if os.path.exists(directory + "/device") else stable_device(directory)
        # A synthetic sysfs tree may omit the device link; chip still survives renumbering.
        if re.search(r"/class/hwmon/hwmon\d+", device):
            device = "chip:" + chip
        for filename in sorted(glob.glob(directory + "/temp*_input")):
            match = re.fullmatch(r"temp(\d+)_input", os.path.basename(filename))
            if not match:
                continue
            value = temperature(read_text(filename))
            if value is None:
                continue
            index = match.group(1)
            label = read_text(directory + "/temp" + index + "_label", chip)
            output.append({"id": "hwmon:" + device + ":" + chip + ":temp" + index,
                           "label": chip + " · " + label, "kind": kind_for(chip, label),
                           "temp": value, "zone": os.path.basename(directory), "type": chip + ":" + label})
    now = time.time()
    for filename in sorted(glob.glob(SMART_ROOT.rstrip("/") + "/attrlog.*.ata.csv")):
        try:
            info = os.stat(filename)
            if not 0 <= now - info.st_mtime <= SMART_MAX_AGE:
                continue
            key = (info.st_mtime_ns, info.st_size)
            cached = _smart_cache.get(filename)
            if not cached or cached[:2] != key:
                with open(filename, "rb") as source:
                    offset = max(0, info.st_size - 65536)
                    source.seek(offset)
                    if offset:
                        source.readline()
                    lines = source.read(65536).decode("ascii", "ignore").splitlines()
                value = None
                for line in reversed(lines):
                    fields = [part.strip() for part in line.split(";")]
                    values = []
                    for index in range(1, len(fields) - 2, 3):
                        if fields[index] in ("190", "194"):
                            try:
                                raw = int(fields[index + 2])
                                if raw >= 0 and 0 < (raw & 255) < 100:
                                    values.append(raw & 255)
                            except ValueError:
                                pass
                    if values:
                        value = max(values)
                        break
                cached = (*key, value)
                _smart_cache[filename] = cached
            if cached[2] is not None:
                name = os.path.basename(filename)[8:-8]
                output.append({"id": "smart:" + name, "label": "SMART · " + name,
                               "kind": "disk", "temp": cached[2], "zone": "smart:" + name, "type": "smart"})
        except OSError:
            continue
    return output


def temperatures():
    available = sensors()
    settings = load_settings()
    output = []
    for kind in ("cpu", "disk"):
        selected = settings.get(kind + "_sensor", "auto")
        matches = [sensor for sensor in available if sensor["kind"] == kind] if selected == "auto" else [sensor for sensor in available if sensor["id"] == selected]
        if selected != "off" and matches:
            hottest = max(matches, key=lambda row: row["temp"])
            output.append({"zone": hottest["zone"], "type": kind, "temp": hottest["temp"]})
    seen = set()
    for sensor in available:
        key = (sensor["type"], sensor["temp"])
        if key not in seen:
            seen.add(key)
            # Preserve firmware's original three-field sensor protocol.
            # Reserved summary names must never accidentally override a disabled selection.
            sensor_type = sensor["type"] if sensor["type"] not in ("cpu", "disk") else "sensor:" + sensor["type"]
            output.append({"zone": sensor["zone"], "type": sensor_type, "temp": sensor["temp"]})
    return output


def helper_gpus():
    try:
        info = os.stat(GPU_SNAPSHOT)
        now = time.time()
        if info.st_size > 131072 or not 0 <= now - info.st_mtime <= 15:
            return [], "宿主机 GPU 采集缓存过期"
        value = json.loads(read_text(GPU_SNAPSHOT))
        if value.get("schema") != 1 or not isinstance(value.get("gpus"), list) or len(value["gpus"]) > 64 or not 0 <= now - float(value["sampled_at"]) <= 15:
            return [], "宿主机 GPU 缓存格式错误或过期"
        rows = []
        seen = set()
        for row in value["gpus"]:
            if not isinstance(row, dict) or row.get("backend") not in ("nvidia", "amdgpu", "i915", "xe"):
                continue
            identity, label = row.get("id"), row.get("label")
            if not isinstance(identity, str) or not 1 <= len(identity) <= 256 or not isinstance(label, str) or len(label) > 256 or identity in seen:
                continue
            seen.add(identity)
            percent = number(row.get("utilization"), 0, 100) if type(row.get("utilization")) in (int, float) else None
            valid = row.get("valid") is True and percent is not None
            reason = row.get("reason") if isinstance(row.get("reason"), str) else ""
            rows.append({"id": identity, "label": label, "backend": row["backend"], "valid": valid,
                         "reason": "" if valid else reason[:500] or "宿主机驱动未提供利用率", "_value": percent, "_helper": True})
        return rows, ""
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return [], "宿主机 GPU 采集缓存不可用"


def engine_runtimes(data):
    """Collect render/video/video-enhancement runtimes, in milliseconds."""
    values = {}
    current = None
    for line in data.splitlines():
        # i915 debugfs emits engine headings at column zero (rcs0/vcs0/vecs0).
        heading = re.match(r"^(rcs\d+|vcs\d+|vecs\d+|render[^:]*(?:/3D)?|Render/3D[^:]*|video[^:]*|Video[^:]*)(?::|\s|$)", line)
        if heading:
            current = heading.group(1).strip()
        elif line and not line[0].isspace():
            current = None
        runtime = re.search(r"\bRuntime:\s*(\d+)\s*(ms|ns)\b", line)
        if current is not None and runtime:
            values[current] = int(runtime.group(1)) / (1000000 if runtime.group(2) == "ns" else 1)
    return values


def gpu_devices():
    rows = []
    helpers, helper_reason = helper_gpus()
    helper_by_id = {row["id"]: row for row in helpers}
    for directory in sorted(glob.glob(SYS_ROOT.rstrip("/") + "/sys/class/drm/card*")):
        card = os.path.basename(directory)
        if not re.fullmatch(r"card\d+", card):
            continue
        device = directory + "/device"
        identity = stable_device(device)
        # The PCI slot in uevent is reliable even when /sys links are not propagated.
        slot = re.search(r"^PCI_SLOT_NAME=([0-9a-fA-F:.]+)$", read_text(device + "/uevent"), re.M)
        if slot:
            identity = "pci:" + slot.group(1).lower()
        vendor = read_text(device + "/vendor").lower()
        driver = os.path.basename(os.path.realpath(device + "/driver"))
        backend = {"0x1002": "amdgpu", "0x8086": "xe" if driver == "xe" else "i915", "0x10de": "nvidia"}.get(vendor, driver if driver in ("amdgpu", "i915", "xe", "nvidia") else "unknown")
        label = {"amdgpu": "AMD", "i915": "Intel", "xe": "Intel Xe", "nvidia": "NVIDIA"}.get(backend, "GPU") + " · " + identity.removeprefix("pci:")
        row = {"id": identity, "label": label, "backend": backend, "valid": False, "reason": "驱动未提供可读利用率"}
        if identity in helper_by_id:
            helper = helper_by_id.pop(identity)
            if helper["valid"]:
                rows.append(helper)
                continue
            row["reason"] = helper["reason"]
        if backend == "amdgpu":
            value = number(read_text(device + "/gpu_busy_percent"), 0, 100)
            if value is not None:
                row.update(valid=True, reason="", _value=value)
        elif backend == "i915":
            debug = DEBUGFS_ROOT.rstrip("/") + "/dri/" + card[4:]
            runtimes = engine_runtimes(read_text(debug + "/i915_engine_info"))
            if runtimes:
                row.update(valid=True, reason="", _engines=runtimes)
            else:
                row["reason"] = "Intel 引擎计数器不可读；需要 debugfs 权限或宿主机 intel_gpu_top 采集"
        elif backend in ("nvidia", "xe"):
            if helper_reason:
                row["reason"] = helper_reason + ("；需要 NVIDIA 驱动及 nvidia-smi" if backend == "nvidia" else "；需要支持 Xe 的 intel_gpu_top")
        rows.append(row)
    rows.extend(helper_by_id.values())
    known = {row["id"] for row in rows}
    # A PCI display controller can exist before its vendor driver creates DRM nodes.
    for device in sorted(glob.glob(SYS_ROOT.rstrip("/") + "/sys/bus/pci/devices/*")):
        if not read_text(device + "/class").lower().startswith("0x03"):
            continue
        identity = "pci:" + os.path.basename(device).lower()
        if identity in known:
            continue
        backend = {"0x1002": "amdgpu", "0x8086": "i915", "0x10de": "nvidia"}.get(read_text(device + "/vendor").lower(), "unknown")
        rows.append({"id": identity, "label": {"amdgpu": "AMD", "i915": "Intel", "nvidia": "NVIDIA"}.get(backend, "GPU") + " · " + identity[4:],
                     "backend": backend, "valid": False, "reason": "PCI 显卡已识别，但驱动未提供 DRM 利用率或宿主机采集数据"})
    return rows


def gpu_sample():
    global LAST_GPU_ID
    selected = load_settings().get("gpu", "auto")
    if selected == "off":
        LAST_GPU_ID = "off"
        return "unavailable", 0.0, False
    devices = gpu_devices()
    candidates = [row for row in devices if row["valid"] and (selected == "auto" or row["id"] == selected)]
    if not candidates:
        LAST_GPU_ID = selected
        return "unavailable", 0.0, False
    row = candidates[0]
    identity = row["id"]
    LAST_GPU_ID = identity
    if "_engines" not in row:
        return row["backend"], row["_value"], False
    engines = row["_engines"]
    old = _engine_samples.get(identity)
    # Accumulate the busiest engine delta, so parallel engines do not inflate load.
    if old and old[0].keys() == engines.keys() and all(engines[name] >= old[0][name] for name in engines):
        counter = old[1] + max(engines[name] - old[0][name] for name in engines)
        generation = old[2]
    else:
        counter = 0.0
        generation = old[2] + 1 if old else 1
    LAST_GPU_ID += ":generation:" + str(generation)
    _engine_samples[identity] = (engines, counter, generation)
    return row["backend"], counter, True


def local_ups_paths():
    paths = []
    for path in sorted(glob.glob(NUT_ROOT.rstrip("/") + "/*")):
        try:
            if stat.S_ISSOCK(os.stat(path).st_mode):
                paths.append(path)
        except OSError:
            continue
    return paths


def recv_until(client, end, deadline):
    data = b""
    while end not in data.splitlines():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout()
        if len(data) >= 32768:
            raise ValueError("NUT response too large")
        client.settimeout(remaining)
        chunk = client.recv(min(4096, 32768 - len(data)))
        if not chunk:
            raise ValueError("incomplete NUT response")
        data += chunk
        if any(line.startswith(b"ERR ") for line in data.splitlines()):
            raise ValueError("NUT rejected read request")
    return data.decode("utf-8").splitlines()


def read_ups(settings=None, local_path=None):
    settings = settings or load_settings()
    mode = settings.get("ups_mode", "auto")
    if mode == "off" and local_path is None:
        return {}, "已停用 UPS 采集"
    try:
        deadline = time.monotonic() + 1.0
        if mode == "remote" and local_path is None:
            name = settings.get("ups_name", "")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name):
                return {}, "请填写有效的远程 NUT 设备名称"
            with socket.create_connection((settings.get("ups_host", ""), int(settings.get("ups_port", 3493))), timeout=1) as client:
                client.sendall(("LIST VAR " + name + "\n").encode("ascii"))
                lines = recv_until(client, ("END LIST VAR " + name).encode("ascii"), deadline)
            if "BEGIN LIST VAR " + name not in lines:
                return {}, "远程 NUT 响应无效"
            values = {}
            for line in lines:
                parts = shlex.split(line)
                if len(parts) == 4 and parts[:2] == ["VAR", name]:
                    values[parts[2]] = parts[3]
            return values, "" if values else "远程 NUT 未提供设备变量"
        paths = local_ups_paths()
        configured = local_path or settings.get("ups_socket") or os.environ.get("NAS_STATUS_UPS_SOCKET", "")
        if configured:
            # Only speak the read-only NUT protocol to sockets in the mounted NUT directory.
            if os.path.normpath(configured) not in {os.path.normpath(path) for path in paths}:
                return {}, "选定的 NUT 驱动 socket 不存在或不可读"
            path = configured
        elif len(paths) == 1:
            path = paths[0]
        else:
            return {}, "发现多个 UPS，请在设置中选择设备" if paths else "未发现 NUT 驱动 socket"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(path)
            client.sendall(b"DUMPALL\n")
            lines = recv_until(client, b"DUMPDONE", deadline)
        if "DATAOK" not in lines or "DATASTALE" in lines:
            return {}, "NUT 驱动数据尚未就绪或已过期"
        values = {}
        for line in lines:
            parts = shlex.split(line)
            if len(parts) == 3 and parts[0] == "SETINFO":
                values[parts[1]] = parts[2]
        return values, "" if values else "NUT 驱动未提供设备变量"
    except socket.timeout:
        return {}, "timeout"
    except PermissionError:
        return {}, "NUT socket 权限不足"
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return {}, "NUT 连接或响应不可用"


def ups_from_values(values, reason=""):
    invalid = {"watts": None, "valid": False, "source": "unavailable"}
    if reason:
        return {**invalid, "reason": reason}
    status = {"status": values.get("ups.status", ""), "alarm": values.get("ups.alarm", "")}
    watts = values.get("ups.realpower", values.get("output.realpower"))
    source = "reported"
    if watts is None and values.get("device.model") == "W120" and values.get("device.mfr") == "WL":
        voltage = number(values.get("output.voltage"), .001, 60)
        current = number(values.get("output.current"), 0, 100)
        if voltage is not None and current is not None:
            watts, source = voltage * current, "dc_voltage_current"
    watts = number(watts, 0, float("inf"))
    if watts is None:
        return {**invalid, **status, "reason": "UPS 可读取状态，但未提供有效有功功率；不会将交流 VA 当作 W"}
    return {"watts": round(watts, 1), "valid": True, "source": source, **status}


def ups_status():
    return ups_from_values(*read_ups())


def inventory():
    settings = load_settings()
    gpu_rows = gpu_devices()
    sensor_rows = sensors()
    ups_rows = [{"id": path, "label": os.path.basename(path), "valid": True, "reason": "已发现本机 NUT 驱动 socket"} for path in local_ups_paths()]
    values, error = read_ups(settings)
    ups = ups_from_values(values, error)
    if settings.get("ups_mode", "auto") in ("auto", "local"):
        selected = settings.get("ups_socket") or os.environ.get("NAS_STATUS_UPS_SOCKET", "") or (ups_rows[0]["id"] if len(ups_rows) == 1 else "")
        for row in ups_rows:
            if row["id"] == selected:
                row.update(valid=bool(values), reason=error or ups.get("reason", "") or "NUT 状态及有功功率可用")
    if settings.get("ups_mode") == "remote":
        label = str(settings.get("ups_host", "")) + ":" + str(settings.get("ups_port", 3493)) + "/" + str(settings.get("ups_name", ""))
        ups_rows.append({"id": "remote:" + label, "label": "远程 NUT · " + label, "valid": bool(values), "reason": error or ups.get("reason", "")})
    diagnostics = []
    for kind in ("cpu", "disk"):
        choice = settings.get(kind + "_sensor", "auto")
        available = [row for row in sensor_rows if row["kind"] == kind] if choice == "auto" else [row for row in sensor_rows if row["id"] == choice]
        diagnostics.append({"component": kind + "_temperature", "status": "disabled" if choice == "off" else "ok" if available else "unavailable",
                            "detail": "已停用" if choice == "off" else "已识别 " + str(len(available)) + " 个可用传感器" if available else "未找到可读传感器或所选传感器已移除；其他指标不受影响"})
    gpu_choice = settings.get("gpu", "auto")
    chosen = [row for row in gpu_rows if gpu_choice == "auto" or row["id"] == gpu_choice]
    diagnostics.append({"component": "gpu", "status": "disabled" if gpu_choice == "off" else "ok" if any(row["valid"] for row in chosen) else "unavailable",
                        "detail": "已停用" if gpu_choice == "off" else "已识别可用 GPU" if any(row["valid"] for row in chosen) else "；".join(row["reason"] for row in chosen) or "未识别到选定 GPU 或没有 GPU；其他指标不受影响"})
    diagnostics.append({"component": "ups", "status": "disabled" if settings.get("ups_mode") == "off" else "ok" if ups["valid"] else "partial" if values else "unavailable",
                        "detail": ups.get("reason") or "NUT 状态及有功功率可用"})
    error = settings_error()
    if error:
        diagnostics.append({"component": "settings", "status": "unavailable", "detail": error})
    return {"gpus": [{key: row[key] for key in ("id", "label", "backend", "valid", "reason")} for row in gpu_rows],
            "sensors": [{key: row[key] for key in ("id", "label", "kind", "temp")} for row in sensor_rows],
            "ups": ups_rows, "diagnostics": diagnostics}
