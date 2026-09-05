#include <cassert>
#include <cstring>
#include <iostream>

// Include the production translation unit so its private helpers and state are exercised unchanged.
#include "../src/ConfigPortal.cpp"

static DeviceConfig makeConfig(const char *host)
{
    DeviceConfig config;
    strlcpy(config.wifiSsid, "test-wifi", sizeof(config.wifiSsid));
    strlcpy(config.wifiPassword, "test-password", sizeof(config.wifiPassword));
    strlcpy(config.nasHost, host, sizeof(config.nasHost));
    strlcpy(config.nasToken, "test-token", sizeof(config.nasToken));
    strlcpy(config.adminPassword, "test-admin-password-1234", sizeof(config.adminPassword));
    return config;
}

static void resetRuntime()
{
    mockFileSystem.reset();
    deviceConfig = DeviceConfig();
    portalStarted = false;
    portalApMode = false;
    restartPending = false;
    restartAt = 0;
    fileSystemMounted = true;
    newAdminPassword = false;
    configProblem = 0;
    std::memset(portalApName, 0, sizeof(portalApName));
    std::memset(portalApSecret, 0, sizeof(portalApSecret));
    std::memset(csrfSecret, 0, sizeof(csrfSecret));
    configServer.reset();
    configDns = DNSServer();
    LittleFS = LittleFSMock();
    WiFi = WiFiMock();
    ESP = ESPMock();
}

static void seedConfig(const DeviceConfig &config)
{
    assert(persistDeviceConfig(config));
    assert(!mockFileSystem.files[CONFIG_PATH].empty());
    assert(mockFileSystem.files[CONFIG_PATH].front() == '{');
    deviceConfig = config;
}

static void assertLoads(const DeviceConfig &expected)
{
    deviceConfig = DeviceConfig();
    assert(loadDeviceConfig());
    assert(configsEqual(deviceConfig, expected));
}

static void testShortWriteKeepsCurrentConfig()
{
    resetRuntime();
    const DeviceConfig current = makeConfig("nas-old");
    seedConfig(current);

    const size_t renamesBefore = mockFileSystem.renameCalls;
    mockFileSystem.maximumWrite = 7;
    assert(!persistDeviceConfig(makeConfig("nas-new")));
    assert(mockFileSystem.renameCalls == renamesBefore);

    mockFileSystem.maximumWrite = std::numeric_limits<size_t>::max();
    assertLoads(current);
}

static void testSecondRenameFailureRecoversBackup()
{
    resetRuntime();
    const DeviceConfig current = makeConfig("nas-old");
    seedConfig(current);

    mockFileSystem.failRenameFrom = CONFIG_TEMP_PATH;
    mockFileSystem.failRenameCount = 1;
    assert(!persistDeviceConfig(makeConfig("nas-new")));
    assert(!LittleFS.exists(CONFIG_PATH));
    assert(LittleFS.exists(CONFIG_BACKUP_PATH));

    mockFileSystem.failRenameFrom.clear();
    assertLoads(current);
    assert(LittleFS.exists(CONFIG_PATH));
    assert(LittleFS.exists(CONFIG_BACKUP_PATH));
}

static void writeLegacyConfig(const char *path, const DeviceConfig &config)
{
    DynamicJsonDocument doc(1024);
    doc["wifi_ssid"] = config.wifiSsid;
    doc["wifi_password"] = config.wifiPassword;
    doc["nas_host"] = config.nasHost;
    doc["nas_port"] = config.nasPort;
    doc["nas_token"] = config.nasToken;
    doc["night_start"] = config.nightStartHour;
    doc["night_end"] = config.nightEndHour;
    doc["day_brightness"] = config.dayBrightness;
    doc["night_brightness"] = config.nightBrightness;
    File file = LittleFS.open(path, "w");
    assert(file);
    assert(serializeJson(doc, file) == measureJson(doc));
    file.close();
}

static void testCorruptPrimaryMigratesLegacyBackup()
{
    resetRuntime();
    DeviceConfig legacy = makeConfig("nas-legacy");
    legacy.adminPassword[0] = '\0';
    writeLegacyConfig(CONFIG_BACKUP_PATH, legacy);
    mockFileSystem.files[CONFIG_PATH] = "corrupt";

    assert(loadDeviceConfig());
    assert(std::strlen(deviceConfig.adminPassword) == 24);
    assert(std::strcmp(deviceConfig.nasHost, legacy.nasHost) == 0);
    assert(newAdminPassword);

    DeviceConfig primary;
    DeviceConfig backup;
    assert(readConfigFile(CONFIG_PATH, primary, true));
    assert(readConfigFile(CONFIG_BACKUP_PATH, backup, false));
    assert(std::strcmp(primary.nasHost, legacy.nasHost) == 0);
    assert(backup.adminPassword[0] == '\0');
}

static void setSaveArguments(const char *csrf, const char *host)
{
    configServer.arguments.clear();
    configServer.arguments["csrf"] = csrf;
    configServer.arguments["nas_host"] = host;
}

static void assertNoSaveOccurred(const DeviceConfig &expected, size_t writesBefore, size_t renamesBefore)
{
    assert(configsEqual(deviceConfig, expected));
    assert(mockFileSystem.writeOpenCalls == writesBefore);
    assert(mockFileSystem.renameCalls == renamesBefore);
    DeviceConfig stored;
    assert(readConfigFile(CONFIG_PATH, stored, true));
    assert(configsEqual(stored, expected));
}

static void testAuthenticationAndCsrfRejectBeforeWrite()
{
    resetRuntime();
    const DeviceConfig current = makeConfig("nas-old");
    seedConfig(current);
    strlcpy(csrfSecret, "0123456789abcdefghijklmnopqrstuv", sizeof(csrfSecret));
    WiFi.connectionStatus = WL_CONNECTED;

    const size_t writesBefore = mockFileSystem.writeOpenCalls;
    const size_t renamesBefore = mockFileSystem.renameCalls;
    setSaveArguments(csrfSecret, "nas-attacker");
    configServer.authenticationAllowed = false;
    saveFromRequest();
    assert(configServer.responseCode == 401);
    assertNoSaveOccurred(current, writesBefore, renamesBefore);

    configServer.responseCode = 0;
    configServer.authenticationAllowed = true;
    setSaveArguments("wrong-token", "nas-attacker");
    saveFromRequest();
    assert(configServer.responseCode == 403);
    assertNoSaveOccurred(current, writesBefore, renamesBefore);
}

static void testMountFailureNeverFormatsNonBlankFlash()
{
    resetRuntime();
    fileSystemMounted = false;
    mockFileSystem.beginResult = false;
    ESP.erasedFlash = false;
    assert(!mountFileSystem());
    assert(mockFileSystem.formatCalls == 0);
    assert(!LittleFS.lastAutoFormat);

    resetRuntime();
    fileSystemMounted = false;
    mockFileSystem.beginResult = false;
    ESP.erasedFlash = true;
    assert(mountFileSystem());
    assert(mockFileSystem.formatCalls == 1);
    assert(!LittleFS.lastAutoFormat);
}

int main()
{
    testShortWriteKeepsCurrentConfig();
    testSecondRenameFailureRecoversBackup();
    testCorruptPrimaryMigratesLegacyBackup();
    testAuthenticationAndCsrfRejectBeforeWrite();
    testMountFailureNeverFormatsNonBlankFlash();
    std::cout << "PASS: executable ConfigPortal failure, recovery, authentication and CSRF paths" << std::endl;
}
