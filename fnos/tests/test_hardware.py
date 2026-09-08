"""Fixture-only hardware and unchanged device-API regression tests."""
import copy
import json
import math
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import api
import hardware
import hardware_settings


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = copy.deepcopy(hardware_settings.DEFAULTS)
        for module in (api, hardware):
            self.enterContext(patch.object(module, "load_settings", lambda: dict(self.settings)))
        self.enterContext(patch.object(api, "intervals", lambda settings=None: hardware_settings.intervals(settings or self.settings)))
        for key, suffix in (("SYS_ROOT", "host"), ("DEBUGFS_ROOT", "debug"), ("SMART_ROOT", "smart"), ("NUT_ROOT", "nut"), ("GPU_SNAPSHOT", "gpu.json")):
            self.enterContext(patch.object(hardware, key, str(self.root / suffix)))
        hardware._engine_samples.clear()

    def write(self, filename, text):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def card(self, index, pci, vendor):
        prefix = "host/sys/class/drm/card" + str(index) + "/device/"
        self.write(prefix + "uevent", "PCI_SLOT_NAME=" + pci)
        self.write(prefix + "vendor", vendor)
        return prefix

    def helper(self, rows):
        stamp = time.time() - 1
        path = self.write("gpu.json", json.dumps({"schema": 1, "sampled_at": stamp, "gpus": rows}))
        os.utime(path, (stamp, stamp))
        return path

    def test_sensors_select_stable_identity_and_preserve_protocol(self):
        self.write("host/sys/class/hwmon/hwmon0/name", "coretemp")
        self.write("host/sys/class/hwmon/hwmon0/temp1_input", "42000")
        self.write("host/sys/class/hwmon/hwmon0/temp1_label", "Package id 0")
        self.write("host/sys/class/hwmon/hwmon0/temp2_input", "63000")
        self.write("host/sys/class/hwmon/hwmon0/temp2_label", "Core 1")
        self.write("host/sys/class/hwmon/hwmon1/name", "nvme")
        self.write("host/sys/class/hwmon/hwmon1/temp1_input", "51000")
        selected = next(row["id"] for row in hardware.sensors() if row["temp"] == 42)
        self.assertEqual(hardware.temperatures()[0]["temp"], 63)
        self.settings["cpu_sensor"] = selected
        self.assertEqual(hardware.temperatures()[0]["temp"], 42)
        (self.root / "host/sys/class/hwmon/hwmon0").rename(self.root / "host/sys/class/hwmon/hwmon8")
        self.assertIn(selected, [row["id"] for row in hardware.sensors()])
        self.assertEqual(hardware.temperatures()[0]["temp"], 42)
        self.settings["cpu_sensor"] = "missing"
        self.assertNotIn("cpu", [row["type"] for row in hardware.temperatures()])
        self.settings["cpu_sensor"] = "off"
        self.settings["disk_sensor"] = "off"
        self.assertNotIn("disk", [row["type"] for row in hardware.temperatures()])
        self.assertTrue(all(set(row) == {"zone", "type", "temp"} for row in hardware.temperatures()))

    def test_reserved_sensor_type_does_not_override_off_summary(self):
        self.write("host/sys/class/thermal/thermal_zone0/type", "cpu")
        self.write("host/sys/class/thermal/thermal_zone0/temp", "50000")
        self.settings["cpu_sensor"] = "off"
        self.assertEqual([row["type"] for row in hardware.temperatures()], ["sensor:cpu"])

    def test_smart_disk_identity_and_expired_log(self):
        path = self.write("smart/attrlog.DISK_SERIAL.ata.csv", "date;194;100;42\n")
        self.assertEqual(hardware.sensors()[0]["id"], "smart:DISK_SERIAL")
        self.assertEqual(hardware.temperatures()[0]["temp"], 42)
        os.utime(path, (time.time() - 8000,) * 2)
        self.assertEqual(hardware.sensors(), [])

    def test_gpu_choice_amd_and_nvidia_and_off(self):
        prefix = self.card(0, "0000:04:00.0", "0x1002")
        self.write(prefix + "gpu_busy_percent", "12.5")
        self.helper([{"id": "pci:0000:05:00.0", "label": "NVIDIA Test", "backend": "nvidia", "utilization": 77, "valid": True}])
        self.assertEqual(hardware.gpu_sample(), ("amdgpu", 12.5, False))
        self.settings["gpu"] = "pci:0000:05:00.0"
        self.assertEqual(hardware.gpu_sample(), ("nvidia", 77, False))
        self.settings["gpu"] = "missing"
        self.assertEqual(hardware.gpu_sample(), ("unavailable", 0, False))
        self.settings["gpu"] = "off"
        self.assertEqual(hardware.gpu_sample(), ("unavailable", 0, False))

    def test_gpu_helper_strict_freshness_and_invalid_data(self):
        rows = [{"id": "pci:0000:05:00.0", "label": "NVIDIA Test", "backend": "nvidia", "utilization": 77, "valid": True}]
        path = self.helper(rows)
        os.utime(path, (time.time() - 16,) * 2)
        self.assertEqual(hardware.helper_gpus()[0], [])
        path = self.helper(rows)
        payload = json.loads(path.read_text())
        payload["sampled_at"] = time.time() - 16
        path.write_text(json.dumps(payload))
        self.assertEqual(hardware.helper_gpus()[0], [])
        for value in (float("nan"), -1, 101, None, "bad", True):
            rows[0]["utilization"] = value
            self.helper(rows)
            self.assertFalse(hardware.helper_gpus()[0][0]["valid"])
        self.write("gpu.json", "{")
        self.assertEqual(hardware.helper_gpus()[0], [])

    def test_intel_render_and_video_counters_and_reset(self):
        self.card(0, "0000:00:02.0", "0x8086")
        path = self.write("debug/dri/0/i915_engine_info", "rcs0\n\tRuntime: 500ms\nvcs0\n\tRuntime: 100ms\nvecs0\n\tRuntime: 1ms\nbcs0\n\tRuntime: 999ms\n")
        self.assertEqual(hardware.gpu_sample(), ("i915", 0, True))
        identity = hardware.LAST_GPU_ID
        path.write_text("rcs0\n\tRuntime: 550ms\nvcs0\n\tRuntime: 700ms\nvecs0\n\tRuntime: 1ms\n")
        self.assertEqual(hardware.gpu_sample(), ("i915", 600, True))
        self.assertEqual(hardware.LAST_GPU_ID, identity)
        path.write_text("rcs0\n\tRuntime: 1ms\nvcs0\n\tRuntime: 2ms\n")
        self.assertEqual(hardware.gpu_sample(), ("i915", 0, True))
        self.assertNotEqual(hardware.LAST_GPU_ID, identity)

    def test_ups_large_power_and_state_without_power(self):
        self.assertEqual(hardware.ups_from_values({"ups.realpower": "2500", "ups.status": "OL"}),
                         {"watts": 2500.0, "valid": True, "source": "reported", "status": "OL", "alarm": ""})
        for value in ("NaN", "inf", "-1"):
            self.assertFalse(hardware.ups_from_values({"ups.realpower": value})["valid"])
        state = hardware.ups_from_values({"ups.status": "OB", "ups.power": "1200"})
        self.assertFalse(state["valid"])
        self.assertEqual(state["status"], "OB")
        self.assertIsNone(state["watts"])
        dc = hardware.ups_from_values({"device.model": "W120", "device.mfr": "WL", "output.voltage": "12", "output.current": "3"})
        self.assertEqual(dc["watts"], 36)
        self.assertEqual(dc["source"], "dc_voltage_current")

    def test_remote_nut_uses_read_only_list_var(self):
        class Client:
            command = b""
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def settimeout(self, value): pass
            def sendall(self, command): self.command += command
            def recv(self, size): return b'BEGIN LIST VAR ups1\nVAR ups1 ups.realpower "3200"\nVAR ups1 ups.status "OL"\nEND LIST VAR ups1\n'
        client = Client()
        self.settings.update(ups_mode="remote", ups_host="ups.example", ups_port=3493, ups_name="ups1")
        with patch.object(hardware.socket, "create_connection", return_value=client) as connect:
            self.assertEqual(hardware.ups_status()["watts"], 3200)
            connect.assert_called_once_with(("ups.example", 3493), timeout=1)
        self.assertEqual(client.command, b"LIST VAR ups1\n")
        self.settings["ups_name"] = "ups1\nINSTCMD ups1 shutdown.return"
        with patch.object(hardware.socket, "create_connection") as connect:
            self.assertFalse(hardware.ups_status()["valid"])
            connect.assert_not_called()

    def test_nut_expiry_and_source_switch_no_old_power(self):
        monitor = api.UpsMonitor()
        self.settings["profile"] = "eco"
        with patch.object(api, "ups_status", return_value={"watts": 123, "valid": True, "source": "reported"}):
            monitor.sample()
        self.assertTrue(monitor.snapshot(monitor.sampled_at + 20)["valid"])
        self.assertFalse(monitor.snapshot(monitor.sampled_at + 31)["valid"])
        self.settings.update(ups_mode="remote", ups_name="other")
        with patch.object(api, "ups_status", return_value={"watts": None, "valid": False, "source": "unavailable", "reason": "timeout"}):
            monitor.sample()
        self.assertFalse(monitor.snapshot(time.monotonic())["valid"])

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix socket support required")
    def test_local_nut_multiple_devices_require_choice_and_read_cache(self):
        directory = self.root / "nut"
        directory.mkdir()
        commands = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as first, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as second:
            first.bind(str(directory / "driver-ups1"))
            first.listen(1)
            first.settimeout(2)
            second.bind(str(directory / "driver-ups2"))
            second.listen(1)
            self.assertIn("多个 UPS", hardware.ups_status()["reason"])
            self.settings.update(ups_mode="local", ups_socket=str(directory / "driver-ups1"))
            def reply():
                connection, _ = first.accept()
                with connection:
                    commands.append(connection.recv(128))
                    connection.sendall(b'DATAOK\nSETINFO ups.realpower "1532"\nSETINFO ups.status "OL"\nDUMPDONE\n')
            thread = threading.Thread(target=reply, daemon=True)
            thread.start()
            value = hardware.ups_status()
            thread.join(2)
            self.assertEqual(value["watts"], 1532)
            self.assertEqual(commands, [b"DUMPALL\n"])

    def test_pci_device_without_driver_is_shown_unavailable(self):
        self.write("host/sys/bus/pci/devices/0000:03:00.0/class", "0x030000")
        self.write("host/sys/bus/pci/devices/0000:03:00.0/vendor", "0x10de")
        row = hardware.inventory()["gpus"][0]
        self.assertEqual(row["id"], "pci:0000:03:00.0")
        self.assertEqual(row["backend"], "nvidia")
        self.assertFalse(row["valid"])
        self.assertIn("驱动", row["reason"])

    def test_cpu_counter_reset_does_not_report_false_load(self):
        class History:
            def snapshot(self): return {}
        metrics = api.Metrics(History())
        metrics.previous_cpu = ([20, 0, 10, 70], time.monotonic() - 1)
        with patch.object(api, "cpu_sample", return_value=[100, 0, 5, 200]), patch.object(api, "gpu_sample", return_value=("unavailable", 0, False)), patch.object(api, "temperatures", return_value=[]), patch.object(api, "storage_status", return_value={}), patch.object(api, "disk_counters", return_value=None):
            result = metrics.snapshot(force=True)
        self.assertFalse(result["cpu"]["valid"])

    def test_eco_network_sampling_accepts_one_second_intervals(self):
        self.settings["profile"] = "eco"
        monitor = api.NetworkMonitor()
        samples = [{"sample_time": stamp, "iface": "eth0", "counter_epoch": "1", "rx_bytes": rx, "tx_bytes": rx * 2} for stamp, rx in ((100, 100), (101.02, 1120))]
        with patch.object(api, "network_snapshot", side_effect=samples):
            monitor.sample()
            monitor.sample()
        with patch.object(api.time, "monotonic", return_value=102.6):
            reply = monitor.response()
            self.assertIsNotNone(reply)
            self.assertEqual(reply["rate"], [1000, 2000])
        with patch.object(api.time, "monotonic", return_value=105):
            self.assertIsNone(monitor.response())

    def test_optional_hardware_failure_does_not_stop_other_metrics(self):
        class History:
            def snapshot(self): return {"rx_bytes": 0, "tx_bytes": 0, "coverage_seconds": 0, "valid": False}
        metrics = api.Metrics(History())
        with patch.object(api, "cpu_sample", return_value=[20, 0, 10, 70]), patch.object(api, "memory_status", return_value={"total": 100, "used": 30, "available": 70, "percent": 30, "valid": True}), patch.object(api, "gpu_sample", side_effect=OSError()), patch.object(api, "temperatures", side_effect=OSError()), patch.object(api, "storage_status", side_effect=OSError()), patch.object(api, "disk_counters", return_value=None), patch.object(api, "uptime_seconds", return_value=123):
            result = metrics.snapshot(force=True)
        self.assertEqual(result["memory"]["percent"], 30)
        self.assertEqual(result["uptime"], 123)
        self.assertFalse(result["gpu"]["valid"])
        self.assertFalse(result["storage"]["valid"])
        self.assertEqual(result["temp"], [])
        self.assertEqual(result["temperature_summary"], {"cpu": None, "disk": None})

    def test_inventory_missing_hardware_is_explicit(self):
        data = hardware.inventory()
        self.assertEqual(set(data), {"gpus", "sensors", "ups", "diagnostics"})
        self.assertEqual(data["gpus"], [])
        self.assertTrue(all(row["status"] == "unavailable" for row in data["diagnostics"]))


class DeviceProtocolTests(unittest.TestCase):
    def test_legacy_v2_display_health_and_token_contract(self):
        snapshot = {"time": 1, "cpu": {"percent": 12, "valid": True}, "gpu": {"utilization": 0, "backend": "unavailable", "valid": False}, "memory": {"percent": 30, "valid": True}, "temp": [], "temperature_summary": {"cpu": None, "disk": None}, "net": {"iface": "eth0", "rx_speed": 0, "tx_speed": 0}, "traffic_24h": {"rx_bytes": 1, "tx_bytes": 2, "coverage_seconds": 3, "valid": True}, "disk_io": {"read_speed": 0, "write_speed": 0, "valid": False}, "storage": {"total": None, "used": None, "percent": None, "valid": False}, "ups": {"watts": None, "valid": False, "source": "unavailable"}, "uptime": None, "v": 2, "seq": 4, "age": 0.1, "metric_age": {"temperature": 0, "storage": 0}}
        class Metrics:
            def read_snapshot(self): return copy.deepcopy(snapshot)
        class Network:
            def latest(self): return {"sample_time": 1, "iface": "eth0", "rx_bytes": 100, "tx_bytes": 200, "counter_epoch": "1"}
            def response(self, since=0, epoch=""): return {"v": 2, "source": "abc", "epoch": "1", "seq": 2, "age": 0.1, "rate": [10, 20], "points": [[2, 1, 10, 20]], "gap": False}
        server = api.LimitedHTTPServer(("127.0.0.1", 0), api.handler_factory(Metrics(), "device-token", Network()))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def get(path, token="device-token"):
            request = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
            with opener.open(request, timeout=2) as response:
                return json.load(response)
        self.assertEqual(get("/health", "bad"), {"status": "ok"})
        with self.assertRaises(urllib.error.HTTPError) as denied:
            get("/status", "bad")
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        old = get("/status")
        self.assertEqual(set(old), set(snapshot))
        self.assertEqual(old["uptime"], 0)
        self.assertEqual(old["gpu"]["utilization"], 0)
        self.assertIsNone(get("/status?v=2")["gpu"]["utilization"])
        self.assertEqual(set(get("/status?v=2&display=1")), {"cpu", "gpu", "memory", "traffic_24h", "disk_io", "storage", "ups", "uptime", "temperature_summary", "v", "seq", "age", "metric_age"})
        self.assertEqual(get("/status?v=2&display=1")["ups"], {"watts": None})
        self.assertEqual(get("/net"), Network().latest())
        self.assertEqual(get("/net?v=2"), Network().response())


if __name__ == "__main__":
    unittest.main(verbosity=2)
