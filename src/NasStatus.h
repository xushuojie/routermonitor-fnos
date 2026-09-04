#ifndef __NAS_STATUS_H
#define __NAS_STATUS_H

#include <ArduinoJson.h>
#include <ESP8266WiFi.h>

#include "ConfigPortal.h"
#include "NetRate.h"

struct NasStatusSnapshot
{
    double cpuPercent = 0;
    double gpuPercent = 0;
    double memoryPercent = 0;
    double cpuTemperature = -1;
    double diskTemperature = -1;
    uint32_t uptimeSeconds = 0;
    double rxBytesPerSecond = -1;
    double txBytesPerSecond = -1;
    double rxBytes24h = -1;
    double txBytes24h = -1;
    uint32_t trafficCoverageSeconds = 0;
    bool trafficHistoryValid = false;
    double diskReadBytesPerSecond = -1;
    double diskWriteBytesPerSecond = -1;
    bool diskIoValid = false;
    double storageTotalBytes = -1;
    double storageUsedBytes = -1;
    double storagePercent = -1;
    bool storageValid = false;
};

bool getNasStatus(NasStatusSnapshot &snapshot)
{
    WiFiClient client;
    client.setTimeout(1000);

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
    StaticJsonDocument<832> filter;
    filter["cpu"]["percent"] = true;
    filter["gpu"]["utilization"] = true;
    filter["memory"]["percent"] = true;
    filter["uptime"] = true;
    filter["temp"][0]["type"] = true;
    filter["temp"][0]["temp"] = true;
    filter["traffic_24h"]["rx_bytes"] = true;
    filter["traffic_24h"]["tx_bytes"] = true;
    filter["traffic_24h"]["coverage_seconds"] = true;
    filter["traffic_24h"]["valid"] = true;
    filter["disk_io"]["read_speed"] = true;
    filter["disk_io"]["write_speed"] = true;
    filter["disk_io"]["valid"] = true;
    filter["storage"]["total"] = true;
    filter["storage"]["used"] = true;
    filter["storage"]["percent"] = true;
    filter["storage"]["valid"] = true;
    if (filter.overflowed())
    {
        client.stop();
        return false;
    }

    DynamicJsonDocument doc(2048);
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
    JsonObject history = doc["traffic_24h"].as<JsonObject>();
    snapshot.rxBytes24h = history["rx_bytes"] | -1.0;
    snapshot.txBytes24h = history["tx_bytes"] | -1.0;
    snapshot.trafficCoverageSeconds = min(history["coverage_seconds"] | 0U, 86400U);
    snapshot.trafficHistoryValid = (history["valid"] | false) &&
        isfinite(snapshot.rxBytes24h) && snapshot.rxBytes24h >= 0 &&
        isfinite(snapshot.txBytes24h) && snapshot.txBytes24h >= 0;

    JsonObject diskIo = doc["disk_io"].as<JsonObject>();
    snapshot.diskReadBytesPerSecond = diskIo["read_speed"] | -1.0;
    snapshot.diskWriteBytesPerSecond = diskIo["write_speed"] | -1.0;
    snapshot.diskIoValid = (diskIo["valid"] | false) &&
        isfinite(snapshot.diskReadBytesPerSecond) && snapshot.diskReadBytesPerSecond >= 0 &&
        isfinite(snapshot.diskWriteBytesPerSecond) && snapshot.diskWriteBytesPerSecond >= 0;

    JsonObject storage = doc["storage"].as<JsonObject>();
    snapshot.storageTotalBytes = storage["total"] | -1.0;
    snapshot.storageUsedBytes = storage["used"] | -1.0;
    snapshot.storagePercent = storage["percent"] | -1.0;
    snapshot.storageValid = (storage["valid"] | false) &&
        isfinite(snapshot.storageTotalBytes) && snapshot.storageTotalBytes >= 0 &&
        isfinite(snapshot.storageUsedBytes) && snapshot.storageUsedBytes >= 0 &&
        isfinite(snapshot.storagePercent) && snapshot.storagePercent >= 0 && snapshot.storagePercent <= 100;

    snapshot.cpuTemperature = -1;
    snapshot.diskTemperature = -1;
    double fallbackTemperature = -1;
    for (JsonObject sensor : doc["temp"].as<JsonArray>())
    {
        double value = sensor["temp"] | 0.0;
        const char *sensorType = sensor["type"] | "";
        if (!strcmp(sensorType, "cpu") || !strcmp(sensorType, "x86_pkg_temp"))
        {
            if (value > snapshot.cpuTemperature)
                snapshot.cpuTemperature = value;
        }
        else if (!strcmp(sensorType, "disk"))
        {
            if (value > snapshot.diskTemperature)
                snapshot.diskTemperature = value;
        }
        else if (value > fallbackTemperature)
        {
            fallbackTemperature = value;
        }
    }
    if (snapshot.cpuTemperature <= 0)
        snapshot.cpuTemperature = fallbackTemperature;

    Serial.printf("NAS CPU=%.1f%% GPU=%.1f%% MEM=%.1f%% TEMP=%.1fC UP=%lus\r\n",
                  snapshot.cpuPercent, snapshot.gpuPercent, snapshot.memoryPercent,
                  snapshot.cpuTemperature, static_cast<unsigned long>(snapshot.uptimeSeconds));
    return true;
}

bool getNasNetSample(NasNetSample &sample)
{
    if (!hasNasConfig() || WiFi.status() != WL_CONNECTED)
        return false;

    WiFiClient client;
    client.setTimeout(500);
    if (!client.connect(deviceConfig.nasHost, deviceConfig.nasPort))
        return false;

    client.print(F("GET /net HTTP/1.0\r\nHost: "));
    client.print(deviceConfig.nasHost);
    client.print(F("\r\nAuthorization: Bearer "));
    client.print(deviceConfig.nasToken);
    client.print(F("\r\nConnection: close\r\n\r\n"));

    char statusLine[48];
    const size_t statusLength = client.readBytesUntil('\n', statusLine, sizeof(statusLine) - 1);
    statusLine[statusLength] = '\0';
    if (strstr(statusLine, " 200 ") == nullptr || !client.find("\r\n\r\n"))
    {
        client.stop();
        return false;
    }

    StaticJsonDocument<256> doc;
    const DeserializationError error = deserializeJson(doc, client);
    client.stop();
    if (error)
        return false;

    const char *iface = doc["iface"] | "";
    const double sampleTime = doc["sample_time"] | -1.0;
    const double rxBytes = doc["rx_bytes"] | -1.0;
    const double txBytes = doc["tx_bytes"] | -1.0;
    if (!iface[0] || strlen(iface) >= sizeof(sample.iface) ||
        !isfinite(sampleTime) || sampleTime <= 0 ||
        !isfinite(rxBytes) || rxBytes < 0 || !isfinite(txBytes) || txBytes < 0)
        return false;

    sample.sampleTime = sampleTime;
    sample.rxBytes = rxBytes;
    sample.txBytes = txBytes;
    strlcpy(sample.iface, iface, sizeof(sample.iface));
    return true;
}

#endif
