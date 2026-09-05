#!/usr/bin/env python3
"""Local emulator smoke fixture: http://10.0.2.2:18998, token fixture-only. Never NAS data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer fixture-only':
            self.send_error(401)
            return
        now = time.monotonic()
        if self.path.startswith('/net?'):
            seq = int(now * 5)
            result = dict(v=2, age=0, epoch='smoke-fixture', seq=seq, rate=[999499999, 999499],
                          points=[[i, i/5, 12000000+(i%10)*100000, 1400000] for i in range(seq-3, seq+1)])
        elif self.path.startswith('/status?'):
            result = dict(v=2, age=0, seq=int(now), uptime=86400*999+3660,
                          cpu=dict(percent=100), gpu=dict(utilization=100), memory=dict(percent=100),
                          disk_io=dict(valid=True, read_speed=999499999, write_speed=999499),
                          ups=dict(watts=35), temperature_summary=dict(cpu=100, disk=99),
                          metric_age=dict(storage=0, temperature=0),
                          traffic_24h=dict(valid=True, coverage_seconds=86400, tx_bytes=999400000000, rx_bytes=999400000000),
                          storage=dict(valid=True, total=18917997150208, used=15900000000000, percent=100))
        else:
            self.send_error(404)
            return
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 18998), Handler).serve_forever()
