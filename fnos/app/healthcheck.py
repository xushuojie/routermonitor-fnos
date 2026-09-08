"""Check API and collector freshness without requiring available hardware."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
from hardware_settings import intervals

try:
    # Read supervisor argv: works with Compose and FPK configurable ports.
    args = Path('/proc/1/cmdline').read_bytes().decode().split('\0')
    port = int(args[args.index('--port') + 1]) if '--port' in args else 18199
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:%d/health' % port, timeout=3) as response:
        assert json.load(response)['status'] == 'ok'
    snapshot = json.loads(Path(os.environ.get('NAS_STATUS_STORAGE_SNAPSHOT', '/tmp/nas-monitor/storage.json')).read_text())
    assert 0 <= time.time() - snapshot['sampled_at'] <= max(90, intervals()['storage'] * 3)
except (OSError, ValueError, KeyError, AssertionError):
    sys.exit(1)
