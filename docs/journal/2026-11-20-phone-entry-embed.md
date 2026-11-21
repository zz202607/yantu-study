# 手机入口：二维码镶进页面，Tailscale 成为固定入口

2026-11-20 · 接入 / M13 增强

## 需求（_ming）

每次双击 `start.bat` 会开两个界面：照片查看器里的二维码 PNG，加上浏览器里的 App。
希望把二维码合并进一个界面，或者有更好的办法。

## 决定

1. **二维码回到页面里。** 服务端按 target 现画，页面显示；`start.bat` 只开一个窗口
   （`/?qr=1`），扫完收起。顶部「手机」、设置页随时能再开。
2. **地址由服务端算。** 局域网地址继续走 `app/net.py` 的 `lan_ip()`；Tailscale 读
   `tailscale status --json`，不靠默认路由猜。
3. **只展示真能打开的入口。** Tailscale 候选依次探测
   HTTPS MagicDNS → HTTP MagicDNS → `100.x` IP，每个都请求 `/api/version`；
   只有回答成功的才出现在卡片里。二维码不能撒谎。
4. **顺序按「长期价值」排。** HTTPS Tailscale（固定、secure context）→ 局域网
   （零配置）→ HTTP Tailscale（固定但非安全上下文）。HTTPS 是 PWA 真安装和
   网页内麦克风的前提。
5. **未就绪状态写下一步，不写错误。** 没装 / 未登录 / 没开 serve 时，卡片直接告诉
   _ming 运行哪条命令，局域网入口始终保留。
6. **`/qr.png` 不接受任意文本。** 只接受 `/api/phone` 算出的已知 target，
   避免把接口变成开放二维码生成器。

## 实现

- `app/phone.py`（新）
  - 纯函数：`with_version`（保留微信旧快照规避）、`parse_status`、`tailscale_candidates`、
    `build_payload`；CI 不依赖 FastAPI / segno / Tailscale。
  - 运行时：`read_status`（4 s 超时）、`reachable`（1 s，绕过 Clash/系统代理）、
    `runtime`（10 s 缓存 + 锁）；登录后点「重新检测」走 `force=True`。
- `app/server.py`
  - `GET /api/phone?refresh=0|1`：返回 `targets`、`preferred`、Tailscale 状态与提示；
    `no-store`。
  - `GET /qr.png?target=lan|tailscale`：segno 现画 PNG（scale=10），`no-store`；
    端口优先从 Host 头取，兼容没设置 `PORT` 的 uvicorn 启动。
- `app/static/index.html`
  - 顶部「手机」键（窄屏隐藏）、设置页「手机访问」块、`#phoneSheet` 卡片：
    入口切换、二维码、地址、复制、重新检测、Tailscale 状态文案。
  - 深链 `?qr=1` 自动打开；`phoneHint()` 只在固定入口未完全就绪时提示。
- `tools/launch.py`
  - 删除 `show_qr()` / `os.startfile` / `shell_build()`；只打开
    `http://localhost:<port>/?qr=1`，不再有第二个图片窗口。
- `tools/tailscale_setup.py`（新）
  - `--status`（默认）/ `--login` / `--serve` / `--reset`；`--serve` 后最多等 20 s
    验证 HTTPS 入口真的可用，再给出手机下一步。

## 验证

- pytest **391 条全绿**（新增 `tests/test_phone.py` 12 条：版本参数、DNS 名归一化、
  status 解析、候选顺序、payload 排序/偏好、未登录/没装提示）。
- `PORT=8011 py -3 tools/launch.py --no-open`：
  - `/api/version` → build `1795187266`
  - `/api/phone` → 仅局域网 target，`tailscale.state=needs_login`，提示下一步登录
  - `/qr.png?target=lan` → `200 image/png`（480 B）；OpenCV `QRCodeDetector`
    解码回 `http://192.168.1.10:8011/?v=1795187266`
- 无头 Edge 1000×900 打开 `http://127.0.0.1:8011/?qr=1`：
  - 只有一个窗口，卡片自动打开；QR、地址、局域网/Tailscale 文案完整
  - DOM 中 `phoneQr.src=/qr.png?target=lan&b=…`、`phoneState` 含未登录提示
  - 内联 JS `node --check` 通过
- `py -3 tools/tailscale_setup.py --status`：识别 `needs_login`，输出 `--login` 下一步。

## 限制 / 下一步

- **Tailscale 目前未登录**（PC 端已安装 1.104.7）。登录必须 _ming 本人完成：
  `py -3 tools/tailscale_setup.py --login`，手机装 Tailscale 并登录同一账号。
  登录后运行 `--serve`，卡片才会出现 HTTPS 固定入口。
- `tailscale serve --bg <port>` 需要 tailnet 启用 Serve/HTTPS。未启用时命令会打印
  一次性链接后阻塞；脚本现在 15 s 超时并原样输出链接（返回码 2），不再抛
  `TimeoutExpired` 栈。启用后证书签发有几秒延迟，脚本会等待并验证。
- PC 已于 2026-11-20 登录 Tailscale（`your-machine.your-tailnet.ts.net`）；Serve 启用仍待
  _ming 在管理台点一次链接。
- MagicDNS 名称解析不了时退回 `100.x` 的 HTTP 入口（固定地址，但不是 secure context）。
- 局域网 HTTP 仍不能真正安装 PWA；语音仍要等 Tailscale HTTPS 生效。
- 旧 `.cache/phone_access.png` 不再生成，已删除；文档不再引用。

## 更新（2026-11-21）：Tailscale 已接通

- PC 登录 `你的 Tailscale 账号`：`BackendState=Running`，MagicDNS
  `your-machine.your-tailnet.ts.net`，100.x.x.x；手机（Android，100.x.x.x）
  在同一 tailnet，`tailscale ping` 直连 105 ms。
- 在管理台启用 Serve 后，`tailscale serve --bg 8000` 配置成功；
  `https://your-machine.your-tailnet.ts.net/` → `http://127.0.0.1:8000`。
- 验证：curl `--ssl-no-revoke` 与 Python `ProxyHandler({})` 各 8/8 成功；
  OpenCV 解码 `/qr.png?target=tailscale` = `https://your-machine.your-tailnet.ts.net/?v=…`；
  无头 Edge 加 `--no-proxy-server` 打开 `?qr=1`，卡片默认 `Tailscale` 标签。
- **代理坑（值得记住）**：本机 Windows 系统代理 `127.0.0.1:7890` 开着；默认
  `urllib`/Edge 会把 tailnet 域名交给 Clash，表现为 TLS `SSLEOFError` 或
  `ERR_CONNECTION_CLOSED`，而 curl / `ProxyHandler({})` / `--no-proxy-server` 正常。
  这不是证书或 Serve 的问题。`app/phone.py:reachable` 一开始就绕过了代理。
- 剩余：手机端验证 4G、添加到主屏幕（HTTPS 下 Service Worker 才会注册）；
  网页内按住说话还没实现，现在仍是输入法麦克风。
