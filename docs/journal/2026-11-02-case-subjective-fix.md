# 案例主观题 / 知识客观题边界修正（M5.1）

日期：2026-11-02 · 触发：_ming 指出「注册岩土专业案例是主观解答题，不能按 A/B/C/D 判分」

## 发现了什么

旧 `tools/score_baseline.py` 读 `index/tags/A2025.json` 的 25 个字母，
用「你选 A/B/C/D vs 资料书答案」算案例基线得分。这是**错的口径**：

- 专业案例（C/A/M，第二天上午/下午各 25 题）是主观题；
  评分标准要求写过程，**只有答案无过程按 0 分**，过程与结果都要对。
- 华S 资料把部分案例题排成带选项的练习（答案页写 `1.【答案】（C）`，
  题面底部有 A/B/C/D 数值选项），这是**资料书选项**，不是考试答题卡。
- `docs/study-plan.md` 早就写了「仅有答案无过程一律 0 分」；脚本与文档自相矛盾。
- 知识题（Z/K）是客观题：40 单选 + 30 多选 × 2 场；
  多选错选/多选/少选均不得分。25 字母也不是知识卷的格式（知识卷是 70 题/半场）。

结论：**案例和知识题不能共用一套判分逻辑**；旧脚本把案例当选择题，必须修。

## 修了什么

### 1. 案例基线：只收四个数

`tools/score_baseline.py` 重写：

```bash
py -3 tools/score_baseline.py --half A --answered 25 --correct 15 --overtime 2 --write
```

- 作答题数 / 做对数 / 放弃数 / 超时题数；做对 = 过程与结果均正确。
- 不再 import `A2025.json`，不读取任何选项字母。
- 报告明确写「案例为过程评分；人工按过程自评，不按选项字母判分」。
- `app/analytics.py` 的基线解析改为读四个数；旧表格格式仍兼容但不再生成。
- `docs/coach/A2025-paper.md` 把「答案」列改名为「资料书选项」，顶部加警告。

### 2. 知识题：独立客观判分模块

新增 `app/objective.py` + `tools/score_objective.py` + `tests/test_objective.py`：

- 单选：一个字母；
- 多选：集合完全相等；少选/多选/错选均 0 分；
- 按类别汇总题数、作答、答对、得分；
- 数据驱动答案键，不内置任何案例选项键；
- 边界文档：`docs/objective-scoring.md`。

### 3. 案例复习：过程状态 + 手写过程照片

- 数据层：`attempts` 增加 `last_process_status` / `process_photo_id`；
  `review_log` 增加 `process_status_before/after`、`process_photo_before/after`。
- 状态（用户手动点选，系统不推断）：
  - `过程对 · 结果对` → correct
  - `过程对 · 结果错` → wrong
  - `过程错` → wrong
  - `没思路` → wrong
- 复习页：案例题显示四个状态按钮 + 「拍手写过程」；知识题仍是「没做对 / 做对了」两按钮。
- 评分时照片随 `grade` 入库；详情页显示上次过程状态与过程照片，可补拍/更换。
- 撤销：排期、过程状态、过程照片一起回滚（review_log 记录 before/after）。
- 删除/孤儿清理：过程照片被 attempts 或 review_log 引用时不会被误删。
- 键盘：案例 1–4 选四个状态；知识题 1/2 仍是错/对。
- 滑动评分对案例禁用：避免把「过程错」误判成普通「做对/没做对」。

## 验证

- `PYTHONIOENCODING=utf-8 py -3 -m pytest -q` → **134 passed**
  （新增：案例过程状态/照片/撤销、知识题拒绝过程状态、客观题单选多选、基线四个数、
  基线解析兼容）。
- API 端到端（临时数据副本）：
  - 上传过程照片 → `POST /api/review/grade` 带 `process_status=process_right_result_wrong`
    + `process_photo_sha` → attempt 返回 `last_result=wrong`、状态和照片；
  - `POST /api/review/undo` → 状态/照片回滚为 None。
- 浏览器（无头 Chrome 390×844）：
  - 案例复习显示「拍手写过程」+ 四状态按钮，选「过程对·结果错」后入库并进入下一题；
  - 知识题（Z1-1）不显示过程行/四状态，只显示两按钮。

## 没做什么 / 边界

- 仍不做手写 OCR / 自动批改过程（`docs/v1-concept.md` 既定）。
- 应用内基线记分（四个数 + 过程状态/照片）尚未做；当前基线仍用 CLI。
- 知识题答案键还没有导入真实 K 卷；`score_objective.py` 是通用模块，等答案键。
- 资料书选项仍保留在 `index/tags/A2025.json` 供题号/类别/资料核对，
  任何判分路径都不得读取它。
