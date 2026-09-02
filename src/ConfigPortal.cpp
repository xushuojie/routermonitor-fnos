#include "ConfigPortal.h"

#include <ArduinoJson.h>
#include <DNSServer.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>
#include <LittleFS.h>

DeviceConfig deviceConfig;

static ESP8266WebServer configServer(80);
static DNSServer configDns;
static bool portalStarted = false;
static bool portalApMode = false;
static bool restartPending = false;
static unsigned long restartAt = 0;

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
<form method="post" action="/save">
<label>Wi-Fi SSID<input name="ssid" maxlength="32" autocomplete="off"></label>
<label>Wi-Fi password<input name="wifi_password" type="password" maxlength="64" autocomplete="new-password"></label>
<label>NAS host / IP<input name="nas_host" maxlength="64" autocomplete="off"></label>
<label>NAS port<input name="nas_port" type="number" min="1" max="65535" value="18199"></label>
<label>Access token<input name="nas_token" type="password" maxlength="128" autocomplete="new-password"></label>
<h3>Night mode</h3><div class="grid">
<label>Start hour<input name="night_start" type="number" min="0" max="23" placeholder="23"></label>
<label>End hour<input name="night_end" type="number" min="0" max="23" placeholder="7"></label>
<label>Day PWM<input name="day_brightness" type="number" min="0" max="255" placeholder="180"></label>
<label>Night PWM<input name="night_brightness" type="number" min="0" max="255" placeholder="235"></label></div>
<div class="hint">For this display, a larger PWM value is darker.</div>
<button type="submit">Save and restart</button></form></main></body></html>
)HTML";

static void copyArg(const String &value, char *target, size_t targetSize)
{
    value.toCharArray(target, targetSize);
}

bool loadDeviceConfig()
{
    if (!LittleFS.begin())
    {
        if (!LittleFS.format() || !LittleFS.begin())
            return false;
    }

    File file = LittleFS.open("/config.json", "r");
    if (!file)
        return false;

    StaticJsonDocument<768> doc;
    const DeserializationError error = deserializeJson(doc, file);
    file.close();
    if (error)
        return false;

    strlcpy(deviceConfig.wifiSsid, doc["wifi_ssid"] | "", sizeof(deviceConfig.wifiSsid));
    strlcpy(deviceConfig.wifiPassword, doc["wifi_password"] | "", sizeof(deviceConfig.wifiPassword));
    strlcpy(deviceConfig.nasHost, doc["nas_host"] | "", sizeof(deviceConfig.nasHost));
    deviceConfig.nasPort = doc["nas_port"] | 18199;
    strlcpy(deviceConfig.nasToken, doc["nas_token"] | "", sizeof(deviceConfig.nasToken));
    deviceConfig.nightStartHour = doc["night_start"] | deviceConfig.nightStartHour;
    deviceConfig.nightEndHour = doc["night_end"] | deviceConfig.nightEndHour;
    deviceConfig.dayBrightness = doc["day_brightness"] | deviceConfig.dayBrightness;
    deviceConfig.nightBrightness = doc["night_brightness"] | deviceConfig.nightBrightness;
    return true;
}

bool saveDeviceConfig()
{
    StaticJsonDocument<768> doc;
    doc["wifi_ssid"] = deviceConfig.wifiSsid;
    doc["wifi_password"] = deviceConfig.wifiPassword;
    doc["nas_host"] = deviceConfig.nasHost;
    doc["nas_port"] = deviceConfig.nasPort;
    doc["nas_token"] = deviceConfig.nasToken;
    doc["night_start"] = deviceConfig.nightStartHour;
    doc["night_end"] = deviceConfig.nightEndHour;
    doc["day_brightness"] = deviceConfig.dayBrightness;
    doc["night_brightness"] = deviceConfig.nightBrightness;

    File file = LittleFS.open("/config.json", "w");
    if (!file)
        return false;
    const bool ok = serializeJson(doc, file) > 0;
    file.close();
    return ok;
}

bool hasNasConfig()
{
    return deviceConfig.nasHost[0] != '\0' && deviceConfig.nasToken[0] != '\0';
}

static void sendConfigPage()
{
    configServer.send_P(200, "text/html; charset=utf-8", CONFIG_PAGE);
}

static void saveFromRequest()
{
    const String ssid = configServer.arg("ssid");
    if (ssid.length())
    {
        copyArg(ssid, deviceConfig.wifiSsid, sizeof(deviceConfig.wifiSsid));
        copyArg(configServer.arg("wifi_password"), deviceConfig.wifiPassword, sizeof(deviceConfig.wifiPassword));
    }

    const String nasHost = configServer.arg("nas_host");
    if (nasHost.length())
        copyArg(nasHost, deviceConfig.nasHost, sizeof(deviceConfig.nasHost));

    const long port = configServer.arg("nas_port").toInt();
    if (port > 0 && port <= 65535)
        deviceConfig.nasPort = static_cast<uint16_t>(port);

    const String token = configServer.arg("nas_token");
    if (token.length())
        copyArg(token, deviceConfig.nasToken, sizeof(deviceConfig.nasToken));

    const String nightStartArg = configServer.arg("night_start");
    const String nightEndArg = configServer.arg("night_end");
    const String dayBrightnessArg = configServer.arg("day_brightness");
    const String nightBrightnessArg = configServer.arg("night_brightness");
    const long nightStart = nightStartArg.toInt();
    const long nightEnd = nightEndArg.toInt();
    const long dayBrightness = dayBrightnessArg.toInt();
    const long nightBrightness = nightBrightnessArg.toInt();
    if (nightStartArg.length() && nightStart >= 0 && nightStart <= 23)
        deviceConfig.nightStartHour = static_cast<uint8_t>(nightStart);
    if (nightEndArg.length() && nightEnd >= 0 && nightEnd <= 23)
        deviceConfig.nightEndHour = static_cast<uint8_t>(nightEnd);
    if (dayBrightnessArg.length() && dayBrightness >= 0 && dayBrightness <= 255)
        deviceConfig.dayBrightness = static_cast<uint8_t>(dayBrightness);
    if (nightBrightnessArg.length() && nightBrightness >= 0 && nightBrightness <= 255)
        deviceConfig.nightBrightness = static_cast<uint8_t>(nightBrightness);

    if (!saveDeviceConfig())
    {
        configServer.send(500, "text/plain; charset=utf-8", "Save failed");
        return;
    }

    configServer.send(200, "text/html; charset=utf-8",
                      "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><h2>Saved</h2><p>The display is restarting.</p>");
    restartPending = true;
    restartAt = millis() + 1200;
}

void startConfigPortal()
{
    if (portalStarted)
        return;

    if (WiFi.status() != WL_CONNECTED)
    {
        WiFi.mode(WIFI_AP_STA);
        char apName[25];
        snprintf(apName, sizeof(apName), "RouterMonitor-%06X", ESP.getChipId());
        WiFi.softAP(apName);
        configDns.start(53, "*", WiFi.softAPIP());
        portalApMode = true;
        Serial.print(F("Setup AP: "));
        Serial.println(apName);
        Serial.print(F("Setup URL: http://"));
        Serial.println(WiFi.softAPIP());
    }

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

bool isConfigPortalStarted()
{
    return portalStarted;
}

bool isConfigPortalApMode()
{
    return portalApMode;
}
