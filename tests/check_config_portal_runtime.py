#!/usr/bin/env python3
"""Build and run the production ConfigPortal.cpp against deterministic host mocks."""

from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ARDUINO_JSON = ROOT / ".pio/libdeps/nodemcuv2/ArduinoJson/src"


def main():
    if not (ARDUINO_JSON / "ArduinoJson.h").is_file():
        raise SystemExit("ArduinoJson is not installed in .pio; install the PlatformIO dependencies first")
    with tempfile.TemporaryDirectory(prefix="routermonitor-config-test-") as output_dir:
        executable = Path(output_dir) / "config_portal_runtime"
        subprocess.run(
            [
                "c++",
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-DARDUINOJSON_ENABLE_ARDUINO_STRING=0",
                "-DARDUINOJSON_ENABLE_ARDUINO_STREAM=0",
                "-DARDUINOJSON_ENABLE_ARDUINO_PRINT=0",
                "-DARDUINOJSON_ENABLE_PROGMEM=0",
                "-I",
                str(ROOT / "tests/config_portal_mocks"),
                "-I",
                str(ROOT / "src"),
                "-I",
                str(ARDUINO_JSON),
                str(ROOT / "tests/config_portal_runtime.cpp"),
                "-o",
                str(executable),
            ],
            check=True,
        )
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
