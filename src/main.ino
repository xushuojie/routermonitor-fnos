#include <lvgl.h>
#include <TFT_eSPI.h>
#include <string>
#include <ESP8266WiFi.h>
#include <time.h>

#include "ConfigPortal.h"
#include "NasStatus.h"

using namespace std;

// extern lv_font_t my_font_name;
LV_FONT_DECLARE(lv_font_montserrat_16)
LV_FONT_DECLARE(lv_font_montserrat_22)
LV_FONT_DECLARE(lv_font_montserrat_46)

TFT_eSPI tft = TFT_eSPI(); /* TFT instance */
static lv_disp_buf_t disp_buf;
static lv_color_t buf[LV_HOR_RES_MAX * 5];

// 定义页面
static lv_obj_t *login_page = NULL;
static lv_obj_t *monitor_page = NULL;

// basic variables
static uint8_t test_data = 0;
// static lv_obj_t* label1;
static lv_obj_t *upload_label;
static lv_obj_t *download_label;
static lv_obj_t *up_speed_label;
static lv_obj_t *up_speed_unit_label;
static lv_obj_t *down_speed_label;
static lv_obj_t *down_speed_unit_label;
static lv_obj_t *cpu_bar;
static lv_obj_t *cpu_value_label;
static lv_obj_t *cpu_percent_label;
static lv_obj_t *gpu_bar;
static lv_obj_t *gpu_value_label;
static lv_obj_t *gpu_percent_label;
static lv_obj_t *mem_bar;
static lv_obj_t *mem_value_label;
static lv_obj_t *mem_percent_label;
static lv_obj_t *uptime_value_label;
static lv_obj_t *uptime_unit_label;
static lv_obj_t *temp_value_label;
static lv_obj_t *temp_unit_label;
static lv_obj_t *time_label;
static lv_obj_t *chart;

static lv_chart_series_t *ser1;
static lv_chart_series_t *ser2;

NasStatusSnapshot nasStatus;

lv_coord_t up_speed_max = 0;
lv_coord_t down_speed_max = 0;
// 监测数值
double up_speed = 0;
double down_speed = 0;
double cpu_usage = 0;
double gpu_usage = 0;
double mem_usage = 0;
double temp_value = 0;
uint32_t nas_uptime_seconds = 0;
lv_coord_t upload_serise[10] = {0};
lv_coord_t download_serise[10] = {0};

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
    lv_style_t login_spinner_style;
    lv_style_init(&login_spinner_style);
    lv_style_set_line_width(&login_spinner_style, LV_STATE_DEFAULT, 5);
    lv_style_set_pad_left(&login_spinner_style, LV_STATE_DEFAULT, 5);
    lv_style_set_line_color(&login_spinner_style, LV_STATE_DEFAULT, lv_color_hex(0xff5d18));

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

    // NTP 使用 UTC+8，显示 24 小时制北京时间。
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

lv_coord_t updateNetSeries(lv_coord_t *series, double speed)
{
    lv_coord_t local_max = series[0];
    for (int index = 0; index < 9; index++)
    {
        series[index] = series[index + 1];
        if (local_max < series[index])
        {
            local_max = series[index];
        }
    }
    series[9] = (lv_coord_t)speed;
    if (local_max < series[9])
        local_max = series[9];

    Serial.print(speed);
    Serial.print("->");
    Serial.print(series[9]);
    Serial.print("    |");
    for (int i = 0; i < 10; i++)
    {
        Serial.print(series[i]);
        Serial.print(" ");
    }
    Serial.println();

    return local_max;
}

bool refreshMonitorData()
{
    if (getNasStatus(nasStatus))
    {
        cpu_usage = nasStatus.cpuPercent;
        gpu_usage = nasStatus.gpuPercent;
        mem_usage = nasStatus.memoryPercent;
        temp_value = nasStatus.temperature;
        nas_uptime_seconds = nasStatus.uptimeSeconds;
        down_speed = nasStatus.rxBytesPerSecond / 1024.0;
        up_speed = nasStatus.txBytesPerSecond / 1024.0;

        down_speed_max = updateNetSeries(download_serise, down_speed);
        up_speed_max = updateNetSeries(upload_serise, up_speed);
        lv_chart_set_points(chart, ser2, download_serise);
        lv_chart_set_points(chart, ser1, upload_serise);
        return true;
    }
    return false;
}

void updateNetworkInfoLabel()
{
    if (up_speed < 100.0)
    {
        // < 99.99 K/S
        lv_label_set_text_fmt(up_speed_label, "%.2f", up_speed);
        lv_label_set_text(up_speed_unit_label, "k/s");
    }
    else if (up_speed < 1000.0)
    {
        // 999.9 K/S
        lv_label_set_text_fmt(up_speed_label, "%.1f", up_speed);
        lv_label_set_text(up_speed_unit_label, "k/s");
    }
    else if (up_speed < 100000.0)
    {
        // 99.99 M/S
        up_speed /= 1024.0;
        lv_label_set_text_fmt(up_speed_label, "%.2f", up_speed);
        lv_label_set_text(up_speed_unit_label, "m/s");
    }
    else if (up_speed < 1000000.0)
    {
        // 999.9 M/S
        up_speed = up_speed / 1024.0;
        lv_label_set_text_fmt(up_speed_label, "%.1f", up_speed);
        lv_label_set_text(up_speed_unit_label, "m/s");
    }

    if (down_speed < 100.0)
    {
        // < 99.99 K/S
        lv_label_set_text_fmt(down_speed_label, "%.2f", down_speed);
        lv_label_set_text(down_speed_unit_label, "k/s");
    }
    else if (down_speed < 1000.0)
    {
        // 999.9 K/S
        lv_label_set_text_fmt(down_speed_label, "%.1f", down_speed);
        lv_label_set_text(down_speed_unit_label, "k/s");
    }
    else if (down_speed < 100000.0)
    {
        // 99.99 M/S
        down_speed /= 1024.0;
        lv_label_set_text_fmt(down_speed_label, "%.2f", down_speed);
        lv_label_set_text(down_speed_unit_label, "m/s");
    }
    else if (down_speed < 1000000.0)
    {
        // 999.9 M/S
        down_speed = down_speed / 1024.0;
        lv_label_set_text_fmt(down_speed_label, "%.1f", down_speed);
        lv_label_set_text(down_speed_unit_label, "m/s");
    }
}

void updateChartRange()
{
    lv_coord_t max_speed = max(down_speed_max, up_speed_max);
    max_speed = max(max_speed, (lv_coord_t)16);
    lv_chart_set_range(chart, 0, (lv_coord_t)(max_speed * 1.1));
}

void styleMetricBar(lv_obj_t *bar, lv_color_t indicatorColor, lv_color_t trackColor)
{
    lv_obj_set_size(bar, 107, 9);
    lv_obj_set_style_local_bg_color(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, trackColor);
    lv_obj_set_style_local_bg_color(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, indicatorColor);
    lv_obj_set_style_local_border_width(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_border_width(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_radius(bar, LV_BAR_PART_BG, LV_STATE_DEFAULT, 5);
    lv_obj_set_style_local_radius(bar, LV_BAR_PART_INDIC, LV_STATE_DEFAULT, 5);
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
        lv_label_set_text(time_label, "--:--");
        return;
    }

    struct tm timeInfo;
    localtime_r(&now, &timeInfo);
    updateNightMode(timeInfo.tm_hour);
    char timeText[6];
    char displayText[24];
    snprintf(timeText, sizeof(timeText), "%02d:%02d", timeInfo.tm_hour, timeInfo.tm_min);
    // 用与信息框相同的颜色隐藏冒号，文字宽度保持不变，数字不会左右跳动。
    snprintf(displayText, sizeof(displayText), "%02d#%s :#%02d", timeInfo.tm_hour,
             (timeInfo.tm_sec & 1) ? "081418" : "ffffff", timeInfo.tm_min);
    lv_label_set_text(time_label, displayText);

    static int lastLoggedMinute = -1;
    if (timeInfo.tm_min != lastLoggedMinute)
    {
        lastLoggedMinute = timeInfo.tm_min;
        Serial.print(F("Time: "));
        Serial.println(timeText);
    }
}

static lv_coord_t labelTextWidth(lv_obj_t *label, const lv_font_t *font)
{
    const char *text = lv_label_get_text(label);
    return _lv_txt_get_width(text, strlen(text), font, 0, LV_TXT_FLAG_NONE);
}

static void layoutCompactInfoRow()
{
    const uint32_t uptimeDays = nas_uptime_seconds / 86400UL;
    const bool compactLongUptime = uptimeDays >= 1000UL;
    const bool threeDigitUptime = uptimeDays >= 100UL;
    const lv_font_t *uptimeValueFont = compactLongUptime
                                              ? &lv_font_montserrat_16
                                              : (threeDigitUptime ? &lv_font_montserrat_22 : &lv_font_montserrat_22);
    const lv_coord_t uptimeValueY = compactLongUptime ? 146 : (threeDigitUptime ? 141 : 143);
    lv_obj_set_style_local_text_font(uptime_value_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, uptimeValueFont);

    const lv_coord_t valueWidth = labelTextWidth(uptime_value_label, uptimeValueFont);
    const lv_coord_t unitWidth = labelTextWidth(uptime_unit_label, &lv_font_montserrat_16);
    const lv_coord_t unitGap = unitWidth > 0 ? 2 : 1;
    const lv_coord_t uptimeUnitX = 58 - unitWidth;
    lv_coord_t uptimeValueX = uptimeUnitX - unitGap - valueWidth;
    if (uptimeValueX < 6)
        uptimeValueX = 6;

    lv_obj_set_width(uptime_value_label, valueWidth);
    lv_label_set_align(uptime_value_label, LV_LABEL_ALIGN_LEFT);
    lv_obj_set_pos(uptime_value_label, uptimeValueX, uptimeValueY);
    lv_obj_set_pos(uptime_unit_label, uptimeUnitX, 152);

    const lv_coord_t tempWidth = labelTextWidth(temp_value_label, &lv_font_montserrat_22);
    const lv_coord_t tempUnitWidth = labelTextWidth(temp_unit_label, &lv_font_montserrat_16);
    const lv_coord_t tempUnitX = 112 - tempUnitWidth;
    const lv_coord_t tempX = tempUnitX - tempWidth - 1;
    lv_obj_set_width(temp_value_label, tempWidth);
    lv_label_set_align(temp_value_label, LV_LABEL_ALIGN_LEFT);
    lv_obj_set_pos(temp_value_label, tempX, 143);
    lv_obj_set_pos(temp_unit_label, tempUnitX, 152);
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
        ++nasFailures;
        if (nasFailures == 30)
            WiFi.reconnect();
        if (nasFailures >= 300)
            ESP.restart();
        return;
    }
    updateChartRange();
    lv_chart_refresh(chart);

    updateNetworkInfoLabel();

    lv_bar_set_value(cpu_bar, cpu_usage, LV_ANIM_OFF);
    lv_label_set_text_fmt(cpu_value_label, "%.0f", cpu_usage);

    lv_bar_set_value(gpu_bar, gpu_usage, LV_ANIM_OFF);
    lv_label_set_text_fmt(gpu_value_label, "%.0f", gpu_usage);

    lv_bar_set_value(mem_bar, mem_usage, LV_ANIM_OFF);
    lv_label_set_text_fmt(mem_value_label, "%.0f", mem_usage);

    lv_label_set_text_fmt(temp_value_label, "%.0f", temp_value);

    const uint32_t displayUptimeSeconds = nas_uptime_seconds;
    if (displayUptimeSeconds >= 86400UL)
    {
        const uint32_t days = displayUptimeSeconds / 86400UL;
        lv_label_set_text(uptime_unit_label, "D");
        if (days < 1000UL)
            lv_label_set_text_fmt(uptime_value_label, "%lu", static_cast<unsigned long>(days));
        else if (days < 10000UL)
        {
            lv_label_set_text_fmt(uptime_value_label, "%.1fK", days / 1000.0);
            lv_label_set_text(uptime_unit_label, "");
        }
        else if (days < 1000000UL)
        {
            lv_label_set_text_fmt(uptime_value_label, "%luK", static_cast<unsigned long>(days / 1000UL));
            lv_label_set_text(uptime_unit_label, "");
        }
        else if (days < 10000000UL)
        {
            lv_label_set_text_fmt(uptime_value_label, "%.1fM", days / 1000000.0);
            lv_label_set_text(uptime_unit_label, "");
        }
        else
        {
            lv_label_set_text_fmt(uptime_value_label, "%luM", static_cast<unsigned long>(days / 1000000UL));
            lv_label_set_text(uptime_unit_label, "");
        }
    }
    else if (displayUptimeSeconds >= 3600UL)
    {
        lv_label_set_text_fmt(uptime_value_label, "%lu", static_cast<unsigned long>(displayUptimeSeconds / 3600UL));
        lv_label_set_text(uptime_unit_label, "H");
    }
    else
    {
        lv_label_set_text_fmt(uptime_value_label, "%lu", static_cast<unsigned long>(displayUptimeSeconds / 60UL));
        lv_label_set_text(uptime_unit_label, "M");
    }
    layoutCompactInfoRow();
    const lv_color_t tempColor = temp_value < 60.0
                                     ? lv_color_hex(0x20c864)
                                     : (temp_value <= 68.0 ? LV_COLOR_WHITE : lv_color_hex(0xd74747));
    lv_obj_set_style_local_text_color(temp_value_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, tempColor);
    lv_obj_set_style_local_text_color(temp_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, tempColor);

    if (ESP.getFreeHeap() < 7000)
        ++lowHeapSamples;
    else
        lowHeapSamples = 0;
    if (lowHeapSamples >= 10)
        ESP.restart();

    // 测试内存泄漏
    Serial.print("⚠ Left Memory:");
    Serial.println(ESP.getFreeHeap());
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

    lv_obj_t *bg;
    bg = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(bg, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(bg, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
    lv_color_t bg_color = lv_color_hex(0x7381a2);
    // bg_color = lv_color_hex(0xecdd5c);
    lv_obj_set_style_local_bg_color(bg, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, bg_color);
    lv_obj_set_size(bg, LV_HOR_RES_MAX, LV_VER_RES_MAX);

    lv_obj_t *cont = lv_cont_create(monitor_page, NULL);
    lv_obj_set_auto_realign(cont, true); /*Auto realign when the size changes*/
    // lv_obj_align_origo(cont, NULL, LV_ALIGN_IN_TOP_LEFT, 120, 35);  /*This parametrs will be sued when realigned*/
    // lv_color_t cont_color = lv_color_hex(0x1a1d25);
    lv_color_t cont_color = lv_color_hex(0x081418);
    lv_obj_set_width(cont, 230);
    lv_obj_set_height(cont, 120);
    lv_obj_set_pos(cont, 5, 5);

    lv_cont_set_fit(cont, LV_FIT_TIGHT);
    lv_cont_set_layout(cont, LV_LAYOUT_COLUMN_MID);
    lv_obj_set_style_local_border_color(cont, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, cont_color);
    lv_obj_set_style_local_bg_color(cont, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, cont_color);
    lv_obj_set_style_local_radius(cont, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, 24);

    // Upload & Download Symbol
    static lv_style_t iconfont;
    lv_style_init(&iconfont);
    lv_style_set_text_font(&iconfont, LV_STATE_DEFAULT, &lv_font_montserrat_16);

    upload_label = lv_label_create(monitor_page, NULL);
    lv_obj_add_style(upload_label, LV_LABEL_PART_MAIN, &iconfont);
    lv_label_set_text(upload_label, LV_SYMBOL_UPLOAD);
    lv_color_t speed_label_color = lv_color_hex(0x838a99);
    lv_obj_set_style_local_text_color(upload_label, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_RED);
    lv_obj_set_pos(upload_label, 10, 18);

    download_label = lv_label_create(monitor_page, NULL);
    lv_obj_add_style(download_label, LV_LABEL_PART_MAIN, &iconfont);
    lv_label_set_text(download_label, LV_SYMBOL_DOWNLOAD);
    speed_label_color = lv_color_hex(0x838a99);
    lv_obj_set_style_local_text_color(download_label, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, lv_color_hex(0x278fbd));
    lv_obj_set_pos(download_label, 120, 18);

    // Upload & Download Speed Display
    static lv_style_t font_22;
    lv_style_init(&font_22);
    // lv_style_set_text_font(&font_22, LV_STATE_DEFAULT, &lv_font_montserrat_22);
    lv_style_set_text_font(&font_22, LV_STATE_DEFAULT, &lv_font_montserrat_22);

    up_speed_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(up_speed_label, "56.78");
    lv_obj_add_style(up_speed_label, LV_LABEL_PART_MAIN, &font_22);
    lv_obj_set_style_local_text_color(up_speed_label, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(up_speed_label, 30, 15);

    up_speed_unit_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(up_speed_unit_label, "K/S");
    lv_obj_set_style_local_text_color(up_speed_unit_label, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, speed_label_color);
    lv_obj_set_pos(up_speed_unit_label, 90, 18);

    down_speed_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(down_speed_label, "12.34");
    lv_obj_add_style(down_speed_label, LV_LABEL_PART_MAIN, &font_22);
    lv_obj_set_style_local_text_color(down_speed_label, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(down_speed_label, 142, 15);

    down_speed_unit_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(down_speed_unit_label, "M/S");
    lv_obj_set_style_local_text_color(down_speed_unit_label, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, speed_label_color);
    lv_obj_set_pos(down_speed_unit_label, 202, 18);

    // 绘制曲线图
    /*Create a chart*/
    chart = lv_chart_create(monitor_page, NULL);
    lv_obj_set_size(chart, 220, 70);
    lv_obj_align(chart, NULL, LV_ALIGN_CENTER, 0, -40);
    lv_obj_set_style_local_radius(chart, LV_CHART_PART_BG, LV_STATE_DEFAULT, 16);
    lv_chart_set_type(chart, LV_CHART_TYPE_LINE); /*Show lines and points too*/
    lv_chart_set_range(chart, 0, 4096);
    lv_chart_set_point_count(chart, 10); // 设置显示点数
    lv_chart_set_update_mode(chart, LV_CHART_UPDATE_MODE_SHIFT);

    /*Add a faded are effect*/
    lv_obj_set_style_local_bg_opa(chart, LV_CHART_PART_SERIES, LV_STATE_DEFAULT, LV_OPA_50); /*Max. opa.*/
    lv_obj_set_style_local_bg_grad_dir(chart, LV_CHART_PART_SERIES, LV_STATE_DEFAULT, LV_GRAD_DIR_VER);
    lv_obj_set_style_local_bg_main_stop(chart, LV_CHART_PART_SERIES, LV_STATE_DEFAULT, 255); /*Max opa on the top*/
    lv_obj_set_style_local_bg_grad_stop(chart, LV_CHART_PART_SERIES, LV_STATE_DEFAULT, 0);   /*Transparent on the bottom*/

    /*Add two data series*/
    ser1 = lv_chart_add_series(chart, LV_COLOR_RED);
    ser2 = lv_chart_add_series(chart, lv_color_hex(0x278fbd));

    // /*Directly set points on 'ser2'*/
    lv_chart_set_points(chart, ser2, download_serise);
    lv_chart_set_points(chart, ser1, upload_serise);

    lv_chart_refresh(chart); /*Required after direct set*/

    // 下半屏右侧：三行紧凑指标，适配 240x240 像素屏幕。
    static lv_style_t metric_value_font;
    lv_style_init(&metric_value_font);
    lv_style_set_text_font(&metric_value_font, LV_STATE_DEFAULT, &lv_font_montserrat_22);

    static lv_style_t metric_title_font;
    lv_style_init(&metric_title_font);
    lv_style_set_text_font(&metric_title_font, LV_STATE_DEFAULT, &lv_font_montserrat_16);

    static lv_style_t metric_unit_font;
    lv_style_init(&metric_unit_font);
    lv_style_set_text_font(&metric_unit_font, LV_STATE_DEFAULT, &lv_font_montserrat_16);

    const lv_color_t cpu_color = lv_color_hex(0xd74747);
    const lv_color_t gpu_color = lv_color_hex(0x278fbd);
    const lv_color_t mem_color = lv_color_hex(0x20c864);
    const lv_color_t track_color = lv_color_hex(0x1e3644);

    lv_obj_t *cpu_title = lv_label_create(monitor_page, NULL);
    lv_label_set_text(cpu_title, "CPU");
    lv_obj_add_style(cpu_title, LV_LABEL_PART_MAIN, &metric_title_font);
    lv_obj_set_style_local_text_color(cpu_title, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(cpu_title, 124, 137);

    cpu_value_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(cpu_value_label, "0");
    lv_obj_add_style(cpu_value_label, LV_LABEL_PART_MAIN, &metric_value_font);
    lv_obj_set_style_local_text_color(cpu_value_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_long_mode(cpu_value_label, LV_LABEL_LONG_CROP);
    lv_obj_set_width(cpu_value_label, 51);
    lv_label_set_align(cpu_value_label, LV_LABEL_ALIGN_RIGHT);
    lv_obj_set_pos(cpu_value_label, 167, 133);

    cpu_percent_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(cpu_percent_label, "%");
    lv_obj_add_style(cpu_percent_label, LV_LABEL_PART_MAIN, &metric_unit_font);
    lv_obj_set_style_local_text_color(cpu_percent_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(cpu_percent_label, 218, 139);

    cpu_bar = lv_bar_create(monitor_page, NULL);
    styleMetricBar(cpu_bar, cpu_color, track_color);
    lv_obj_set_pos(cpu_bar, 124, 154);

    lv_obj_t *gpu_title = lv_label_create(monitor_page, NULL);
    lv_label_set_text(gpu_title, "GPU");
    lv_obj_add_style(gpu_title, LV_LABEL_PART_MAIN, &metric_title_font);
    lv_obj_set_style_local_text_color(gpu_title, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(gpu_title, 124, 168);

    gpu_value_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(gpu_value_label, "0");
    lv_obj_add_style(gpu_value_label, LV_LABEL_PART_MAIN, &metric_value_font);
    lv_obj_set_style_local_text_color(gpu_value_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_long_mode(gpu_value_label, LV_LABEL_LONG_CROP);
    lv_obj_set_width(gpu_value_label, 51);
    lv_label_set_align(gpu_value_label, LV_LABEL_ALIGN_RIGHT);
    lv_obj_set_pos(gpu_value_label, 167, 164);

    gpu_percent_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(gpu_percent_label, "%");
    lv_obj_add_style(gpu_percent_label, LV_LABEL_PART_MAIN, &metric_unit_font);
    lv_obj_set_style_local_text_color(gpu_percent_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(gpu_percent_label, 218, 170);

    gpu_bar = lv_bar_create(monitor_page, NULL);
    styleMetricBar(gpu_bar, gpu_color, track_color);
    lv_obj_set_pos(gpu_bar, 124, 185);

    lv_obj_t *mem_title = lv_label_create(monitor_page, NULL);
    lv_label_set_text(mem_title, "MEM");
    lv_obj_add_style(mem_title, LV_LABEL_PART_MAIN, &metric_title_font);
    lv_obj_set_style_local_text_color(mem_title, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(mem_title, 124, 199);

    mem_value_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(mem_value_label, "0");
    lv_obj_add_style(mem_value_label, LV_LABEL_PART_MAIN, &metric_value_font);
    lv_obj_set_style_local_text_color(mem_value_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_long_mode(mem_value_label, LV_LABEL_LONG_CROP);
    lv_obj_set_width(mem_value_label, 51);
    lv_label_set_align(mem_value_label, LV_LABEL_ALIGN_RIGHT);
    lv_obj_set_pos(mem_value_label, 167, 195);

    mem_percent_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(mem_percent_label, "%");
    lv_obj_add_style(mem_percent_label, LV_LABEL_PART_MAIN, &metric_unit_font);
    lv_obj_set_style_local_text_color(mem_percent_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(mem_percent_label, 218, 201);

    mem_bar = lv_bar_create(monitor_page, NULL);
    styleMetricBar(mem_bar, mem_color, track_color);
    lv_obj_set_pos(mem_bar, 124, 216);

    // 下半屏左侧：简洁显示温度和 24 小时制北京时间。
    lv_obj_t *info_panel = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(info_panel, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(info_panel, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_100);
    lv_obj_set_style_local_bg_color(info_panel, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, cont_color);
    lv_obj_set_style_local_border_width(info_panel, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_radius(info_panel, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, 24);
    lv_obj_set_size(info_panel, 110, 105);
    lv_obj_set_pos(info_panel, 5, 128);

    lv_obj_t *info_divider = lv_obj_create(monitor_page, NULL);
    lv_obj_clean_style_list(info_divider, LV_OBJ_PART_MAIN);
    lv_obj_set_style_local_bg_opa(info_divider, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, LV_OPA_40);
    lv_obj_set_style_local_bg_color(info_divider, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, speed_label_color);
    lv_obj_set_style_local_border_width(info_divider, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, 0);
    lv_obj_set_style_local_radius(info_divider, LV_OBJ_PART_MAIN, LV_STATE_DEFAULT, 0);
    lv_obj_set_size(info_divider, 110, 1);
    lv_obj_set_pos(info_divider, 5, 166);

    static lv_style_t time_font;
    lv_style_init(&time_font);
    lv_style_set_text_font(&time_font, LV_STATE_DEFAULT, &lv_font_montserrat_46);
    lv_style_set_text_letter_space(&time_font, LV_STATE_DEFAULT, -1);

    static lv_style_t uptime_font;
    lv_style_init(&uptime_font);
    lv_style_set_text_font(&uptime_font, LV_STATE_DEFAULT, &lv_font_montserrat_16);

    static lv_style_t temperature_value_font;
    lv_style_init(&temperature_value_font);
    lv_style_set_text_font(&temperature_value_font, LV_STATE_DEFAULT, &lv_font_montserrat_22);

    uptime_value_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(uptime_value_label, "--");
    lv_obj_add_style(uptime_value_label, LV_LABEL_PART_MAIN, &metric_value_font);
    lv_obj_set_style_local_text_color(uptime_value_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_long_mode(uptime_value_label, LV_LABEL_LONG_CROP);
    lv_obj_set_width(uptime_value_label, 43);
    lv_label_set_align(uptime_value_label, LV_LABEL_ALIGN_RIGHT);
    lv_obj_set_pos(uptime_value_label, 7, 144);

    uptime_unit_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(uptime_unit_label, "D");
    lv_obj_add_style(uptime_unit_label, LV_LABEL_PART_MAIN, &uptime_font);
    lv_obj_set_style_local_text_color(uptime_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, speed_label_color);
    lv_obj_set_pos(uptime_unit_label, 50, 152);

    temp_value_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(temp_value_label, "--");
    lv_obj_add_style(temp_value_label, LV_LABEL_PART_MAIN, &temperature_value_font);
    lv_obj_set_style_local_text_color(temp_value_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_long_mode(temp_value_label, LV_LABEL_LONG_CROP);
    lv_obj_set_width(temp_value_label, 35);
    lv_label_set_align(temp_value_label, LV_LABEL_ALIGN_RIGHT);
    lv_obj_set_pos(temp_value_label, 59, 143);

    temp_unit_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(temp_unit_label, "°C");
    lv_obj_add_style(temp_unit_label, LV_LABEL_PART_MAIN, &metric_unit_font);
    lv_obj_set_style_local_text_color(temp_unit_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_obj_set_pos(temp_unit_label, 95, 152);

    time_label = lv_label_create(monitor_page, NULL);
    lv_label_set_text(time_label, "00:00");
    lv_obj_add_style(time_label, LV_LABEL_PART_MAIN, &time_font);
    lv_obj_set_style_local_text_color(time_label, LV_LABEL_PART_MAIN, LV_STATE_DEFAULT, LV_COLOR_WHITE);
    lv_label_set_recolor(time_label, true);
    lv_label_set_long_mode(time_label, LV_LABEL_LONG_CROP);
    lv_obj_set_width(time_label, 110);
    lv_label_set_align(time_label, LV_LABEL_ALIGN_CENTER);
    lv_obj_set_pos(time_label, 5, 182);

    lv_task_create(task_cb, 1000, LV_TASK_PRIO_MID, &test_data);
    lv_task_create(clock_task_cb, 1000, LV_TASK_PRIO_MID, NULL);
}

void loop()
{
    handleConfigPortal();
    lv_task_handler(); /* let the GUI do its work */
}
