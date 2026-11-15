# 手机还显示旧页面：版本握手与缓存失效

2026-11-15 · bug fix

## 症状

服务端已经是新首页（LAN 上直接访问能看到「今天这么走」），但手机扫码或从主屏幕
打开仍是旧页面：没有每日路线、还是四张打印卡。

## 根因

三层都不是「服务器没更新」：

1. **安装版 PWA 恢复而不导航**：从后台切回来时复用已打开的 DOM，服务器文件变了
   页面也不会重新加载。
2. **外壳没有显式缓存策略**：`/`、`/sw.js`、manifest 只有 etag/last-modified，
   没有 `Cache-Control`，浏览器/PWA 可能继续用旧壳。
3. **旧页面没有版本检查**：SW 升级本身要靠一次导航或 `registration.update()`
   才会被发现；旧页面这两件事都不做。

## 修复

- **build 握手**：`/` 每次读 `index.html`，把文件 mtime 注入
  `<meta name="app-build">`，响应 `no-store`；`/api/version` 返回同一个值。
- **页面自检**：记录 meta 的 build；回到前台（`visibilitychange`）和每 5 分钟
  比较 `/api/version`，不一致 `location.reload()`。
- **SW v7**：导航请求用 `fetch(..., {cache: "no-store"})`；注册用
  `updateViaCache: "none"`，加载时和每小时 `registration.update()`；
  `controllerchange` 时（已有旧 controller）reload 一次。
- `/sw.js` 与 `/` 都 `no-store`，manifest `no-cache`。
- 设置页显示「页面版本：<build>」，方便确认手机是否真的更新。
- **二维码带版本号**：桌面快捷方式每次运行 `launch.py` 都读取 `/api/version`，
  生成 `http://<LAN>:8000/?v=<build>`。微信内置浏览器会存自己的页面快照，
  光靠响应头压不住；换一个完整 URL 才能真正绕开旧快照。
- 鼓励卡移到倒计时 hero 后面：首页顺序变成 倒计时 → 鼓励 → 三个统计 →
  「今天这么走」，不用往下滚才看到。

## 验证

- LAN 直接访问 `http://192.168.1.93:8000/`：有「今天这么走」「记今天做了多少题」。
- 无头 Chrome（新 profile）：meta 与 `/api/version` 相等；`touch index.html`
  后调用 `checkAppBuild()`，页面 reload，meta 变为新 build；SW 控制器存在，
  cache 只有 `yantu-shell-v7`；无 JS 异常。
- `/sw.js` 响应头 `Cache-Control: no-store`，manifest `no-cache`。

## 限制

- 已经打开、且加载的是**没有版本检查的旧页面**，服务器无法主动让它刷新：
  需要用户把主屏幕 App 彻底划掉重开，或在浏览器里下拉刷新一次。
  更新到包含本修复的页面后，后续版本会自动发现并刷新。
- 微信里已经打开的旧页面必须重新扫**新版二维码**（带 `?v=`）；旧二维码图片
  打开的还是同一个无版本 URL，微信仍可能命中自己的快照。
- build id 取 `index.html` 的 mtime：只改 `sw.js` 或后端代码不会触发页面 reload，
  分别靠 SW 更新链路和服务端 JSON 正常生效。
