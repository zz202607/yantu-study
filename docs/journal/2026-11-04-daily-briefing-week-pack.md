# 2026-11-04 · M14 每日任务单 + 出差周包（打开就知道今天做什么）

## 问题

1. **每天打开 App，看到的是统计和「下一步」卡片，不是一张可执行的任务单。**
   「下一步」只有 3 条建议，且和今日复习、训练、伴读各自分散在不同页面；
   用户需要自己把「今天该做什么」拼起来。手机 App 的第一屏应该直接回答
   「现在做什么」，并且这个回答要能查看、不是一次性的提示。
2. **打印文件全叫 `yantu-print-<timestamp>.pdf`。** 手机下载文件夹里一份份长得
   一样，分不清哪份是今日复习、哪份是任务卷；页面页脚也只到日期。
3. **出差要打一周的量，现有 scope 最多 30 题、只覆盖「今天」。**
   复习队列每天上限 20，一周最多 140 题；必须按天分组、答案封存，
   并且如实说明这是排期预测。

## 决定

1. **任务单是「日志视图」，不是第 4 份计划。**
   每个任务的 done 只读 `review_log` / `practice_sessions` / `drill_sessions` /
   `reading_log` 当天的真实行；不在别处再存一份「今日勾选」。
   这样纸上做完不录，任务就不会假打勾——和复习排期是同一个事实源。
2. **首次打开自动弹，关闭后当天不再打扰。**
   `localStorage.briefingSeen = 当天日期`；deep link（`?qid=` / `?tab=`）不弹，
   避免盖住用户明确要去的页面。顶部常驻「今日」和首页「今日任务单」随时重开。
3. **四项任务按「基本盘 → 加练」排序。**
   错题复习、新题练习（周目标摊到剩余天数，台账记录合在这一项）、
   限时拆解/认题卡（只在基线可练 ≥5 题或训练未完成时出现）、
   规范伴读下一站。后两项标 optional，但都显示完成态。
4. **说明分两层。**
   任务单内嵌「每天三步」；「完整说明」/`?tab=guide` 打开四段式手册
   （每天/打印/出差/手机与数据），同一内容落 `docs/usage.md`，可打印可分享。
5. **文件名 = 卷名 + 秒级时间戳，服务端是唯一命名者。**
   `岩土-<safe_title>-YYYYMMDD-HHMMSS.pdf`；`safe_title` 去掉 Windows 非法字符
   并截 40 字。响应头回传 `X-Print-Filename`（percent-encoded，ASCII 安全）和
   `X-Print-Stats.filename`，前端优先用服务端名字，固定名只作降级。
   卷名带筛选条件（如 `错题卷-案例-算错-未掌握`），页脚时间到分钟。
6. **周包按现有排期逐日模拟，20 题/天、超量顺延。**
   `review_forecast()` 只用 `next_at`（空则 `last_at`），沿用 `_interleave`
   章节交错；每天一页小封面（日期、题数、预计分钟、qid 回填格），
   当天题目跟在封面后；末尾附 `week_drill()` 选出的「本周任务 · 未完成原型」
   新题（≤30，和复习去重），保证复习队列薄的时候周包仍有整周工作量。
   所有答案在整包末尾按天分组（caption 前缀 D1/D2/W）。
   上限 100 题：新题先占额度，复习依次裁剪；超出在封面上显式写「只排前 100 题」。
7. **不假装预测是准确值。**
   封面上写「今天做对/做错会改变后面几天」；回来录结果后在 App 里排期自动续上，
   周包只用于出发前的一次性纸面计划。

## 实现

- 新模块 `app/briefing.py`：`build()` 产出日期、倒计时、`day_activity`、
  任务数组（`key/title/detail/route/minutes/done/progress/optional`）、
  `done_count/total_count/lead/manual`；`_drillable()` 复用基线四象限口径。
- `app/store.py`
  - `day_activity(day)`：一条 SQL 取当天复习/做对/新掌握/新错题/练习/训练/伴读计数。
  - `review_forecast(days, now, limit)`：按日切桶、章节交错、20 题上限、
    剩余顺延；返回 `day/weekday/qids/count/overdue`。
- `app/print_export.py`
  - `safe_title()` / `print_filename()`；
  - `export_pdf(sections=...)`：每节可带 `title/subtitle/short`，先生成小节封面，
    再排当天题目；答案 Sheet 在全部题面之后渲染，caption 带 `D1/D2` 前缀；
    `_stamp` 支持跳过任意封面页；`max_questions` 可按 scope 放大到 100。
  - 显式 `qids: []` 的封面不再回退到全部题号（否则 100 题回填表会溢出）。
- `app/server.py`
  - `GET /api/briefing`（读取失败不影响主页）；
  - `/api/print` 新增 `scope=week`，`days` 1–10，构造 daily sections；
    `filter` 卷标题拼筛选条件；输出名走 `print_filename()`；
    响应头 `X-Print-Filename` + `X-Print-Stats.filename`。
- `app/static/index.html`
  - 顶部「今日」、首页「今日任务单」；`#briefSheet` 自动弹层（当日一次）、
    `#manualSheet` 完整说明；任务点击走 review/drill/reading/plan 路由；
  - 打印面板新增「未来 N 天复习包」+ 3/5/7 天选择；`runPrint` 使用
    `stats.filename` / `X-Print-Filename`，下载名不再固定；
  - 深链 `?tab=guide` 直达完整说明。
- 文档：`docs/usage.md`（每日/每周/打印/出差/手机/备份/FAQ）。

## 验证

- 单测 277 条全绿（新增 15 条）：
  - 任务单完成态只随日志翻转、空日复习打勾、练习/伴读计数、训练只在
    ≥5 题可练或未完成时出现；部分复习（3/5）不会误判完成；
  - 周目标按剩余天数摊到当天（周三 10/32 → 今天约 5 题），记台账后翻转；
  - 预测 25 题到期 → 20+5、跨日不重复、未来题落在自己的日期、limit 夹到 20；
  - 文件名含卷名+时间戳、两次同名不同秒、Windows 非法字符替换、空标题回退、
    sections 去重和全局上限；
  - 周包导出：每节封面 + 答案封存 + 无题面/答案混页。
- 真实数据 `/api/briefing`：错题 20（到期 20）、训练 13 道可练、伴读抗震 4.1、
  台账 4 项约 93 分钟；JSON 四项任务与完成态正确。
- 打印接口实测：`qids=C3-8` → `岩土-单题卷-20261104-094712.pdf`，
  Content-Disposition/`X-Print-Filename`/`X-Print-Stats.filename` 三处一致；
  `scope=week&days=1&cover=1` → 29 页 / 20 题 / 23 s / 0 错误，
  页序：总封面 → 每日封面 → 题面 → 答案（D1 前缀）；
  加入本周新题后再测 → 50 题 / 68 页 / 65 s / 0 错误，
  两节：`第 1 天 20 题` + `本周任务 · 未完成原型 30 题`，答案前缀 D1/W。
- 无头 Chrome 800 px：今日任务单弹层与完整说明逐屏验收；390 px 为无头窗口
  最小宽度裁剪（非布局溢出），800 px 下排版完整。

## 遗留 / 下一步

- 周包超过 100 题时分两次打印；若真实需求经常超，再考虑后台异步任务 + 进度轮询。
- 手机端首次打开自动弹层的时机依赖 `/api/briefing` 返回；弱网时首页会先渲染，
  弹层稍后出现；失败静默，不阻塞驾驶舱。
- 打印文件仍保留在 `data/exports/` 7 天，之后自动清理；如需要「打印历史」另加。
