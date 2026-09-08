import importlib.util, json, sys, unittest, tempfile, subprocess, time, os
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('host_collector', ROOT/'docker/host_collector.py')
host = importlib.util.module_from_spec(spec); spec.loader.exec_module(host)
class HostCollectorTests(unittest.TestCase):
 def test_pci_normalization(self):
  self.assertEqual(host.pci_id('00000000:0A:00.0'), 'pci:0000:0a:00.0')
  self.assertIsNone(host.pci_id('not-a-device'))
 def test_nvidia_query_readonly_and_missing_utilization(self):
  result = type('Result', (), {'stdout': '00000000:01:00.0, NVIDIA test, 42\n00000000:02:00.0, NVIDIA unknown, N/A\n'})()
  with patch.object(host.shutil, 'which', return_value='/usr/bin/nvidia-smi'), patch.object(host.subprocess, 'run', return_value=result) as run:
   rows = host.nvidia_rows()
   self.assertEqual(rows[0]['utilization'], 42); self.assertFalse(rows[1]['valid'])
   self.assertEqual(run.call_args.args[0][1], '--query-gpu=pci.bus_id,name,utilization.gpu')
 def test_intel_complete_intervals_and_truncated_tail(self):
  first = {'engines': {'Render/3D/0': {'busy': 0}, 'Video/0': {'busy': 0}}}
  second = {'engines': {'Render/3D/0': {'busy': 2}, 'Video/0': {'busy': 87}}}
  payload = '['+json.dumps(first)+','+json.dumps(second)+', {"engines":'
  self.assertEqual(host.intel_percent(payload), 87)
  self.assertIsNone(host.intel_percent(json.dumps([first])))
 def test_no_tools_does_not_fail(self):
  with patch.object(host.shutil, 'which', return_value=None): self.assertEqual(host.nvidia_rows(), [])
 @unittest.skipUnless(sys.platform=='linux', 'Linux lifecycle')
 def test_worker_lifecycle_and_duplicate_start(self):
  with tempfile.TemporaryDirectory(prefix='nas-gpu-check-') as folder:
   output=Path(folder)
   try:
    host.control('start', output)
    deadline=time.monotonic()+12
    while time.monotonic()<deadline and not (output/'gpu.json').exists(): time.sleep(.1)
    self.assertTrue((output/'gpu.json').exists())
    pid=(output/'collector.pid').read_text()
    host.control('start',output);time.sleep(.3)
    self.assertEqual((output/'collector.pid').read_text(),pid)
    self.assertEqual(json.loads((output/'gpu.json').read_text())['schema'],1)
   finally:host.control('stop',output)
   self.assertFalse((output/'collector.pid').exists())
if __name__=='__main__':unittest.main()
