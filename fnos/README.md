# 飞牛 NAS Monitor 1.3.0-5

[安装包](../downloads/routermonitor-fnos-1.3.0-5-amd64.fpk) · [使用说明书](../docs/使用说明书.md)

本目录为最新 FPK 对应源码；仓库根目录的 `nas-docker/` 是此前的独立 Docker 部署版本。

| 路径 | 内容 |
| --- | --- |
| `app/` | 镜像内完整 Python 应用及网页资源 |
| `cmd/` | 最新生命周期与宿主机采集进程管理源码 |
| `docker/` | Compose 与 GPU 宿主机只读采集辅助程序 |
| `package/` | 从发布 FPK 提取的安装向导、权限、图标、生命周期及 app.tgz 展开文件；不重复存储镜像 |
| `tests/` | 服务端、硬件、存储、Token、局域网地址回归测试 |
| `build_fpk.py` | 复用离线镜像底层、覆盖本目录应用源码并重新打包 |
| `verify_fpk.py` | 校验已发布 FPK、OCI 摘要、源码一致性及权限 |

## 打包

需要 Python 3.11 及以上。仓库 `downloads/` 中的 FPK 提供离线镜像基底：

```sh
python3 fnos/build_fpk.py
python3 fnos/verify_fpk.py dist/routermonitor-fnos-1.3.0-5-amd64.fpk
```

输出为仓库 `dist/routermonitor-fnos-1.3.0-5-amd64.fpk` 及 SHA-256 文件，不覆盖已发布安装包。脚本将 `app/`、`cmd/`、`docker/` 覆盖进镜像和应用包，更新 OCI 摘要、镜像标签及 app.tgz 校验值。底层操作系统及 Python 来自已发布 FPK；这不是从零构建基础镜像的流程，也不保证重新生成的 gzip 文件逐字节一致。`package/` 是已发布包的可读快照，修改该目录不会自动参与这个覆盖打包脚本。

## 验证

运行后端测试需要 Linux（可使用 WSL），因为测试涉及 Unix socket、Linux 路径与文件系统操作：

```sh
python3 fnos/verify_fpk.py
python3 fnos/tests/run_all.py
node fnos/tests/test_lan_ui.cjs
```

FPK 的 amd64 离线镜像保留原始运行环境；硬件能力由宿主机驱动、传感器和工具决定。未完成飞牛实机安装验收。

本次同步在 WSL Ubuntu 复跑 50 项后端测试全部通过，局域网地址前端测试通过；发布包与重新打包的 FPK 均通过包体和源码校验。
