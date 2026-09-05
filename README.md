# Router Monitor for FNOS NAS

![Router Monitor for FNOS NAS 项目封面](images/project-cover.png)

一台基于 ESP8266 和 240 × 240 彩屏的 NAS 桌面监控小电视。设备每秒获取完整 NAS 状态，同时每 200 毫秒获取轻量网络计数，显示网络与硬盘读写速率、CPU/GPU/内存占用、时间及四页轮播信息。

本项目基于 [404SynapseNotFound/routermonitor](https://github.com/404SynapseNotFound/routermonitor) 修改，数据源由 Netdata 改为随仓库提供的 NAS 状态服务，并增加了网页配网、Token 鉴权、夜间亮度和故障自动恢复。

### 实机照片

![Router Monitor 实机效果](images/routermonitor.jpeg)

## 功能

![Router Monitor 功能亮点](images/feature-overview.png)

- `/status?display=1` 每 1 秒刷新 CPU、GPU、内存、硬盘读写和运行时间；温度每 5 秒、容量每 30 秒、滚动流量每 5 秒采样
- `/net` 每 200 毫秒更新红色上传、蓝色下载折线，实时网速数字约每 1 秒更新；请求失败后退避重试，恢复后回到目标周期
- 实时网速使用固定 42px 窄体数字，`HH:MM:SS` 使用固定 42px 原比例数字；等宽数字减少跳动，保留 5px 真黑边
- 硬盘读写速率每 1 秒刷新
- 近 24 小时流量、开机时间、CPU/最高硬盘温度、总容量/已用容量/占用率每 5 秒横向滑动轮播
- NAS 每 5 秒独立采样并持久保存流量，屏幕断电不影响采集
- 时间按 UTC+8 显示，附星期和日期，屏幕不显示时区字样
- 配网 AP 使用随机 WPA2 密码；局域网配置使用独立管理密码、Digest 认证与 CSRF 校验
- Wi-Fi、NAS 地址和 Token 只保存在设备 LittleFS，不写入固件源码
- 夜间自动降低亮度
- Wi-Fi 恢复后自动显示主页、校时并关闭临时 AP；NAS 故障退避重试，过期指标显示 `--`
- 进入主界面后释放开机动画对象，降低运行时内存占用

## 仓库结构

```text
.
├─ include/TFT_eSPI_Setup.h  # 屏幕驱动与引脚
├─ src/                      # ESP8266 固件和字体资源
├─ nas-docker/               # NAS 状态 API、Dockerfile、Compose
├─ platformio.ini            # PlatformIO 构建配置
└─ images/                   # 项目图片
```

## 硬件

两种配置都使用 ESP8266 / NodeMCU v2 和 4 MB Flash，界面为 240 × 240：

| PlatformIO 环境 | 屏幕控制器 | CS | SPI 时钟 | 验证状态 |
| --- | --- | --- | --- | --- |
| `nodemcuv2`（默认） | ST7789，240 × 240 | 无，`-1` | 40 MHz | 已实机烧录；240 秒、47 次滑动、延迟联网及 NAS 故障恢复通过 |
| `nodemcuv2_ili9341` | ILI9341，240 × 240 界面区域 | D8 | 27 MHz | 保留原仓库配置，编译验证，未实机验收 |

公共接线：DC=D3、RST=D4、背光=D1、MOSI=D7、SCLK=D5。ST7789 配置与旧 `sd2` 项目实际选中的 `Setup24_ST7789.h` 一致。ILI9341 的原生面板通常为 240 × 320，本项目保持原有 240 × 240 界面，不会自动拉伸。

固件不自动识别屏幕，烧录时必须选择对应环境。不同批次可能使用其他接线，按需修改 `include/TFT_eSPI_Setup.h` 后先运行 `pio run -e nodemcuv2 -e nodemcuv2_ili9341 --target clean` 再编译，避免复用旧驱动缓存。两套默认背光均按 D1 低电平有效处理；其他背光电路需相应调整。

## 架构与部署

![Router Monitor 架构与部署](images/architecture-setup.png)

## 1. 部署 NAS 服务

NAS 需安装 Docker Compose。进入 `nas-docker` 后执行：

```bash
cp .env.example .env
```

编辑 `.env`：

- `NAS_STATUS_IFACE`：默认 `physical`，合计宿主机全部物理网卡；也可设为 `auto` 或具体接口名
- `NAS_STATUS_PORT`：对局域网开放的端口，默认 `18199`
- `NAS_STATUS_TOKEN`：长随机 Token，可用 `openssl rand -hex 32` 生成

启动并测试：

```bash
docker compose up -d --build
docker compose logs -f nas-status
curl http://NAS局域网IP:18199/health
curl -H "Authorization: Bearer 你的Token" http://NAS局域网IP:18199/status
```

详细说明见 [nas-docker/README.md](nas-docker/README.md)。不建议把该端口直接暴露到公网；优先在可信局域网、VPN 或防火墙白名单内使用。

## 2. 编译和烧录固件

安装 [Visual Studio Code](https://code.visualstudio.com/) 和 PlatformIO 扩展，打开仓库根目录。连接 ESP8266 后运行 PlatformIO 的 Upload；或使用命令行：

默认 ST7789：

```bash
pio run -e nodemcuv2
pio run -e nodemcuv2 --target upload
pio device monitor -e nodemcuv2
```

ILI9341：

```bash
pio run -e nodemcuv2_ili9341
pio run -e nodemcuv2_ili9341 --target upload
pio device monitor -e nodemcuv2_ili9341
```

仅验证两种配置能否编译（不烧录）：

```bash
pio run -e nodemcuv2 -e nodemcuv2_ili9341
```

不指定 `-e` 时默认使用 ST7789。两种环境分别输出到 `.pio/build/nodemcuv2/` 和 `.pio/build/nodemcuv2_ili9341/`，共用相同的 LittleFS 配置布局；切换程序固件不需要重新填写 Wi-Fi 和 NAS 参数。

默认调试串口和烧录速率为 115200（当前 CH340 设备已验证稳定）。界面保留内置 Montserrat 12px 文本字体，数字使用 `src/DisplayFonts.c` 中的 22/42px 字体子集；可运行 `python3 scripts/generate_fonts.py` 从已安装的 LVGL 原始位图重新生成。网速数字在生成时固定为原宽度的 75%，时间保持原比例，所有数字采用固定步进，不在运行中缩放。R/W 和上下行箭头使用固定线条。显示缓冲为 5 行。启用 ESP8266 Core 的 `NON32XFER_HANDLER`，支持 LVGL 对 Flash 字形的字节读取，避免 Exception (3)。

### 240 × 240 主屏布局

| 区域 | 像素范围 | 排版 |
| --- | --- | --- |
| 真黑边 | x/y=0–4、235–239 | 内容严格限制在 x/y=5–234 |
| 实时上传/下载 | x=5–116、123–234，y=5–50 | 数字固定 42px 窄体、70px 槽；箭头 10×14、单位 12px，基线固定 |
| 网络趋势 | x=10，y=51，220 × 18 | 红色上传、蓝色下载，每 200ms 更新，显示近 10 秒 |
| 硬盘读/写 | x=5–116、123–234，y=69–93 | 数字固定 22px、53px 槽；R/W 固定 16 × 16、单位 12px |
| 四页轮播 | x=5–234，y=95–136 | 内容 y=96–130、标题 12px、数值 22px；页点 y=132–134 |
| 日期与时间 | x=5–234，y=138–184 | 左侧星期/日期 12px；`HH:MM:SS` 固定 42px、字间距 -2px |
| CPU / GPU / MEM | x=5–234，y=186–234 | 三列各 72px；百分比 22px、标题 12px、底部 3px 进度条 |

网速与硬盘速率只在文本变化时更新，字号、图标和单位位置固定。实时速率保留最多三位有效数字，例如 `99.4 MB/s`、`100 MB/s`、`140 MB/s`、`999 MB/s`、`1.0 GB/s`；采用十进制单位。累计量保留一位小数，容量页使用三位有效数字与短单位。全部 86400 个时间组合实际宽度均为 174px，不超过 182px 时间槽。

![新界面实际 LVGL 渲染，示例数据](images/ui-optimized.png)

轮播复用一个 230 × 35 内容层，旧页 200ms 缓入滑出、新页 200ms 缓出滑入；页点固定，移动期间保持内容不变，完成后显示最新快照。只在切页时设置布局和样式，普通刷新只替换变化的文字。温度页采用上下对齐的标题与数值；容量页显示 `TOTAL/USED/USAGE`。数据过期时 CPU/GPU/MEM、硬盘和累计量统一显示 `--`，不保留看似正常的旧占用率。

故障注入验收可临时定义 `MONITOR_WIFI_RECOVERY_TEST`：只在该测试固件中延迟正确 Wi-Fi 凭据 20 秒，不修改已保存配置；交付固件不启用此宏。

USB 常供电时关闭 Wi-Fi 省电等待；字形使用 512B 共用缓存，从 Flash 按字形复制后绘制，避免逐像素触发慢速字节读取。缓存前后五张 LVGL 画面逐字节一致。

网络层使用 ESP8266 Core 自带 lwIP 异步 DNS/TCP 回调，`loop()` 按字节预算推进请求，不在 LVGL 回调里等待连接或响应。HTTP 响应限制头部/正文长度并验证完整性。服务器提供固定展示投影，避免温度传感器数量增加撑满设备 JSON 内存。`/net` 的 `counter_epoch` 改变时重建基线，不把单卡回退掩盖成合计增长。

可运行以下检查：

```bash
python3 -m unittest discover -s nas-docker -p 'test_*.py' -v
python3 tests/check_net_rate.py
python3 tests/check_layout.py
python3 tests/check_config_portal.py
python3 tests/check_config_portal_runtime.py
python3 tests/check_async_http.py
python3 tests/render_ui.py /tmp/routermonitor-ui
```

布局测试覆盖全部可输出数字组合；渲染检查编译真实 `main.ino` UI 与主机 LVGL，输出四页及极限数字画面，并逐像素验证 5px 黑边。主机仅替换设备 I/O，因 64 位指针使用更大的 LVGL 内存池，不能代替实机内存或屏幕验收。串口 `HTTP/PERF/STATE` 诊断分别记录请求、系统堆/最大连续块/LVGL 独立池/动画帧间隔与有效数据状态。当前双屏固件静态 RAM 为 63064B（77.0%，原版 68124B）；ST7789 240 秒实测最低空闲堆 12632B、LVGL 池空闲 4100B，47 次轮播未重启。10 秒 NAS 无响应期间依旧更新动画，恢复后无需手动重启；该轮测试包含预期的 5 次网络及 3 次状态超时，正常阶段未新增失败。动画 706 个记录间隔中 6 个超过 50ms，最大 65ms，因此不宣称恒定 60FPS。ILI9341 仍仅编译验证。

流量统计不会补造部署前的历史；默认合计物理网卡经过网线的局域网传输、广播与协议开销，虚拟网卡不重复累加。SQLite 每 5 秒持久采样，窗口边界按区间比例估算；升级保留旧区间，无法核实的迁移边界不补算。详见服务端说明。

## 3. 首次配网

1. 小电视超过 15 秒无法连接已保存 Wi-Fi 时，会建立 `RouterMonitor-XXXXXX` 热点；屏幕和本地串口显示本次随机 AP 密码。
2. 连接该热点并访问 `http://192.168.4.1/`，填写 Wi-Fi、NAS 地址、端口及 Token。
3. 保存后设备自动重启。AP 首配依赖随机 WPA2 密码；从局域网访问配置页面需登录 `admin`。
4. 独立管理密码在首次初始化或旧配置迁移时随机生成，只在本地串口首次输出；它不是 Wi-Fi 密码或 NAS Token。请保存这条输出。管理页不会回显原有敏感配置。
5. Wi-Fi 恢复后关闭临时 AP/DNS，局域网配置仍可通过设备 IP 访问。路由器启动较慢时无需手工重启屏幕。

配置先写临时文件、完整读回校验，再通过 LittleFS rename 替换，保留已验证的备份以恢复中断保存。挂载失败不会自动格式化；仅能确认整个分区均为擦除态时才执行首次初始化。文件系统已损坏时保留恢复提示，不自动擦除数据。

亮度可在配置页调整：当前电路 PWM 数值越小越亮，0 最亮，255 关闭；默认白天 180、夜间 235，夜间时段为北京时间 23:00–07:00。

配置页不会回显已保存的 Wi-Fi 密码或 Token。敏感配置保存在 ESP8266 的 LittleFS 中；转让设备前应擦除 Flash。

## 数据与兼容性

- CPU、内存、网络和运行时间来自宿主机只读挂载的 `/proc`、`/sys`
- 默认合计所有物理网卡；也可选择默认路由或明确指定 bridge、bond、OVS 等逻辑接口
- 硬盘读写速率来自全部物理块设备的 Linux 计数器；RAID 会体现底层硬盘的实际 I/O
- 总容量与已用容量来自显式只读挂载的 `/vol1`、`/vol2`，并按文件系统去重
- CPU 温度综合读取 thermal zone 与 hwmon，识别 Intel `coretemp`、AMD `k10temp/zenpower` 及常见 ARM CPU thermal
- 最高硬盘温度合并内核 `drivetemp`/`nvme` hwmon 与 smartd 的新鲜 ATA 日志
- GPU 使用率支持 Intel i915 debugfs 和 AMDGPU sysfs；NVIDIA 或未暴露指标的 GPU 稳定降级为 0%
- 固件使用流式 JSON 过滤，避免在 ESP8266 内存中保存完整响应

飞牛 NAS 本质上运行 Linux，但不同机型使用的内核、CPU、GPU 和传感器驱动并不完全相同，因此无法承诺每台设备都具备全部指标。服务会保证 CPU、内存、网络和开机时间尽可能通用；温度和 GPU 属于硬件/驱动可选项，取不到时不会影响其他数据显示。

## 安全说明

- `.env`、Token、固件二进制和 PlatformIO 缓存已被 `.gitignore` 排除
- API 启动时强制要求 Token
- Compose 仅只读挂载系统指标目录、smartd 日志和明确配置的 NAS 数据卷根目录，不挂载 Docker socket
- HTTP Token 在网络中不是加密传输；不要将服务直接映射到互联网
- 已经公开过的 Token 应立即轮换，仅从 Git 历史删除字符串并不等于撤销泄露

## 许可证与致谢

本项目是 `404SynapseNotFound/routermonitor` 的衍生版本，继续使用 [GNU GPL v3](LICENSE)。界面字体及数字子集均派生自 LVGL 依赖中的 Montserrat 位图，不使用 Windows 系统字体。感谢原作者及 LVGL、TFT_eSPI、ArduinoJson、PlatformIO 等开源项目。
