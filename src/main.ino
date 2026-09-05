#include <lvgl.h>
#include <TFT_eSPI.h>
#include <ESP8266WiFi.h>
#include <time.h>

#include "ConfigPortal.h"
#include "NasStatus.h"
#include "TrafficFormat.h"

// extern lv_font_t my_font_name;
LV_FONT_DECLARE(lv_font_montserrat_12)
LV_FONT_DECLARE(monitor_value_22)
LV_FONT_DECLARE(monitor_clock_42)
LV_FONT_DECLARE(monitor_rate_42)

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
static lv_obj_t *carousel_viewport;
static lv_obj_t *carousel_layer;
static lv_obj_t *carousel_title[3];
static lv_obj_t *carousel_value[3];
static lv_obj_t *carousel_unit[2];
static lv_obj_t *carousel_arrow[2];
static lv_obj_t *carousel_dots[4];
static lv_point_t net_arrow_points[2][5] = {
    {{5, 13}, {5, 1}, {1, 5}, {5, 1}, {9, 5}},
    {{5, 1}, {5, 13}, {1, 9}, {5, 13}, {9, 9}},
};
static lv_point_t disk_read_points[9] = {
    {1, 15}, {1, 1}, {9, 1}, {13, 4}, {13, 7}, {9, 9}, {1, 9}, {9, 9}, {14, 15},
};
static lv_point_t disk_write_points[5] = {
    {1, 1}, {4, 15}, {8, 7}, {12, 15}, {15, 1},
};
static lv_point_t carousel_arrow_points[2][5] = {
    {{8, 15}, {8, 1}, {2, 7}, {8, 1}, {14, 7}},
    {{8, 1}, {8, 15}, {2, 9}, {8, 15}, {14, 9}},
};
static uint8_t carouselPage = 0;
static bool carouselAnimating = false;
static bool nasOnline = false;
static bool animationFrameStarted = false;
static uint32_t animationFrames = 0;
static uint32_t animationMaxGapMs = 0;
static uint32_t animationSlowFrames = 0;

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
    lv_obj_clean_style_list(monitor_page, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(monitor_page, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
    lv_obj_set_style_local_bg_color(monitor_page, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_BLACK);
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

// Connection transitions run independently of HTTP and never wait for Wi-Fi.
static void serviceWiFi()
{
    static bool started = false;
    static bool connected = false;
    static uint32_t startedAt = 0;
    static uint32_t retryAt = 0;
    static lv_obj_t *setupLabel = NULL;
    if (!started)
    {
        started = true;
        startedAt = millis();
        WiFi.persistent(false);
        WiFi.mode(WIFI_STA);
        WiFi.setSleepMode(WIFI_NONE_SLEEP);
        WiFi.setAutoReconnect(true);
#ifdef MONITOR_WIFI_RECOVERY_TEST
        WiFi.begin("RouterMonitor-recovery-test-unavailable");
#else
        if (deviceConfig.wifiSsid[0])
            WiFi.begin(deviceConfig.wifiSsid, deviceConfig.wifiPassword);
        else
            WiFi.begin(); // Migrate credentials already held by the SDK.
#endif
        Serial.println(F("Connecting to Wi-Fi ..."));
    }
#ifdef MONITOR_WIFI_RECOVERY_TEST
    static bool released = false;
    if (!released && millis() - startedAt >= 20000)
    {
        released = true;
        WiFi.begin(deviceConfig.wifiSsid, deviceConfig.wifiPassword);
        Serial.println(F("TEST Wi-Fi available after 20 seconds"));
    }
#endif
    const bool online = WiFi.status() == WL_CONNECTED;
    if (online && !connected)
    {
        stopConfigPortalAp();
        lv_obj_set_hidden(monitor_page, false);
        if (login_page)
        {
            lv_obj_del(login_page);
            login_page = NULL;
            setupLabel = NULL;
        }
        startConfigPortal();
        configTime(8 * 3600, 0, "ntp.aliyun.com", "ntp1.aliyun.com", "pool.ntp.org");
        Serial.print(F("Connection established! IP="));
        Serial.println(WiFi.localIP());
    }
    if (!online && millis() - startedAt >= 15000 && !connected)
    {
        startConfigPortal();
        if (isConfigPortalApMode() && !login_page)
        {
            login_page = lv_obj_create(lv_scr_act(), NULL);
            lv_obj_clean_style_list(login_page, LV_OBJ_PART_MAIN);
            lv_obj_set_style_local_bg_opa(login_page, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
            lv_obj_set_style_local_bg_color(login_page, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_BLACK);
            lv_obj_set_size(login_page, 240, 240);
            lv_obj_set_hidden(monitor_page, true);
        }
        if (login_page && !setupLabel)
        {
            lv_obj_clean(login_page);
            setupLabel = lv_label_create(login_page, NULL);
            lv_label_set_long_mode(setupLabel, LV_LABEL_LONG_BREAK);
            lv_obj_set_style_local_text_font(setupLabel, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
            lv_obj_set_style_local_text_color(setupLabel, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, lv_color_hex(0xf6f8fa));
            lv_obj_set_width(setupLabel, 218);
            lv_obj_set_pos(setupLabel, 11, 42);
            lv_label_set_text_fmt(setupLabel, "WI-FI SETUP\n\n%s\nPassword: %s\n\nhttp://192.168.4.1", configPortalApSsid(), configPortalApPassword());
        }
        if (millis() - retryAt >= 10000)
        {
            retryAt = millis();
            WiFi.reconnect();
        }
    }
    if (connected && !online)
        startedAt = millis();
    connected = online;
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

static void setLabelText(lv_obj_t *label, const char *text)
{
    if (strcmp(lv_label_get_text(label), text) != 0)
        lv_label_set_text(label, text);
}

// Fixed boxes and fonts keep changing digits and units from moving their neighbors.
static void layoutRateValue(lv_obj_t *icon, lv_obj_t *value, lv_obj_t *unit,
                            lv_coord_t left, lv_coord_t baseline, bool prominent)
{
    const lv_font_t *valueFont = prominent ? &monitor_rate_42 : &monitor_value_22;
    const lv_coord_t valueWidth = prominent ? 70 : 53;
    const lv_coord_t unitWidth = 30;
    const lv_coord_t iconX = left + (prominent ? 0 : 2);
    const lv_coord_t valueX = left + (prominent ? 11 : 22);
    const lv_coord_t unitX = left + (prominent ? 82 : 78);

    lv_obj_set_pos(icon, iconX, baseline - (prominent ? 14 : 16));

    lv_obj_set_style_local_text_font(value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, valueFont);
    lv_label_set_align(value, LV_LABEL_ALIGN_RIGHT);
    lv_label_set_long_mode(value, LV_LABEL_LONG_CROP);
    lv_obj_set_size(value, valueWidth, valueFont->line_height);
    lv_obj_set_pos(value, valueX, baseline - (valueFont->line_height - valueFont->base_line));

    lv_obj_set_style_local_text_font(unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_label_set_align(unit, LV_LABEL_ALIGN_LEFT);
    lv_label_set_long_mode(unit, LV_LABEL_LONG_CROP);
    lv_obj_set_size(unit, unitWidth, lv_font_montserrat_12.line_height);
    lv_obj_set_pos(unit, unitX, baseline - (lv_font_montserrat_12.line_height - lv_font_montserrat_12.base_line));
}

static void updateRateValue(lv_obj_t *value, lv_obj_t *unit, double bytes)
{
    const TransferText text = formatTransfer(bytes, true);
    setLabelText(value, text.number);
    setLabelText(unit, text.unit[0] ? text.unit : "B/s");
}

void updateNetworkInfoLabel()
{
    updateRateValue(up_speed_label, up_speed_unit_label, nasStatus.txBytesPerSecond);
    updateRateValue(down_speed_label, down_speed_unit_label, nasStatus.rxBytesPerSecond);
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
    NasNetSample current;
    if (!takeNasNetSample(current))
    {
        if (millis() - lastSuccessAt > 2000)
        {
            havePrevious = false;
            if (rateVisible)
            {
                rateVisible = false;
                nasStatus.rxBytesPerSecond = nasStatus.txBytesPerSecond = -1;
                updateNetworkInfoLabel();
            }
        }
        return;
    }
    lastSuccessAt = millis();
    if (!havePrevious)
    {
        previous = speedBaseline = current;
        havePrevious = true;
        return;
    }
    NetRate adjacent;
    if (!calculateNetRate(previous, current, adjacent) || adjacent.elapsedSeconds > 1.0)
    {
        speedBaseline = current;
        // Counter reset and sample gaps are unknown, not a plausible zero rate.
        nasStatus.rxBytesPerSecond = nasStatus.txBytesPerSecond = -1;
        updateNetworkInfoLabel();
        rateVisible = false;
    }
    else
    {
        appendChartSample(adjacent.rxBytesPerSecond, adjacent.txBytesPerSecond);
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

static bool carouselLayoutChanged = true;

static void showCarouselLabel(lv_obj_t *label, const char *text, const lv_font_t *font,
                              lv_color_t color, lv_coord_t x, lv_coord_t y,
                              lv_coord_t width, lv_coord_t height, lv_label_align_t align)
{
    setLabelText(label, text);
    if (!carouselLayoutChanged)
        return;
    lv_obj_set_hidden(label, false);
    lv_obj_set_style_local_text_font(label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, font);
    lv_obj_set_style_local_text_color(label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, color);
    lv_label_set_align(label, align);
    lv_obj_set_size(label, width, height);
    lv_obj_set_pos(label, x, y);
}

static void hideCarouselContent()
{
    for (uint8_t index = 0; index < 3; ++index)
    {
        lv_obj_set_hidden(carousel_title[index], true);
        lv_obj_set_hidden(carousel_value[index], true);
    }
    for (uint8_t index = 0; index < 2; ++index)
    {
        lv_obj_set_hidden(carousel_unit[index], true);
        lv_obj_set_hidden(carousel_arrow[index], true);
    }
}

static void formatStorage(char *buffer, size_t size, double bytes)
{
    const TransferText text = formatTransfer(bytes, true);
    if (!text.unit[0])
        snprintf(buffer, size, "%s", text.number);
    else
        snprintf(buffer, size, "%s%c", text.number, text.unit[0]);
}

static void renderCarousel()
{
    static const lv_color_t primary = lv_color_hex(0xf6f8fa);
    static const lv_color_t muted = lv_color_hex(0xb9cad3);
    static const lv_color_t secondary = lv_color_hex(0x8fa8b2);
    static const lv_color_t green = lv_color_hex(0x45dfaa);
    char first[20] = "--";
    char second[20] = "--";
    char third[20] = "--";
    const char *firstUnit = "";
    const char *secondUnit = "";
    static uint8_t renderedPage = 255;
    carouselLayoutChanged = renderedPage != carouselPage;
    if (carouselLayoutChanged)
    {
        hideCarouselContent();
        for (uint8_t index = 0; index < 4; ++index)
            lv_obj_set_style_local_bg_color(carousel_dots[index], LV_OBJ_PART_MAIN, LV_STATE_DEFAULT,
                                            index == carouselPage ? lv_color_hex(0xd7e1e6) : lv_color_hex(0x48616a));
        renderedPage = carouselPage;
    }

    if (carouselPage == 0)
    {
        if (nasOnline && nasStatus.trafficHistoryValid)
        {
            const TransferText upload = formatTransfer(nasStatus.txBytes24h, false);
            const TransferText download = formatTransfer(nasStatus.rxBytes24h, false);
            snprintf(first, sizeof(first), "%s", upload.number);
            snprintf(second, sizeof(second), "%s", download.number);
            firstUnit = upload.unit;
            secondUnit = download.unit;
        }
        showCarouselLabel(carousel_title[0], "LAST 24H", &lv_font_montserrat_12, muted,
                          0, -1, 230, 15, LV_LABEL_ALIGN_CENTER);
        for (uint8_t index = 0; index < 2; ++index)
        {
            const lv_coord_t left = index * 115;
            if (carouselLayoutChanged)
            {
                lv_obj_set_hidden(carousel_arrow[index], false);
                lv_obj_set_pos(carousel_arrow[index], left + 3, 15);
            }
            showCarouselLabel(carousel_value[index], index ? second : first, &monitor_value_22,
                              primary, left + 21, 12, 65, 24, LV_LABEL_ALIGN_RIGHT);
            showCarouselLabel(carousel_unit[index], index ? secondUnit : firstUnit,
                              &lv_font_montserrat_12, muted, left + 88, 20, 20, 15, LV_LABEL_ALIGN_LEFT);
        }
    }
    else if (carouselPage == 1)
    {
        unsigned long days = 0;
        unsigned long hours = 0;
        if (nasOnline)
        {
            days = nas_uptime_seconds / 86400UL;
            hours = (nas_uptime_seconds / 3600UL) % 24UL;
            snprintf(first, sizeof(first), "%lu", days);
            snprintf(second, sizeof(second), "%lu", hours);
        }
        showCarouselLabel(carousel_title[0], "UPTIME", &lv_font_montserrat_12, muted,
                          0, -1, 230, 15, LV_LABEL_ALIGN_CENTER);
        showCarouselLabel(carousel_value[0], first, &monitor_value_22, primary,
                          10, 12, 75, 24, LV_LABEL_ALIGN_RIGHT);
        showCarouselLabel(carousel_unit[0], "D", &lv_font_montserrat_12, muted,
                          88, 20, 10, 15, LV_LABEL_ALIGN_LEFT);
        showCarouselLabel(carousel_value[1], second, &monitor_value_22, primary,
                          145, 12, 48, 24, LV_LABEL_ALIGN_RIGHT);
        showCarouselLabel(carousel_unit[1], "H", &lv_font_montserrat_12, muted,
                          195, 20, 10, 15, LV_LABEL_ALIGN_LEFT);
    }
    else if (carouselPage == 2)
    {
        if (nasOnline && nasStatus.cpuTemperature > 0)
            snprintf(first, sizeof(first), "%.0f", nasStatus.cpuTemperature);
        if (nasOnline && nasStatus.diskTemperature > 0)
            snprintf(second, sizeof(second), "%.0f", nasStatus.diskTemperature);
        showCarouselLabel(carousel_title[0], "CPU", &lv_font_montserrat_12, secondary,
                          0, -1, 115, 15, LV_LABEL_ALIGN_CENTER);
        showCarouselLabel(carousel_value[0], first, &monitor_value_22, green,
                          31, 12, 45, 24, LV_LABEL_ALIGN_CENTER);
        showCarouselLabel(carousel_unit[0], "°C", &lv_font_montserrat_12, muted,
                          80, 20, 14, 15, LV_LABEL_ALIGN_LEFT);
        showCarouselLabel(carousel_title[1], "DISK MAX", &lv_font_montserrat_12, secondary,
                          115, -1, 115, 15, LV_LABEL_ALIGN_CENTER);
        showCarouselLabel(carousel_value[1], second, &monitor_value_22, green,
                          146, 12, 45, 24, LV_LABEL_ALIGN_CENTER);
        showCarouselLabel(carousel_unit[1], "°C", &lv_font_montserrat_12, muted,
                          195, 20, 14, 15, LV_LABEL_ALIGN_LEFT);
    }
    else
    {
        if (nasOnline && nasStatus.storageValid)
        {
            formatStorage(first, sizeof(first), nasStatus.storageTotalBytes);
            formatStorage(second, sizeof(second), nasStatus.storageUsedBytes);
            snprintf(third, sizeof(third), "%.0f%%", nasStatus.storagePercent);
        }
        static const char *titles[] = {"TOTAL", "USED", "USAGE"};
        const char *values[] = {first, second, third};
        static const lv_coord_t left[] = {0, 77, 153};
        static const lv_coord_t width[] = {77, 76, 77};
        for (uint8_t index = 0; index < 3; ++index)
        {
            showCarouselLabel(carousel_title[index], titles[index], &lv_font_montserrat_12, secondary,
                              left[index], 0, width[index], 15, LV_LABEL_ALIGN_CENTER);
            showCarouselLabel(carousel_value[index], values[index], &monitor_value_22,
                              index == 2 ? green : primary, left[index], 12, width[index], 24,
                              LV_LABEL_ALIGN_CENTER);
        }
    }
}

static void updateDiskIo(bool online)
{
    const bool valid = online && nasStatus.diskIoValid;
    updateRateValue(disk_read_value, disk_read_unit,
                    valid ? nasStatus.diskReadBytesPerSecond : -1);
    updateRateValue(disk_write_value, disk_write_unit,
                    valid ? nasStatus.diskWriteBytesPerSecond : -1);
}

static void carouselSetX(void *object, lv_anim_value_t x)
{
    static uint32_t lastFrameAt = 0;
    const uint32_t now = millis();
    if (animationFrameStarted && lastFrameAt)
    {
        const uint32_t gap = now - lastFrameAt;
        animationMaxGapMs = max(animationMaxGapMs, gap);
        animationSlowFrames += gap > 50;
        ++animationFrames;
    }
    lastFrameAt = now;
    animationFrameStarted = true;
    lv_obj_set_x(static_cast<lv_obj_t *>(object), x);
}

static void carouselEnterReady(lv_anim_t *animation)
{
    carouselAnimating = false;
    renderCarousel();
}

static void carouselExitReady(lv_anim_t *animation)
{
    carouselPage = (carouselPage + 1) % 4;
    renderCarousel();
    Serial.printf("CAROUSEL page=%u heap=%u\r\n", carouselPage, ESP.getFreeHeap());
    lv_obj_set_x(carousel_layer, 230);

    lv_anim_t enter;
    lv_anim_init(&enter);
    lv_anim_set_var(&enter, carousel_layer);
    lv_anim_set_exec_cb(&enter, carouselSetX);
    lv_anim_set_values(&enter, 230, 0);
    lv_anim_set_time(&enter, 200);
    lv_anim_path_t path;
    lv_anim_path_init(&path);
    lv_anim_path_set_cb(&path, lv_anim_path_ease_out);
    lv_anim_set_path(&enter, &path);
    lv_anim_set_ready_cb(&enter, carouselEnterReady);
    lv_anim_start(&enter);
}

static void carousel_task_cb(lv_task_t *task)
{
    if (carouselAnimating)
        return;
    carouselAnimating = true;
    animationFrameStarted = false;

    lv_anim_t exit;
    lv_anim_init(&exit);
    lv_anim_set_var(&exit, carousel_layer);
    lv_anim_set_exec_cb(&exit, carouselSetX);
    lv_anim_set_values(&exit, 0, -230);
    lv_anim_set_time(&exit, 200);
    lv_anim_path_t path;
    lv_anim_path_init(&path);
    lv_anim_path_set_cb(&path, lv_anim_path_ease_in);
    lv_anim_set_path(&exit, &path);
    lv_anim_set_ready_cb(&exit, carouselExitReady);
    lv_anim_start(&exit);
}

void styleMetricBar(lv_obj_t *bar, lv_color_t indicatorColor, lv_color_t trackColor)
{
    lv_obj_set_size(bar, 72, 3);
    lv_obj_set_style_local_bg_color(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, trackColor);
    lv_obj_set_style_local_bg_color(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, indicatorColor);
    lv_obj_set_style_local_border_width(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_border_width(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_radius(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_radius(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, 0);
}

static void createMetric(const char *titleText, lv_coord_t left, lv_color_t color,
                         lv_obj_t *&value, lv_obj_t *&bar)
{
    lv_obj_t *title = lv_label_create(monitor_page, NULL);
    lv_label_set_text(title, titleText);
    lv_obj_set_style_local_text_font(title, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(title, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, lv_color_hex(0xf6f8fa));
    lv_label_set_long_mode(title, LV_LABEL_LONG_CROP);
    lv_label_set_align(title, LV_LABEL_ALIGN_CENTER);
    lv_obj_set_size(title, 72, 15);
    lv_obj_set_pos(title, left, 184);

    value = lv_label_create(monitor_page, NULL);
    lv_label_set_text(value, "--");
    lv_obj_set_style_local_text_font(value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &monitor_value_22);
    lv_obj_set_style_local_text_color(value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, lv_color_hex(0xf6f8fa));
    lv_label_set_align(value, LV_LABEL_ALIGN_CENTER);
    lv_label_set_long_mode(value, LV_LABEL_LONG_CROP);
    lv_obj_set_size(value, 72, 24);
    lv_obj_set_pos(value, left, 203);

    bar = lv_bar_create(monitor_page, NULL);
    styleMetricBar(bar, color, lv_color_hex(0x23414b));
    lv_obj_set_pos(bar, left, 232);
}

static void createDivider(lv_coord_t x, lv_coord_t y, lv_coord_t width, lv_color_t color)
{
    lv_obj_t *divider = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(divider, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(divider, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
    lv_obj_set_style_local_bg_color(divider, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, color);
    lv_obj_set_size(divider, width, 1);
    lv_obj_set_pos(divider, x, y);
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
        setLabelText(weekday_label, "---");
        setLabelText(date_label, "-- --");
        setLabelText(time_label, "--:--:--");
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
    setLabelText(weekday_label, weekdays[timeInfo.tm_wday]);
    setLabelText(date_label, dateText);
    setLabelText(time_label, timeText);

    static int lastLoggedMinute = -1;
    if (timeInfo.tm_min != lastLoggedMinute)
    {
        lastLoggedMinute = timeInfo.tm_min;
        Serial.print(F("Time: "));
        Serial.printf("%02d:%02d\r\n", timeInfo.tm_hour, timeInfo.tm_min);
    }
}

// Rendering consumes completed snapshots; HTTP never runs in an LVGL callback.
static void updateMetric(lv_obj_t *bar, lv_obj_t *label, double percentage, bool valid)
{
    char text[8] = "--";
    int value = 0;
    if (valid && isfinite(percentage))
    {
        value = constrain(static_cast<int>(round(percentage)), 0, 100);
        snprintf(text, sizeof(text), "%d%%", value);
    }
    if (lv_bar_get_value(bar) != value)
        lv_bar_set_value(bar, value, LV_ANIM_OFF);
    setLabelText(label, text);
}

static void task_cb(lv_task_t *task)
{
    static uint32_t lastSuccessAt = 0;
    const bool received = takeNasStatus(nasStatus);
    if (received)
    {
        lastSuccessAt = millis();
        nasOnline = true;
        cpu_usage = nasStatus.cpuPercent;
        gpu_usage = nasStatus.gpuPercent;
        mem_usage = nasStatus.memoryPercent;
        nas_uptime_seconds = nasStatus.uptimeSeconds;
    }
    else if (!nasOnline || (WiFi.status() == WL_CONNECTED && millis() - lastSuccessAt <= 3000))
        return;
    else
        nasOnline = false;
    updateDiskIo(nasOnline);
    if (!carouselAnimating)
        renderCarousel();
    updateMetric(cpu_bar, cpu_value_label, cpu_usage, nasOnline);
    updateMetric(gpu_bar, gpu_value_label, gpu_usage, nasOnline);
    updateMetric(mem_bar, mem_value_label, mem_usage, nasOnline);
}

#ifdef ESP8266
// LVGL finishes a glyph before requesting the next. Copying its small bitmap
// once avoids thousands of slow unaligned Flash byte-load exceptions per frame.
static const uint8_t *readDisplayGlyph(const lv_font_t *font, uint32_t letter)
{
    static uint8_t glyphBuffer[512];
    const uint8_t *bitmap = lv_font_get_bitmap_fmt_txt(font, letter);
    lv_font_glyph_dsc_t glyph;
    if (!bitmap || !lv_font_get_glyph_dsc_fmt_txt(font, &glyph, letter, 0))
        return NULL;
    const size_t length = (glyph.box_w * glyph.box_h * glyph.bpp + 7) / 8;
    if (length > sizeof(glyphBuffer))
        return bitmap; // NON32XFER_HANDLER remains the fallback for larger glyphs.
    memcpy_P(glyphBuffer, bitmap, length);
    return glyphBuffer;
}
#endif

void setup()
{
    Serial.begin(115200); /* prepare for possible serial debug */
    loadDeviceConfig();

#ifdef ESP8266
    lv_font_montserrat_12.get_glyph_bitmap = readDisplayGlyph;
    monitor_clock_42.get_glyph_bitmap = readDisplayGlyph;
    monitor_rate_42.get_glyph_bitmap = readDisplayGlyph;
    monitor_value_22.get_glyph_bitmap = readDisplayGlyph;
#endif
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

    setupPages();
    initLoginPage();

    const lv_color_t contentColor = lv_color_hex(0x061315);
    lv_obj_t *outer = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(outer, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(outer, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
    lv_obj_set_style_local_bg_color(outer, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, contentColor);
    lv_obj_set_size(outer, 240, 240);

    const lv_color_t primaryColor = lv_color_hex(0xf6f8fa);
    const lv_color_t mutedColor = lv_color_hex(0xb9cad3);
    const lv_color_t secondaryColor = lv_color_hex(0x8fa8b2);
    const lv_color_t red = lv_color_hex(0xff6670);
    const lv_color_t blue = lv_color_hex(0x55c7f3);
    const lv_color_t green = lv_color_hex(0x45dfaa);
    const lv_color_t amber = lv_color_hex(0xf4bd62);

    lv_obj_t *content = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(content, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(content, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
    lv_obj_set_style_local_bg_color(content, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, contentColor);
    lv_obj_set_style_local_radius(content, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, 10);
    lv_obj_set_size(content, 230, 230);
    lv_obj_set_pos(content, 5, 5);

    upload_label = lv_line_create(monitor_page, NULL);
    lv_line_set_points(upload_label, net_arrow_points[0], 5);
    lv_obj_set_style_local_line_width(upload_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, 1);
    lv_obj_set_style_local_line_color(upload_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, red);
    up_speed_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(up_speed_label, "--");
    lv_obj_set_style_local_text_font(up_speed_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &monitor_clock_42);
    lv_obj_set_style_local_text_color(up_speed_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, primaryColor);
    up_speed_unit_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(up_speed_unit_label, "");
    lv_obj_set_style_local_text_font(up_speed_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(up_speed_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);

    download_label = lv_line_create(monitor_page, NULL);
    lv_line_set_points(download_label, net_arrow_points[1], 5);
    lv_obj_set_style_local_line_width(download_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, 1);
    lv_obj_set_style_local_line_color(download_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, blue);
    down_speed_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(down_speed_label, "--");
    lv_obj_set_style_local_text_font(down_speed_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &monitor_clock_42);
    lv_obj_set_style_local_text_color(down_speed_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, primaryColor);
    down_speed_unit_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(down_speed_unit_label, "");
    lv_obj_set_style_local_text_font(down_speed_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(down_speed_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);
    layoutRateValue(upload_label, up_speed_label, up_speed_unit_label, 5, 43, true);
    layoutRateValue(download_label, down_speed_label, down_speed_unit_label, 123, 43, true);
    updateNetworkInfoLabel();

    chart = lv_chart_create(monitor_page, NULL);
    lv_obj_set_size(chart, 220, 18);
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
    ser1 = lv_chart_add_series(chart, red);
    ser2 = lv_chart_add_series(chart, blue);
    lv_chart_init_points(chart, ser1, 0);
    lv_chart_init_points(chart, ser2, 0);

    disk_read_label = lv_line_create(monitor_page, NULL);
    lv_line_set_points(disk_read_label, disk_read_points, 9);
    lv_obj_set_style_local_line_width(disk_read_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, 2);
    lv_obj_set_style_local_line_rounded(disk_read_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, true);
    lv_obj_set_style_local_line_color(disk_read_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, green);
    disk_read_value = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_read_value, "--");
    lv_obj_set_style_local_text_font(disk_read_value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &monitor_value_22);
    lv_obj_set_style_local_text_color(disk_read_value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, primaryColor);
    disk_read_unit = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_read_unit, "");
    lv_obj_set_style_local_text_font(disk_read_unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(disk_read_unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);

    disk_write_label = lv_line_create(monitor_page, NULL);
    lv_line_set_points(disk_write_label, disk_write_points, 5);
    lv_obj_set_style_local_line_width(disk_write_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, 2);
    lv_obj_set_style_local_line_rounded(disk_write_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, true);
    lv_obj_set_style_local_line_color(disk_write_label, LV_LINE_PART_MAIN, LV_STATE_DEFAULT, amber);
    disk_write_value = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_write_value, "--");
    lv_obj_set_style_local_text_font(disk_write_value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &monitor_value_22);
    lv_obj_set_style_local_text_color(disk_write_value, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, primaryColor);
    disk_write_unit = lv_label_create(monitor_page, NULL);
    lv_label_set_text(disk_write_unit, "");
    lv_obj_set_style_local_text_font(disk_write_unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(disk_write_unit, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, mutedColor);
    layoutRateValue(disk_read_label, disk_read_value, disk_read_unit, 5, 91, false);
    layoutRateValue(disk_write_label, disk_write_value, disk_write_unit, 123, 91, false);
    updateDiskIo(false);

    carousel_viewport = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(carousel_viewport, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(carousel_viewport, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_TRANSP);
    lv_obj_set_size(carousel_viewport, 230, 35);
    lv_obj_set_pos(carousel_viewport, 5, 96);

    carousel_layer = lv_obj_create(carousel_viewport, NULL);
    lv_obj_clean_style_list(carousel_layer, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(carousel_layer, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_TRANSP);
    lv_obj_set_size(carousel_layer, 230, 35);
    lv_obj_set_pos(carousel_layer, 0, 0);

    for (uint8_t index = 0; index < 3; ++index)
    {
        carousel_title[index] = lv_label_create(carousel_layer, NULL);
        lv_obj_set_style_local_text_font(carousel_title[index], LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
        lv_label_set_align(carousel_title[index], LV_LABEL_ALIGN_CENTER);
        lv_label_set_long_mode(carousel_title[index], LV_LABEL_LONG_CROP);
        carousel_value[index] = lv_label_create(carousel_layer, NULL);
        lv_obj_set_style_local_text_font(carousel_value[index], LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &monitor_value_22);
        lv_label_set_align(carousel_value[index], LV_LABEL_ALIGN_CENTER);
        lv_label_set_long_mode(carousel_value[index], LV_LABEL_LONG_CROP);
    }

    for (uint8_t index = 0; index < 2; ++index)
    {
        carousel_unit[index] = lv_label_create(carousel_layer, NULL);
        lv_label_set_text(carousel_unit[index], "");
        lv_label_set_long_mode(carousel_unit[index], LV_LABEL_LONG_CROP);

        carousel_arrow[index] = lv_line_create(carousel_layer, NULL);
        lv_line_set_points(carousel_arrow[index], carousel_arrow_points[index], 5);
        lv_obj_set_style_local_line_width(carousel_arrow[index], LV_LINE_PART_MAIN, LV_STATE_DEFAULT, 2);
        lv_obj_set_style_local_line_rounded(carousel_arrow[index], LV_LINE_PART_MAIN, LV_STATE_DEFAULT, true);
        lv_obj_set_style_local_line_color(carousel_arrow[index], LV_LINE_PART_MAIN, LV_STATE_DEFAULT,
                                          index == 0 ? red : blue);
    }

    for (uint8_t index = 0; index < 4; ++index)
    {
        carousel_dots[index] = lv_obj_create(monitor_page, NULL);
        lv_obj_clean_style_list(carousel_dots[index], LV_OBJ_PART_MAIN);
        lv_obj_set_style_local_bg_opa(carousel_dots[index], LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
        lv_obj_set_style_local_radius(carousel_dots[index], LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_RADIUS_CIRCLE);
        lv_obj_set_size(carousel_dots[index], 3, 3);
        lv_obj_set_pos(carousel_dots[index], 108 + index * 7, 132);
    }
    renderCarousel();

    weekday_label = lv_label_create(monitor_page, NULL);
    setLabelText(weekday_label, "---");
    lv_obj_set_style_local_text_font(weekday_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(weekday_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, secondaryColor);
    lv_label_set_long_mode(weekday_label, LV_LABEL_LONG_CROP);
    lv_label_set_align(weekday_label, LV_LABEL_ALIGN_CENTER);
    lv_obj_set_size(weekday_label, 46, 15);
    lv_obj_set_pos(weekday_label, 5, 147);

    date_label = lv_label_create(monitor_page, NULL);
    setLabelText(date_label, "-- --");
    lv_obj_set_style_local_text_font(date_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &lv_font_montserrat_12);
    lv_obj_set_style_local_text_color(date_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, secondaryColor);
    lv_label_set_long_mode(date_label, LV_LABEL_LONG_CROP);
    lv_label_set_align(date_label, LV_LABEL_ALIGN_CENTER);
    lv_obj_set_size(date_label, 46, 15);
    lv_obj_set_pos(date_label, 5, 161);

    time_label = lv_label_create(monitor_page, NULL);
    setLabelText(time_label, "--:--:--");
    lv_obj_set_style_local_text_font(time_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, &monitor_clock_42);
    lv_obj_set_style_local_text_letter_space(time_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, -2);
    lv_obj_set_style_local_text_color(time_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_align(time_label, LV_LABEL_ALIGN_CENTER);
    lv_label_set_long_mode(time_label, LV_LABEL_LONG_CROP);
    lv_obj_set_size(time_label, 182, 46);
    lv_obj_set_pos(time_label, 53, 138);

    createMetric("CPU", 5, red, cpu_value_label, cpu_bar);
    createMetric("GPU", 84, blue, gpu_value_label, gpu_bar);
    createMetric("MEM", 163, green, mem_value_label, mem_bar);

    createDivider(10, 67, 220, lv_color_hex(0x23414b));
    createDivider(5, 69, 230, lv_color_hex(0x173038));
    createDivider(5, 95, 230, lv_color_hex(0x23414b));
    createDivider(5, 136, 230, lv_color_hex(0x23414b));

    lv_task_create(task_cb, 20, LV_TASK_PRIO_MID, NULL);
    lv_task_create(net_task_cb, 20, LV_TASK_PRIO_HIGH, NULL);
    lv_task_create(clock_task_cb, 1000, LV_TASK_PRIO_MID, NULL);
    lv_task_create(carousel_task_cb, 5000, LV_TASK_PRIO_LOW, NULL);
}

static void reportDiagnostics()
{
    static uint32_t reportedAt = 0;
    static uint32_t lastLoopAt = 0;
    static uint32_t maxLoopGapMs = 0;
    static uint32_t minHeap = UINT32_MAX;
    const uint32_t now = millis();
    if (lastLoopAt)
        maxLoopGapMs = max(maxLoopGapMs, now - lastLoopAt);
    lastLoopAt = now;
    minHeap = min(minHeap, ESP.getFreeHeap());
    if (now - reportedAt < 10000 || carouselAnimating)
        return;
    reportedAt = now;
    lv_mem_monitor_t memory;
    lv_mem_monitor(&memory);
    const NasRequestHealth &health = getNasRequestHealth();
    Serial.printf("HTTP net=%lu/%lu fail=%lu max=%lums status=%lu/%lu fail=%lu max=%lums error=%u\r\n",
                  (unsigned long)health.netSuccesses, (unsigned long)health.netRequests,
                  (unsigned long)health.netFailures, (unsigned long)health.netMaxLatencyMs,
                  (unsigned long)health.statusSuccesses, (unsigned long)health.statusRequests,
                  (unsigned long)health.statusFailures, (unsigned long)health.statusMaxLatencyMs,
                  (unsigned)health.lastError);
    Serial.printf("PERF heap=%lu min=%lu block=%lu stack=%lu lvfree=%lu lvblock=%lu lvfrag=%u loop=%lums frames=%lu frameMax=%lums slow=%lu\r\n",
                  (unsigned long)ESP.getFreeHeap(), (unsigned long)minHeap,
                  (unsigned long)ESP.getMaxFreeBlockSize(), (unsigned long)ESP.getFreeContStack(),
                  (unsigned long)memory.free_size, (unsigned long)memory.free_biggest_size,
                  (unsigned)memory.frag_pct, (unsigned long)maxLoopGapMs,
                  (unsigned long)animationFrames, (unsigned long)animationMaxGapMs,
                  (unsigned long)animationSlowFrames);
    Serial.printf("STATE online=%u history=%u storage=%u rx24=%.0f tx24=%.0f cpu=%.1f temp=%.0f disk=%.0f\r\n",
                  nasOnline, nasStatus.trafficHistoryValid, nasStatus.storageValid,
                  nasStatus.rxBytes24h, nasStatus.txBytes24h, nasStatus.cpuPercent,
                  nasStatus.cpuTemperature, nasStatus.diskTemperature);
    maxLoopGapMs = animationFrames = animationMaxGapMs = animationSlowFrames = 0;
}

void loop()
{
    reportDiagnostics();
    serviceWiFi();
    pollNasRequests();
    handleConfigPortal();
    lv_task_handler(); /* let the GUI do its work */
}
