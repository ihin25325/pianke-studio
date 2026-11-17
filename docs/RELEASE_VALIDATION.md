# 0.1.0 源码发布验证

验证日期：2026-11-17。当前是准备上传 GitHub 的源码版本，不代表远程仓库、Release 或云端 CI 已创建成功。

## 本地验证结果

- 现有完整环境：`unittest discover` 共 187 项，报告通过，9 项可选测试跳过。
- 纯公开源码＋新建 Python 环境：共 184 项，报告通过，15 项可选资源/私有历史样本/联网测试跳过。没有把用户原表复制到测试副本。
- 新增发布测试 10 项全部通过：公开文件范围、通用模板、手动默认模式、校验和/解压安全、固定下载源备用、安装重试、源码 ZIP 清单、Node 配置、合成 XLSX 和实际随包 CSV。
- 全新目录真实安装了 Pillow 13.0.0、yt-dlp 2026.10.26、默认 Oswald/思源黑体及 FFmpeg/ffprobe。FFmpeg 实际走 BtbN 公共构建源并核对发布方 SHA256；Gyan 源曾返回 503，已提供固定备用源与有限重试，不跳过校验。
- 合成演示经真实商品导入及工作台渲染线路导出：6 秒、1080×1440、30fps、H.264/AAC；整片解码通过，原片摘要不变，真实待办未被演示写入。
- 新目录服务启动后，首页、来源设置、文字编辑、API 设置、品牌素材页及主要状态接口返回成功；新安装付费服务默认关闭。
- PowerShell 脚本解析、全部前端 JavaScript 语法、发布文件凭据模式检查、Git 差异空白检查通过。
- 原本机工作台的 64 个商品、172 条任务及队列文件摘要在准备过程中保持不变。

## 未验证或不承诺

- 浏览器自动化工具因基础设施错误无法启动；仅完成 HTTP 与前端脚本/控件检查。README 动图来自真实合成成片，不是浏览器操作录屏。
- 未在 macOS/Linux 验证；不提供多用户公网部署。
- 没有使用真实 SnapAny API 或新增付费云调用来完成本次发布验收，不承诺国内外站点全部能下载。
- 没有执行完整的 100 条真实商品素材批次；识别型号、素材授权与翻译质量仍需人工核对。
- 未安装的额外字体、字幕/翻译/视觉模型需要按 README 单独安装。核心安装不伪造这些功能已就绪。
- 模式扫描和 SHA256 检查不构成全面安全审计或素材授权证明。

## 复验

```powershell
.\.venv\Scripts\python.exe -B scripts\doctor.py
.\.venv\Scripts\python.exe -B -m unittest discover -s scripts -p "test_*.py" -v
.\.venv\Scripts\python.exe -B scripts\verify_clean_release.py
.\.venv\Scripts\python.exe -B scripts\release_check.py --package
```

验证脚本在 `work/release_smoke` 使用独立队列，默认待办只用于检查没有被改动。首次启动允许创建空白默认队列。原片、测试成片、日志与本机证明 JSON 均不在源码包中；README 中只包含明确标注为合成演示的 GIF。
