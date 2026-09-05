#!/usr/bin/env python3
"""Run the firmware's actual JSON decoders with host ArduinoJson."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'src/NasStatus.h').read_text()

def method(name):
    start = source.index('    ' + name)
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

snapshot = source[source.index('struct NasStatusSnapshot'):source.index('enum class NasRequestError')]
methods = '\n'.join(method(name) for name in (
    'bool parseNet()', 'bool parseNetV2(', 'static bool number(',
    'static double optionalNumber(', 'bool parseStatus()'))
# ArduinoJson slots contain pointers: scale only host scratch space, not protocol bounds.
methods = methods.replace('StaticJsonDocument<768>', 'StaticJsonDocument<768 * sizeof(void*) / 4>')
methods = methods.replace('StaticJsonDocument<1024>', 'StaticJsonDocument<1024 * sizeof(void*) / 4>')
program = r'''
#include <ArduinoJson.h>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <cassert>
#include <string>
#include "NetRate.h"
#include "BoundedHttpResponse.h"
''' + snapshot + '''
class Decoder {
public:
 static size_t strlcpy(char *target, const char *value, size_t capacity) {
    const size_t length = strlen(value);
    if(capacity) { const size_t count = length < capacity - 1 ? length : capacity - 1; memcpy(target,value,count); target[count]=0; }
    return length;
 }
 struct GraphPoint { double time; float rx, tx; };
 GraphPoint points_[4];
 uint8_t pointIndex_ = 0, pointCount_ = 0;
 uint32_t sequence_ = 0;
 char streamEpoch_[33] = {0};
 NasNetSample netSample_;
 NasStatusSnapshot statusSnapshot_;
 bool netReady_ = false, statusReady_ = false;
 BoundedHttpResponse<1024> response_;
''' + methods + r'''
 bool parse(const char *body, bool net) {
    response_.reset();
    std::string packet = "HTTP/1.1 200 OK\r\nContent-Length: " + std::to_string(strlen(body)) + "\r\n\r\n" + body;
    response_.feed(packet.data(), packet.size());
    assert(response_.state() == HttpResponseState::Complete);
    return net ? parseNet() : parseStatus();
 }
};
int main() {
 Decoder d;
 assert(d.parse(R"({"sample_time":10,"iface":"eth0","rx_bytes":100,"tx_bytes":200,"counter_epoch":"1"})", true));
 assert(d.netReady_ && !d.netSample_.serverRates);
 assert(d.parse(R"({"v":2,"source":"0123456789abcdef","epoch":"1234567890123456789","seq":4,"age":0.1,"rate":[1000,500],"points":[[1,10,1000,500],[2,10.2,1000,500],[3,10.4,1000,500],[4,10.6,1000,500]],"gap":true})", true));
 assert(d.pointCount_ == 4 && d.netSample_.serverRates && d.netSample_.gap);
 assert(d.netSample_.rxAverage == 1000 && d.points_[3].tx == 500);
 assert(d.parse(R"({"v":2,"source":"0123456789abcdef","epoch":"1234567890123456789","seq":4,"age":0.1,"rate":[1000,500],"points":[],"gap":false})", true));
 assert(!d.parse(R"({"v":2,"source":"0123456789abcdef","epoch":"1234567890123456789","seq":7,"age":0.1,"rate":[1000,500],"points":[[7,11,1000,500]],"gap":false})", true));
 assert(!d.parse(R"({"v":2,"source":"0123456789abcdef","epoch":"1234567890123456789","seq":5,"age":2,"rate":[1000,500],"points":[[5,11,1000,500]],"gap":false})", true));
 assert(d.parse(R"({"v":2,"source":"new","epoch":"999","seq":1,"age":0,"rate":[null,null],"points":[[1,12,null,null]],"gap":true})", true));
 assert(d.netSample_.rxAverage == -1 && d.points_[0].rx == -1);
 assert(d.parse(R"({"v":2,"seq":1,"age":0.2,"metric_age":{"temperature":1,"storage":2},"cpu":{"percent":23},"gpu":{"utilization":null},"memory":{"percent":48},"uptime":null,"ups":{"watts":14.1},"temperature_summary":{"cpu":46,"disk":39},"traffic_24h":{"rx_bytes":236400000000,"tx_bytes":18600000000,"coverage_seconds":86400,"valid":true},"disk_io":{"read_speed":84000000,"write_speed":12000000,"valid":true},"storage":{"total":24000000000000,"used":15600000000000,"percent":65,"valid":true}})", false));
 assert(d.statusSnapshot_.cpuPercent == 23 && d.statusSnapshot_.gpuPercent == -1);
 assert(!d.statusSnapshot_.uptimeValid && fabs(d.statusSnapshot_.powerWatts - 14.1) < .001);
 assert(d.statusSnapshot_.trafficHistoryValid && d.statusSnapshot_.storageValid);
 assert(d.parse(R"({"cpu":{"percent":1},"gpu":{"utilization":2},"memory":{"percent":3},"uptime":100})", false));
 assert(d.statusSnapshot_.uptimeValid && d.statusSnapshot_.gpuPercent == 2);
 assert(!d.parse(R"({"v":3,"cpu":{},"gpu":{},"memory":{}})", false));
}
'''
with tempfile.TemporaryDirectory() as tmp:
    cpp, binary = Path(tmp) / 'check.cpp', Path(tmp) / 'check'
    cpp.write_text(program)
    subprocess.run(['c++', '-std=c++11', '-I' + str(ROOT / 'src'),
                    '-I' + str(ROOT / '.pio/libdeps/nodemcuv2/ArduinoJson/src'),
                    str(cpp), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PASS: firmware v1/v2, bounded replay, duplicate/reset/gap, stale data and missing GPU')
