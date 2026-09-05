#!/usr/bin/env python3
"""Run: python3 tests/check_layout.py (first run `pio run -e nodemcuv2`).

Uses the installed LVGL glyph/kerning tables and compiles the real formatter
with a host C++ compiler. Checks pixel budgets, not hardware rendering.
"""
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / ".pio/libdeps/nodemcuv2/lv_arduino/src/src/lv_font"


def font_width(size):
    if size != 12:
        name = {22: "monitor_value_22", 42: "monitor_clock_42", "rate": "monitor_rate_42", "metric": "monitor_metric_22"}[size]
        source = (ROOT / "src/DisplayFonts.c").read_text()
        glyphs = source.split(name + "_glyphs[] =", 1)[1].split("};", 1)[0]
        advances = [int(n) for n in re.findall(r"\.adv_w = (\d+)", glyphs)]
        codes = source.split(name + "_unicode[] = {", 1)[1].split("}", 1)[0]
        ids = {int(n) + 32: i + 1 for i, n in enumerate(re.findall(r"\d+", codes))}
        return lambda text, spacing=0: sum((advances[ids[ord(c)]] + 8) >> 4 for c in text) + max(0, len(text) - 1) * spacing
    path = FONTS / f"lv_font_montserrat_{size}.c"
    if not path.is_file():
        raise SystemExit(f"Missing {path}; run `pio run -e nodemcuv2` first.")
    source = path.read_text()

    def array(name):
        body = source.split(name + "[] =", 1)[1].split("};", 1)[0]
        return [int(n, 0) for n in re.findall(r"-?0x[0-9a-f]+|-?\d+", body)]

    glyphs = source.split("glyph_dsc[] =", 1)[1].split("};", 1)[0]
    advances = [int(n) for n in re.findall(r"\.adv_w = (\d+)", glyphs)]
    unicode = array("unicode_list_1")
    left, right, pairs = map(array, ("kern_left_class_mapping", "kern_right_class_mapping", "kern_class_values"))
    columns = int(re.search(r"\.right_class_cnt\s*=\s*(\d+)", source)[1])
    scale = int(re.search(r"\.kern_scale\s*=\s*(\d+)", source)[1])

    def width(text, spacing=0):
        ids = [ord(c) - 31 if 32 <= ord(c) <= 126 else 96 + unicode.index(ord(c) - 176) for c in text]
        total = 0
        for index, glyph in enumerate(ids):
            adjustment = 0
            if index + 1 < len(ids) and glyph < len(left) and ids[index + 1] < len(right):
                lclass, rclass = left[glyph], right[ids[index + 1]]
                if lclass and rclass:
                    adjustment = pairs[(lclass - 1) * columns + rclass - 1] * scale >> 4
            total += (advances[glyph] + adjustment + 8) >> 4
        return total + max(0, len(ids) - 1) * spacing

    return width


def formatted(cases):
    compiler = shutil.which("c++")
    if not compiler:
        raise SystemExit("A host C++ compiler is required (on macOS: xcode-select --install).")
    with tempfile.TemporaryDirectory() as directory:
        source, binary = Path(directory) / "check.cpp", Path(directory) / "check"
        source.write_text('''#include "TrafficFormat.h"
#include <stdlib.h>
int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i)
        for (int rate = 0; rate < 2; ++rate) {
            const TransferText text = formatTransfer(strtod(argv[i], nullptr), rate);
            printf("%s\\t%s\\n", text.number, text.unit);
        }
}
''')
        subprocess.run([compiler, "-std=c++11", "-I", str(ROOT / "src"), str(source), "-o", str(binary)], check=True)
        output = subprocess.check_output([str(binary), *map(str, cases)], text=True)
    return [tuple(line.split("\t")) for line in output.splitlines()]


def main():
    widths = {size: font_width(size) for size in (12, 22, 42, "rate")}
    cases = [-1, math.nan, math.inf, 0, 1, 999.95]
    expected = [("--", ""), ("--", ""), ("--", ""), ("0", "B"), ("1", "B"), ("1.0", "KB")]
    units = ("KB", "MB", "GB", "TB", "PB")
    for power, unit in enumerate(units, 1):
        for value in (0.1, 444.4, 999.9):
            cases.append(value * 1000 ** power)
            expected.append((f"{value:.1f}", unit) if value >= 1 else ("100", "B") if power == 1 else ("100.0", units[power - 2]))
        if power < len(units):
            cases.append(999.95 * 1000 ** power)
            expected.append(("1.0", units[power]))
    cases.append(1e21)
    expected.append(("999+", "PB"))
    results = formatted(cases)
    for index, (number, unit) in enumerate(expected):
        actual = results[index * 2]
        assert actual == (number, unit), (cases[index], actual)

    rate_cases = formatted((99_400, 99_500, 1_200_000, 12_300_000,
                            100_000_000, 140_000_000, 999_400_000, 999_500_000, 1e21))[1::2]
    assert rate_cases == [("99.4", "KB/s"), ("99.5", "KB/s"), ("1.2", "MB/s"),
                          ("12.3", "MB/s"), ("100", "MB/s"), ("140", "MB/s"),
                          ("999", "MB/s"), ("1.0", "GB/s"), ("999", "PB/s")], rate_cases

    # Exhaust all possible rounded strings, including wide 4s and decimal carries.
    rates = ["--"] + [str(n) for n in range(1000)] + [f"{n / 10:.1f}" for n in range(1000)]
    totals = ["--", "999+", "1000"] + [f"{n / 10:.1f}" for n in range(10000)]
    assert max(widths["rate"](n) for n in rates) <= 70
    assert max(widths[22](n) for n in rates) <= 53
    assert max(widths[22](n) for n in totals) <= 65
    assert max(widths[22](n + unit) for n in rates for unit in "BKMGTP") <= 76
    assert max(widths[12](unit) for unit in ("B/s", "KB/s", "MB/s", "GB/s", "TB/s", "PB/s")) <= 30
    assert max(widths[12](unit) for unit in ("B", "KB", "MB", "GB", "TB", "PB")) <= 21
    # Digit widths are equal in every numeric font, including the narrow digit 1.
    for font in (22, 42, "rate"):
        assert len({widths[font](str(n)) for n in range(10)}) == 1

    max_clock = max(widths[42](f"{hour:02}:{minute:02}:{second:02}", -2)
                    for hour in range(24) for minute in range(60) for second in range(60))
    assert max_clock <= 182, max_clock
    assert max(widths[12](day) for day in ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")) <= 46
    assert max(widths[12](f"{month:02}-{day:02}") for month in range(1, 13) for day in range(1, 32)) <= 46
    for title, width in (("LAST 24H", 230), ("UPTIME", 230), ("CPU", 28),
                         ("DISK MAX", 60), ("TOTAL", 77), ("USED", 76),
                         ("USAGE", 77), ("CPU", 72), ("GPU", 72), ("MEM", 72)):
        assert widths[12](title) <= width, title
    assert font_width("metric")("100%") <= 53
    assert font_width("metric")("99.9") + 1 + widths[12]("W") <= 53
    assert widths[12]("POWER") <= 53
    assert max(widths[22](value) for value in ("99", "100")) <= 45
    assert widths[22]("49710") <= 75 and widths[12]("°C") <= 14

    source = (ROOT / "src/main.ino").read_text()
    update_rate = source.split("static void updateRateValue", 1)[1].split("void updateNetworkInfoLabel", 1)[0]
    assert "lv_obj_set_pos" not in update_rate and "text_font" not in update_rate
    assert "textWidth" not in source
    for snippet in (
        "lv_obj_set_size(chart, 220, 18)",
        "lv_obj_set_size(carousel_viewport, 230, 35)",
        "lv_obj_set_pos(carousel_viewport, 5, 96)",
        "lv_obj_set_pos(carousel_dots[index], 108 + index * 7, 132)",
        "lv_obj_set_pos(time_label, 53, 138)",
        "lv_obj_set_pos(bar, left, 232)",
        "lv_anim_set_values(&exit, 0, -230)",
        "lv_anim_set_values(&enter, 230, 0)",
    ):
        assert snippet in source, snippet
    assert source.count("carousel_layer = lv_obj_create(") == 1
    carousel_code = source.split("static void carouselEnterReady", 1)[1].split("void styleMetricBar", 1)[0]
    assert "delay(" not in carousel_code and "while (" not in carousel_code
    assert all(label in source for label in ('"LAST 24H"', '"UPTIME"', '"DISK MAX"', '"USAGE"'))
    assert all(divider in source for divider in (
        "createDivider(10, 67, 220", "createDivider(5, 69, 230",
        "createDivider(5, 95, 230", "createDivider(5, 136, 230"))
    assert widths[42]("00:00:00", -2) == max_clock
    print(f"PASS: {len(cases) * 2} formatter cases; {len(rates)} rate strings, {len(totals)} totals; fixed digit advances; "
          f"all 86400 clock strings <= {max_clock}/182 px; 5px border bands fit.")


if __name__ == "__main__":
    main()
