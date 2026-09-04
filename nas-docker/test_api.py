import importlib.util
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("nas_status_api", Path(__file__).with_name("api.py"))
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        api.PROC_ROOT = str(self.root)
        api.SYS_ROOT = str(self.root)
        api.DEBUGFS_ROOT = str(self.root / "sys/kernel/debug")
        api.CONFIGURED_IFACE = "auto"
        api.STORAGE_PATHS = ()
        api.SMART_ROOT = str(self.root / "smartmontools")
        api.SMART_MAX_AGE_SECONDS = 7200
        api._smart_temperature_cache.clear()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, value):
        path = self.root / relative.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value), encoding="utf-8")

    def physical_interface(self, name, rx, tx):
        (self.root / f"sys/class/net/{name}/device").mkdir(parents=True)
        self.write(f"/sys/class/net/{name}/statistics/rx_bytes", rx)
        self.write(f"/sys/class/net/{name}/statistics/tx_bytes", tx)

    def physical_block(self, name, read_sectors, write_sectors, partition=False):
        (self.root / f"sys/class/block/{name}/device").mkdir(parents=True, exist_ok=True)
        if partition:
            self.write(f"/sys/class/block/{name}/partition", 1)
        self.write(f"/sys/class/block/{name}/stat",
                   f"0 0 {read_sectors} 0 0 0 {write_sectors} 0 0 0 0")

    def smart_log(self, name, text, modified):
        path = Path(api.SMART_ROOT) / f"attrlog.{name}.ata.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="ascii")
        os.utime(path, (modified, modified))

    def test_physical_interfaces_are_sorted_and_aggregated(self):
        api.CONFIGURED_IFACE = "physical"
        self.physical_interface("enp3s0", 300, 400)
        self.physical_interface("enp2s0", 100, 200)
        self.write("/sys/class/net/br0/statistics/rx_bytes", 9000)
        self.write("/sys/class/net/br0/statistics/tx_bytes", 9000)
        self.write("/proc/sys/kernel/random/boot_id", "test-boot")
        self.assertEqual(api.selected_network_interfaces(),
                         ("physical:enp2s0,enp3s0", ["enp2s0", "enp3s0"]))
        self.assertEqual(api.traffic_sample(),
                         ("physical:enp2s0,enp3s0", 400, 600, "test-boot"))

        history = Mock()
        history.snapshot.return_value = {}
        metrics = api.Metrics(history)
        with patch.object(api.time, "monotonic", side_effect=(10, 20)):
            self.assertEqual(metrics.snapshot()["net"]["rx_speed"], 0)
            self.write("/sys/class/net/enp2s0/statistics/rx_bytes", 130)
            self.write("/sys/class/net/enp2s0/statistics/tx_bytes", 240)
            self.write("/sys/class/net/enp3s0/statistics/rx_bytes", 370)
            self.write("/sys/class/net/enp3s0/statistics/tx_bytes", 460)
            net = metrics.snapshot()["net"]
        self.assertEqual(net, {"iface": "physical:enp2s0,enp3s0",
                               "rx_speed": 10, "tx_speed": 10})

        (self.root / "sys/class/net/enp3s0/statistics/tx_bytes").unlink()
        self.assertIsNone(api.traffic_sample())

        api.CONFIGURED_IFACE = "br0"
        self.assertEqual(api.traffic_sample(), ("br0", 9000, 9000, "test-boot"))

    def test_net_endpoint_is_authenticated_lightweight_and_fails_as_a_unit(self):
        api.CONFIGURED_IFACE = "physical"
        self.physical_interface("enp3s0", 300, 400)
        self.physical_interface("enp2s0", 100, 200)
        metrics = Mock()
        server = api.ThreadingHTTPServer(("127.0.0.1", 0), api.handler_factory(metrics, "secret"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request("GET", "/net")
            self.assertEqual(connection.getresponse().status, 401)
            with patch.object(api.time, "monotonic", return_value=12.5):
                connection.request("GET", "/net", headers={"Authorization": "Bearer secret"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {
                    "sample_time": 12.5,
                    "iface": "physical:enp2s0,enp3s0",
                    "rx_bytes": 400,
                    "tx_bytes": 600,
                })
            metrics.snapshot.assert_not_called()

            (self.root / "sys/class/net/enp3s0/statistics/tx_bytes").unlink()
            connection.request("GET", "/net", headers={"Authorization": "Bearer secret"})
            response = connection.getresponse()
            self.assertEqual(response.status, 503)
            self.assertEqual(json.loads(response.read()), {"error": "network counters unavailable"})
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_auto_interface_and_amd_gpu(self):
        self.write("/proc/net/route", "Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
                   "enp2s0 00000000 01020304 0003 0 0 10 00000000\n")
        self.write("/sys/class/net/enp2s0/operstate", "up")
        self.write("/sys/class/net/enp2s0/statistics/rx_bytes", "100")
        self.write("/sys/class/net/enp2s0/statistics/tx_bytes", "200")
        self.write("/proc/sys/kernel/random/boot_id", "test-boot")
        self.write("/sys/class/drm/card0/device/gpu_busy_percent", "37")
        self.assertEqual(api.detect_network_interface(), "enp2s0")
        self.assertEqual(api.traffic_sample(), ("enp2s0", 100, 200, "test-boot"))
        self.assertEqual(api.gpu_sample(), ("amdgpu", 37.0, False))
        history = Mock()
        history.snapshot.return_value = {"rx_bytes": 300, "tx_bytes": 400, "valid": True}
        self.assertEqual(api.Metrics(history).snapshot()["traffic_24h"], history.snapshot.return_value)

    def test_cpu_and_hottest_disk_temperature(self):
        self.write("/sys/class/hwmon/hwmon0/name", "k10temp")
        self.write("/sys/class/hwmon/hwmon0/temp1_label", "Tctl")
        self.write("/sys/class/hwmon/hwmon0/temp1_input", "52500")
        self.write("/sys/class/hwmon/hwmon1/name", "drivetemp")
        self.write("/sys/class/hwmon/hwmon1/temp1_input", "41000")
        self.write("/sys/class/hwmon/hwmon2/name", "nvme")
        self.write("/sys/class/hwmon/hwmon2/temp1_label", "Composite")
        self.write("/sys/class/hwmon/hwmon2/temp1_input", "47000")
        values = api.temperatures()
        self.assertEqual(values[0]["type"], "cpu")
        self.assertEqual(values[0]["temp"], 52.5)
        self.assertEqual(values[1]["type"], "disk")
        self.assertEqual(values[1]["temp"], 47.0)

    def test_fresh_smart_logs_supply_hottest_disk_temperature(self):
        now = 10000
        self.smart_log("one", "2026-09-05 03:00:00; 5;100;0; 194;116;31531;\n"
                              "malformed latest line\n", now - 10)
        self.smart_log("two", "2026-09-05 03:00:00; 190;70;00035;\n", now - 20)
        self.smart_log("three", "2026-09-05 03:00:00; 194;100;37;\n", now - 30)
        self.smart_log("stale", "2026-09-04 00:00:00; 194;100;88;\n", now - 7201)
        with patch.object(api.time, "time", return_value=now):
            self.assertEqual(api.smart_disk_temperatures(), [43, 37, 35])
            self.assertEqual(api.smart_temperature_from_line(
                "2026-09-05 03:00:00; 194;100;00043;"), 43)
            values = api.temperatures()
        self.assertEqual(values[0]["type"], "disk")
        self.assertEqual(values[0]["temp"], 43)

    def test_physical_disk_rate_and_storage_filesystem_deduplication(self):
        self.physical_block("sda", 10, 20)
        self.physical_block("nvme0n1", 30, 40)
        self.physical_block("sda1", 9999, 9999, partition=True)
        self.assertEqual(api.physical_block_names(), ["nvme0n1", "sda"])
        self.assertEqual(api.disk_counters(),
                         ("physical:nvme0n1,sda", 40 * 512, 60 * 512))

        history = Mock()
        history.snapshot.return_value = {}
        metrics = api.Metrics(history)
        with patch.object(api.time, "monotonic", side_effect=(10, 12)):
            first = metrics.snapshot()
            self.assertEqual(first["disk_io"]["read_speed"], 0)
            self.assertEqual(first["storage"], {
                "total": None, "used": None, "percent": None,
                "valid": False, "filesystems": 0,
            })
            self.physical_block("sda", 14, 30)
            self.physical_block("nvme0n1", 36, 50)
            disk = metrics.snapshot()["disk_io"]
        self.assertEqual(disk, {
            "devices": "physical:nvme0n1,sda",
            "read_speed": 2560,
            "write_speed": 5120,
            "valid": True,
        })

        api.STORAGE_PATHS = ("/one", "/one-again", "/two", "/missing")
        devices = {"/one": 1, "/one-again": 1, "/two": 2}
        filesystems = {
            "/one": SimpleNamespace(f_frsize=100, f_bsize=100, f_blocks=1000, f_bfree=300),
            "/one-again": SimpleNamespace(f_frsize=100, f_bsize=100, f_blocks=1000, f_bfree=300),
            "/two": SimpleNamespace(f_frsize=100, f_bsize=100, f_blocks=500, f_bfree=100),
        }

        def fake_stat(path):
            if path not in devices:
                raise OSError("missing")
            return SimpleNamespace(st_dev=devices[path])

        def fake_statvfs(path):
            return filesystems[path]

        with patch.object(api.os, "stat", side_effect=fake_stat), \
             patch.object(api.os, "statvfs", side_effect=fake_statvfs):
            self.assertEqual(api.storage_status(), {
                "total": 150000,
                "used": 110000,
                "percent": 73.3,
                "valid": True,
                "filesystems": 2,
            })


if __name__ == "__main__":
    unittest.main()
