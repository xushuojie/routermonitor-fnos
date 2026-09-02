import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


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

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, value):
        path = self.root / relative.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value), encoding="utf-8")

    def test_auto_interface_and_amd_gpu(self):
        self.write("/proc/net/route", "Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
                   "enp2s0 00000000 01020304 0003 0 0 10 00000000\n")
        self.write("/sys/class/net/enp2s0/operstate", "up")
        self.write("/sys/class/net/enp2s0/statistics/rx_bytes", "100")
        self.write("/sys/class/net/enp2s0/statistics/tx_bytes", "200")
        self.write("/sys/class/drm/card0/device/gpu_busy_percent", "37")
        self.assertEqual(api.detect_network_interface(), "enp2s0")
        self.assertEqual(api.gpu_sample(), ("amdgpu", 37.0, False))

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


if __name__ == "__main__":
    unittest.main()
