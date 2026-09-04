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
    widths = {size: font_width(size) for size in (12, 22, 42)}
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

    rate_cases = formatted((1_200_000, 12_300_000, 999_400_000, 999_500_000, 1e21))[1::2]
    assert rate_cases == [("1.2", "MB/s"), ("12", "MB/s"), ("999", "MB/s"),
                          ("1.0", "GB/s"), ("999+", "PB/s")], rate_cases

    max_cell = 0
    # 42px is preferred for network rates; every overflow must fit at 22px.
    for number, unit in results + [("444.4", unit + suffix) for unit in units for suffix in ("", "/s")]:
        for icon in ("\uf019", "\uf093"):
            fixed = widths[12](icon) + widths[12](unit) + 2
            size = 42 if fixed + widths[42](number) <= 112 else 22
            used = fixed + widths[size](number)
            assert used <= 112, (number, unit, size, used)
            max_cell = max(max_cell, used)

    max_clock = max(widths[42](f"{hour:02}:{minute:02}:{second:02}", -2)
                    for hour in range(24) for minute in range(60) for second in range(60))
    assert max_clock <= 182, max_clock
    assert max(widths[12](day) for day in ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")) <= 42
    assert max(widths[12](f"{month:02}-{day:02}") for month in range(1, 13) for day in range(1, 32)) <= 42
    for number, unit in results[::2]:
        compact = number + unit
        size = 22 if widths[22](compact) <= 72 else 12
        assert widths[size](compact) <= 72, (compact, size)
    for title, width in (("UP 24H", 112), ("DOWN 24H", 112), ("UP 59M", 112),
                         ("DOWN 59M", 112), ("DOWN 23H", 112), ("UPTIME", 230),
                         ("CPU TEMP", 112), ("DISK MAX", 112), ("TOTAL", 72),
                         ("USED", 72), ("USE", 72), ("CPU", 72), ("GPU", 72), ("MEM", 72)):
        assert widths[12](title) <= width, title
    assert widths[22]("100%") <= 72
    assert widths[22]("99°C") <= 112

    bands = (("network", 5, 51), ("chart", 51, 71), ("disk", 71, 95),
             ("carousel", 95, 135), ("clock", 135, 181), ("metrics", 181, 235))
    assert bands[0][1] == 5 and bands[-1][2] == 235
    assert all(current[2] == following[1] for current, following in zip(bands, bands[1:])), bands
    assert widths[42]("00:00:00", -2) == max_clock
    print(f"PASS: {len(cases) * 2} formatter cases; network cells <= {max_cell}/112 px; "
          f"all 86400 clock strings <= {max_clock}/182 px; 5px border bands fit.")


if __name__ == "__main__":
    main()
