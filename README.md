# Router Monitor for FNOS NAS

![Router Monitor for FNOS NAS 项目封面](images/project-cover.png)

一台基于 ESP8266 和 240 × 240 彩屏的 NAS 桌面监控小电视。设备每秒获取完整 NAS 状态，同时每 200 毫秒获取轻量网络计数，显示网络与硬盘读写速率、CPU/GPU/内存占用、时间及四页轮播信息。

本项目基于 [404SynapseNotFound/routermonitor](https://github.com/404SynapseNotFound/routermonitor) 修改，数据源由 Netdata 改为随仓库提供的 NAS 状态服务，并增加了网页配网、Token 鉴权、夜间亮度和故障自动恢复。

### 实机照片

![Router Monitor 实机效果](images/routermonitor.jpeg)

## 功能

![Router Monitor 功能亮点](images/feature-overview.png)

- `/status` 每 1 秒刷新 CPU、GPU、内存、硬盘读写、温度、容量、运行时间和近 24 小时统计
- `/net` 每 200 毫秒更新红色上传、蓝色下载折线，实时网速数字约每 1 秒更新；请求失败后每 1 秒重试，成功即恢复
- 实时网速和 `HH:MM:SS` 使用 42px 大字，保留 5px 真黑边
- 硬盘读写速率每 1 秒刷新
- 近 24 小时流量、开机时间、CPU/最高硬盘温度、总容量/已用容量/占用率每 5 秒横向滑动轮播
- NAS 每 5 秒独立采样并持久保存流量，屏幕断电不影响采集
- 时间按 UTC+8 显示，附星期和日期，屏幕不显示时区字样
- 首次启动 AP 配网，之后可通过设备局域网 IP 修改配置
- Wi-Fi、NAS 地址和 Token 只保存在设备 LittleFS，不写入固件源码
- 夜间自动降低亮度
- Wi-Fi/NAS 连续失败自动重连，严重异常自动重启
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
| `nodemcuv2`（默认） | ST7789，240 × 240 | 无，`-1` | 40 MHz | 已实机烧录；180 秒内 800 次稳定网络请求和 34 次滑动通过 |
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

默认调试串口和烧录速率为 115200（当前 CH340 设备已验证稳定）。界面只启用 LVGL 内置 Montserrat 12/22/42 字体；硬盘 R/W 和轮播箭头使用固定线条绘制，因此无需为 16px 图标额外加载一套字体。显示缓冲为 5 行，以减少 RAM 占用。启用 ESP8266 Core 的 `NON32XFER_HANDLER`，支持 LVGL 对 Flash 字形的字节读取，避免 Exception (3)。

### 240 × 240 主屏布局

| 区域 | 像素范围 | 排版 |
| --- | --- | --- |
| 真黑边 | x/y=0–4、235–239 | 内容严格限制在 x/y=5–234 |
| 实时上传/下载 | x=5–116、123–234，y=5–50 | 数字固定 42px；箭头和单位固定 12px，三者使用固定槽位 |
| 网络趋势 | x=10，y=51，220 × 18 | 红色上传、蓝色下载，每 200ms 更新，显示近 10 秒 |
| 硬盘读/写 | x=5–116、123–234，y=69–93 | 数字固定 22px；R/W 固定 16 × 16、单位 12px，每秒更新 |
| 四页轮播 | x=5–234，y=95–136 | 内容 y=96–130、标题 12px、数值 22px；页点 y=132–134 |
| 日期与时间 | x=5–234，y=138–184 | 左侧星期/日期 12px；`HH:MM:SS` 固定 42px、字间距 -2px |
| CPU / GPU / MEM | x=5–234，y=186–234 | 三列各 72px；百分比 22px、标题 12px、底部 3px 进度条 |

网速与硬盘速率刷新时只更新文本，不会改变字号或重算图标、数字、单位的位置。速率在数值达到 99.5 时提前换到下一级十进制单位（1 GB = 1000 MB），从而让 42px 数字始终留在固定槽内。全部 86400 个 `HH:MM:SS` 组合在 42px 字体下不超过 182px。可运行 `python3 tests/check_layout.py` 检查真实字体字宽、固定槽位、动画端点、5px 边框和格式化边界；先编译一次以安装 LVGL 字体源码。

轮播使用一个 230 × 35 内容层：旧页在 200ms 内滑出，更新复用对象后新页在 200ms 内滑入，固定页点不移动，也不会阻塞 200ms 网络任务。四页依次显示 `LAST 24H` 上传/下载、`UPTIME` 天/小时、CPU/最高硬盘温度、`TOTAL/USED/USAGE`。流量统计不会补造服务部署前的历史，采样中断或 NAS 离线时未知值显示 `--`。默认统计全部物理网卡，包含经过网线的局域网传输、广播和协议开销，并非运营商账单流量；OVS、Docker、VPN、TUN 和回环等虚拟接口不会重复相加。SQLite 保存在 Docker 命名卷中，容器重启保留数据；采样间隔 5 秒，窗口边界按采样区间比例估算。

## 3. 首次配网

1. 小电视无法连接已保存 Wi-Fi 时，会建立 `RouterMonitor-XXXXXX` 热点。
2. 手机或电脑连接该热点，访问 `http://192.168.4.1/`。
3. 填写 Wi-Fi、NAS 局域网 IP、端口及与 `.env` 相同的 Token。
4. 保存后设备自动重启。
5. 联网后仍可通过设备的局域网 IP 打开 Router Monitor 配置页。

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

本项目是 `404SynapseNotFound/routermonitor` 的衍生版本，继续使用 [GNU GPL v3](LICENSE)。界面字体均来自 LVGL 依赖包，不再提交从 Windows 系统字体生成的位图。感谢原作者及 LVGL、TFT_eSPI、ArduinoJson、PlatformIO 等开源项目。
