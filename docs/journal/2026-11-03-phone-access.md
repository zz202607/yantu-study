# M13 手机接入：一次配置，扫码进 App

## 背景

用户反馈「每次在手机上打开都很麻烦」。排查后先确认：系统**没有账号密码**，
手机端不是「登录」，而是打开电脑上运行的本地服务。摩擦来自三件事：

- 电脑服务未启动（实测 `127.0.0.1:8000` 为 down 时手机必然打不开）；
- 路由器 DHCP 重新分配后 IP 变化，主屏图标存的地址失效；
- 局域网 HTTP 不是 secure context，Service Worker 不注册，不能真正安装 PWA。

## 做了什么

- **`app/net.py`**：选手机可达的局域网 IPv4。
  - 优先 `192.168/16` → `10/8` → `172.16/12`；
  - 排除 loopback、link-local、公网，以及 VPN TUN 常用的 `198.18.0.0/15`
    （Clash 等会把默认路由抢走，原来的 UDP 取源地址方法会返回这个假地址）；
  - `100.64.0.0/10`（Tailscale）不算局域网，只有手机也装 Tailscale 才可达；
  - `YANTU_HOST` 可原样覆盖（允许主机名）。
  - `app/server.py` 启动打印改用同一函数。
- **`tools/launch.py`**：一键启动。
  - 已运行则复用；未运行则以 `CREATE_NO_WINDOW` 拉起后台服务，日志写 `.cache/server.log`；
  - 打印电脑/手机地址，用 segno 生成 372×372 二维码 PNG 并打开，同时打开电脑浏览器；
  - `--stop` 用 PowerShell 精确停止监听该端口的进程，不误杀其它 Python；
  - 端口被别的程序占用时明确报错，不假装启动成功。
- **`start.bat` / `stop.bat`**：双击入口。
- **`tools/autostart.py`**：在当前用户启动文件夹写入 `YantuStudy.lnk`，无需管理员；
  `--install` / `--status` / `--remove`。
- **`tools/serve_background.py`**：后台启动包装。
  - 快捷方式第一次直接指向 `app/server.py` 时，服务启动即崩：pythonw 的
    `sys.stdout` / `sys.stderr` 是 `None`，服务启动那行 `print()` 直接抛异常；
  - 包装器先把 stdout/stderr 重定向到 `.cache/server.log`，再 `runpy` 执行服务入口。
- **依赖**：`segno==1.6.6`（纯 Python，无额外依赖）。

## 验证

- `tests/test_net.py` 5 条：家庭地址优先、10 段压过 Docker 段、Tailscale/公网被排除、
  override 原样生效、非法值忽略；全套 **262 条全绿**。
- 启动链路：服务 down → `launch.py --no-open` → 健康检查 **200**，二维码 **372×372**；
  再次运行显示「服务已在运行」。
- 停止链路：`--stop` 输出 `stopped <pid>`，8000 随即不可达。
- 自启链路：用启动文件夹快捷方式拉起 → **200**，`.cache/server.log` 有启动行。
- 真实地址：`192.168.1.3:8000`（本机只有一个物理网卡，默认路由与主机名解析一致）。

## 限制 / 下一步

- 路由器 DHCP 保留只能由用户在路由器后台完成；不做的话 IP 仍可能变。
- 局域网 HTTP 没有 secure context：SW 不注册、不能自动安装、不能离线；
  需要真正 PWA 时走 Tailscale（HTTPS + 固定 MagicDNS 名称，4G 也能用）。
- 服务无鉴权，同 Wi-Fi 内任何人都能读写 `data/`；只在家用可信网络开启。
- 重装 Python 或换机器后要重跑 `tools/autostart.py --install`。
