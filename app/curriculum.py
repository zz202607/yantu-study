"""Exam structure: ten case-book chapters and the nine exam categories.

`C1` (土力学) is the numerical core of the case paper but never gets its own
question, so its exam weight is zero.  `C2`..`C10` map one-for-one to the
nine categories in `docs/exam-blueprint.md`.  The 2025 case paper also carries
per-question category tags; `category_for_qid()` uses those for `A2025-*`.

Everything here is static curriculum knowledge, not user data.
"""

import json
import re
from pathlib import Path

# The case paper: 50 questions x 2 points.  `weight` is the number of
# questions the blueprint allocates (docs/exam-blueprint.md, checked against
# the source page image).
CASE_CATEGORIES = [
    {
        "key": "C1",
        "name": "土力学",
        "short": "土力学",
        "weight": 0,
        "exam_category": None,
        "core": [{"title": "土力学（李广信 第 3 版）", "code": ""}],
        "companions": [],
        "handbook": [
            {"name": "第四篇 地基评价和计算", "start": 435, "end": 531},
        ],
        "prototypes": "有效应力、渗透、固结、抗剪强度、土压力、承载力",
        "advice": "案例计算的内核；不单独出题，但每类题都可能用到。",
    },
    {
        "key": "C2",
        "name": "岩土工程勘察",
        "short": "勘察",
        "weight": 8,
        "exam_category": "勘察",
        "core": [
            {"title": "岩土工程勘察规范", "code": "GB 50021-2001(2009 年版)"},
        ],
        "companions": [
            {"title": "土工试验方法标准", "code": "GB/T 50123-2019"},
            {"title": "工程岩体试验方法标准", "code": "GB/T 50266-2013"},
            {"title": "工程岩体分级标准", "code": "GB/T 50218-2014"},
            {"title": "建筑工程地质勘探与取样技术规程", "code": "JGJ/T 87-2012"},
            {"title": "地基动力特性测试规范", "code": "GB/T 50269-2015"},
            {"title": "高层建筑岩土工程勘察标准", "code": "JGJ/T 72-2017"},
        ],
        "handbook": [
            {"name": "第二篇 工程勘察", "start": 79, "end": 183},
            {"name": "第三篇 岩土测试", "start": 184, "end": 434},
        ],
        "prototypes": "载荷试验、土工试验、水文参数测定、岩体质量分级",
        "advice": "不熟悉的偏题可先不做；先抓载荷试验与水文参数。",
    },
    {
        "key": "C3",
        "name": "浅基础",
        "short": "浅基础",
        "weight": 8,
        "exam_category": "浅基础",
        "core": [
            {"title": "建筑地基基础设计规范", "code": "GB 50007-2011"},
        ],
        "companions": [
            {"title": "公路桥涵地基与基础设计规范", "code": "JTG 3363-2019"},
            {"title": "铁路桥涵地基和基础设计规范", "code": "TB 10093-2017"},
            {"title": "水运工程地基设计规范", "code": "JTS 147-2017"},
        ],
        "handbook": [
            {"name": "第四篇 地基评价和计算", "start": 435, "end": 531},
            {"name": "第八篇第一章 浅基础", "start": 987, "end": 998},
        ],
        "prototypes": "承载力修正、基底压力、软弱下卧层、沉降计算",
        "advice": "常规题确保拿分；承载力修正的宽度/深度项是高频坑。",
    },
    {
        "key": "C4",
        "name": "深基础",
        "short": "深基础",
        "weight": 5,
        "exam_category": "深基础",
        "core": [
            {"title": "建筑桩基技术规范", "code": "JGJ 94-2008"},
        ],
        "companions": [
            {"title": "公路桥涵地基与基础设计规范", "code": "JTG 3363-2019"},
            {"title": "铁路桥涵地基和基础设计规范", "code": "TB 10093-2017"},
        ],
        "handbook": [
            {"name": "第八篇第二章 深基础", "start": 999, "end": 1063},
        ],
        "prototypes": "单桩竖向承载力、水平承载力、桩基沉降、负摩阻力",
        "advice": "细心，注意规范小字与桩型适用条件。",
    },
    {
        "key": "C5",
        "name": "地基处理",
        "short": "地基处理",
        "weight": 6,
        "exam_category": "地基处理",
        "core": [
            {"title": "建筑地基处理技术规范", "code": "JGJ 79-2012"},
        ],
        "companions": [],
        "handbook": [
            {"name": "第五篇 特殊性土（处理部分）", "start": 532, "end": 661},
            {"name": "第八篇第五章 地基处理", "start": 1165, "end": 1225},
        ],
        "prototypes": "预压固结度、复合地基置换率、复合地基承载力与沉降、多桩型",
        "advice": "大部分题要确保拿分；置换率与面积换算别混。",
    },
    {
        "key": "C6",
        "name": "土工结构与边坡防护",
        "short": "边坡",
        "weight": 6,
        "exam_category": "边坡",
        "core": [
            {"title": "建筑边坡工程技术规范", "code": "GB 50330-2013"},
        ],
        "companions": [
            {"title": "公路路基设计规范", "code": "JTG D30-2015"},
            {"title": "铁路路基设计规范", "code": "TB 10001-2016"},
            {"title": "铁路路基支挡结构设计规范", "code": "TB 10025-2019"},
            {"title": "土工合成材料应用技术规范", "code": "GB/T 50290-2014"},
        ],
        "handbook": [
            {"name": "第八篇第四章 边坡工程", "start": 1122, "end": 1164},
            {"name": "第六篇第三章 滑坡和崩塌", "start": 690, "end": 719},
        ],
        "prototypes": "朗肯土压力、重力式挡墙稳定、圆弧/直线/折线滑动、锚杆拉力",
        "advice": "部分难题留到最后；先保重力式挡墙稳定与土压力。",
    },
    {
        "key": "C7",
        "name": "基坑工程与地下工程",
        "short": "基坑",
        "weight": 4,
        "exam_category": "基坑",
        "core": [
            {"title": "建筑基坑支护技术规程", "code": "JGJ 120-2012"},
        ],
        "companions": [
            {"title": "铁路隧道设计规范", "code": "TB 10003-2016"},
            {"title": "公路隧道设计规范 第一册 土建工程", "code": "JTG 3370.1-2018"},
            {"title": "建筑基坑工程监测技术标准", "code": "GB 50497-2019"},
        ],
        "handbook": [
            {"name": "第八篇第三章 基坑工程", "start": 1064, "end": 1121},
            {"name": "第七篇第三章 地下工程", "start": 814, "end": 854},
        ],
        "prototypes": "弹性支点法、锚杆长度、稳定性、基坑降水、围岩压力",
        "advice": "计算量大的偏题留后；先做稳定性与降水。",
    },
    {
        "key": "C8",
        "name": "特殊条件下的岩土工程",
        "short": "特殊土",
        "weight": 7,
        "exam_category": "特殊土",
        "core": [
            {"title": "湿陷性黄土地区建筑标准", "code": "GB 50025-2018"},
            {"title": "膨胀土地区建筑技术规范", "code": "GB 50112-2013"},
            {"title": "盐渍土地区建筑技术规范", "code": "GB/T 50942-2014"},
        ],
        "companions": [
            {"title": "铁路工程特殊岩土勘察规程", "code": "TB 10038-2022"},
            {"title": "公路路基设计规范（特殊路基）", "code": "JTG D30-2015"},
        ],
        "handbook": [
            {"name": "第五篇 特殊性土", "start": 532, "end": 661},
            {"name": "第六篇 特殊地质条件", "start": 662, "end": 787},
        ],
        "prototypes": "湿陷性土、膨胀土、冻土、盐渍土、滑坡、泥石流",
        "advice": "首选拿分题；湿陷与膨胀的判别标准要背到能直接翻。",
    },
    {
        "key": "C9",
        "name": "地震工程",
        "short": "地震",
        "weight": 4,
        "exam_category": "地震",
        "core": [
            {"title": "建筑抗震设计规范", "code": "GB 50011-2010(2016 年版)"},
        ],
        "companions": [
            {"title": "公路工程抗震规范", "code": "JTG B02-2013"},
            {"title": "水电工程水工建筑物抗震设计规范", "code": "NB/T 35047-2015"},
            {"title": "中国地震动参数区划图", "code": "GB 18306-2015"},
            {"title": "地基动力特性测试规范", "code": "GB/T 50269-2015"},
        ],
        "handbook": [
            {"name": "第六篇第七章 地震", "start": 760, "end": 785},
        ],
        "prototypes": "场地类型判别、液化指数、反应谱、地震作用",
        "advice": "首选拿分；场地类别与液化复判是送分点。",
    },
    {
        "key": "C10",
        "name": "岩土工程检测与监测",
        "short": "检测",
        "weight": 2,
        "exam_category": "检测",
        "core": [
            {"title": "建筑基桩检测技术规范", "code": "JGJ 106-2014"},
            {"title": "建筑地基检测技术规范", "code": "JGJ 340-2015"},
        ],
        "companions": [
            {"title": "建筑基坑工程监测技术标准", "code": "GB 50497-2019"},
            {"title": "建筑变形测量规范", "code": "JGJ 8-2016"},
        ],
        "handbook": [
            {"name": "第八篇第六章 现场检验与监测", "start": 1226, "end": 1247},
            {"name": "第三篇 岩土测试", "start": 184, "end": 434},
        ],
        "prototypes": "桩基检测、载荷试验、基坑监测、变形测量",
        "advice": "题量少但简单，视情况先做。",
    },
]

CASE_BY_KEY = {row["key"]: row for row in CASE_CATEGORIES}

# Questions in these categories carry the most marks per hour of study for
# most candidates.  The 2025 baseline showed the opposite for this user, so
# the list is a starting weight, not a personal priority (see the baseline
# postmortem); the app keeps it in one place so plan and baseline agree.
MUST_HAVE_KEYS = ("C3", "C5", "C8", "C9")
CASE_BY_EXAM_NAME = {
    row["exam_category"]: row["key"]
    for row in CASE_CATEGORIES
    if row["exam_category"]
}
CASE_WEIGHT_TOTAL = sum(row["weight"] for row in CASE_CATEGORIES)

_CASE_QID_RE = re.compile(r"^C(\d{1,2})-\d")
_A2025_PREFIX = "A2025-"


def category_key(qid, exam_tags=None):
    """Return the `C*` key a question belongs to, or None when unmapped.

    C-family qids carry the chapter number directly.  A2025 exam questions
    carry a human category in `index/tags/A2025.json`; callers pass that map
    when they have it.  Other exam and mock questions stay unmapped rather
    than being forced into a category.
    """
    match = _CASE_QID_RE.match((qid or "").upper())
    if match:
        key = "C%d" % int(match.group(1))
        return key if key in CASE_BY_KEY else None
    if exam_tags and qid in exam_tags:
        return CASE_BY_EXAM_NAME.get(exam_tags[qid].get("category"))
    return None


def category(key):
    return CASE_BY_KEY.get(key)


def category_rows():
    """JSON-safe curriculum rows for the standards screen."""
    return [
        {
            "key": row["key"],
            "name": row["name"],
            "short": row["short"],
            "weight": row["weight"],
            "weight_share": round(row["weight"] / CASE_WEIGHT_TOTAL, 4),
            "exam_category": row["exam_category"],
            "core": row["core"],
            "companions": row["companions"],
            "handbook": row["handbook"],
            "prototypes": row["prototypes"],
            "advice": row["advice"],
        }
        for row in CASE_CATEGORIES
    ]


_TAGS_CACHE = {"stamp": None, "data": {}}


def load_exam_tags(index_dir):
    """Per-question 2025 tags, or an empty map when the file is absent."""
    path = Path(index_dir) / "tags" / "A2025.json"
    if not path.exists():
        return {}
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    if _TAGS_CACHE["stamp"] == stamp:
        return _TAGS_CACHE["data"]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    _TAGS_CACHE.update(stamp=stamp, data=data)
    return data
