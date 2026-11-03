# 岩土学习驾驶舱 · Yantu Study Cockpit

[![CI](https://github.com/zz202607/yantu-study/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/zz202607/yantu-study/actions/workflows/ci.yml)

把 12 本扫描题库变成「拍题号 → 秒出解析 → 自动归类错因 → 按数据复习」的本地学习系统，
服务 2027 年注册岩土工程师（专业考试）备考。

> 个人备考项目：不追求通用题库平台，只解决「纸面做题 + 手机记录 + 数据驱动复习」这一条链路。

## 现在能做什么

- **拍题查题**：手机拍整页 → 题号候选 → 题干/解析裁图；支持整页多题勾选、批量查看、自动入库。
- **错题本**：一题一行，4 类错因、备注、手写照片；按册/篇/时间/掌握状态过滤；一键备份/恢复。
- **零决策复习**：到期 → 新错 → 顽固，自动排期，每日硬上限 20；可续做、撤销、打印复习卷。
  - 案例题按过程评分（过程对·结果对 / 过程对·结果错 / 过程错 / 没思路），可附过程照片；
    知识题另走客观题判分，不用选项字母判案例。
- **学习驾驶舱**：考试倒计时、今日复习、连续天数、九大类弱项、证据型鼓励、下一步行动卡。
- **计划控制**：五阶段（基线/基础/强化/套卷/冲刺）、按周题量、练习记录、阶段目标、落后/超时纠偏。
- **2025 案例基线**：手机内直接记两半（四个数 + 可选逐题状态 + 答案纸照片），
  半卷 50 分、两半合计 100 / 通过线 60；九大类分项；错题一键入本。
- **规范 / 手册联动**：九大类 → 核心规范 → 章节目录 → App 内页图；条文号定位；
  《工程地质手册》全文检索 + 页图查看。
- **规范伴读**：9 本核心规范、67 站，按案例权重排路线；每站 6–18 分钟，
  看标注版原页，配「为什么考 / 先看什么 / 最容易错 / 30 秒自检 / 条文 / 同类题」；
  已读/需回看进度落库，首页直达下一站。
- **周报 / 月报**：按自然周/月生成一页，含练习、复习、计划达成、对比上期、九大类分项、
  基线、下一步与证据型鼓励；可保存为 Markdown 快照（`.md` + `.json`），在 App 内查看/下载。
- **打印**：A4 题面/答案分页、手写区、Z 族答案遮罩、封面任务单、每题回填格、基线卷与周任务卷。

## 快速开始

环境：**Python 3.13**（Windows 11 实测；Linux/macOS 未验证），扫描件需自备。

```bash
git clone https://github.com/zz202607/yantu-study.git
cd yantu-study
py -3 -m pip install -r requirements-dev.txt
py -3 app/server.py
```

启动后会打印局域网地址，手机和电脑连同一 Wi-Fi 即可访问；Windows 防火墙首次弹窗
允许**专用网络**。手机浏览器「添加到主屏幕」可当 PWA 打开。

### 本地数据（不入库）

仓库只提交可重建的索引与代码。以下目录按需在本地准备：

| 路径 | 说明 |
|---|---|
| `compressed/` | 12 本扫描题库的唯一副本（约 1.5 GB）；书名需与 `index/books.json` 对应 |
| `工程地质手册（第五版）.pdf` | 手册原件，放项目根目录（不入库） |
| `data/` | **唯一用户数据**：`wrongbook.db`、照片、备份、导出、周报快照；首次启动自动建库 |
| `print_samples/` | 打印样张与基线卷 PDF，可由 `tools/print_baseline.py` 重建 |
| `拍照错题/` | 18 张真实错题照片，仅作 OCR/裁图基准 |

没有扫描件时，索引浏览与计划/基线仍可用；裁题、打印、手册页图需要对应 PDF。

### 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `PORT` | `8000` | 服务端口 |
| `YANTU_DATA` | `data/` | 用户数据库目录（测试可指向临时库） |
| `YANTU_STANDARDS_DIR` | 本机规范库目录 | 50 本电子规范所在目录 |
| `YANTU_ANNOTATED_DIR` | 标注版核心规范目录 | 9 本伴读路线用到的标注版 PDF |
| `YANTU_HANDBOOK` | 根目录手册 PDF | 手册路径 |

## 测试

```bash
PYTHONIOENCODING=utf-8 py -3 -m pytest -q
```

- **242 条**单测全绿；GitHub Actions 在 PR / push `main` 时运行 compileall + pytest。
- CI 只装 `requirements-ci.txt`（pytest / pymupdf / pillow / numpy），
  不含 OCR / ONNX / FastAPI 重依赖。

## 项目结构

```text
app/
  server.py          FastAPI：拍照识别、裁图、错题本、复习、计划、基线、规范/手册、打印
  store.py           SQLite：错题、复习排期、练习台账、2025 基线与弃题分流
  baseline.py        2025 案例两半：四个数、逐题状态、四象限、必拿分项、考场推演
  plan.py            五阶段计划、周题量、纠偏、任务卷选题
  report.py          自然周/月报告：聚合、对比上期、下一步、Markdown 快照
  analytics.py       首页/统计：倒计时、连续天数、九大类、错误型鼓励
  curriculum.py      十篇 / 九大类 / 权重 / 核心规范
  standards.py       规范库与手册：索引、条文定位、页图、全文检索
  reading.py         规范伴读：路线、进度、推荐、标注版页图/干净版回退
  objective.py       知识题客观判分（案例不走这里）
  print_export.py    A4 题面/答案/手写区/封面/回填格
  static/            移动优先前端 + PWA manifest/Service Worker

tools/              索引提取、题号识别、裁题、打印、基准与备份 CLI
index/              lookup.json（8,826 题）、书目/章节/题号框/2025 分类/规范索引/标注版页索引
tests/              242 条单测
docs/               备考策略、陪练规则、架构总结与 journal
data/               用户数据（本地，不入库；含 reports/ 周报快照）
compressed/         扫描题库（本地，不入库）
```

数据边界：`index/` 只读、可重建；`data/` 是唯一用户数据，重建索引不影响错题本。
备份/恢复：

```bash
PYTHONIOENCODING=utf-8 py -3 tools/backup_wrongbook.py backup
PYTHONIOENCODING=utf-8 py -3 tools/backup_wrongbook.py restore data/backups/<name>.zip --yes
```

## 2025 基线怎么记

1. 打印 `print_samples/baseline_2025_A.pdf` / `_P.pdf` 的题面页（上午 1–17 页、下午 1–16 页）。
2. 各限时 3 小时，按考场规则写过程；两半都做完再对答案。
3. 打开 App「计划 → 2025 案例基线」：
   - 逐题点「对 / 错 / 弃 + 超时」；录满 25 题自动算四个数并锁定；
   - 或只填四个数（作答 / 做对 / 放弃 / 超时）；
   - 可附答案纸照片，把做错的题一键加入错题本。

案例是主观题：**做对 = 过程与结果均正确，只有答案无过程按 0 分**。系统不按 A/B/C/D 判案例。

## 文档

| 文件 | 内容 |
|---|---|
| `PROGRESS.md` | 热点状态：已完成、关键事实、下一步、怎么继续 |
| `docs/SUMMARY.md` | 一页项目总结：目标、架构、决策、指标、限制、下一步 |
| `docs/journal/` | 每个里程碑的决策与踩坑记录 |
| `docs/study-plan.md` | 备考策略与题量拆分 |
| `docs/coach/playbook.md` | 陪练规则、基线协议、纠偏阈值 |
| `docs/coach/practice-order.md` | 做题顺序与六步标准 |
| `docs/coach/prototypes.md` | 47 个原型小节与题号范围 |
| `docs/coach/standards-map.md` | 九大类 → 规范 → 手册页码 |
| `docs/objective-scoring.md` | 知识题客观判分边界 |

## 已知限制

- 扫描件无文字层，OCR 只用于题号/规范号定位，题干与公式保留原图。
- 2025 基线与九大类分项只覆盖案例 A 族；K 族知识题基线未接入。
- 规范库路径、题库文件名与本机绑定；换机器需设置环境变量或重建索引。
- 个人项目，未附带第三方教材/规范版权文件，也未声明开源许可证。

---

从 `PROGRESS.md` 开始读，能在一屏内了解当前状态与下一步。
