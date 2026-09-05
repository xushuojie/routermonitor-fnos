# NAS Docker 状态服务

这是给 ESP8266 Router Monitor 使用的无第三方 Python 依赖状态 API。服务只读取宿主机指标，并返回体积较小的 JSON。

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

首次采样时 CPU、GPU、网络和硬盘速率可能为 0；下一次采样后会根据两次累计计数的差值计算。所有客户端共享 1 秒状态缓存，密集请求不会反复扫描宿主机或缩短速率采样区间；温度每 5 秒、容量每 30 秒刷新一次。CPU 总时间不重复累加已包含于 user/nice 的 guest/guest_nice，运行虚拟机时也保持正确比例。

小电视每 1 秒请求 `GET /status?display=1`。该响应只保留 `cpu.percent`、`gpu.utilization`、`memory.percent`、`uptime`、`temperature_summary` 和流量/磁盘/容量的显示数值及有效标记；不含完整温度数组、网卡/磁盘列表或其他明细。`temperature_summary` 固定包含 `cpu`、`disk` 两个最高温度，无可用传感器时为 `null`。无 query 的 `/status` 保留旧字段，便于排查；`Content-Length` 始终给出完整响应体长度。

`GET /net` 使用同一 Bearer Token，只读取所选网卡的累计计数，适合高频轮询。它不采集 CPU、GPU、内存、温度、磁盘或容器：

```json
{"sample_time":123456.25,"iface":"physical:enp2s0,enp3s0","rx_bytes":1690000000000,"tx_bytes":394000000000,"counter_epoch":"1788364800000000000"}
```

`sample_time` 是服务器单调时钟秒数，客户端用相邻样本的字节差除以时间差计算网速，可避开 HTTP 延迟抖动。小电视每 200 毫秒请求一次 `/net` 并更新折线，约每 1 秒更新网速数字；请求失败后改为每 1 秒重试，成功后恢复 200 毫秒。任一所选网卡计数读取失败时，整个 `/net` 请求返回 HTTP 503，不会输出不完整的合计。

`counter_epoch` 是十进制字符串标识。服务逐网卡比较计数；任一卡回退、网卡集合变化或读取失败都会改变标识，即使多卡合计仍增加也不会掩盖重置。客户端发现标识变化时只更新基线，不计算这一段速率。新库会保存逐卡基线和标识，短暂重启后可以恢复；连接旧服务时，客户端仍可使用原接口名、时间和合计回退规则。

### 近 24 小时流量

后台每 5 秒采样所选网卡的累计字节数，独立于 HTTP 请求；小电视断开时仍继续记录。默认 `physical` 会聚合宿主机 `/sys/class/net/<名称>/device` 存在的全部物理网卡，并忽略 bridge、OVS、Docker、VPN 等虚拟接口，避免重复计算。`rx_bytes` 是下载总字节数，`tx_bytes` 是上传总字节数，包含局域网传输。窗口截至最近一次采样，向前滚动 86400 秒，数据通常延迟 0–5 秒，不在零点清零。

`coverage_seconds` 只累计窗口内确实观测到的时间：首次部署约 5 秒后才有第一个有效区间，不会从网卡开机累计量补造过去 24 小时。固件在覆盖不足 24 小时时显示已记录时长；无可用区间、采样失败或最新样本超过 10 秒时，`valid` 为 `false`，两个总量为 `null`，显示 `--`。

每个区间用相邻两次计数器的差值。窗口左边界落在一个区间中时，按该区间的平均流量分摊，因此是 5 秒精度的滚动统计。通常边界区间约 5 秒；允许调度抖动形成最多 10 秒的区间。任何一个所选网卡的计数器读取失败时，整次采样无效，不会把缺失值当成 0。采样失败、超过 10 秒的间隔、NAS 重启或计数器回退都会中断差值计算，缺口不会计作零流量覆盖时间。网卡集合或接口切换后只展示新标识自己的历史；系统时钟回拨时清空时间位置已不可靠的历史重新采集。

24 小时总量按最新采样缓存，同一次采样之后的 HTTP 请求复用汇总，仅重新计算数据年龄与有效性。后台每次写入或清理区间都会使缓存失效。逐卡计数和重置标识也参与区间连续性判断；高频 `/net` 在两个历史采样之间观测到的回退同样会中断区间。

SQLite 数据库位于 `/data/traffic.sqlite3`，Compose 的 `traffic-history` 命名卷保存它。重建/重启容器、小电视重启不会清零；`docker compose down -v` 会删除历史。备份时先停止容器再备份该卷，或使用 SQLite 的在线备份功能。

升级旧数据库时自动给 `baseline` 增加一个 `counters TEXT` 列，现有 `intervals` 历史完整保留。旧基线缺少逐网卡计数，升级后的首次采样只建立新基线，不补算无法核实的跨版本区间；后续恢复正常采样。新格式的短重启仍可在同一 NAS 启动、同一网卡集合且间隔不超过 10 秒时连续计数。

### 硬盘读写与存储容量

`disk_io.read_speed` 和 `disk_io.write_speed` 是全部物理块设备在相邻两次状态采样间的合计字节速率。服务扫描 `/sys/class/block`，忽略分区、loop、device-mapper 和 md 等逻辑层，避免同时统计逻辑卷与底层硬盘。RAID1 等阵列会按每块物理盘的实际 I/O 合计，因此写入值可能高于应用层写入量。计数器不可读时 `valid` 为 `false`，速率返回 0。

`storage` 汇总 `NAS_STATUS_STORAGE_PATHS` 指定的数据卷。Compose 默认把 fnOS 的 `/vol1`、`/vol2` 只读挂载到容器，并按 `st_dev` 去重后计算总容量和已用容量，避免同一文件系统的重复挂载或子卷被重复相加。`percent` 等于 `used / total × 100`。路径不可读或没有有效文件系统时，三个数值为 `null` 且 `valid` 为 `false`。只有一个数据卷时，应同时从 `compose.yml` 删除另一个挂载，并把 `NAS_STATUS_STORAGE_PATHS` 改为实际路径。

## 环境变量

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `NAS_STATUS_IFACE` | `physical` | 聚合全部物理网卡；也可设为 `auto` 或具体接口名 |
| `NAS_STATUS_PORT` | `18199` | Compose 发布端口 |
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
