#pragma once

#include <math.h>
#include <stdio.h>

struct TransferText
{
    char number[8];
    const char *unit;
};

inline TransferText formatTransfer(double bytes, bool perSecond)
{
    static const char *totals[] = {"B", "KB", "MB", "GB", "TB", "PB"};
    static const char *rates[] = {"B/s", "KB/s", "MB/s", "GB/s", "TB/s", "PB/s"};
    TransferText text = {{'-', '-', '\0'}, ""};
    if (!isfinite(bytes) || bytes < 0)
        return text;

    unsigned unit = 0;
    // Live rates stay at two digits so their fixed 42px boxes never change font.
    const double unitLimit = perSecond ? 99.5 : 999.95;
    while (bytes >= unitLimit && unit < 5)
    {
        bytes /= 1000.0;
        ++unit;
    }
    if (bytes >= unitLimit)
        snprintf(text.number, sizeof(text.number), perSecond ? "99" : "999+");
    else if (unit == 0 || (perSecond && bytes >= 9.95))
        snprintf(text.number, sizeof(text.number), "%.0f", bytes);
    else
        snprintf(text.number, sizeof(text.number), "%.1f", bytes);
    text.unit = perSecond ? rates[unit] : totals[unit];
    return text;
}
