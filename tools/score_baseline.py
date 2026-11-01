"""Score one 2025 case half after a timed baseline attempt.

Protocol (docs/coach/playbook.md §7): 3 hours per half, exam rules, then
record 作答题数 / 做对数 / 放弃数 / 超时题数.  This tool compares the 25
answers you wrote down with index/tags/A2025.json, prints the four numbers
plus a per-category table, and can append the section to
docs/coach/baseline-2025.md.

Answers are 25 characters, A/B/C/D, use `-` for a question you abandoned.

Usage:
    py -3 tools/score_baseline.py --half A --answers "CBDAB..." --overtime 2
    py -3 tools/score_baseline.py --half P --answers "..." --write
"""

import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAGS = os.path.join(ROOT, "index", "tags", "A2025.json")
REPORT = os.path.join(ROOT, "docs", "coach", "baseline-2025.md")
HALF_NAMES = {"A": "上午卷", "P": "下午卷"}
PER_QUESTION = 2  # 案例 50 题 × 2 分


def key_rows(tags, half):
    """[(qid, category, answer)] in exam order."""
    prefix = "A2025-%s-" % half
    rows = [
        (qid, entry.get("category", "未分类"), entry.get("answer", "").upper())
        for qid, entry in tags.items()
        if qid.startswith(prefix)
    ]
    rows.sort(key=lambda row: int(row[0].rsplit("-", 1)[-1]))
    return rows


def parse_answers(text, n=25):
    """25 letters A-D or '-'; spaces, commas and newlines are ignored."""
    cleaned = re.sub(r"[\s,，、;；]", "", (text or "").upper())
    bad = sorted(set(ch for ch in cleaned if ch not in "ABCD-"))
    if bad:
        raise ValueError("有不认识的字符：%s（只能 A/B/C/D/-）" % " ".join(bad))
    if len(cleaned) != n:
        raise ValueError("应有 %d 个答案，实际 %d 个" % (n, len(cleaned)))
    return list(cleaned)


def score_half(answers, rows):
    """Per-question results and per-category accuracy."""
    if len(answers) != len(rows):
        raise ValueError("答案 %d 个，题目 %d 道" % (len(answers), len(rows)))
    by_category = {}
    wrong, blank = [], []
    correct = 0
    for gave, (qid, category, answer) in zip(answers, rows):
        bucket = by_category.setdefault(
            category, {"n": 0, "answered": 0, "correct": 0, "wrong": []}
        )
        bucket["n"] += 1
        if gave == "-":
            blank.append(qid)
            continue
        bucket["answered"] += 1
        if gave == answer:
            correct += 1
            bucket["correct"] += 1
        else:
            wrong.append(
                {"qid": qid, "category": category, "gave": gave, "answer": answer}
            )
            bucket["wrong"].append(qid)
    return {
        "n": len(rows),
        "answered": len(rows) - len(blank),
        "correct": correct,
        "wrong": len(wrong),
        "blank": len(blank),
        "score": correct * PER_QUESTION,
        "max_score": len(rows) * PER_QUESTION,
        "by_category": by_category,
        "wrong_rows": wrong,
        "blank_qids": blank,
    }


def format_report(half, result, answers, skipped, overtime, day):
    lines = [
        "## %s · %s" % (day, HALF_NAMES[half]),
        "",
        "- 作答题数 **%d** / %d · 做对 **%d** · 放弃 **%d** · 超时 **%d**"
        % (result["answered"], result["n"], result["correct"], skipped, overtime),
        "- 得分 **%d / %d**（每题 %d 分；合格线 60/100）"
        % (result["score"], result["max_score"], PER_QUESTION),
        "- 作答串：`%s`" % "".join(answers),
        "",
        "| 类别 | 题数 | 作答 | 做对 | 正确率 |",
        "|---|---|---|---|---|",
    ]
    for category, bucket in sorted(
        result["by_category"].items(), key=lambda item: -item[1]["n"]
    ):
        rate = (
            "%d%%" % round(100.0 * bucket["correct"] / bucket["answered"])
            if bucket["answered"]
            else "—"
        )
        lines.append(
            "| %s | %d | %d | %d | %s |"
            % (category, bucket["n"], bucket["answered"], bucket["correct"], rate)
        )
    if result["wrong_rows"]:
        lines.extend(["", "**错题**：" + "、".join(
            "%s（%s，你 %s / 答案 %s）"
            % (row["qid"], row["category"], row["gave"], row["answer"])
            for row in result["wrong_rows"]
        )])
    if result["blank_qids"]:
        lines.extend(["", "**放弃**：" + "、".join(result["blank_qids"])])
    lines.append("")
    return "\n".join(lines)


def append_report(section, path=REPORT, day=None):
    """Append one half's section; refuse to overwrite an existing one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "# 2025 案例基线\n\n"
                "四数：作答题数 / 做对数 / 放弃数 / 超时题数。"
                "上午、下午各限时 3 小时，考场规则。\n\n"
            )
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    heading = section.split("\n", 1)[0]
    if heading in text:
        raise ValueError("已有同名小节：%s（手动编辑请直接改文件）" % heading)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(section + "\n")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--half", choices=["A", "P"], required=True)
    parser.add_argument("--answers", required=True,
                        help="25 个字母 A/B/C/D，放弃用 -")
    parser.add_argument("--skipped", type=int, default=None,
                        help="放弃题数；默认等于答案串里的 - 数")
    parser.add_argument("--overtime", type=int, default=0,
                        help="超时题数（单题 >10 分钟）")
    parser.add_argument("--write", action="store_true",
                        help="追加到 docs/coach/baseline-2025.md")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    with open(TAGS, encoding="utf-8") as fh:
        tags = json.load(fh)
    rows = key_rows(tags, args.half)
    try:
        answers = parse_answers(args.answers, len(rows))
        result = score_half(answers, rows)
    except ValueError as exc:
        raise SystemExit("错误：%s" % exc)

    skipped = args.skipped if args.skipped is not None else result["blank"]
    if skipped < result["blank"]:
        raise SystemExit(
            "放弃题数 %d 小于答案串里的空题 %d，请核对"
            % (skipped, result["blank"])
        )
    if skipped > len(rows):
        raise SystemExit("放弃题数不能超过 %d" % len(rows))

    day = args.date or datetime.date.today().isoformat()
    section = format_report(
        args.half, result, answers, skipped, args.overtime, day
    )
    print(section)
    if args.write:
        print("已写入：%s" % append_report(section))
    else:
        print("（加 --write 可写入 docs/coach/baseline-2025.md）")


if __name__ == "__main__":
    main()
