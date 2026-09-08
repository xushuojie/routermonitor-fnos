"""Local integration check: actual backend routes and Linux metric collectors, isolated data."""
import json, os, sys, tempfile, threading, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
with tempfile.TemporaryDirectory(prefix='nas-monitor-check-') as data:
    os.environ.update(NAS_STATUS_HISTORY_DB=str(Path(data)/'traffic.sqlite3'), NAS_STATUS_ADMIN_PASSWORD='1',
                      NAS_STATUS_TOKEN='browser-check-device-token', NAS_STATUS_STORAGE_SNAPSHOT=str(Path(data)/'storage.json'),
                      PROC_ROOT='', SYS_ROOT='', DEBUGFS_ROOT='/sys/kernel/debug')
    stop=threading.Event()
    def snapshot():
        while not stop.is_set():
            value={'sampled_at':time.time(),'total':1000000000000,'used':500000000000,'percent':50,'valid':True,'filesystems':1,'mode':'auto','reason':'',
                   'volumes':[{'id':'test-volume','path':'/vol1','mounts':['/vol1'],'filesystem':'btrfs','source':'test-device','total':1000000000000,'used':500000000000,'percent':50,'valid':True,'included':True,'selected':True,'reason':''}]}
            path=Path(data)/'storage.next';path.write_text(json.dumps(value));os.replace(path,Path(data)/'storage.json');stop.wait(10)
    worker=threading.Thread(target=snapshot,daemon=True);worker.start()
    import api
    sys.argv=['api.py','--bind','0.0.0.0','--port','18731']
    try:api.main()
    finally:stop.set();worker.join()
