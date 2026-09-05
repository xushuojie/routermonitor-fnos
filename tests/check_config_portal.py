#!/usr/bin/env python3
"""Static contract for the device's destructive and authentication boundaries."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/ConfigPortal.cpp").read_text()
HEADER = (ROOT / "src/ConfigPortal.h").read_text()


def function_body(name):
    start = SOURCE.index(name)
    opening = SOURCE.index("{", start)
    depth = 0
    for index in range(opening, len(SOURCE)):
        if SOURCE[index] == "{":
            depth += 1
        elif SOURCE[index] == "}":
            depth -= 1
            if depth == 0:
                return SOURCE[opening + 1:index]
    raise AssertionError(f"unterminated function: {name}")


def main():
    mount = function_body("static bool mountFileSystem()")
    assert "LittleFSConfig config(false)" in mount
    assert SOURCE.count("LittleFS.format()") == 1
    assert mount.index("isErasedFileSystem()") < mount.index("LittleFS.format()")

    persist = function_body("static bool persistDeviceConfig(")
    assert persist.index("writeAndVerifyTemp(config)") < persist.index("LittleFS.rename(CONFIG_PATH, CONFIG_BACKUP_PATH)")
    assert persist.index("LittleFS.rename(CONFIG_PATH, CONFIG_BACKUP_PATH)") < persist.index("LittleFS.rename(CONFIG_TEMP_PATH, CONFIG_PATH)")
    assert "remove(CONFIG_BACKUP_PATH" not in SOURCE
    assert "readConfigFile(CONFIG_BACKUP_PATH" in function_body("bool loadDeviceConfig()")

    save = function_body("static void saveFromRequest()")
    assert save.index("DeviceConfig candidate = deviceConfig") < save.index("persistDeviceConfig(candidate)")
    assert save.index("persistDeviceConfig(candidate)") < save.index("deviceConfig = candidate")

    assert "char adminPassword[33]" in HEADER
    assert "configServer.authenticate" not in SOURCE
    assert "requestAuthentication" not in SOURCE
    assert "equalsConstantTime(csrfSecret)" in SOURCE
    assert "SameSite=Strict" in SOURCE

    setup_ap = function_body("static bool startSetupAp()")
    assert setup_ap.index("generateSecret(portalApSecret") < setup_ap.index("WiFi.softAP(portalApName, portalApSecret)")
    start_portal = function_body("void startConfigPortal()")
    assert "WiFi.status() != WL_CONNECTED || configProblem != 0" in start_portal
    assert start_portal.index("startSetupAp()") < start_portal.index("if (portalStarted)")
    stop_ap = function_body("void stopConfigPortalAp()")
    assert "configProblem != 0" in stop_ap
    assert stop_ap.index("configDns.stop()") < stop_ap.index("WiFi.softAPdisconnect(true)")
    assert "configServer.stop" not in stop_ap

    print("PASS: no mount-failure format; verified atomic save/backup recovery; password-free page, CSRF and protected AP lifecycle")


if __name__ == "__main__":
    main()
