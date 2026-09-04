#include <lvgl.h>
#include <TFT_eSPI.h>
#include <ESP8266WiFi.h>
#include <time.h>

#include "ConfigPortal.h"
#include "NasStatus.h"
#include "TrafficFormat.h"

// extern lv_font_t my_font_name;
LV_FONT_DECLARE(lv_font_montserrat_12)
LV_FONT_DECLARE(lv_font_montserrat_22)
LV_FONT_DECLARE(lv_font_montserrat_42)

TFT_eSPI tft = TFT_eSPI(); /* TFT instance */
static lv_disp_buf_t disp_buf;
static lv_color_t buf[LV_HOR_RES_MAX * 5];

// 定义页面
static lv_obj_t *login_page = NULL;
static lv_obj_t *monitor_page = NULL;

static lv_obj_t *upload_label;
static lv_obj_t *download_label;
static lv_obj_t *up_speed_label;
static lv_obj_t *up_speed_unit_label;
static lv_obj_t *down_speed_label;
static lv_obj_t *down_speed_unit_label;
static lv_obj_t *cpu_bar;
static lv_obj_t *cpu_value_label;
static lv_obj_t *gpu_bar;
static lv_obj_t *gpu_value_label;
static lv_obj_t *mem_bar;
static lv_obj_t *mem_value_label;
static lv_obj_t *time_label;
static lv_obj_t *weekday_label;
static lv_obj_t *date_label;
static lv_obj_t *chart;
static lv_obj_t *disk_read_label;
static lv_obj_t *disk_read_value;
static lv_obj_t *disk_read_unit;
static lv_obj_t *disk_write_label;
static lv_obj_t *disk_write_value;
static lv_obj_t *disk_write_unit;
static lv_obj_t *carousel_title[3];
static lv_obj_t *carousel_value[3];
static uint8_t carouselPage = 0;
static bool nasOnline = false;

static lv_chart_series_t *ser1;
static lv_chart_series_t *ser2;

NasStatusSnapshot nasStatus;

static const uint16_t NET_SAMPLE_INTERVAL_MS = 200; // Physical-device tuning knob: 300ms is the fallback if telemetry shows misses.
static const uint8_t CHART_POINT_COUNT = 10000 / NET_SAMPLE_INTERVAL_MS;
static uint32_t uploadSeries[CHART_POINT_COUNT] = {0};
static uint32_t downloadSeries[CHART_POINT_COUNT] = {0};

// 监测数值
double cpu_usage = 0;
double gpu_usage = 0;
double mem_usage = 0;
uint32_t nas_uptime_seconds = 0;

#if LV_USE_LOG != 0
/* Serial debugging */
void my_print(lv_log_level_t level, const char *file, uint32_t line, const char *dsc, const char *params)
{

    Serial.printf("%s@%d->%s [%s]\r\n", file, line, dsc, params);
    Serial.flush();
}
#endif

// 屏幕亮度设置，value [0, 256] 越小月亮,越大越暗
void setBrightness(int value) {
    pinMode(TFT_BL, INPUT);
    analogWrite(TFT_BL, value);
    pinMode(TFT_BL, OUTPUT);
}

// 页面初始化
void setupPages()
{
    setBrightness(deviceConfig.dayBrightness);
    login_page = lv_cont_create(lv_scr_act(), NULL);
    lv_obj_set_size(login_page, 240, 240); // 设置容器大小
    lv_obj_set_style_local_bg_color(login_page, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_BLACK);
    lv_obj_set_style_local_border_color(login_page, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_BLACK);
    lv_obj_set_style_local_radius(login_page, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, 0);

    monitor_page = lv_cont_create(lv_scr_act(), NULL);
    lv_obj_set_size(monitor_page, 240, 240);

    lv_obj_set_hidden(login_page, false);
    lv_obj_set_hidden(monitor_page, true);
}

// 设置login_page显示组件
void initLoginPage()
{
    lv_obj_t *preload = lv_spinner_create(login_page, NULL);
    lv_obj_set_size(preload, 100, 100);
    lv_obj_align(preload, NULL, LV_ALIGN_CENTER, 0, 0);
}

// 连接WiFi
void connectWiFi()
{
    static bool attempted = false;
    if (attempted)
        return;
    attempted = true;

    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    if (deviceConfig.wifiSsid[0])
        WiFi.begin(deviceConfig.wifiSsid, deviceConfig.wifiPassword);
    else
        WiFi.begin(); // 使用 ESP8266 SDK 已保存的凭据迁移现有设备。
    Serial.println(F("Connecting to Wi-Fi ..."));

    const unsigned long startedAt = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startedAt < 15000)
        delay(250);

    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println(F("Wi-Fi unavailable; starting setup portal"));
        startConfigPortal();
        return;
    }
    // Wi-Fi 验证成功：显示主界面，并彻底释放开机页、转圈对象和动画。
    lv_obj_set_hidden(monitor_page, false);
    if (login_page != NULL)
    {
        lv_obj_del(login_page);
        login_page = NULL;
    }

    Serial.println("");                                // WiFi连接成功后
    Serial.println("Connection established!");         // NodeMCU将通过串口监视器输出"连接成功"信息。
    Serial.print("IP address:    ");                   // 同时还将输出NodeMCU的IP地址。这一功能是通过调用
    Serial.println(WiFi.localIP().toString().c_str()); // WiFi.localIP()函数来实现的。该函数的返回值即NodeMCU的IP地址。

    startConfigPortal();

    // NTP uses UTC+8 and a 24-hour clock.
    configTime(8 * 3600, 0, "ntp.aliyun.com", "ntp1.aliyun.com", "pool.ntp.org");
}

/* Display flushing */
void my_disp_flush(lv_disp_drv_t *disp, const lv_area_t *area, lv_color_t *color_p)
{
    uint32_t w = (area->x2 - area->x1 + 1);
    uint32_t h = (area->y2 - area->y1 + 1);

    tft.startWrite();
    tft.setAddrWindow(area->x1, area->y1, w, h);
    tft.pushColors(&color_p->full, w * h, true);
    tft.endWrite();

    lv_disp_flush_ready(disp);
}

/* Reading input device (simulated encoder here) */
bool read_encoder(lv_indev_drv_t *indev, lv_indev_data_t *data)
{
    static int32_t last_diff = 0;
    int32_t diff = 0;                   /* Dummy - no movement */
    int btn_state = LV_INDEV_STATE_REL; /* Dummy - no press */

    data->enc_diff = diff - last_diff;
    data->state = btn_state;

    last_diff = diff;

    return false;
}

bool refreshMonitorData()
{
    if (getNasStatus(nasStatus))
    {
        cpu_usage = nasStatus.cpuPercent;
        gpu_usage = nasStatus.gpuPercent;
        mem_usage = nasStatus.memoryPercent;
        nas_uptime_seconds = nasStatus.uptimeSeconds;
        return true;
    }
    return false;
}

static lv_coord_t textWidth(const char *text, const lv_font_t *font)
{
    return _lv_txt_get_width(text, strlen(text), font, 0, LV_TXT_FLAG_NONE);
}

// Each direction owns 112px. Large network values fall back to 22px.
static void updateTransferValue(lv_obj_t *icon, lv_obj_t *value, lv_obj_t *unit,
                                double bytes, bool rate, lv_coord_t left, lv_coord_t baseline,
                                const lv_font_t *preferredFont)
{
    const TransferText text = formatTransfer(bytes, rate);
    const lv_font_t *font = preferredFont;
    const lv_coord_t iconWidth = textWidth(lv_label_get_text(icon), &lv_font_montserrat_12);
    const lv_coord_t unitWidth = textWidth(text.unit, &lv_font_montserrat_12);
    if (iconWidth + textWidth(text.number, font) + unitWidth + 2 > 112)
        font = &lv_font_montserrat_22;
    const lv_coord_t valueWidth = textWidth(text.number, font);
    const lv_coord_t x = left + (112 - iconWidth - valueWidth - unitWidth - 2) / 2;
    lv_obj_set_style_local_text_font(value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, font);
    lv_label_set_text(value, text.number);
    lv_label_set_text(unit, text.unit);
    lv_obj_set_pos(icon, x, baseline - (lv_font_montserrat_12.line_height - lv_font_montserrat_12.base_line));
    lv_obj_set_pos(value, x + iconWidth + 1, baseline - (font->line_height - font->base_line));
    lv_obj_set_pos(unit, x + iconWidth + valueWidth + 2,
                   baseline - (lv_font_montserrat_12.line_height - lv_font_montserrat_12.base_line));
}

void updateNetworkInfoLabel()
{
    updateTransferValue(upload_label, up_speed_label, up_speed_unit_label,
                        nasStatus.txBytesPerSecond, true, 5, 43, &lv_font_montserrat_42);
    updateTransferValue(download_label, down_speed_label, down_speed_unit_label,
                        nasStatus.rxBytesPerSecond, true, 123, 43, &lv_font_montserrat_42);
}

static uint32_t chartRate(double bytesPerSecond)
{
    if (!isfinite(bytesPerSecond) || bytesPerSecond <= 0)
        return 0;
    return bytesPerSecond >= UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(bytesPerSecond);
}

static void appendChartSample(double rxBytesPerSecond, double txBytesPerSecond)
{
    memmove(downloadSeries, downloadSeries + 1, sizeof(downloadSeries) - sizeof(downloadSeries[0]));
    memmove(uploadSeries, uploadSeries + 1, sizeof(uploadSeries) - sizeof(uploadSeries[0]));
    downloadSeries[CHART_POINT_COUNT - 1] = chartRate(rxBytesPerSecond);
    uploadSeries[CHART_POINT_COUNT - 1] = chartRate(txBytesPerSecond);

    uint32_t maximum = 1;
    for (uint8_t index = 0; index < CHART_POINT_COUNT; ++index)
        maximum = max(maximum, max(downloadSeries[index], uploadSeries[index]));
    const uint32_t scale = maximum / 1000 + (maximum % 1000 != 0);

    lv_coord_t points[CHART_POINT_COUNT];
    for (uint8_t index = 0; index < CHART_POINT_COUNT; ++index)
        points[index] = static_cast<lv_coord_t>(uploadSeries[index] / scale);
    lv_chart_set_points(chart, ser1, points);
    for (uint8_t index = 0; index < CHART_POINT_COUNT; ++index)
        points[index] = static_cast<lv_coord_t>(downloadSeries[index] / scale);
    lv_chart_set_points(chart, ser2, points);
}

static void net_task_cb(lv_task_t *task)
{
    static NasNetSample previous;
    static NasNetSample speedBaseline;
    static bool havePrevious = false;
    static bool rateVisible = false;
    static uint32_t lastSuccessAt = 0;
    static uint32_t reportAt = 0;
    static uint32_t latencyTotal = 0;
    static uint16_t requests = 0;
    static uint16_t successes = 0;
    static uint16_t failures = 0;
    static uint16_t late = 0;
    static uint16_t plotted = 0;
    static uint16_t maximumLatency = 0;

    const uint32_t startedAt = millis();
    NasNetSample current;
    const bool ok = getNasNetSample(current);
    const uint16_t latency = static_cast<uint16_t>(min(millis() - startedAt, 65535UL));
    ++requests;
    latencyTotal += latency;
    maximumLatency = max(maximumLatency, latency);
    if (latency > NET_SAMPLE_INTERVAL_MS)
        ++late;

    if (!ok)
    {
        lv_task_set_period(task, 1000);
        ++failures;
        if (rateVisible && millis() - lastSuccessAt > 2000)
        {
            rateVisible = false;
            havePrevious = false;
            nasStatus.rxBytesPerSecond = -1;
            nasStatus.txBytesPerSecond = -1;
            updateNetworkInfoLabel();
        }
    }
    else
    {
        lv_task_set_period(task, NET_SAMPLE_INTERVAL_MS);
        ++successes;
        lastSuccessAt = millis();
        if (!havePrevious)
        {
            previous = current;
            speedBaseline = current;
            havePrevious = true;
        }
        else
        {
            NetRate adjacent;
            if (!calculateNetRate(previous, current, adjacent))
            {
                speedBaseline = current;
            }
            else
            {
                if (adjacent.elapsedSeconds <= 1.0)
                {
                    appendChartSample(adjacent.rxBytesPerSecond, adjacent.txBytesPerSecond);
                    ++plotted;
                }

                NetRate average;
                if (!calculateNetRate(speedBaseline, current, average))
                    speedBaseline = current;
                else if (average.elapsedSeconds >= 1.0)
                {
                    if (average.elapsedSeconds <= 2.0)
                    {
                        nasStatus.rxBytesPerSecond = average.rxBytesPerSecond;
                        nasStatus.txBytesPerSecond = average.txBytesPerSecond;
                        rateVisible = true;
                        updateNetworkInfoLabel();
                    }
                    speedBaseline = current;
                }
            }
            previous = current;
        }
    }

    if (millis() - reportAt >= 10000)
    {
        Serial.printf("NET %ums req=%u ok=%u fail=%u plot=%u avg=%lums max=%ums late=%u heap=%u\r\n",
                      NET_SAMPLE_INTERVAL_MS, requests, successes, failures, plotted,
                      requests ? static_cast<unsigned long>(latencyTotal / requests) : 0,
                      maximumLatency, late, ESP.getFreeHeap());
        reportAt = millis();
        latencyTotal = requests = successes = failures = late = plotted = maximumLatency = 0;
    }
}

static lv_coord_t carouselColumnWidth(uint8_t count)
{
    return count == 1 ? 230 : (count == 2 ? 112 : 72);
}

static lv_coord_t carouselColumnLeft(uint8_t count, uint8_t index)
{
    if (count == 1)
        return 5;
    if (count == 2)
        return index == 0 ? 5 : 123;
    return index == 0 ? 5 : (index == 1 ? 84 : 163);
}

static void layoutCarousel(uint8_t count)
{
    const lv_coord_t width = carouselColumnWidth(count);
    for (uint8_t index = 0; index < 3; ++index)
    {
        const bool hidden = index >= count;
        lv_obj_set_hidden(carousel_title[index], hidden);
        lv_obj_set_hidden(carousel_value[index], hidden);
        if (hidden)
            continue;
        const lv_coord_t left = carouselColumnLeft(count, index);
        lv_obj_set_size(carousel_title[index], width, 15);
        lv_obj_set_pos(carousel_title[index], left, 95);
        lv_obj_set_size(carousel_value[index], width, 24);
        lv_obj_set_pos(carousel_value[index], left, 110);
    }
}

static void setCarouselText(uint8_t index, const char *title, const char *value, lv_color_t color,
                            lv_coord_t width)
{
    const lv_font_t *font = textWidth(value, &lv_font_montserrat_22) <= width
                                ? &lv_font_montserrat_22
                                : &lv_font_montserrat_12;
    lv_label_set_text(carousel_title[index], title);
    lv_label_set_text(carousel_value[index], value);
    lv_obj_set_style_local_text_color(carousel_title[index], LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, color);
    lv_obj_set_style_local_text_color(carousel_value[index], LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, color);
    lv_obj_set_style_local_text_font(carousel_value[index], LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, font);
}

static void formatBytes(char *buffer, size_t size, double bytes)
{
    const TransferText text = formatTransfer(bytes, false);
    snprintf(buffer, size, "%s%s", text.number, text.unit);
}

static void renderCarousel()
{
    static const lv_color_t red = LV_COLOR_RED;
    static const lv_color_t blue = lv_color_hex(0x278fbd);
    static const lv_color_t green = lv_color_hex(0x20c864);
    static const lv_color_t amber = lv_color_hex(0xedbd76);
    char first[20] = "--";
    char second[20] = "--";
    char third[20] = "--";
    char firstTitle[20] = "UP 24H";
    char secondTitle[20] = "DOWN 24H";

    if (carouselPage == 0)
    {
        layoutCarousel(2);
        if (nasOnline && nasStatus.trafficHistoryValid)
        {
            formatBytes(first, sizeof(first), nasStatus.txBytes24h);
            formatBytes(second, sizeof(second), nasStatus.rxBytes24h);
            if (nasStatus.trafficCoverageSeconds < 3600)
            {
                snprintf(firstTitle, sizeof(firstTitle), "UP %luM", static_cast<unsigned long>(nasStatus.trafficCoverageSeconds / 60));
                snprintf(secondTitle, sizeof(secondTitle), "DOWN %luM", static_cast<unsigned long>(nasStatus.trafficCoverageSeconds / 60));
            }
            else if (nasStatus.trafficCoverageSeconds < 86400)
            {
                snprintf(firstTitle, sizeof(firstTitle), "UP %luH", static_cast<unsigned long>(nasStatus.trafficCoverageSeconds / 3600));
                snprintf(secondTitle, sizeof(secondTitle), "DOWN %luH", static_cast<unsigned long>(nasStatus.trafficCoverageSeconds / 3600));
            }
        }
        setCarouselText(0, firstTitle, first, red, 112);
        setCarouselText(1, secondTitle, second, blue, 112);
    }
    else if (carouselPage == 1)
    {
        layoutCarousel(1);
        if (nasOnline)
        {
            if (nas_uptime_seconds >= 86400UL)
                snprintf(first, sizeof(first), "%lu D", static_cast<unsigned long>(nas_uptime_seconds / 86400UL));
            else if (nas_uptime_seconds >= 3600UL)
                snprintf(first, sizeof(first), "%lu H", static_cast<unsigned long>(nas_uptime_seconds / 3600UL));
            else
                snprintf(first, sizeof(first), "%lu M", static_cast<unsigned long>(nas_uptime_seconds / 60UL));
        }
        setCarouselText(0, "UPTIME", first, LV_COLOR_WHITE, 230);
    }
    else if (carouselPage == 2)
    {
        layoutCarousel(2);
        if (nasOnline && nasStatus.cpuTemperature > 0)
            snprintf(first, sizeof(first), "%.0f°C", nasStatus.cpuTemperature);
        if (nasOnline && nasStatus.diskTemperature > 0)
            snprintf(second, sizeof(second), "%.0f°C", nasStatus.diskTemperature);
        setCarouselText(0, "CPU TEMP", first, green, 112);
        setCarouselText(1, "DISK MAX", second, amber, 112);
    }
    else
    {
        layoutCarousel(3);
        if (nasOnline && nasStatus.storageValid)
        {
            formatBytes(first, sizeof(first), nasStatus.storageTotalBytes);
            formatBytes(second, sizeof(second), nasStatus.storageUsedBytes);
            snprintf(third, sizeof(third), "%.0f%%", nasStatus.storagePercent);
        }
        setCarouselText(0, "TOTAL", first, LV_COLOR_WHITE, 72);
        setCarouselText(1, "USED", second, amber, 72);
        setCarouselText(2, "USE", third, green, 72);
    }
}

static void updateDiskIo(bool online)
{
    const bool valid = online && nasStatus.diskIoValid;
    updateTransferValue(disk_read_label, disk_read_value, disk_read_unit,
                        valid ? nasStatus.diskReadBytesPerSecond : -1, true, 5, 91, &lv_font_montserrat_22);
    updateTransferValue(disk_write_label, disk_write_value, disk_write_unit,
                        valid ? nasStatus.diskWriteBytesPerSecond : -1, true, 123, 91, &lv_font_montserrat_22);
}

static void carousel_task_cb(lv_task_t *task)
{
    carouselPage = (carouselPage + 1) % 4;
    renderCarousel();
}

void styleMetricBar(lv_obj_t *bar, lv_color_t indicatorColor, lv_color_t trackColor)
{
    lv_obj_set_size(bar, 72, 3);
    lv_obj_set_style_local_bg_color(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, trackColor);
    lv_obj_set_style_local_bg_color(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, indicatorColor);
    lv_obj_set_style_local_border_width(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_border_width(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_radius(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, 5);
    lv_obj_set_style_local_radius(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, 5);
}

static void createMetric(const char *titleText, lv_coord_t left, lv_color_t color,
                         lv_obj_t *&value, lv_obj_t *&bar)
{
    lv_obj_t *title = lv_label_create(monitor_page, NULL);
    lv_label_set_text(title, titleText);
    lv_obj_set_style_local_text_font(title, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(title, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_align(title, LV_LABEL_ALIGN_CENTER);
    lv_obj_set_size(title, 72, 15);
    lv_obj_set_pos(title, left, 183);

    value = lv_label_create(monitor_page, NULL);
    lv_label_set_text(value, "0%");
    lv_obj_set_style_local_text_font(value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_22);
    lv_obj_set_style_local_text_color(value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_align(value, LV_LABEL_ALIGN_CENTER);
    lv_label_set_long_mode(value, LV_LABEL_LONG_CROP);
    lv_obj_set_size(value, 72, 24);
    lv_obj_set_pos(value, left, 199);

    bar = lv_bar_create(monitor_page, NULL);
    styleMetricBar(bar, color, lv_color_hex(0x1e3644));
    lv_obj_set_pos(bar, left, 228);
}

static void updateNightMode(uint8_t hour)
{
    const uint8_t start = deviceConfig.nightStartHour;
    const uint8_t end = deviceConfig.nightEndHour;
    const bool isNight = start == end ? false : (start < end ? hour >= start && hour < end : hour >= start || hour < end);
    static int8_t lastMode = -1;
    if (lastMode != static_cast<int8_t>(isNight))
    {
        lastMode = isNight;
        setBrightness(isNight ? deviceConfig.nightBrightness : deviceConfig.dayBrightness);
    }
}

static void clock_task_cb(lv_task_t *task)
{
    time_t now = time(nullptr);
    if (now < 1609459200)
    {
        lv_label_set_text(weekday_label, "---");
        lv_label_set_text(date_label, "-- --");
        lv_label_set_text(time_label, "--:--:--");
        return;
    }

    struct tm timeInfo;
    localtime_r(&now, &timeInfo);
    updateNightMode(timeInfo.tm_hour);
    static const char *weekdays[] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
    char timeText[9];
    char dateText[16];
    snprintf(timeText, sizeof(timeText), "%02d:%02d:%02d", timeInfo.tm_hour, timeInfo.tm_min, timeInfo.tm_sec);
    snprintf(dateText, sizeof(dateText), "%02d-%02d", timeInfo.tm_mon + 1, timeInfo.tm_mday);
    lv_label_set_text(weekday_label, weekdays[timeInfo.tm_wday]);
    lv_label_set_text(date_label, dateText);
    lv_label_set_text(time_label, timeText);

    static int lastLoggedMinute = -1;
    if (timeInfo.tm_min != lastLoggedMinute)
    {
        lastLoggedMinute = timeInfo.tm_min;
        Serial.print(F("Time: "));
        Serial.printf("%02d:%02d\r\n", timeInfo.tm_hour, timeInfo.tm_min);
    }
}

// task循环执行的函数
static void task_cb(lv_task_t *task)
{
    static uint16_t wifiFailures = 0;
    static uint16_t nasFailures = 0;
    static uint8_t lowHeapSamples = 0;

    if (WiFi.status() != WL_CONNECTED)
    {
        connectWiFi();
        if (WiFi.status() != WL_CONNECTED)
        {
            nasOnline = false;
            updateDiskIo(false);
            renderCarousel();
            if (!isConfigPortalApMode())
            {
                ++wifiFailures;
                if (wifiFailures % 5 == 1)
                    WiFi.reconnect();
                if (wifiFailures >= 45)
                    ESP.restart();
            }
            return;
        }
    }
    wifiFailures = 0;

    const bool nasOk = refreshMonitorData();
    if (nasOk)
        nasFailures = 0;
    else
    {
        nasOnline = false;
        updateDiskIo(false);
        renderCarousel();
        ++nasFailures;
        if (nasFailures == 30)
            WiFi.reconnect();
        if (nasFailures >= 300)
            ESP.restart();
        return;
    }
    nasOnline = true;
    updateDiskIo(true);
    renderCarousel();

    lv_bar_set_value(cpu_bar, cpu_usage, LV_ANIM_OFF);
    lv_label_set_text_fmt(cpu_value_label, "%.0f%%", cpu_usage);

    lv_bar_set_value(gpu_bar, gpu_usage, LV_ANIM_OFF);
    lv_label_set_text_fmt(gpu_value_label, "%.0f%%", gpu_usage);

    lv_bar_set_value(mem_bar, mem_usage, LV_ANIM_OFF);
    lv_label_set_text_fmt(mem_value_label, "%.0f%%", mem_usage);

    if (ESP.getFreeHeap() < 7000)
        ++lowHeapSamples;
    else
        lowHeapSamples = 0;
    if (lowHeapSamples >= 10)
        ESP.restart();

    // Runtime heap diagnostics for physical-device tuning.
    Serial.print("⚠ Left Memory:");
    Serial.println(ESP.getFreeHeap());
    Serial.printf("24H RX=%.0f TX=%.0f COVER=%lus VALID=%d\r\n", nasStatus.rxBytes24h,
                  nasStatus.txBytes24h, static_cast<unsigned long>(nasStatus.trafficCoverageSeconds), nasStatus.trafficHistoryValid);
}

void setup()
{
    Serial.begin(115200); /* prepare for possible serial debug */
    srand((unsigned)time(NULL));
    loadDeviceConfig();

    lv_init();

#if LV_USE_LOG != 0
    lv_log_register_print_cb(my_print); /* register print function for debugging */
#endif

    tft.begin();        /* TFT init */
    tft.setRotation(0); /* Landscape orientation */
    tft.fillScreen(TFT_BLACK);

    lv_disp_buf_init(&disp_buf, buf, NULL, LV_HOR_RES_MAX * 5);

    /*Initialize the display*/
    lv_disp_drv_t disp_drv;
    lv_disp_drv_init(&disp_drv);
    disp_drv.hor_res = 240;
    disp_drv.ver_res = 240;
    disp_drv.flush_cb = my_disp_flush;
    disp_drv.buffer = &disp_buf;
    lv_disp_drv_register(&disp_drv);

    /*Initialize the (dummy) input device driver*/
    lv_indev_drv_t indev_drv;
    lv_indev_drv_init(&indev_drv);
    indev_drv.type = LV_INDEV_TYPE_ENCODER;
    indev_drv.read_cb = read_encoder;
    lv_indev_drv_register(&indev_drv);

    setupPages();
    initLoginPage();

    lv_obj_t *outer = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(outer, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(outer, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
    lv_obj_set_style_local_bg_color(outer, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_BLACK);
    lv_obj_set_size(outer, 240, 240);

    const lv_color_t contentColor = lv_color_hex(0x081418);
    const lv_color_t mutedColor = lv_color_hex(0x838a99);
    const lv_color_t blue = lv_color_hex(0x278fbd);
    const lv_color_t green = lv_color_hex(0x20c864);
    const lv_color_t amber = lv_color_hex(0xedbd76);

    lv_obj_t *content = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(content, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(content, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
    lv_obj_set_style_local_bg_color(content, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, contentColor);
    lv_obj_set_size(content, 230, 230);
    lv_obj_set_pos(content, 5, 5);

    upload_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(upload_label, LV_SYMBOL_UPLOAD);
    lv_obj_set_style_local_text_font(upload_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(upload_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_RED);
    up_speed_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(up_speed_label, "--");
    lv_obj_set_style_local_text_font(up_speed_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_42);
    lv_obj_set_style_local_text_color(up_speed_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    up_speed_unit_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(up_speed_unit_label, "");
    lv_obj_set_style_local_text_font(up_speed_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(up_speed_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);

    download_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(download_label, LV_SYMBOL_DOWNLOAD);
    lv_obj_set_style_local_text_font(download_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(download_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, blue);
    down_speed_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(down_speed_label, "--");
    lv_obj_set_style_local_text_font(down_speed_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_42);
    lv_obj_set_style_local_text_color(down_speed_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    down_speed_unit_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(down_speed_unit_label, "");
    lv_obj_set_style_local_text_font(down_speed_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(down_speed_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);
    updateNetworkInfoLabel();

    chart = lv_chart_create(monitor_page, NULL);
    lv_obj_set_size(chart, 220, 20);
    lv_obj_set_pos(chart, 10, 51);
    lv_obj_set_style_local_pad_left(chart, LV_CHART_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_pad_right(chart, LV_CHART_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_pad_top(chart, LV_CHART_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_pad_bottom(chart, LV_CHART_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_bg_color(chart, LV_CHART_PART_BG, LV_STATE_DEFAULT, contentColor);
    lv_obj_set_style_local_border_width(chart, LV_CHART_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_radius(chart, LV_CHART_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_size(chart, LV_CHART_PART_SERIES, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_line_width(chart, LV_CHART_PART_SERIES, LV_STATE_DEFAULT, 2);
    lv_chart_set_div_line_count(chart, 0, 0);
    lv_chart_set_type(chart, LV_CHART_TYPE_LINE);
    lv_chart_set_range(chart, 0, 1000);
    lv_chart_set_point_count(chart, CHART_POINT_COUNT);
    ser1 = lv_chart_add_series(chart, LV_COLOR_RED);
    ser2 = lv_chart_add_series(chart, blue);
    lv_chart_init_points(chart, ser1, 0);
    lv_chart_init_points(chart, ser2, 0);

    disk_read_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_read_label, "R");
    lv_obj_set_style_local_text_font(disk_read_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(disk_read_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, green);
    disk_read_value = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_read_value, "--");
    lv_obj_set_style_local_text_font(disk_read_value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_22);
    lv_obj_set_style_local_text_color(disk_read_value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    disk_read_unit = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_read_unit, "");
    lv_obj_set_style_local_text_font(disk_read_unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(disk_read_unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);

    disk_write_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_write_label, "W");
    lv_obj_set_style_local_text_font(disk_write_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(disk_write_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, amber);
    disk_write_value = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_write_value, "--");
    lv_obj_set_style_local_text_font(disk_write_value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_22);
    lv_obj_set_style_local_text_color(disk_write_value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    disk_write_unit = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_write_unit, "");
    lv_obj_set_style_local_text_font(disk_write_unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(disk_write_unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);
    updateDiskIo(false);

    for (uint8_t index = 0; index < 3; ++index)
    {
        carousel_title[index] = lv_label_create(monitor_page, NULL);
        lv_obj_set_style_local_text_font(carousel_title[index], LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
        lv_label_set_align(carousel_title[index], LV_LABEL_ALIGN_CENTER);
        lv_label_set_long_mode(carousel_title[index], LV_LABEL_LONG_CROP);
        carousel_value[index] = lv_label_create(monitor_page, NULL);
        lv_obj_set_style_local_text_font(carousel_value[index], LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_22);
        lv_label_set_align(carousel_value[index], LV_LABEL_ALIGN_CENTER);
        lv_label_set_long_mode(carousel_value[index], LV_LABEL_LONG_CROP);
    }
    renderCarousel();

    weekday_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(weekday_label, "---");
    lv_obj_set_style_local_text_font(weekday_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(weekday_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);
    lv_label_set_align(weekday_label, LV_LABEL_ALIGN_CENTER);
    lv_obj_set_size(weekday_label, 42, 15);
    lv_obj_set_pos(weekday_label, 5, 141);

    date_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(date_label, "-- --");
    lv_obj_set_style_local_text_font(date_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(date_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_align(date_label, LV_LABEL_ALIGN_CENTER);
    lv_obj_set_size(date_label, 42, 15);
    lv_obj_set_pos(date_label, 5, 158);

    time_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(time_label, "--:--:--");
    lv_obj_set_style_local_text_font(time_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_42);
    lv_obj_set_style_local_text_letter_space(time_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, -2);
    lv_obj_set_style_local_text_color(time_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_align(time_label, LV_LABEL_ALIGN_CENTER);
    lv_label_set_long_mode(time_label, LV_LABEL_LONG_CROP);
    lv_obj_set_size(time_label, 182, 46);
    lv_obj_set_pos(time_label, 53, 135);

    createMetric("CPU", 5, lv_color_hex(0xd74747), cpu_value_label, cpu_bar);
    createMetric("GPU", 84, blue, gpu_value_label, gpu_bar);
    createMetric("MEM", 163, green, mem_value_label, mem_bar);

    lv_task_create(task_cb, 1000, LV_TASK_PRIO_MID, NULL);
    lv_task_create(net_task_cb, NET_SAMPLE_INTERVAL_MS, LV_TASK_PRIO_HIGH, NULL);
    lv_task_create(clock_task_cb, 1000, LV_TASK_PRIO_MID, NULL);
    lv_task_create(carousel_task_cb, 5000, LV_TASK_PRIO_LOW, NULL);
}

void loop()
{
    handleConfigPortal();
    lv_task_handler(); /* let the GUI do its work */
}
