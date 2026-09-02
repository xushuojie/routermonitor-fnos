#ifndef __NAS_STATUS_H
#define __NAS_STATUS_H

#include <ArduinoJson.h>
#include <ESP8266WiFi.h>

#include "ConfigPortal.h"

struct NasStatusSnapshot
{
    double cpuPercent = 0;
    double gpuPercent = 0;
    double memoryPercent = 0;
    double temperature = 0;
    uint32_t uptimeSeconds = 0;
    double rxBytesPerSecond = 0;
    double txBytesPerSecond = 0;
};

bool getNasStatus(NasStatusSnapshot &snapshot)
{
    WiFiClient client;
    client.setTimeout(5000);

    if (!hasNasConfig())
        return false;

    if (!client.connect(deviceConfig.nasHost, deviceConfig.nasPort))
    {
        Serial.println(F("NAS status connection failed"));
        return false;
    }

    client.print(F("GET /status HTTP/1.0\r\nHost: "));
    client.print(deviceConfig.nasHost);
    client.print(F("\r\nAuthorization: Bearer "));
    client.print(deviceConfig.nasToken);
    client.print(F("\r\nConnection: close\r\n\r\n"));

    String statusLine = client.readStringUntil('\n');
    statusLine.trim();
    if (statusLine.indexOf(" 200 ") < 0)
    {
        Serial.print(F("NAS status HTTP error: "));
        Serial.println(statusLine);
        client.stop();
        return false;
    }

    if (!client.find("\r\n\r\n"))
    {
        Serial.println(F("NAS status response has no header terminator"));
        client.stop();
        return false;
    }

    // The full response includes Docker and disk details. Filtering while
    // streaming keeps ESP8266 heap usage small and parses only display fields.
    StaticJsonDocument<384> filter;
    filter["cpu"]["percent"] = true;
    filter["gpu"]["utilization"] = true;
    filter["memory"]["percent"] = true;
    filter["uptime"] = true;
    filter["temp"][0]["type"] = true;
    filter["temp"][0]["temp"] = true;
    filter["net"]["rx_speed"] = true;
    filter["net"]["tx_speed"] = true;

    DynamicJsonDocument doc(1536);
    DeserializationError error = deserializeJson(
        doc, client, DeserializationOption::Filter(filter));
    client.stop();

    if (error)
    {
        Serial.print(F("NAS status JSON error: "));
        Serial.println(error.f_str());
        return false;
    }

    snapshot.cpuPercent = doc["cpu"]["percent"] | 0.0;
    snapshot.gpuPercent = doc["gpu"]["utilization"] | 0.0;
    snapshot.memoryPercent = doc["memory"]["percent"] | 0.0;
    snapshot.uptimeSeconds = doc["uptime"] | 0U;
    snapshot.rxBytesPerSecond = doc["net"]["rx_speed"] | 0.0;
    snapshot.txBytesPerSecond = doc["net"]["tx_speed"] | 0.0;

    snapshot.temperature = 0;
    double fallbackTemperature = 0;
    for (JsonObject sensor : doc["temp"].as<JsonArray>())
    {
        double value = sensor["temp"] | 0.0;
        if (value > fallbackTemperature)
            fallbackTemperature = value;
        const String sensorType = sensor["type"].as<String>();
        if (sensorType == "cpu" || sensorType == "x86_pkg_temp")
            snapshot.temperature = value;
    }
    if (snapshot.temperature <= 0)
        snapshot.temperature = fallbackTemperature;

    Serial.printf("NAS CPU=%.1f%% GPU=%.1f%% MEM=%.1f%% TEMP=%.1fC UP=%lus RX=%.0fB/s TX=%.0fB/s\r\n",
                  snapshot.cpuPercent, snapshot.gpuPercent, snapshot.memoryPercent,
                  snapshot.temperature, static_cast<unsigned long>(snapshot.uptimeSeconds), snapshot.rxBytesPerSecond,
                  snapshot.txBytesPerSecond);
    return true;
}

#endif
