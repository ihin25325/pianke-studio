# SnapAny 接入与验证

## 从这里开始

本地入口：[国内外素材 → SnapAny API](http://127.0.0.1:8765/sources#snapany)。工作台原启动器照常使用；不需要部署到公网、不依赖聊天会话一直打开。

1. 打开 [官方密钥管理](https://platform.snapany.com/console/keys)，自己登录并创建 API Key。不要把密钥发给任何聊天。
2. 在本机入口填写 `sk_snapany_` 密钥，选择大陆或国际接口，保存后点击免费余额检查。
3. 首次建议「优先使用 SnapAny」验证一条；确认计费后保存模式。正常批量时可改成「本地失败后使用」，减少积分消耗。默认「仅本地」不自动调用 API。
4. 在上方链接框填写有权下载的具体公开视频页/分享链接，选择商品，点「保存并仅下载原片」。可先用单次解析确认标题，但只解析并不代表文件已下载。10 分钟内单次解析与随后自动下载复用同一结果；更换模式不重复解析，重启/换密钥/区域会失效。
5. 回工作台查看进度；下载成功后原有预览、起止时间、字幕、Logo 与成片功能不变。已下载原片直接复用，不为了 API 重下。

新账户可按官方当前政策自行领取体验积分。是否充值由本人决定；集成不会自动充值。查询余额免费，单帖成功解析通常 1 积分；实际费用见 [计费说明](https://platform.snapany.com/zh/docs/credits)。不提供按人民币金额的预算保证。

## 范围与安全

- 实现端点：`GET /openapi/v1/credits/balance` 与 `POST /openapi/v1/extract/post`。只允许 `api-cn.snapany.com`、`api.snapany.com` 两个 HTTPS 官方域名，不允许任意自定义接口，携密钥请求不跟随重定向。
- 根据 [官方单帖文档](https://platform.snapany.com/zh/docs/extract-post) 接收 `medias`、`resource_url`、`variants`、`headers`，优先选不超过 1080p 的最高变体，分离音视频分别下载后本机合并。输出沿用原有 1080×1440 成片设置，不擅自改比例。
- CDN 原生 HTTPS 文件下载只访问公网地址，包含每次 DNS 和重定向校验；跨域重定向移除 Cookie/Authorization，若因此无法下载会清楚失败。保留 API 返回的空 User-Agent 等合法请求头，但拒绝头注入、Host/代理/Range 等危险字段与疑似 API 密钥。
- 单条原片最多 15 分钟、输入总大小和输出原片均最多 300 MB，直播/DRM/多视频帖/图片帖不自动处理。清单流不交给 FFmpeg 联网，ffprobe/FFmpeg 只准本地常见媒体格式，避免伪装清单访问本机或内网。
- 不做额外付费的代理下载、ASR、列表遍历，不解锁私密/付费/会员内容，不承诺去除创作者水印。视频网站解析成功与商业使用权无关。
- 每次发送解析前先落本机台账并检查每日次数；失败和超时也占次数，防止失控重试。台账损坏时停止解析。已发送请求可能已扣积分，即使后续下载失败。真实账单以官方为准。
- 所有密钥、缓存、测试、下载文件位于 D 盘项目。Windows DPAPI 文件不能直接迁移到另一个 Windows 用户；迁移后需要重新填写密钥。停用不删除任何业务数据；正在执行的请求不能撤回。

## 测试

新增测试文件 `scripts/test_snapany.py`：加密不联网保存、重启恢复、免费余额合约、固定官方域名、禁止携密钥跳转、错误脱敏、计费确认、次数上限、缓存复用、设置切换、非法链接/头/私网拦截、单帖限制、清晰度/分离音轨、已有素材复用、生产下载分支、同源保护与私有文件隔离。

PowerShell（在项目根目录）：

```powershell
$taskPython = '.\.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
& $taskPython -B -m unittest discover -s scripts -p 'test_*.py' -v
```

真实网络下载/转码验证（不使用 SnapAny 账号，不调用付费端点）：

```powershell
$env:SNAPANY_CDN_SMOKE = '1'
& $taskPython -B -m unittest discover -s scripts -p test_snapany.py -v
Remove-Item Env:SNAPANY_CDN_SMOKE
```

此测试用模拟 API 响应指向 MDN 的 CC0 `flower.mp4`，真实执行生产下载子进程、媒体验证、MP4 转码、全片解码及拒绝覆盖。证据保存在 `work/snapany_tests/run_*/cdn_proof.json`。它验证下载与转码线路，但不等于 SnapAny 真账号、B站、抖音、小红书链接提取已实测成功。没有真实密钥，不会假报 API 已连通。

2026-11-17 验证：全项目共 177 项，168 项通过、9 项既有可选测试跳过；新增 SnapAny 19 项全部通过，包含真实 CC0 下载验证。另启用并通过原有多站点真实短片剪辑、1080×1440 输出、完整解码与原片哈希不变检查。前端 `node --check web/sources.js`、页面控件对应检查与 Git 差异空白检查通过。没有真实 SnapAny API 调用、积分消费或生产清单写入。

浏览器自动化当前因工具基础设施缺失 `failed to write kernel assets` 无法启动；可完成 HTTP 路由、同源、前端 JS 语法/控件与后端测试，但浏览器实际画面和点击交互仍需人工确认。
