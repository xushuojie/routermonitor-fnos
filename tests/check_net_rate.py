#!/usr/bin/env python3
"""Compile and run the firmware's counter-delta logic on the host."""
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    compiler = shutil.which("c++")
    if not compiler:
        raise SystemExit("A host C++ compiler is required.")
    source = r'''
#include "NetRate.h"
#include <cassert>
#include <cmath>
#include <cstring>

static NasNetSample sample(double time, double rx, double tx, const char *iface = "physical:enp2s0,enp3s0") {
    NasNetSample value;
    value.sampleTime = time;
    value.rxBytes = rx;
    value.txBytes = tx;
    std::strcpy(value.iface, iface);
    return value;
}

int main() {
    NetRate rate;
    const NasNetSample first = sample(100.0, 1.0e12, 2.0e12);
    assert(calculateNetRate(first, sample(100.2, 1.0e12 + 200000, 2.0e12 + 100000), rate));
    assert(std::fabs(rate.rxBytesPerSecond - 1000000.0) < 0.1);
    assert(std::fabs(rate.txBytesPerSecond - 500000.0) < 0.1);
    assert(!calculateNetRate(first, sample(100.0, 1.0e12 + 1, 2.0e12 + 1), rate));
    assert(!calculateNetRate(first, sample(100.2, 1, 2.0e12 + 1), rate));
    assert(!calculateNetRate(first, sample(100.2, 1.0e12 + 1, 2.0e12 + 1, "enp3s0"), rate));
}
'''
    with tempfile.TemporaryDirectory() as directory:
        cpp = Path(directory) / "check.cpp"
        binary = Path(directory) / "check"
        cpp.write_text(source)
        subprocess.run([compiler, "-std=c++11", "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print("PASS: server-time deltas, terabyte counters, reset and interface changes")


if __name__ == "__main__":
    main()
