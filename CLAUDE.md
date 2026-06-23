# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# 核心构建与执行指令 (Commands)
- 安装环境依赖: `pip install -r requirements.txt`
- 运行主服务 (截屏获取机制): `python main.py`
- 运行测试模式 (不启动Web,只测试二维码获取): `python main.py 1`
- 运行代理服务 (代理抓包机制): `python main_proxy.py`（需先 `pip install mitmproxy`，配合微信代理设置使用）

# 项目上下文 (Project Context)

本项目派生自 `UsagiPassQRCodeGetter`，目标是打造一个纯粹、稳定、只专注于**舞萌(MaiMai)公众号二维码获取与分发**的后端服务。原项目的 UsagiPass/dxpass UI 嵌套展示功能已被剥离。

## 高层架构

项目提供**两种独立的二维码获取机制**，对应两个入口文件：

### 1. 截屏模式 (`main.py`) — 同步阻塞
通过 Win32 API + UI Automation 操控微信窗口，用 `PrintWindow` 无视遮挡截取窗口位图，再经 pyzbar 解析二维码。
- **流程**: 查找窗口句柄 → 激活/还原窗口 → 点击"玩家二维码"按钮 → 轮询消息列表截图 → OCR识别 → 返回 MAID 码
- **关键函数**: `main()` (同步, L539-676), `component_screenshot()` (L455-522, GDI位图捕获), `find_window_handle()` (L237-260)
- **并发控制**: 全局变量 `on_active` 防止 10+waitTime 秒内重复获取

### 2. 代理模式 (`main_proxy.py`) — 异步协程
通过 mitmproxy 中间人代理拦截微信客户端请求，直接从网络响应中提取二维码图片字节。
- **流程**: 启动 mitmproxy → 微信配置代理 → 点击按钮 → mitmproxy 拦截 `/mmecoa_png` 响应 → 缓存到 `CACHE_QRCODE` → pyzbar 解码
- **关键差异**: `main()` 是 async (L547-660)，不操作 UI 截图，而是轮询 `CACHE_QRCODE` 全局 bytes；`WXPictureCapture` addon (L794-804) 负责拦截
- **代理服务启动**: `start_proxy_server()` (L807-818) 创建 mitmproxy Master 实例

### 代码重复
两个文件共享约 70% 的代码：认证系统、限流、配置管理、会话管理、Web 路由、Win32 工具函数完全相同。这是已知的技术债务，重构时应将共享逻辑提取到公共模块。

### Web 层 (aiohttp)
两种模式共用几乎相同的 aiohttp Web 服务，路由注册在 `web_server()` 中。

**重要架构变更**：`htmlPage` (GET `/`) 和 `QRCodePage` (GET `/qrc`) **不再同步调用 `main()`** 获取二维码。它们只返回 `qrcode.html` 静态壳，前端通过 AJAX 调用 `/maimai` JSON API 获取二维码数据。这样：
- 页面瞬间加载（无 10+ 秒阻塞等待）
- 加载/成功/失败三种状态在前端统一展示，风格一致
- 错误不再跳转到 `traceback.html`（动漫背景页）

| 路由 | 方法 | 功能 |
|------|------|------|
| `/{entryPoint}` (默认 `/login`) | GET | 登录页面 |
| `/{entryPoint}` | POST | 登录处理 (含 CapJS 验证 + 指纹限流) |
| `/` | GET | 二维码展示页壳 — 前端 AJAX 调 `/maimai` 获取数据 |
| `/` | POST | 未认证则重定向登录页 |
| `/qrc` 或 `/qrcode` | GET | 同 `/` |
| `/maimai` | GET | **核心 JSON API** — 调用 `main()` 获取二维码，返回 `{success, maid, time, spend}` 或 `{success:false, error}` |
| `/logout` | GET | 单设备登出 |
| `/logout_all` | GET | 清除全部会话 |
| `/204` | GET | 空响应 (用于连通性检查/Nginx 前置) |
| `/static/{filepath}` | GET | 静态文件服务，仅允许 `ALLOW_EXT` 后缀 |

**认证中间件** `auth_middleware` (L319-343)：除登录页和静态资源外，所有请求校验 `session_token` cookie。非默认入口时返回 404 而非重定向（安全加固）。

### 四种运行模式 (`mode` 配置)
- **`normal`**: 真实二维码，完整输出
- **`marked`**: 真实二维码，但日志/控制台输出时中间 80% 字符替换为 `*`（防屏幕截图泄露）
- **`demo`**: 将真实二维码哈希后拼接当前时间戳生成假码，**无法用于登录**，可安全公开展示
- **`web_only`**: 完全脱离微信，用时间戳+SHA256 生成随机假码，用于纯前端演示

### 安全防线 (不可删除)
1. **会话鉴权**: 内存字典 `active_sessions`，session token 由 `secrets.token_urlsafe(32)` 生成，7天过期，重启即失效
2. **爆破防御**: 基于浏览器指纹 (`fp`) 的滑动窗口限流 — 5分钟内最多 10 次失败 (`MAX_FAIL=10`, `WINDOW=300`)，最多缓存 50,000 个 visitorId
3. **CapJS 验证码**: 可选 Cloudflare Worker 驱动的 PoW 验证码，通过 `capjs_endpoint` 配置启用，token 去重队列最多 1000 条
4. **入口隐藏**: `entryPoint` 非默认值时，未认证请求返回 404 而非重定向

### 配置系统
用户需复制 `config.example.ini` 为 `config.ini` 并编辑配置项。程序启动时若未找到 `config.ini` 会打印错误提示并退出。支持的配置项：`entryPoint`, `loginUserName`, `loginPassword`, `port`, `waitTime`, `minimize_after_success`, `capjs_endpoint`, `mode`, `certfile`, `keyfile`。代理模式额外增加 `proxyPort`。

### 前端页面

#### `qrcode.html` — SPA 单页面应用（加载 / 成功 / 失败三态统一）
页面加载后通过 AJAX 调用 `/maimai` JSON API 获取二维码，**不再由后端模板替换占位符**。三种状态在同一简洁白底页面切换：
- **加载态**: CSS 旋转 spinner + 实时等待秒数计时器
- **成功态**: QR 码 canvas + 机台提示 + 耗时 + 有效期 + 「退出登录」「刷新二维码」按钮
- **错误态**: ⚠ 图标 + 错误详情（含 `max-height` + 内部滚动，防止长文本撑出视口）+ 「退出登录」「重试」按钮

关键 CSS 约束模式：QR 码用 CSS 自定义属性 `--qr-cap` 同时约束 `max-width` 和 `max-height`，**必须同一值**，否则 canvas 正方形会被压扁。

页面针对极小屏幕做了多层适配：
- 基础版用 `clamp()` 做弹性字号/间距
- `@media (max-height:399px)` 矮屏适配（最关键 — 收紧所有间距、缩小 QR 上限、按钮最小高度降至 28px）
- `@media (max-width:319px)` 极窄屏适配
- 横屏时卡片内元素横向排列
- 容器用 `margin:auto` 在子元素上实现安全居中（避免 `align-items:center` + `overflow:auto` 导致顶部内容被截断的经典 Flexbox bug）

#### `login.html` — 亚克力风格登录页
集成 FingerprintJS + SweetAlert2 + 可选 CapJS widget。自适应横竖屏背景图（引用 `static/images/` 下两张 webp）。同样做了矮屏/窄屏/横屏适配和 `margin:auto` 安全居中。

#### `traceback.html` — 已废弃
异常堆栈展示页（Prism.js 语法高亮 + 动漫背景）。**后端路由已不再使用此文件**（`htmlPage` / `QRCodePage` 不再 fallback 到它），保留在磁盘上仅为历史遗留。

# ✅ 已完成的清理 (Completed Cleanup)
- **`dxpass` 及 `UsagiPass` UI**: 已删除 `main.html`（iframe 页面）、`forbidden.html`；`/` 路由已重构为纯二维码展示页
- **配置文件清理**: 已从 config 生成/读取逻辑中删除 `dxpass_url` 配置项
- **路由精简**: `/` 和 `/qrc` 路由返回相同的 `qrcode.html` 壳，前端通过 `/maimai` API 获取数据

# 💡 需要强化与保留的核心
- **获取稳定性**: 优化微信窗口唤醒、截图 OCR 解析、代理抓包容错。增加详细 try-except 和日志
- **并发与异步**: 优化二维码获取时的阻塞逻辑
- **安全防线 (绝对不可删除)**: 上述四种安全机制必须保留

# 🛠 编码规范
- **环境**: Python 3.12+，新增/修改的函数必须有类型提示 (Type Hints)
- **模块化解耦**: 将 `main.py` 和 `main_proxy.py` 的共享代码（认证、限流、配置、Web路由）提取到公共模块，消除 ~70% 的代码重复
- **HTML/CSS**: 使用最简洁的原生 HTML/CSS/JS，不引入臃肿第三方前端资源。页面必须适配 160~750 CSS px 宽、128~1000 CSS px 高的范围。遵循以下模式：
  - 字号/间距用 `clamp(最小值, 首选值, 最大值)`，不用固定 px
  - 必须写 `@media (max-height:399px)` 矮屏适配，收紧所有间距
  - 滚动容器禁止 `align-items:center`（溢出时顶部被截断），改用子元素 `margin:auto`
  - QR 码 canvas 用 CSS 变量同时约束 `max-width` 和 `max-height`，确保正方形
  - 报错文本容器必须有 `max-height` + `overflow-y:auto`，防止长错误撑出视口
- **注释**: 保留中文注释，复杂图像处理/代理回调必须加清晰逻辑说明
- **Win32 注意**: 截屏逻辑依赖 Windows API（user32/gdi32/kernel32）、`uiautomation`、微信客户端窗口，仅在 Windows 平台可用
