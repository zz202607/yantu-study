"""Build the read-only catalog tables the app loads at startup.

Two outputs in index/:

- books.json    : 13-row registry (12 question books + the handbook)
- chapters.json : every 篇/节 of the case exercise set, with its qid range,
                 解析 page and blank-book page.

The section table below is transcribed from the OCR of the printed 目录
(200 dpi, `.probe/frontpages/`) and cross-checked against the page that each
section's first question actually falls on. Book pages are the numbers printed
in the corner, not PDF pages.

Usage:
    py -3 tools/build_catalog.py
"""

import json
import os
import re

import pymupdf

from bookpath import book_pdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index")

# qid chapter number -> the volume that owns it. The other volumes contain a
# few phantom labels (a figure label that happens to read like `8-16`), so a
# page lookup must always go through the owning volume.
OWNER = {
    1: "案例习题集上", 2: "案例习题集上", 3: "案例习题集上",
    4: "案例习题集中", 5: "案例习题集中", 6: "案例习题集中",
    7: "案例习题集下", 8: "案例习题集下", 9: "案例习题集下", 10: "案例习题集下",
}
# printed book page -> PDF page offset (PDF = book + offset)
OFFSET = {1: 18, 2: 18, 3: 18, 4: -506, 5: -506, 6: -506, 7: -994, 8: -994, 9: -994, 10: -994}

# (title, printed start page). Boundaries are the first question of each 节.
SECTIONS = {
    1: [("第1章 土的物理性质与工程分类", 1), ("第2章 土的渗透性和渗流问题", 30),
        ("第3章 土体中的应力计算", 58), ("第4章 土的变形特性和地基沉降计算", 83),
        ("第5章 土的抗剪强度", 112), ("第6章 挡土结构物上的土压力", 133),
        ("第7章 边坡稳定分析", 161), ("第8章 地基承载力", 180),
        ("第9章 土的动力特性", 190)],
    2: [("第一章 勘探、取样和土的分类、参数统计", 195), ("第二章 原位测试", 213),
        ("第三章 土工试验", 245), ("第四章 工程岩体试验方法标准", 273),
        ("第五章 工程地质、岩石分类和岩体分级", 285), ("第六章 地下水勘察、腐蚀性评价", 312),
        ("第七章 工程勘察其它相关偏僻规范", 336)],
    3: [("第一节 地基承载力计算与修正", 365), ("第一节 基底压力/持力层验算", 380),
        ("第一节 软弱下卧层验算", 407), ("第二节 地基稳定性/抗浮", 422),
        ("第三节 变形与沉降", 432), ("第四节 基础设计（冲切/剪切/受弯）", 462),
        ("第二章 公路桥涵基础", 491), ("第三章 铁路桥涵基础", 508), ("第四章 水运地基", 516)],
    4: [("第一节 桩基承载力（竖向/群桩/负摩阻/抗拔/桩身/水平）", 523),
        ("第二节 桩基沉降", 608), ("第三节 承台计算", 633),
        ("第二章 公路桥涵桩基", 651), ("第三章 铁路桥涵桩基", 669)],
    5: [("第一节 基本规定（承载力修正）", 680), ("第二节 换填垫层", 681),
        ("第三节 压实与夯实地基", 690), ("第四节 预压地基", 694),
        ("第五节 复合地基", 724), ("第六节 注浆加固", 794), ("第七节 微型桩加固", 801)],
    6: [("第一节 岩土压力计算", 805), ("第二节 边坡稳定性", 862),
        ("第三节 重力式挡土墙", 908), ("第四节 锚杆（索）与板桩", 931),
        ("第五节 板桩式挡墙", 944), ("第二章 铁路路基/支挡结构", 946),
        ("第三章 公路路基", 970), ("第四章 土工合成材料", 982), ("第五章 其他边坡规范", 1001)],
    7: [("第一节 水平荷载（水土压力）", 1007), ("第二节 支挡式结构", 1025),
        ("第三节 土钉墙", 1053), ("第四节 重力式水泥土墙", 1060),
        ("第五节 地下水控制", 1073), ("第二章 铁路隧道", 1083), ("第三章 公路隧道", 1098)],
    8: [("第一章 湿陷性黄土", 1109), ("第二章 膨胀土", 1143), ("第三章 盐渍土", 1170),
        ("第四章 冻土", 1189), ("第五章 其他特殊性土", 1202), ("第六章 岩溶和土洞", 1210),
        ("第六章 滑坡和崩塌", 1215), ("第六章 泥石流", 1222),
        ("第六章 采空区", 1230), ("第六章 地面沉降", 1235)],
    9: [("第一节 场地、地基与场地类别", 1243), ("第一节 液化判别与液化指数", 1259),
        ("第二节 地震作用与地震影响系数", 1285), ("第二章 公路工程抗震", 1303),
        ("第三章 水电工程抗震", 1319), ("第四章 中国地震动参数区划图", 1335),
        ("第五章 地基动力特性测试", 1336)],
    10: [("第一章 建筑基桩检测", 1341), ("第二章 其他规范检测内容", 1369),
         ("第三节 锚杆抗拔检测", 1393)],
}

BOOKS = [
    # id, file, short name, family, role, owned 篇, note
    ("case_sol_1", "2026【正版高清扫】华S案例习题集上_扫描版.pdf", "案例习题集·上", "C", "solutions", [1, 2, 3], ""),
    ("case_sol_2", "2026【正版高清扫】华S案例习题集中_扫描版.pdf", "案例习题集·中", "C", "solutions", [4, 5, 6], ""),
    ("case_sol_3", "2026【正版高清扫】华S案例习题集下_扫描版.pdf", "案例习题集·下", "C", "solutions", [7, 8, 9, 10], ""),
    ("case_blank_1", "26华山空白习题第一册_扫描版.pdf", "空白习题·1", "C", "blank", [1, 2], "土力学~岩土工程勘察"),
    ("case_blank_2", "26华山空白习题第二册_扫描版.pdf", "空白习题·2", "C", "blank", [3, 4], "封面误印「第三册」，以文件名和内部目录为准"),
    ("case_blank_3", "26华山空白习题第三册_扫描版.pdf", "空白习题·3", "C", "blank", [5, 6, 7], "地基处理~基坑"),
    ("case_blank_4", "26华山空白习题第四册_扫描版.pdf", "空白习题·4", "C", "blank", [8, 9, 10], "特殊土~检测"),
    ("know_sol_1", "华S26专业知识习题册上_扫描版.pdf", "知识习题册·上", "Z", "self-contained", list(range(1, 9)), "自包含答案"),
    ("know_sol_2", "华S26专业知识习题册下_扫描版.pdf", "知识习题册·下", "Z", "self-contained", list(range(9, 14)), "自包含答案"),
    ("case_exam", "2025【正版高清扫】华S案例真题12-25套题_扫描版.pdf", "案例真题12-25", "A", "exam", [], ""),
    ("know_exam", "华S26专业知识13~25真题解析_扫描版.pdf", "知识真题13-25", "K", "exam", [], ""),
    ("mock", "26华S案例模拟题集高清版.pdf", "案例模拟33套", "M", "mock", [], "576dpi 存档扫描，压缩需降采样"),
    ("handbook", "工程地质手册（第五版）.pdf", "工程地质手册", "H", "reference", [], "唯一带文字层与书签"),
]


def load_lookup():
    with open(os.path.join(INDEX, "lookup.json"), encoding="utf-8") as fh:
        return json.load(fh)["chapter"]


def pdf_page(book_page, chapter):
    return book_page + OFFSET[chapter]


def build_chapters(lookup):
    """Every 篇 -> its 节 list, with qid ranges resolved from lookup.json."""
    # (chapter, page, qid) for the owning volume only
    rows = {}
    for qid, entry in lookup.items():
        match = re.match(r"C(\d+)-(\d+)$", qid)
        if not match:
            continue
        chapter = int(match.group(1))
        if chapter not in OWNER:
            continue
        own = [pages[0] for book, pages in entry["books"].items() if OWNER[chapter] in book]
        blank = [pages[0] for book, pages in entry["books"].items() if "空白" in book]
        if own:
            rows.setdefault(chapter, []).append((own[0], int(match.group(2)), qid, blank[0] if blank else None))
    for chapter in rows:
        rows[chapter].sort()

    out = {}
    for chapter, sections in SECTIONS.items():
        seq = rows.get(chapter, [])
        if not seq:
            continue
        starts = []
        for title, book_page in sections:
            target = pdf_page(book_page, chapter)
            hit = next((row for row in seq if row[0] >= target - 1), None)
            starts.append((title, hit, book_page))

        items = []
        for i, (title, hit, book_page) in enumerate(starts):
            if hit is None:
                continue
            last = starts[i + 1][1][1] - 1 if i + 1 < len(starts) and starts[i + 1][1] else seq[-1][1]
            if last < hit[1]:
                continue  # section has no questions of its own
            items.append({
                "name": title,
                "first": "C%d-%d" % (chapter, hit[1]),
                "last": "C%d-%d" % (chapter, last),
                "count": last - hit[1] + 1,
                "page": hit[0],
                "blank_page": hit[3],
                "book_page": book_page,
            })
        out["C%d" % chapter] = {
            "title": items[0]["name"] if len(items) == 1 else None,
            "volume": OWNER[chapter],
            "sections": items,
        }
    # fill 篇 titles from the printed 篇 names
    titles = {1: "第一篇 土力学", 2: "第二篇 岩土工程勘察", 3: "第三篇 浅基础",
              4: "第四篇 深基础", 5: "第五篇 地基处理", 6: "第六篇 土工结构与边坡防护",
              7: "第七篇 基坑工程与地下工程", 8: "第八篇 特殊条件下的岩土工程",
              9: "第九篇 地震工程", 10: "第十篇 岩土工程检测与监测"}
    for key, value in out.items():
        value["title"] = titles[int(key[1:])]
    return out


def build_books():
    books = []
    for bid, filename, short, family, role, chapters, note in BOOKS:
        path = book_pdf(filename)
        doc = pymupdf.open(path)
        first = doc[0]
        images = first.get_images()
        img_w = img_h = None
        if images:
            info = doc.extract_image(images[0][0])
            img_w, img_h = info["width"], info["height"]
        books.append({
            "id": bid,
            "file": filename,
            "short": short,
            "family": family,
            "role": role,
            "chapters": chapters,
            "pages": doc.page_count,
            "page_pt": [round(first.rect.width, 1), round(first.rect.height, 1)],
            "image_px": [img_w, img_h],
            "text_layer": sum(1 for p in doc if p.get_text().strip()) > doc.page_count // 2,
            "size_mb": round(os.path.getsize(path) / 1e6),
            "note": note,
        })
        doc.close()
    return books


def main():
    lookup = load_lookup()
    chapters = build_chapters(lookup)
    books = build_books()

    with open(os.path.join(INDEX, "chapters.json"), "w", encoding="utf-8") as fh:
        json.dump(chapters, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(INDEX, "books.json"), "w", encoding="utf-8") as fh:
        json.dump({"books": books}, fh, ensure_ascii=False, indent=1)

    total = sum(s["count"] for c in chapters.values() for s in c["sections"])
    print("books: %d | chapters: %d | sections: %d | covered questions: %d"
          % (len(books), len(chapters), sum(len(c["sections"]) for c in chapters.values()), total))
    for c in chapters.values():
        print("  %-4s %-22s %2d 节 %4d 题" % (c["title"][:10], c["volume"], len(c["sections"]),
                                              sum(s["count"] for s in c["sections"])))


if __name__ == "__main__":
    main()
