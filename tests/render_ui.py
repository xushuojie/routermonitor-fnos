#!/usr/bin/env python3
"""Render the real main.ino UI with host LVGL, replacing only device I/O.

Requires a C/C++ compiler and the installed PlatformIO LVGL sources. Outputs
five 240x240 PPM frames; host memory usage is not a device benchmark.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LVGL = ROOT / ".pio/libdeps/nodemcuv2/lv_arduino/src"


def replace_function(source, name, body):
    match = re.search(r"(?:static )?(?:void|bool) " + name + r"\([^)]*\)\s*\{", source)
    if not match:
        return source
    start, depth, end = match.end(), 1, match.end()
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[:start] + "\n" + body + "\n}" + source[end:]


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="monitor-preview-") as tmp:
        build = Path(tmp)
        config = (ROOT / "include/lv_conf.h").read_text().replace("PROGMEM", "")
        config = config.replace("#define LV_TICK_CUSTOM     1", "#define LV_TICK_CUSTOM     0")
        config = config.replace("(16U * 1024U)", "(64U * 1024U)")  # 64-bit host pointers.
        (build / "lv_conf.h").write_text(config)
        snapshot = (ROOT / "src/NasStatus.h").read_text().split("struct NasStatusSnapshot", 1)[1].split("};", 1)[0]
        source = (ROOT / "src/main.ino").read_text()
        for header in ("TFT_eSPI.h", "ESP8266WiFi.h", "ConfigPortal.h", "NasStatus.h"):
            source = re.sub(r'#include [<"]' + re.escape(header) + r'[>"]', "", source)
        source = re.sub(r"TFT_eSPI tft[^\n]+", "", source)
        for name in ("setBrightness", "serviceWiFi", "net_task_cb", "task_cb", "loop", "reportDiagnostics"):
            source = replace_function(source, name, "")
        source = replace_function(source, "my_disp_flush", '''
    for (int y = area->y1; y <= area->y2; ++y)
        for (int x = area->x1; x <= area->x2; ++x)
            frame[y * 240 + x] = *color_p++;
    lv_disp_flush_ready(disp);
''')
        source = re.sub(r"    tft\.[^\n]+", "", source)
        source = source.replace("    loadDeviceConfig();", "")
        prelude = '''#include <cstdio>
#include <cstring>
#include <algorithm>
#include <lvgl.h>
using std::min; using std::max;
#define F(text) text
#define ESP8266 1
#define memcpy_P memcpy
#define constrain(x, a, b) std::min(std::max((x), (a)), (b))
static lv_color_t frame[240 * 240];
struct SerialStub {
 void begin(int) {} template<class T> void print(T) {} template<class T> void println(T) {}
 void printf(const char *, ...) {} void flush() {}
} Serial;
struct EspStub { unsigned getFreeHeap() { return 24000; } } ESP;
unsigned long millis() { return lv_tick_get(); }
struct { unsigned char dayBrightness=180, nightBrightness=235, nightStartHour=23, nightEndHour=7; } deviceConfig;
struct NasStatusSnapshot''' + snapshot + "};\n"
        postlude = r'''
int main(int argc, char **argv) {
    setup();
    lv_obj_del(login_page); login_page = NULL;
    lv_obj_set_hidden(monitor_page, false);
    nasOnline = true;
    nasStatus.rxBytesPerSecond=8600000; nasStatus.txBytesPerSecond=1200000;
    nasStatus.rxBytes24h=236400000000.; nasStatus.txBytes24h=18600000000.;
    nasStatus.trafficHistoryValid=true; nasStatus.trafficCoverageSeconds=86400;
    nasStatus.cpuTemperature=46; nasStatus.diskTemperature=39;
    nasStatus.diskReadBytesPerSecond=84000000; nasStatus.diskWriteBytesPerSecond=12000000;
    nasStatus.diskIoValid=true; nas_uptime_seconds=7*86400+13*3600;
    nasStatus.storageTotalBytes=24000000000000.; nasStatus.storageUsedBytes=15600000000000.;
    nasStatus.storagePercent=65; nasStatus.storageValid=true;
    updateNetworkInfoLabel(); updateDiskIo(true);
    setLabelText(time_label, "13:42:36"); setLabelText(weekday_label, "FRI"); setLabelText(date_label, "09-05");
    updateMetric(cpu_bar,cpu_value_label,23,true);
    updateMetric(gpu_bar,gpu_value_label,0,true);
    updateMetric(mem_bar,mem_value_label,48,true);
    for (int i=0;i<CHART_POINT_COUNT;i++) appendChartSample(6500000+1700000*sin(i*.41),1900000+1200000*sin(i*.6));
    carouselPage=2;
    const double cpuTemps[]={74,75,85,46,85};
    const double diskTemps[]={49,50,60,39,60};
    const unsigned colors[]={0xdce4ea,0xffc66d,0xff7185,0xdce4ea,0xdce4ea};
    for(int i=0;i<5;i++) {
        nasOnline=i!=4; nasStatus.cpuTemperature=cpuTemps[i]; nasStatus.diskTemperature=diskTemps[i];
        renderCarousel();
        for(int j=0;j<2;j++)
            if(lv_obj_get_style_text_color(carousel_value[j],LV_LABEL_PART_MAIN).full != lv_color_hex(colors[i]).full) return 4;
    }
    nasOnline=true; nasStatus.cpuTemperature=46; nasStatus.diskTemperature=39;
    for (int page=0;page<5;page++) {
        carouselPage=page % 4;
        if(page==4) {
            nasStatus.txBytesPerSecond=140000000; nasStatus.rxBytesPerSecond=99400000;
            nasStatus.txBytes24h=444400000000.; nasStatus.rxBytes24h=999900000000.;
            updateNetworkInfoLabel();
        }
        renderCarousel(); lv_obj_invalidate(lv_scr_act()); lv_refr_now(NULL);
        char path[1024]; snprintf(path,sizeof(path),"%s/page-%d.ppm",argv[1],page);
        FILE *out=fopen(path,"wb"); fprintf(out,"P6\n240 240\n255\n");
        int borderErrors=0;
        const uint32_t background=lv_color_to32(lv_color_hex(0x101820));
        for(int i=0;i<240*240;i++) {
            lv_color32_t c; c.full=lv_color_to32(frame[i]);
            const unsigned char rgb[]={c.ch.red,c.ch.green,c.ch.blue}; fwrite(rgb,1,3,out);
            int x=i%240,y=i/240;
            if((x<5||x>=235||y<5||y>=235) && c.full != background) { if(!borderErrors) fprintf(stderr,"Border pixel %d,%d rgb %d,%d,%d\n",x,y,rgb[0],rgb[1],rgb[2]); borderErrors++; }
        }
        fclose(out);
        if(borderErrors) return 3;
    }
}
'''
        cpp = build / "preview.cpp"
        cpp.write_text(prelude + source + postlude)
        flags = ["-O1", "-DLV_CONF_INCLUDE_SIMPLE", "-I" + str(build), "-I" + str(LVGL), "-I" + str(ROOT / "src")]
        files = [*LVGL.glob("src/**/*.c"), ROOT / "src/DisplayFonts.c"]
        def compile_one(item):
            index, path = item
            target = build / f"{index}.o"
            subprocess.run(["cc", *flags, "-c", str(path), "-o", str(target)], check=True, capture_output=True)
            return str(target)
        with ThreadPoolExecutor(max_workers=8) as pool:
            objects = list(pool.map(compile_one, enumerate(files)))
        binary = build / "preview"
        subprocess.run(["c++", "-std=c++11", *flags, str(cpp), *objects, "-o", str(binary)], check=True)
        subprocess.run([str(binary), str(output)], check=True)
    print(f"Rendered five actual LVGL frames; all 5px edge pixels match the background: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    main(parser.parse_args().output.resolve())
