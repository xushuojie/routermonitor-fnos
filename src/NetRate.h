#ifndef ROUTER_MONITOR_NET_RATE_H
#define ROUTER_MONITOR_NET_RATE_H

#include <cmath>
#include <cstring>

struct NasNetSample
{
    bool serverRates = false;
    bool gap = false;
    float rxRate = -1, txRate = -1;
    float rxAverage = -1, txAverage = -1;
    double sampleTime = 0;
    double rxBytes = 0;
    double txBytes = 0;
    char iface[65] = {0};
    char counterEpoch[33] = {0};
};

struct NetRate
{
    double rxBytesPerSecond = 0;
    double txBytesPerSecond = 0;
    double elapsedSeconds = 0;
};

inline bool calculateNetRate(const NasNetSample &previous, const NasNetSample &current, NetRate &rate)
{
    const double elapsed = current.sampleTime - previous.sampleTime;
    if (!previous.iface[0] || strcmp(previous.iface, current.iface) != 0 ||
        ((previous.counterEpoch[0] || current.counterEpoch[0]) &&
         strcmp(previous.counterEpoch, current.counterEpoch) != 0) ||
        !isfinite(elapsed) || elapsed <= 0 ||
        !isfinite(previous.rxBytes) || !isfinite(previous.txBytes) ||
        !isfinite(current.rxBytes) || !isfinite(current.txBytes) ||
        current.rxBytes < previous.rxBytes || current.txBytes < previous.txBytes)
        return false;

    rate.elapsedSeconds = elapsed;
    rate.rxBytesPerSecond = (current.rxBytes - previous.rxBytes) / elapsed;
    rate.txBytesPerSecond = (current.txBytes - previous.txBytes) / elapsed;
    return isfinite(rate.rxBytesPerSecond) && isfinite(rate.txBytesPerSecond);
}

#endif
