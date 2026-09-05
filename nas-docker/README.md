# NAS Docker 状态服务

这是供飞牛 / Linux NAS 与 ESP8266 Router Monitor 使用的监控服务，包含原生 HTML/CSS/JavaScript 网页和无第三方 Python 依赖的 API。服务读取宿主指标，只保存监控配置及统计历史，不修改实际网卡或路由。

## 快速部署

```bash
cp .env.example .env
# 编辑 .env 后启动
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:18199/health
```

测试鉴权接口：

```bash
set -a; . ./.env; set +a
curl -H "Authorization: Bearer $NAS_STATUS_TOKEN" http://127.0.0.1:18199/status
curl -H "Authorization: Bearer $NAS_STATUS_TOKEN" http://127.0.0.1:18199/net
```

`.env` 不会被 Git 跟踪。请勿把真实 Token 写入 Compose 或截图。

## 网页控制台

![NAS 网页控制台：实时数据概览](../images/web-overview.png)

打开 `http://NAS局域网IP:18199/`，使用独立管理员密码登录。未设置 `NAS_STATUS_ADMIN_PASSWORD` 时，首次启动自动生成密码并写入数据目录 `initial-admin-password.txt`（权限 600）；仓库模板读取命令：

```bash
docker compose exec nas-status cat /data/initial-admin-password.txt
```

数据目录由 `NAS_STATUS_HISTORY_DB` 所在目录决定：仓库模板为 `/data`；旧版 `/app/data/traffic.sqlite3` 部署仍使用 `/app/data`。已有数据卷不要删除。管理员密码只在首次引导时读取环境变量，后续在网页修改；修改后删除初始密码文件并使全部网页会话失效，ESP Token 保持不变。

### 页面与网络选择

1. **数据概览**：实时指标、近 30 秒浏览曲线（初次可载入服务端最近 10 秒）、每项来源与传感器、数据年龄和不可用状态。CPU/GPU/MEM 缺失分别显示不可用。
2. **网络数据源**：物理接口优先，全部虚拟接口可展开/搜索；详情含 ifindex、MAC/永久 MAC、硬件路径、驱动、链路、IP、原始字节、丢包/错误、逐卡 24h 和观测覆盖。
3. **设备与设置**：最近显示端状态、接入地址和只读 Token、配置导入导出、脱敏诊断、密码修改。Token 只有主动点击才显示。

选择“推荐物理端口”“单个接口”或“多个接口合计”，预览后点击“应用设置”。保存时再次验证身份、可读性、拓扑和 revision；其他网页已修改时返回 409，保留本页未保存内容。别名最多 40 字符。导入先进入预览，不直接覆盖配置。

已知 bond/bridge/VLAN 与成员、父接口混选会被拒绝。无法完整验证的 Open vSwitch 混合选择也会被拒绝；可单独选 OVS 接口或选择物理端口层。网桥自身计数不等于全部转发流量。其他虚拟接口提示其统计范围，接口合计不代表逐包去重、也不等于互联网有效流量。

保存后固定设备集合，新网卡不会自动加入。物理身份结合硬件路径、永久 MAC（未提供时使用当前 MAC）和设备类型；虚拟身份使用 NAS 启动标识、ifindex、MAC 和类型。接口改名可跟随身份；身份不能可靠匹配时保留缺失成员等待重新绑定。选中成员消失或不可读，合计失效，其他卡的独立读数仍可查看；未插线且计数可读可如实显示 0。

### 宿主网络与性能

Compose 使用 `network_mode: host`，无需端口映射。每 200ms 通过只读 rtnetlink dump 批量读取网卡计数；地址、驱动、协商速率等详情约每 5 秒刷新，不为每张卡启动命令。优先核对网络命名空间 inode；飞牛限制此访问时，交叉核对宿主物理网卡的名称、ifindex 和 MAC。核对失败降级读取宿主 sysfs，并明确提示 IP / 拓扑不完整，不允许未经验证的多虚拟接口合并。不需要 privileged、Docker socket 或额外网卡管理权限。

网页概览和每卡数字约 1 秒更新，曲线目标 200ms；客户端只读取共享快照。其他磁盘、容量、温度、GPU 和 UPS 仍使用现有自动采集方式及挂载；本版未增加它们的手动来源选择，也不承诺所有 GPU / UPS 驱动均已支持。

### 持久化与迁移

- `settings.json`：版本、固定成员、别名及旧组合来源；文件原子替换并同步落盘。
- `admin.json`：PBKDF2-SHA256 密码哈希和随机盐，不保存明文管理员密码。
- `interfaces.sqlite3`：逐接口增量和有效时间段，每 5 秒批量写入，保留滚动 24h；连续零流量区间合并，仍保留覆盖时间。
- 原 `traffic.sqlite3` 继续采集升级前的固定组合。切换小屏幕范围不会停止或清空旧组合历史；切回原成员可继续展示它。

新组合的 24h 只计算所有成员共同有效的时间区间，并按边界比例分摊；不会把升级前的组合总量拆成单卡历史。每卡历史从升级采集时开始，界面明确显示覆盖时长。单卡计数回退即使随后合计增加也会中断该区间；NAS 重启、成员缺失和超过 10 秒的采样间隔不会补造数据。只改变别名或统计模式但成员不变时不清除历史。

### 网页 API 与认证

`/` 现在返回网页；读取 JSON 请使用原 `/status`。ESP `/net` 和 `/status?display=1&v=2` 保持不变，已有固件无需重刷。

| 路由 | 用途 |
| --- | --- |
| `GET /api/session`、`POST /api/login`、`POST /api/logout` | 网页会话 |
| `GET /api/overview` | 网页概览与来源 |
| `GET /api/network/interfaces` | 逐接口数据、拓扑、历史与当前选择 |
| `GET /api/network/stream` | 网页曲线，支持 since/epoch |
| `POST /api/network/preview` | 只验证和预览候选配置 |
| `GET /api/settings`、`PUT /api/settings` | 读取 / 保存（需当前 revision） |
| `GET /api/capabilities`、`GET /api/device-access` | 能力、显示端状态、只读 Token |
| `PUT /api/password` | 核对当前密码后更新管理员密码 |

管理员会话为内存中的 8 小时 HttpOnly / SameSite=Strict Cookie，服务重启后需重新登录。写请求检查同源 Origin、CSRF Token、JSON 格式和 32KiB 上限。ESP Bearer Token 不能创建管理员会话或写配置。静态资源带 CSP / nosniff，界面通过 textContent 展示数据。同一来源连续失败 8 次后暂停登录一分钟。

本次验证覆盖 20 项服务端测试、真实浏览器登录/单口保存/双口恢复/详情/手机版无横向溢出，以及重启配置持久化。拓扑冲突、计数回退和身份改名由可运行测试覆盖；未在真实 NAS 上创建 bond/VLAN 或拔插网卡。当前实机网页发现 77 个接口，默认仍为两张物理网卡；ESP 376 次连续请求无失败。

## API

`GET /health` 无需鉴权，返回 `{"status":"ok"}`。

`GET /status` 必须提供 `Authorization: Bearer <token>`。响应结构示例：

```json
{
  "time": 1788364800,
  "cpu": {"percent": 12.3},
  "gpu": {"utilization": 4.5},
  "memory": {"total": 17179869184, "used": 8589934592, "available": 8589934592, "percent": 50.0},
  "temp": [
    {"zone": "0", "type": "cpu", "temp": 51.0},
    {"zone": "hwmon2", "type": "disk", "temp": 43.0}
  ],
  "temperature_summary": {"cpu": 51.0, "disk": 43.0},
  "net": {"iface": "physical:enp2s0,enp3s0", "rx_speed": 16384, "tx_speed": 8192},
  "disk_io": {
    "devices": "physical:nvme0n1,sda,sdb",
    "read_speed": 1048576,
    "write_speed": 524288,
    "valid": true
  },
  "storage": {
    "total": 24000000000000,
    "used": 15600000000000,
    "percent": 65.0,
    "valid": true,
    "filesystems": 2
  },
  "traffic_24h": {
    "rx_bytes": 253832108441,
    "tx_bytes": 19971597926,
    "coverage_seconds": 86400,
    "window_seconds": 86400,
    "valid": true,
    "sample_age_seconds": 2,
    "iface": "physical:enp2s0,enp3s0"
  },
  "uptime": 86400
}
```

后台独立采样，与客户端数量无关；HTTP 请求只读取已发布快照。首次采样尚无差值基线，v2 的 CPU/GPU/网络使用 null，硬盘 valid=false；旧投影保留数值 0 兼容。所有客户端共享同一批数据；温度每 5 秒、容量每 30 秒刷新一次。CPU 总时间不重复累加已包含于 user/nice 的 guest/guest_nice，运行虚拟机时也保持正确比例。

新版小电视每 1 秒请求 `GET /status?display=1&v=2`；旧版 `display=1` 投影继续保留。该响应只保留 `cpu.percent`、`gpu.utilization`、`memory.percent`、`uptime`、`temperature_summary` 和流量/磁盘/容量的显示数值及有效标记；不含完整温度数组、网卡/磁盘列表或其他明细。`temperature_summary` 固定包含 `cpu`、`disk` 两个最高温度，无可用传感器时为 `null`。无 query 的 `/status` 保留旧字段，便于排查；`Content-Length` 始终给出完整响应体长度。

`GET /net` 使用同一 Bearer Token，读取后台 200ms 采样的所选网卡累计计数快照，保留给旧客户端。它不采集 CPU、GPU、内存、温度、磁盘或容器：

```json
{"sample_time":123456.25,"iface":"physical:enp2s0,enp3s0","rx_bytes":1690000000000,"tx_bytes":394000000000,"counter_epoch":"1788364800000000000"}
```

`sample_time` 是服务器单调时钟秒数，客户端用相邻样本的字节差除以时间差计算网速，可避开 HTTP 延迟抖动。新版小电视每 200 毫秒请求 v2 网络快照，使用服务端采样点绘图及约 1 秒平均速率显示数字；失败后指数退避，网络最长 5 秒、状态最长 30 秒，认证失败每 30 秒重试。任一所选网卡计数读取失败时，整个 `/net` 请求返回 HTTP 503，不会输出不完整的合计。

`counter_epoch` 是十进制字符串标识。服务逐网卡比较计数；任一卡回退、网卡集合变化或读取失败都会改变标识，即使多卡合计仍增加也不会掩盖重置。客户端发现标识变化时只更新基线，不计算这一段速率。新库会保存逐卡基线和标识，短暂重启后可以恢复；连接旧服务时，客户端仍可使用原接口名、时间和合计回退规则。

### 通信协议 v2

保留 HTTP/JSON，不增加 MQTT broker 或其他依赖。支持 HTTP/1.1 长连接（空闲 5 秒关闭），所有响应包含 Content-Length，不使用 chunked 或压缩。ESP 单连接、单个在途请求，按 256B 循环预算处理数据；网络请求最多 400ms、状态最多 750ms。每次成功后复用连接，关闭响应、残余数据、超时、认证错误或 Wi-Fi 中断时关闭重连。某个失败通道恢复成功时会提前唤醒另一个失败通道；单独的状态接口故障仍保留退避，避免健康网络通道不断触发重试。双方均兼容原 HTTP/1.0/旧 JSON；未知的新协议版本由固件拒绝。

`GET /net?v=2&since=42&epoch=1788364800000000000` 示例：

```json
{"v":2,"source":"2cdd44d43a9c8918","epoch":"1788364800000000000","seq":43,"age":0.012,"rate":[8600000,1200000],"points":[[43,123456.2,8800000,1250000]],"gap":false}
```

- `source`：所选接口集合名称的 SHA-256 前 16 位，长度不随网卡数量增长；完整名字仍在 `/status` 与旧 `/net` 中。它表示采集范围，不是硬件身份认证。
- `epoch`：十进制字符串。服务重启、范围变化、计数回退、读取失败或采样中断超过 1 秒时更换。与 `source` 一起构成当前数据流身份。
- `seq`：最新序号；`age`：最新采样距今秒数。网络超过 1 秒或状态超过 3 秒未更新，API 返回 503。
- `rate`：`[下载B/s,上传B/s]`，由实际累计计数差和单调时钟时间差计算，约每 1 秒更新。完整 `/status` 使用相同网速。
- `points`：每行 `[序号,单调时钟秒数,下载B/s,上传B/s]`，目标间隔 200ms。后台保留 50 点，按 since 补回最多 4 点；重复请求可返回空数组，null 表示没有有效差值。
- `gap`：范围/流变化、请求序号不连续或待补点超过 4 时为 true。ESP 清除旧曲线后绘制新点，避免把未知时间压成一个正常 200ms 间隔。最多 4 点按 LVGL 消费周期逐点绘制，不做虚构插值。

`/status?display=1&v=2` 增加 `v/seq/age/metric_age`。CPU/GPU/MEM 单项无法采集时为 null，uptime 无效时也为 null，其他指标独立更新；温度和容量带各自采样年龄，ESP 分别在超过 10/60 秒时不再展示。UPS 继续使用独立 2 秒采样、6 秒过期规则，完整状态保留 UPS 的年龄和有效性。滚动流量仍由原 SQLite 后台每 5 秒持久化，升级不更换历史范围键，也不清空数据库。

长连接与采样解耦减少重复连接和硬件读取；它不保证所有 NAS 都有 GPU、温度或有功功率数据，未检测到时明确显示不可用。

### 近 24 小时流量

后台每 5 秒采样所选网卡的累计字节数，独立于 HTTP 请求；小电视断开时仍继续记录。首次启动的默认 `physical` 会选择宿主机 `/sys/class/net/<名称>/device` 存在的全部物理网卡，并忽略 bridge、OVS、Docker、VPN 等虚拟接口，避免重复计算。`rx_bytes` 是下载总字节数，`tx_bytes` 是上传总字节数，包含局域网传输。窗口截至最近一次采样，向前滚动 86400 秒，数据通常延迟 0–5 秒，不在零点清零。

`coverage_seconds` 只累计窗口内确实观测到的时间：首次部署约 5 秒后才有第一个有效区间，不会从网卡开机累计量补造过去 24 小时。固件在覆盖不足 24 小时时显示已记录时长；无可用区间、采样失败或最新样本超过 10 秒时，`valid` 为 `false`，两个总量为 `null`，显示 `--`。

每个区间用相邻两次计数器的差值。窗口左边界落在一个区间中时，按该区间的平均流量分摊，因此是 5 秒精度的滚动统计。通常边界区间约 5 秒；允许调度抖动形成最多 10 秒的区间。任何一个所选网卡的计数器读取失败时，整次采样无效，不会把缺失值当成 0。采样失败、超过 10 秒的间隔、NAS 重启或计数器回退都会中断差值计算，缺口不会计作零流量覆盖时间。网卡集合或接口切换后只展示新标识自己的历史；系统时钟回拨时清空时间位置已不可靠的历史重新采集。

24 小时总量按最新采样缓存，同一次采样之后的 HTTP 请求复用汇总，仅重新计算数据年龄与有效性。后台每次写入或清理区间都会使缓存失效。逐卡计数和重置标识也参与区间连续性判断；后台 200ms 采样在两个历史采样之间观测到的回退同样会中断区间。

SQLite 数据库位于 `/data/traffic.sqlite3`，Compose 的 `traffic-history` 命名卷保存它。重建/重启容器、小电视重启不会清零；`docker compose down -v` 会删除历史。备份时先停止容器再备份该卷，或使用 SQLite 的在线备份功能。

升级旧数据库时自动给 `baseline` 增加一个 `counters TEXT` 列，现有 `intervals` 历史完整保留。旧基线缺少逐网卡计数，升级后的首次采样只建立新基线，不补算无法核实的跨版本区间；后续恢复正常采样。新格式的短重启仍可在同一 NAS 启动、同一网卡集合且间隔不超过 10 秒时连续计数。

### 硬盘读写与存储容量

`disk_io.read_speed` 和 `disk_io.write_speed` 是全部物理块设备在相邻两次状态采样间的合计字节速率。服务扫描 `/sys/class/block`，忽略分区、loop、device-mapper 和 md 等逻辑层，避免同时统计逻辑卷与底层硬盘。RAID1 等阵列会按每块物理盘的实际 I/O 合计，因此写入值可能高于应用层写入量。计数器不可读时 `valid` 为 `false`，速率返回 0。

`storage` 汇总 `NAS_STATUS_STORAGE_PATHS` 指定的数据卷。Compose 默认把 fnOS 的 `/vol1`、`/vol2` 只读挂载到容器，并按 `st_dev` 去重后计算总容量和已用容量，避免同一文件系统的重复挂载或子卷被重复相加。`percent` 等于 `used / total × 100`。路径不可读或没有有效文件系统时，三个数值为 `null` 且 `valid` 为 `false`。只有一个数据卷时，应同时从 `compose.yml` 删除另一个挂载，并把 `NAS_STATUS_STORAGE_PATHS` 改为实际路径。

## 环境变量

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `NAS_STATUS_IFACE` | `physical` | 聚合全部物理网卡；也可设为 `auto` 或具体接口名 |
| `NAS_STATUS_PORT` | `18199` | host 网络下服务监听端口 |
| `NAS_STATUS_ADMIN_PASSWORD` | 自动生成 | 仅首次引导的网页管理员密码，至少 12 字符 |
| `NAS_STATUS_TOKEN` | 无 | 必填鉴权 Token |
| `NAS_STATUS_STORAGE_PATHS` | `/vol1,/vol2` | 逗号分隔的容器内 fnOS 数据卷根目录；必须与只读挂载一致 |
| `NAS_STATUS_HISTORY_DB` | `api.py` 同目录下的 `data/traffic.sqlite3` | 流量历史数据库；仓库 Compose 显式设为 `/data/traffic.sqlite3` |
| `NAS_STATUS_SMART_MAX_AGE_SECONDS` | `7200` | smartd 温度日志的最大允许年龄；超时数据不参与最高硬盘温度 |
| `PROC_ROOT` | `/host` | 容器内宿主 `/proc` 前缀 |
| `SYS_ROOT` | `/host` | 容器内宿主 `/sys` 前缀 |
| `DEBUGFS_ROOT` | `/host/debug` | Intel i915 debugfs 前缀 |
| `SMART_ROOT` | `/host/smartmontools` | 宿主 smartd 属性日志的只读挂载点 |

## GPU 和硬盘温度

Intel 核显利用率会探测各个 DRI 节点的 i915 engine 数据；AMD 显卡会读取 sysfs 的 `gpu_busy_percent`。若 NAS 内核未开放这些文件、使用 NVIDIA 或其他 GPU，接口仍正常工作，但 GPU 利用率为 0。

硬盘温度优先合并两个只读来源：Linux hwmon 的 `drivetemp`/`nvme` 传感器，以及 smartd 写入 `/var/lib/smartmontools/attrlog.*.ata.csv` 的 ATA 属性 190/194。服务从最新记录的 SMART raw 值低字节读取摄氏温度，只接受 `0 < temp < 100`，并与 hwmon 结果取最高值。日志文件超过 2 小时未更新时会被忽略，休眠盘不会把旧温度长期冒充当前温度。

Compose 只读挂载 smartd 已有日志，不运行 `smartctl`，也不需要 `/dev`、额外 capability 或 Docker socket。可在 NAS 上检查 hwmon：

```bash
find /sys/class/hwmon -name 'temp*_input' -print
```

CPU 温度会同时扫描 thermal zone 和全部 hwmon 节点，兼容 `coretemp`、`k10temp`、`zenpower`、`cpu_thermal` 等常见驱动。API 会把识别到的最高 CPU 温度标准化为 `type: "cpu"`，把最高硬盘温度标准化为 `type: "disk"`。

## 兼容性与排查

| 指标 | 通用性 | 取不到时的行为 |
| --- | --- | --- |
| CPU 占用 | Linux `/proc/stat`，基本通用 | 返回 0，不影响接口 |
| 内存 | Linux `/proc/meminfo`，基本通用 | 返回 0，不影响接口 |
| 开机时间 | Linux `/proc/uptime`，基本通用 | 返回 0 |
| 网络 | 默认聚合所有带 sysfs `device` 的物理网卡 | 无物理接口或任一计数不可读时速率为 0 |
| 硬盘读写 | 聚合所有非分区且带 sysfs `device` 的物理块设备 | `disk_io.valid=false`，速率为 0 |
| 存储容量 | 对显式只读挂载的数据卷执行 `statvfs`，按 `st_dev` 去重 | `storage.valid=false`，数值为 `null` |
| CPU/硬盘温度 | CPU 使用 hwmon/thermal；硬盘合并 hwmon 与新鲜 smartd ATA 日志 | 没有有效来源时不返回对应的通用温度项 |
| GPU | Intel i915、AMD GPU | 其他型号返回 0，backend 为 unavailable |

`physical` 的 `net.iface` 值按名称稳定排序，例如 `physical:enp2s0,enp3s0`。若只想统计默认路由，设置 `NAS_STATUS_IFACE=auto`；若要统计 bridge、bond、OVS、VPN 等一个确定的逻辑接口，直接填写接口名。接口可通过下面的命令确认：

```bash
ip route show default
ls /sys/class/net
for nic in /sys/class/net/*; do [ -e "$nic/device" ] && basename "$nic"; done
```

项目附带不同硬件目录结构的自动化测试：

```bash
python3 -m unittest discover -s . -p 'test_*.py' -v
```

## 权限与网络

Compose 只读挂载 `/proc`、`/sys`、debugfs、smartd 日志和 `/vol1`、`/vol2`，并启用只读根文件系统、移除 Linux capabilities、禁止权限提升；只将 `/data` 持久卷开放写入以保存流量历史。服务不需要 Docker socket，也不会读取容器列表。

该 API 使用 HTTP Bearer Token，不提供 TLS。推荐只绑定在可信局域网；若需远程访问，请通过 WireGuard/Tailscale 等 VPN 或带 TLS 的反向代理，并设置防火墙访问控制。


## UPS 功率

只读挂载 `/run/nut:/host/nut:ro`，复用飞牛已经运行的 NUT 驱动。仅发送 `DUMPALL` 读取缓存，不重新占用 USB、不修改 NUT/断电保护配置、不开放 UPS 控制接口。默认要求恰好一个 `usbhid-ups-*` socket；多设备时用 `NAS_STATUS_UPS_SOCKET` 指定容器内路径。

`/status` 新增 `ups: {watts, valid, source, status, alarm}`，展示投影只包含 `ups.watts`。优先使用驱动提供的 `ups.realpower` / `output.realpower`；仅已验证的 `WL W120` 直流型号允许用输出电压×电流计算瓦数，source 为 `dc_voltage_current`，其他无瓦数的型号返回不可用，避免将交流视在功率当作有功功率。原始告警保留，不把已知 W120 电池误报过滤成“保护正常”。

后台线程每 2 秒采样，单次读取总时限 1 秒、最大 32KiB，HTTP 只读取快照、不等待 UPS。短暂超时保留最近有效读数，满 6 秒没有新有效读数则返回 null；socket 断开、DATASTALE、非有限数或异常数值立即返回不可用，屏幕显示 `-- W`。完整响应附带 `age_seconds`。网络 `/net` 采样路径独立。当前展示支持 0–999W，0–35W 为进度条量程，35W 不是危险阈值。
