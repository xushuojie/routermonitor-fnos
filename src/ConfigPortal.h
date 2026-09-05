#ifndef ROUTER_MONITOR_CONFIG_PORTAL_H
#define ROUTER_MONITOR_CONFIG_PORTAL_H

#include <Arduino.h>

struct DeviceConfig
{
    char wifiSsid[33] = {0};
    char wifiPassword[65] = {0};
    char nasHost[65] = {0};
    uint16_t nasPort = 18199;
    char nasToken[129] = {0};
    char adminPassword[33] = {0};
    uint8_t nightStartHour = 23;
    uint8_t nightEndHour = 7;
    uint8_t dayBrightness = 180;
    uint8_t nightBrightness = 235;
};

extern DeviceConfig deviceConfig;

bool loadDeviceConfig();
bool saveDeviceConfig();
void startConfigPortal();
void handleConfigPortal();
void stopConfigPortalAp();
bool isConfigPortalStarted();
bool isConfigPortalApMode();
bool hasNasConfig();
const char *configPortalApSsid();
const char *configPortalApPassword();
const char *configPortalAdminPasswordForSetup();

#endif
