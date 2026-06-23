# MaiQRCodeGetter

通过电脑端微信自动获取舞萌（MaiMai DX / 中二节奏）公众号登录二维码，让你无需手机即可出勤。


---

## 特性

- **超快获取** — 1.7~2.5 秒完成，延迟主要来自网络
- **两种获取方式** — 截屏模式（Win32 API 操控微信窗口）和代理模式（mitmproxy 拦截网络请求）
- **PWA 支持** — 可安装到手机/电脑主屏幕，独立窗口运行
- **响应式界面** — 适配 160px 宽智能手表到 4K 显示器
- **安全防线** — 会话鉴权、指纹限流、CapJS 验证码、入口隐藏
- **四种运行模式** — 正常、脱敏、演示、纯前端演示

---

## 环境要求

- Windows 系统
- Python 3.12+
- 微信桌面版（版本 ≥ 4.0.0）
- 公网可访问（或使用内网穿透）

---

## 快速开始

### 1. 配置

```shell
cp config.example.ini config.ini
# 编辑 config.ini，务必修改默认密码！
```

配置文件：

```ini
[Default]
entryPoint = /login          # 登录入口路径
loginUserName = admin        # 用户名
loginPassword = maimaidx     # 密码（一定要修改！）
port = 8080                  # 监听端口
mode = marked                # normal | marked | demo | web_only
```

### 2. 设置微信

打开「舞萌 | 中二」公众号 → 双击分离窗口 → 点击「玩家二维码」按钮 → 确认能正常扫出二维码。然后按以下顺序设置：

![](.github/static/setting1.png)

### 3. 安装依赖并测试

```shell
pip install -r requirements.txt
python main.py 1    # 测试模式：只获取一次二维码，不启动 Web 服务
```

若控制台打印出 `Code: MAID...` 即表示获取成功。

### 4. 启动服务

```shell
python main.py
```

打开浏览器访问 `http://127.0.0.1:8080/login`，使用配置的用户名密码登录。

---

## 运行模式

| 模式 | 说明 |
|------|------|
| `normal` | 真实二维码，完整输出 |
| `marked` | 真实二维码，日志中敏感部分替换为 `*` |
| `demo` | 基于真实二维码哈希生成假码，**无法登录**，可安全展示 |
| `web_only` | 完全脱离微信，随机生成假码 |

---

## 代理模式

相比截屏模式平均快约 0.2 秒。

```shell
pip install mitmproxy
python main_proxy.py
```

启动后在微信中配置代理（地址 `127.0.0.1`，端口见控制台输出），参考下图：

![](.github/static/wxproxy1.png)
![](.github/static/wxproxy2.png)

配置正确后微信会闪回登录界面，其余操作与截屏模式相同。

---

## API 接口

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 二维码展示页（前端 AJAX 获取数据） |
| `/qrc` | GET | 同上 |
| `/maimai` | GET | JSON API — `{"success":true,"maid":"...","time":"...","spend":1.5}` |
| `/{entryPoint}` | GET | 登录页面 |
| `/{entryPoint}` | POST | 登录处理 |
| `/logout` | GET | 退出登录 |
| `/logout_all` | GET | 强制下线全部设备 |
| `/204` | GET | 空响应（连通性检查） |

---

## 安全性

1. **会话鉴权** — 内存会话，重启即失效，7 天过期
2. **爆破防御** — 浏览器指纹限流，5 分钟最多 10 次失败
3. **CapJS 验证码** — 可选 Cloudflare Worker PoW 验证码，防止暴力破解
4. **入口隐藏** — 修改 `entryPoint` 后，未认证请求返回 404 而非重定向

> **重要**：务必修改默认密码和登录入口。公开部署时建议启用 CapJS 验证码。

部署 CapJS：

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/xyTom/cap-worker)

部署后将 Worker 域名填入 `config.ini` 的 `capjs_endpoint`（需带 `https://`）。

---

## 内网穿透

无需公网服务器，推荐以下方式：

1. **SakuraFrp** — 在客户端绑定域名后下载证书，配置 `certfile` / `keyfile` 即可启用 HTTPS
2. **Cloudflare Tunnel** — 将本地端口暴露到 Cloudflare 边缘网络
3. **SSH 反向隧道** — 使用 `ssh -R` 转发端口

---

## 界面

- **登录页** — 简洁卡片式布局，支持 CapJS 验证码
- **二维码页** — 三态切换（加载中 / 二维码 / 错误），单击二维码可全屏放大，双击刷新
- **加载中** — 旋转动画 + 实时计时 + 随机知识语录
- **PWA** — 可安装到桌面，离线缓存静态资源

---

## 技术栈

- **后端** — Python 3.12 + aiohttp
- **截图** — Win32 API (PrintWindow) + UIAutomation + pyzbar
- **代理** — mitmproxy
- **前端** — 原生 HTML/CSS/JS，零框架依赖
- **PWA** — Service Worker + Web App Manifest

---

## License

MIT
