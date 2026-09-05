#include "ConfigPortal.h"

#include <ArduinoJson.h>
#include <DNSServer.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>
#include <LittleFS.h>
#include <flash_hal.h>

DeviceConfig deviceConfig;

static ESP8266WebServer configServer(80);
static DNSServer configDns;
static bool portalStarted = false;
static bool portalApMode = false;
static bool restartPending = false;
static unsigned long restartAt = 0;
static bool fileSystemMounted = false;
static bool newAdminPassword = false;
static uint8_t configProblem = 0; // 0: ready, 1: mount failed, 2: config invalid or write failed.
static char portalApName[25] = {0};
static char portalApSecret[17] = {0};
static char csrfSecret[33] = {0};

static const char *const CONFIG_PATH = "/config.json";
static const char *const CONFIG_TEMP_PATH = "/config.tmp";
static const char *const CONFIG_BACKUP_PATH = "/config.bak";
static const char *const ADMIN_USER = "admin";

static const char CONFIG_PAGE[] PROGMEM = R"HTML(
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Router Monitor Setup</title><style>
body{margin:0;background:#101820;color:#eef4f7;font:16px system-ui}main{max-width:420px;margin:auto;padding:24px}
h2{margin:0 0 8px}.hint{color:#9db0ba;font-size:13px;margin-bottom:22px}label{display:block;margin-top:14px}
input{box-sizing:border-box;width:100%;margin-top:6px;padding:11px;border:1px solid #38505c;border-radius:10px;background:#192832;color:white}
button{width:100%;margin-top:22px;padding:12px;border:0;border-radius:12px;background:#278fbd;color:white;font-weight:700}
a{color:#63d0fc}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
</style></head><body><main><h2>Router Monitor</h2>
	<div class="hint">Saved passwords and tokens are never shown. Leave a NAS field blank to keep its saved value.</div>
	<div id="storage_error" class="hint" hidden></div>
	<form method="post" action="/save"><input id="csrf" name="csrf" type="hidden">
<label>Wi-Fi SSID<input name="ssid" maxlength="32" autocomplete="off"></label>
<label>Wi-Fi password<input name="wifi_password" type="password" maxlength="64" autocomplete="new-password"></label>
<label>NAS host / IP<input name="nas_host" maxlength="64" autocomplete="off"></label>
	<label>NAS port<input name="nas_port" type="number" min="1" max="65535" placeholder="18199"></label>
<label>Access token<input name="nas_token" type="password" maxlength="128" autocomplete="new-password"></label>
<h3>Night mode</h3><div class="grid">
<label>Start hour<input name="night_start" type="number" min="0" max="23" placeholder="23"></label>
<label>End hour<input name="night_end" type="number" min="0" max="23" placeholder="7"></label>
<label>Day PWM<input name="day_brightness" type="number" min="0" max="255" placeholder="180"></label>
<label>Night PWM<input name="night_brightness" type="number" min="0" max="255" placeholder="235"></label></div>
<div class="hint">For this display, a larger PWM value is darker.</div>
	<button type="submit">Save and restart</button></form>
	<noscript><p class="hint">JavaScript is required to save settings.</p></noscript>
	<script>const m=document.cookie.match(/(?:^|; )router_setup=([^;]*)/);if(m){const p=m[1].split('.');document.getElementById('csrf').value=p[0];if(p[1]!=='ok'){const e=document.getElementById('storage_error');e.hidden=false;e.textContent=p[1]==='mount'?'Storage could not be mounted and was not formatted automatically. Saving is disabled until local recovery.':'Saved configuration is invalid. Saving here replaces the config file without formatting storage.';}}</script>
	</main></body></html>
)HTML";

static void generateSecret(char *target, size_t length)
{
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    static_assert(sizeof(alphabet) - 1 == 64, "secret alphabet must contain 64 characters");
    uint8_t randomBytes[32];
    ESP.random(randomBytes, length);
    for (size_t index = 0; index < length; ++index)
        target[index] = alphabet[randomBytes[index] & 0x3f];
    target[length] = '\0';
}

static bool isErasedFileSystem()
{
    uint32_t words[64];
    for (size_t offset = 0; offset < FS_PHYS_SIZE; offset += sizeof(words))
    {
        const size_t bytes = min(sizeof(words), static_cast<size_t>(FS_PHYS_SIZE - offset));
        if (!ESP.flashRead(FS_PHYS_ADDR + offset, words, bytes))
            return false;
        for (size_t index = 0; index < bytes / sizeof(words[0]); ++index)
            if (words[index] != UINT32_MAX)
                return false;
        if ((offset & 0xfff) == 0)
            yield();
    }
    return true;
}

static bool mountFileSystem()
{
    if (fileSystemMounted)
        return true;

    LittleFSConfig config(false);
    if (!LittleFS.setConfig(config))
    {
        configProblem = 1;
        return false;
    }
    if (LittleFS.begin())
    {
        fileSystemMounted = true;
        configProblem = 0;
        return true;
    }

    // A failed mount can mean corruption. Format only a conclusively erased, first-use partition.
    if (!isErasedFileSystem() || !LittleFS.format() || !LittleFS.begin())
    {
        configProblem = 1;
        return false;
    }
    fileSystemMounted = true;
    configProblem = 0;
    return true;
}

static bool isTerminated(const char *value, size_t size)
{
    return memchr(value, '\0', size) != NULL;
}

static bool isHeaderSafe(const char *value)
{
    for (; *value; ++value)
        if (static_cast<uint8_t>(*value) < 0x20 || *value == 0x7f)
            return false;
    return true;
}

static bool validDeviceConfig(const DeviceConfig &config, bool requireAdmin)
{
    if (!isTerminated(config.wifiSsid, sizeof(config.wifiSsid)) ||
        !isTerminated(config.wifiPassword, sizeof(config.wifiPassword)) ||
        !isTerminated(config.nasHost, sizeof(config.nasHost)) ||
        !isTerminated(config.nasToken, sizeof(config.nasToken)) ||
        !isTerminated(config.adminPassword, sizeof(config.adminPassword)) || config.nasPort == 0 ||
        config.nightStartHour > 23 || config.nightEndHour > 23 || !isHeaderSafe(config.nasHost) ||
        !isHeaderSafe(config.nasToken))
        return false;

    const size_t adminLength = strlen(config.adminPassword);
    return requireAdmin ? adminLength >= 16 : adminLength == 0 || adminLength >= 16;
}

static bool readText(JsonObjectConst root, const char *name, char *target, size_t size, bool required)
{
    JsonVariantConst value = root[name];
    if (value.isNull())
        return !required;
    if (!value.is<const char *>())
        return false;
    const char *text = value.as<const char *>();
    if (strlen(text) >= size)
        return false;
    strlcpy(target, text, size);
    return true;
}

static bool readNumber(JsonObjectConst root, const char *name, long minimum, long maximum,
                       long &target, bool required)
{
    JsonVariantConst value = root[name];
    if (value.isNull())
        return !required;
    if (!value.is<long>())
        return false;
    const long number = value.as<long>();
    if (number < minimum || number > maximum)
        return false;
    target = number;
    return true;
}

static bool readConfigFile(const char *path, DeviceConfig &config, bool requireAdmin)
{
    File file = LittleFS.open(path, "r");
    if (!file || file.size() == 0 || file.size() > 1536)
    {
        if (file)
            file.close();
        return false;
    }

    DynamicJsonDocument doc(1024);
    const DeserializationError error = deserializeJson(doc, file);
    file.close();
    if (error || !doc.is<JsonObject>())
        return false;

    DeviceConfig parsed;
    JsonObjectConst root = doc.as<JsonObjectConst>();
    long nasPort = parsed.nasPort;
    long nightStart = parsed.nightStartHour;
    long nightEnd = parsed.nightEndHour;
    long dayBrightness = parsed.dayBrightness;
    long nightBrightness = parsed.nightBrightness;
    // Every field written by the original firmware is required. Only the new admin password is optional for migration.
    if (!readText(root, "wifi_ssid", parsed.wifiSsid, sizeof(parsed.wifiSsid), true) ||
        !readText(root, "wifi_password", parsed.wifiPassword, sizeof(parsed.wifiPassword), true) ||
        !readText(root, "nas_host", parsed.nasHost, sizeof(parsed.nasHost), true) ||
        !readNumber(root, "nas_port", 1, 65535, nasPort, true) ||
        !readText(root, "nas_token", parsed.nasToken, sizeof(parsed.nasToken), true) ||
        !readText(root, "admin_password", parsed.adminPassword, sizeof(parsed.adminPassword), requireAdmin) ||
        !readNumber(root, "night_start", 0, 23, nightStart, true) ||
        !readNumber(root, "night_end", 0, 23, nightEnd, true) ||
        !readNumber(root, "day_brightness", 0, 255, dayBrightness, true) ||
        !readNumber(root, "night_brightness", 0, 255, nightBrightness, true))
        return false;

    parsed.nasPort = static_cast<uint16_t>(nasPort);
    parsed.nightStartHour = static_cast<uint8_t>(nightStart);
    parsed.nightEndHour = static_cast<uint8_t>(nightEnd);
    parsed.dayBrightness = static_cast<uint8_t>(dayBrightness);
    parsed.nightBrightness = static_cast<uint8_t>(nightBrightness);
    if (!validDeviceConfig(parsed, requireAdmin))
        return false;
    config = parsed;
    return true;
}

static bool configsEqual(const DeviceConfig &left, const DeviceConfig &right)
{
    return strcmp(left.wifiSsid, right.wifiSsid) == 0 &&
           strcmp(left.wifiPassword, right.wifiPassword) == 0 && strcmp(left.nasHost, right.nasHost) == 0 &&
           left.nasPort == right.nasPort && strcmp(left.nasToken, right.nasToken) == 0 &&
           strcmp(left.adminPassword, right.adminPassword) == 0 && left.nightStartHour == right.nightStartHour &&
           left.nightEndHour == right.nightEndHour && left.dayBrightness == right.dayBrightness &&
           left.nightBrightness == right.nightBrightness;
}

static bool writeAndVerifyTemp(const DeviceConfig &config)
{
    {
        DynamicJsonDocument doc(1024);
        doc["wifi_ssid"] = config.wifiSsid;
        doc["wifi_password"] = config.wifiPassword;
        doc["nas_host"] = config.nasHost;
        doc["nas_port"] = config.nasPort;
        doc["nas_token"] = config.nasToken;
        doc["admin_password"] = config.adminPassword;
        doc["night_start"] = config.nightStartHour;
        doc["night_end"] = config.nightEndHour;
        doc["day_brightness"] = config.dayBrightness;
        doc["night_brightness"] = config.nightBrightness;
        if (doc.overflowed())
            return false;

        File file = LittleFS.open(CONFIG_TEMP_PATH, "w");
        if (!file)
            return false;
        const size_t expected = measureJson(doc);
        const size_t written = serializeJson(doc, file);
        file.flush();
        file.close();
        if (written != expected)
            return false;
    }

    DeviceConfig verified;
    return readConfigFile(CONFIG_TEMP_PATH, verified, true) && configsEqual(config, verified);
}

static bool persistDeviceConfig(const DeviceConfig &config)
{
    if (!fileSystemMounted || !validDeviceConfig(config, true) || !writeAndVerifyTemp(config))
        return false;

    {
        DeviceConfig current;
        if (readConfigFile(CONFIG_PATH, current, false) && !LittleFS.rename(CONFIG_PATH, CONFIG_BACKUP_PATH))
            return false;
    }

    if (!LittleFS.rename(CONFIG_TEMP_PATH, CONFIG_PATH))
        return false; // The last valid config remains at CONFIG_BACKUP_PATH.

    DeviceConfig verified;
    return readConfigFile(CONFIG_PATH, verified, true) && configsEqual(config, verified);
}

static void announceNewAdminPassword()
{
    newAdminPassword = true;
    Serial.println(F("New configuration login: admin"));
    Serial.print(F("New configuration password: "));
    Serial.println(deviceConfig.adminPassword);
}

bool loadDeviceConfig()
{
    if (!mountFileSystem())
        return false;

    const bool primaryExists = LittleFS.exists(CONFIG_PATH);
    const bool backupExists = LittleFS.exists(CONFIG_BACKUP_PATH);
    DeviceConfig loaded;
    bool fromBackup = false;
    if (!readConfigFile(CONFIG_PATH, loaded, false))
    {
        if (!readConfigFile(CONFIG_BACKUP_PATH, loaded, false))
        {
            if (primaryExists || backupExists)
            {
                configProblem = 2;
                return false;
            }
            generateSecret(loaded.adminPassword, 24);
            if (!persistDeviceConfig(loaded))
            {
                configProblem = 2;
                return false;
            }
            deviceConfig = loaded;
            configProblem = 0;
            announceNewAdminPassword();
            return true;
        }
        fromBackup = true;
    }

    if (!loaded.adminPassword[0])
    {
        generateSecret(loaded.adminPassword, 24);
        if (persistDeviceConfig(loaded))
        {
            deviceConfig = loaded;
            announceNewAdminPassword();
            return true;
        }
        deviceConfig = loaded; // Monitoring still works; LAN configuration remains closed.
        configProblem = 2;
        return true;
    }

    deviceConfig = loaded;
    if (fromBackup)
        configProblem = persistDeviceConfig(loaded) ? 0 : 2; // Repair primary while retaining validated backup.
    else
        configProblem = 0;
    return true;
}

bool saveDeviceConfig()
{
    return persistDeviceConfig(deviceConfig);
}

bool hasNasConfig()
{
    return deviceConfig.nasHost[0] != '\0' && deviceConfig.nasToken[0] != '\0';
}

static void sendConfigPage()
{
    const bool requestFromAp = portalApMode && configServer.client().localIP() == WiFi.softAPIP();
    if (!requestFromAp)
    {
        if (!deviceConfig.adminPassword[0])
        {
            configServer.send(503, "text/plain; charset=utf-8", "Configuration login is unavailable; use local setup recovery.");
            return;
        }
        if (!configServer.authenticate(ADMIN_USER, deviceConfig.adminPassword))
        {
            configServer.requestAuthentication(DIGEST_AUTH, "Router Monitor");
            return;
        }
    }

    String cookie = F("router_setup=");
    cookie += csrfSecret;
    cookie += configProblem == 1 ? F(".mount") : (configProblem == 2 ? F(".config") : F(".ok"));
    cookie += F("; Path=/; SameSite=Strict");
    configServer.sendHeader(F("Set-Cookie"), cookie);
    configServer.sendHeader(F("Cache-Control"), F("no-store"));
    configServer.sendHeader(F("X-Frame-Options"), F("DENY"));
    configServer.send_P(200, "text/html; charset=utf-8", CONFIG_PAGE);
}

static bool authorizeSave()
{
    const bool requestFromAp = portalApMode && configServer.client().localIP() == WiFi.softAPIP();
    if (!requestFromAp && !deviceConfig.adminPassword[0])
    {
        configServer.send(503, "text/plain; charset=utf-8", "Configuration login is unavailable; use local setup recovery.");
        return false;
    }
    if (!requestFromAp && !configServer.authenticate(ADMIN_USER, deviceConfig.adminPassword))
    {
        configServer.requestAuthentication(DIGEST_AUTH, "Router Monitor");
        return false;
    }
    const String csrf = configServer.arg("csrf");
    if (csrf.length() != 32 || !csrf.equalsConstantTime(csrfSecret))
    {
        configServer.send(403, "text/plain; charset=utf-8", "Invalid form token");
        return false;
    }
    return true;
}

static bool copyArg(const String &value, char *target, size_t targetSize)
{
    if (value.length() >= targetSize)
        return false;
    value.toCharArray(target, targetSize);
    return true;
}

static bool readRequestNumber(const char *name, long minimum, long maximum, long &value, bool &present)
{
    const String text = configServer.arg(name);
    present = text.length() != 0;
    if (!present)
        return true;
    char *end = NULL;
    const long parsed = strtol(text.c_str(), &end, 10);
    if (end == text.c_str() || *end != '\0' || parsed < minimum || parsed > maximum)
        return false;
    value = parsed;
    return true;
}

static void saveFromRequest()
{
    if (!authorizeSave())
        return;

    DeviceConfig candidate = deviceConfig;
    const String ssid = configServer.arg("ssid");
    if (ssid.length())
    {
        if (!copyArg(ssid, candidate.wifiSsid, sizeof(candidate.wifiSsid)) ||
            !copyArg(configServer.arg("wifi_password"), candidate.wifiPassword, sizeof(candidate.wifiPassword)))
        {
            configServer.send(400, "text/plain; charset=utf-8", "Invalid Wi-Fi settings");
            return;
        }
    }

    const String nasHost = configServer.arg("nas_host");
    if (nasHost.length() && !copyArg(nasHost, candidate.nasHost, sizeof(candidate.nasHost)))
    {
        configServer.send(400, "text/plain; charset=utf-8", "Invalid NAS host");
        return;
    }

    const String token = configServer.arg("nas_token");
    if (token.length() && !copyArg(token, candidate.nasToken, sizeof(candidate.nasToken)))
    {
        configServer.send(400, "text/plain; charset=utf-8", "Invalid access token");
        return;
    }

    long port = candidate.nasPort;
    long nightStart = candidate.nightStartHour;
    long nightEnd = candidate.nightEndHour;
    long dayBrightness = candidate.dayBrightness;
    long nightBrightness = candidate.nightBrightness;
    bool portPresent = false;
    bool nightStartPresent = false;
    bool nightEndPresent = false;
    bool dayBrightnessPresent = false;
    bool nightBrightnessPresent = false;
    if (!readRequestNumber("nas_port", 1, 65535, port, portPresent) ||
        !readRequestNumber("night_start", 0, 23, nightStart, nightStartPresent) ||
        !readRequestNumber("night_end", 0, 23, nightEnd, nightEndPresent) ||
        !readRequestNumber("day_brightness", 0, 255, dayBrightness, dayBrightnessPresent) ||
        !readRequestNumber("night_brightness", 0, 255, nightBrightness, nightBrightnessPresent))
    {
        configServer.send(400, "text/plain; charset=utf-8", "Invalid numeric setting");
        return;
    }
    if (portPresent)
        candidate.nasPort = static_cast<uint16_t>(port);
    if (nightStartPresent)
        candidate.nightStartHour = static_cast<uint8_t>(nightStart);
    if (nightEndPresent)
        candidate.nightEndHour = static_cast<uint8_t>(nightEnd);
    if (dayBrightnessPresent)
        candidate.dayBrightness = static_cast<uint8_t>(dayBrightness);
    if (nightBrightnessPresent)
        candidate.nightBrightness = static_cast<uint8_t>(nightBrightness);

    const bool generatedAdmin = candidate.adminPassword[0] == '\0';
    if (generatedAdmin)
        generateSecret(candidate.adminPassword, 24);
    if (!validDeviceConfig(candidate, true))
    {
        configServer.send(400, "text/plain; charset=utf-8", "Invalid settings");
        return;
    }
    if (!persistDeviceConfig(candidate))
    {
        if (fileSystemMounted)
            configProblem = 2;
        configServer.send(500, "text/plain; charset=utf-8", "Save failed");
        return;
    }
    deviceConfig = candidate;
    configProblem = 0;
    if (generatedAdmin)
        announceNewAdminPassword();

    configServer.send(200, "text/html; charset=utf-8",
                      "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><h2>Saved</h2><p>The display is restarting.</p>");
    restartPending = true;
    restartAt = millis() + 1200;
}

static bool startSetupAp()
{
    WiFi.mode(WIFI_AP_STA);
    snprintf(portalApName, sizeof(portalApName), "RouterMonitor-%06X", ESP.getChipId());
    generateSecret(portalApSecret, 16);
    if (!WiFi.softAP(portalApName, portalApSecret))
        return false;
    configDns.start(53, "*", WiFi.softAPIP());
    portalApMode = true;
    Serial.print(F("Setup AP: "));
    Serial.println(portalApName);
    Serial.print(F("Setup AP password: "));
    Serial.println(portalApSecret);
    Serial.print(F("Setup URL: http://"));
    Serial.println(WiFi.softAPIP());
    return true;
}

void startConfigPortal()
{
    if (!csrfSecret[0])
        generateSecret(csrfSecret, 32);
    if ((WiFi.status() != WL_CONNECTED || configProblem != 0) && !portalApMode)
        startSetupAp();
    if (portalStarted)
        return;

    configServer.on("/", HTTP_GET, sendConfigPage);
    configServer.on("/save", HTTP_POST, saveFromRequest);
    configServer.on("/generate_204", HTTP_GET, sendConfigPage);
    configServer.on("/hotspot-detect.html", HTTP_GET, sendConfigPage);
    configServer.onNotFound(sendConfigPage);
    configServer.begin();
    portalStarted = true;
}

void handleConfigPortal()
{
    if (!portalStarted)
        return;
    if (portalApMode)
        configDns.processNextRequest();
    configServer.handleClient();
    if (restartPending && static_cast<long>(millis() - restartAt) >= 0)
        ESP.restart();
}

void stopConfigPortalAp()
{
    if (!portalApMode || configProblem != 0)
        return;
    const bool stationConnected = WiFi.status() == WL_CONNECTED;
    configDns.stop();
    WiFi.softAPdisconnect(true);
    portalApMode = false;
    memset(portalApSecret, 0, sizeof(portalApSecret));
    if (stationConnected)
        WiFi.mode(WIFI_STA);
}

bool isConfigPortalStarted()
{
    return portalStarted;
}

bool isConfigPortalApMode()
{
    return portalApMode;
}

const char *configPortalApSsid()
{
    return portalApMode ? portalApName : "";
}

const char *configPortalApPassword()
{
    return portalApMode ? portalApSecret : "";
}

const char *configPortalAdminPasswordForSetup()
{
    return newAdminPassword ? deviceConfig.adminPassword : "";
}
