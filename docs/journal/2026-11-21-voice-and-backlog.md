# 按住说话（本机 ASR）+ 该补的账（每日弹窗直达）

2026-11-21 · 问 AI / 每日任务单

## 需求（_ming）

1. 把「网页内按住说话」做出来（Tailscale HTTPS 已通）。
2. 遗留待办（16 道放弃题分流、5 道超时题训练、2024 复测）不知道怎么做；
   希望每天有弹窗提示，点链接直接去做。

## 决定

1. **语音识别放本机，不接云 ASR。** SenseVoice int8 + sherpa-onnx，230 MB 模型
   一次性下载。理由：无 API 费用、无隐私外传、断网可用；实测 3 秒语音 0.15–0.25 s，
   首次加载 1.9 s，足够按住说话。
2. **浏览器直接录 PCM WAV。** 不用 MediaRecorder/ffmpeg：AudioContext +
   ScriptProcessor 收 Float32，前端降采样到 16 kHz 并编码 16-bit WAV，
   服务端只收 WAV；避免 webm/opus 解码依赖，手机兼容面大。
3. **语音只做输入草稿。** 转出的文字放进输入框，不直接发送；识别不准可改。
   手机键盘麦克风保留为兜底。
4. **遗留待办单独成区，不混进「今天的任务」。** 它由真实状态驱动（分流行、错题本、
   训练完成、复测记录），做完就消失；每天第一次打开的任务单自动展示。
   理由：这些是欠账，不应计入「今天还有几项没做」的时间预算。
5. **每条欠账一个直达动作。**
   - 分流 → 直接打开四象限「放弃」页（不用先找计划页）；
   - 入本 → 两半一次性导入 `no_idea`，幂等跳过已在库的题；
   - 训练 → 直接 POST 开始 5 题 × 8 分钟；来源优先 `slow`，不足 5 道用 `skipped`；
   - 复测 → 打开 2024 两半 PDF + 录入区，12 月 15 日为提醒日。
6. **「续做上次的训练」直接续。** 原任务只把用户带到计划页的训练卡，仍要再点一次；
   现在任务自带 `active_id`，点击直接打开冻结队列。

## 实现

- `app/asr.py`（新）：WAV 校验（16-bit PCM、单/双声道、8–48 kHz、≤45 s、≤8 MB）、
  线性重采样到 16k、SenseVoice 懒加载、解码锁、SenseVoice 标签清理、
  技术词小词表纠错、重复标点合并。
- `app/server.py`：`GET /api/stt`（就绪状态，不加载模型）、`POST /api/stt`
  （原始 WAV，线程池跑推理，503/400 分类）。
- `app/static/index.html`：问 AI 面板「🎙 按住说话」；pointer 事件、录音计时、
  松手编码上传、结果填框；关闭面板/换题时停止录音；首次在用户手势内创建
  AudioContext，避免 Android/iOS 因权限弹窗把上下文挂起。
- `app/briefing.py`：`_backlog()` 生成欠账；`_retest_todo()` 按 12-15 算 D-天数；
  `_drill_task()` 增加 `active_id`。
- `app/static/index.html`：任务单「该补的账」区、四个直达动作、
  `openBriefTask` 处理 `active_id`。
- `tools/fetch_asr_model.py`（新）：hf-mirror 续传模型 / tokens / 测试音，
  `--check` / `--force`。
- `requirements.txt`：`sherpa-onnx==2.0.2`；模型和 `.cache/` 不入 git。
- 文档：README、`docs/usage.md`、`docs/SUMMARY.md` 同步。

## 验证

- `py -3 -m pytest -q` → **403 passed, 10 subtests**（新增 7 条 ASR + 4 条 briefing）。
- 真实 HTTP：`POST /api/stt` 发 SenseVoice 自带 `zh.wav` →
  「开饭时间早上9点至下午5点」，5.59 s 音频推理 0.53 s（含首次加载）。
- 无头 Edge + CDP：
  - 今日弹窗出现「16 道放弃题还没分流」「2024 整卷复测（D-24）」；
  - 点第一条 → 四象限「放弃」页，16 行分流按钮；
  - 点「续做上次的训练」→ 直接打开限时拆解，进度 0/5（不再经过计划页）；
  - 假麦克风喂真实语音 WAV（“为什么负摩阻力取这个值”）→ 按住 3.6 秒 →
    `POST /api/stt` → 输入框出现「为什么负摩阻力取这个值？」；
  - `start.bat` 打开 `/?qr=1` 曾因带参数压制每日弹窗；现在收起二维码卡片后
    自动补开当天任务单（只允许 `qr` / `v` 两个参数，不抢深链目标）。
- 内联 JS `node --check` 通过。

## 限制 / 下一步

- SenseVoice 对生僻技术词仍会听错（示例：负摩阻力在 TTS 音上被听成“父母/父魔阻力”），
  已做小词表纠错；其他词以输入框可编辑兜底，不假装全对。
- 模型 230 MB 只下一次；换电脑要重跑 `tools/fetch_asr_model.py`。
- 网页内录音需要 HTTPS（Tailscale 或 localhost）；局域网 HTTP 下按钮会提示不可用。
- 本次与上一轮手机入口/Tailscale 的改动都还在工作区、未提交 PR；需要收束后一起过 CI。
- 手机端真实麦克风还需要 _ming 实机按住一次验证（CDP 用的是文件假麦克风）。

## 更新（2026-11-21）：手机桌面图标报「读取学习数据失败」

- 现场：PC 本机与 `https://your-machine.your-tailnet.ts.net/api/home` 都是 200；`tailscale status`
  显示手机 **offline（last seen 36 分钟前）**，`tailscale ping` 无回应。
- 机制：PWA 外壳由 Service Worker 缓存继续打开；`/api/*` 永远走网络，所以外壳能进、
  数据全失败。桌面图标的 origin 不能改，若它是 `.ts.net`，手机 Tailscale 没连上就必然失败；
  若它是旧局域网 HTTP，换 Wi-Fi/流量也必然失败。
- 处理：首页错误文案按来源给下一步，加「重新连接」；`online` / 回前台自动重试。
  操作侧：手机打开 Tailscale 确认已连接（安卓可设始终开启 VPN、关闭电池优化）；
  在家也可用电脑「扫码」的局域网入口；长期图标用 Tailscale HTTPS 地址重新添加。
