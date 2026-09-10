# 教程图片来源与复现

[返回项目首页](../../README.md)

| 图片 | 来源与说明 |
| --- | --- |
| `images/android-phone.png`、`android-tablet.png` | 已有 Android 1.1.0 / API 37 模拟器截图，极限示例数据用于布局验证，不是 1.1.5 实机截图 |
| `images/guide/web-*.png`（下列功率图片除外） | 1.3.0-5 真实前端、本地浏览器截图，固定示例 API 数据，不连接真实 NAS |
| `images/device-photo.png` | 已有 ESP8266 实拍 |
| `images/screen-*.png` | 已有实际 LVGL 界面渲染，示例数据 |
| README 流程图 | Mermaid 操作示意，不是飞牛或 Android 系统截图 |

网页以 `192.168.1.10` 为示例 NAS、`192.168.1.20` 为示例显示端。性能数字与示例 Token 均为文档数据。局部截图将固定栏改为静态定位以避免遮挡，不替换页面控件和文案。

## 生成网页截图

需要 Node.js、Playwright 和 Chromium。可在单独工具目录安装依赖并用 `NODE_PATH` 指向其 `node_modules`，不必将依赖加入产品目录。

```bash
npm install --no-save playwright
npx playwright install chromium
node docs/screenshots/capture-web.cjs
```

已有 Microsoft Edge 时，可设置环境变量 `DOCS_BROWSER_CHANNEL=msedge`。脚本使用独立无头浏览器，不读取个人浏览器资料。

脚本读取 `fnos/app/web/`，用 Playwright 路由提供示例响应，操作登录、设置和 Token 展开，保存八张 PNG。服务只监听本机临时端口，结束后关闭。此过程验证页面渲染，不替代后端或实机验收。

更新前端后重新生成并检查文字、按钮是否完整；新增 API 时补充示例，防止错误页面进入文档。不要加入真实凭据或设备标识。

功率图片：`web-power-cpu.png` 为 1.3.0-6 自动降级示例；`web-power-settings.png`、`web-power-settings-mobile.png`、`web-power-sources.png` 为 1.3.0-7 来源选择和独立读数示例。均使用真实前端与合成数据，不是实机传感器截图。

运行 `node docs/screenshots/capture-web.cjs --power-settings-test` 可检查四种模式保存、刷新、三项独立读数、不可用来源和手机布局，并生成上述三张新版截图。
