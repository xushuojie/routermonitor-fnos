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
  "temp": [{"zone": "0", "type": "x86_pkg_temp", "temp": 51.0}],
  "net": {"iface": "eth0", "rx_speed": 16384, "tx_speed": 8192},
  "uptime": 86400
}
```

首次采样时 CPU、GPU 和网络速率可能为 0；第二次请求后会根据两次累计计数的差值计算。

## 环境变量

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `NAS_STATUS_IFACE` | `auto` | 自动识别默认路由网卡，也可手动指定 |
| `NAS_STATUS_PORT` | `18199` | Compose 发布端口 |
| `NAS_STATUS_TOKEN` | 无 | 必填鉴权 Token |
| `PROC_ROOT` | `/host` | 容器内宿主 `/proc` 前缀 |
| `SYS_ROOT` | `/host` | 容器内宿主 `/sys` 前缀 |
| `DEBUGFS_ROOT` | `/host/debug` | Intel i915 debugfs 前缀 |

## GPU 和硬盘温度

Intel 核显利用率会探测各个 DRI 节点的 i915 engine 数据；AMD 显卡会读取 sysfs 的 `gpu_busy_percent`。若 NAS 内核未开放这些文件、使用 NVIDIA 或其他 GPU，接口仍正常工作，但 GPU 利用率为 0。

硬盘温度依赖 Linux hwmon 的 `drivetemp` 或 `nvme` 驱动。可在 NAS 上检查：

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
| 网络 | 自动读取默认路由和 sysfs 计数 | 无合适接口时速率为 0 |
| CPU/硬盘温度 | 取决于内核传感器驱动 | 温度数组为空 |
| GPU | Intel i915、AMD GPU | 其他型号返回 0，backend 为 unavailable |

多网卡、旁路由、虚拟交换机或默认路由特殊的机器，可以在 `.env` 中将 `NAS_STATUS_IFACE=auto` 改成实际接口名。接口名可通过下面的命令确认：

```bash
ip route show default
ls /sys/class/net
```

项目附带不同硬件目录结构的自动化测试：

```bash
python3 -m unittest discover -s . -p 'test_*.py' -v
```

## 权限与网络

Compose 只读挂载 `/proc`、`/sys` 和 debugfs，并启用只读根文件系统、移除 Linux capabilities、禁止权限提升。不要为了额外数据挂载 Docker socket，除非明确理解其等同于主机高权限访问。

该 API 使用 HTTP Bearer Token，不提供 TLS。推荐只绑定在可信局域网；若需远程访问，请通过 WireGuard/Tailscale 等 VPN 或带 TLS 的反向代理，并设置防火墙访问控制。
