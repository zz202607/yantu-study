"""Record one 2025 case half from the four manual numbers.

Case questions are **subjective**: a correct final number without the written
process scores zero, and the process itself has to be judged by a human.  This
recorder therefore takes counts, not A/B/C/D option letters.  The letters in
the printed source book are only the publisher's option key; they are not an
exam answer key and are never read here.

Protocol (docs/coach/playbook.md §7): 3 hours per half, exam rules, then
record 作答 / 做对 / 放弃 / 超时.  "做对" means process and result are both
correct; an answer-only guess is counted as wrong.

Usage:
    py -3 tools/score_baseline.py --half A --answered 25 --correct 15 --overtime 2
    py -3 tools/score_baseline.py --half P --answered 23 --correct 12 --skipped 2 --write
"""

import argparse
import datetime
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "docs", "coach", "baseline-2025.md")
HALF_NAMES = {"A": "上午卷", "P": "下午卷"}
PER_QUESTION = 2  # 案例 25 题 × 2 分 = 50 分/半场
TOTAL_QUESTIONS = 25


def validate_counts(answered, correct, skipped=None, overtime=0):
    """Validate the four numbers; return a normalized dict."""
    try:
        answered = int(answered)
        correct = int(correct)
        overtime = int(overtime or 0)
        skipped = TOTAL_QUESTIONS - answered if skipped is None else int(skipped)
    except (TypeError, ValueError):
        raise ValueError("四个数都必须是整数")
    if not 0 <= answered <= TOTAL_QUESTIONS:
        raise ValueError("作答题数应在 0–%d 之间" % TOTAL_QUESTIONS)
    if not 0 <= correct <= answered:
        raise ValueError("做对数应在 0–作答题数之间")
    if not 0 <= skipped <= TOTAL_QUESTIONS:
        raise ValueError("放弃题数应在 0–%d 之间" % TOTAL_QUESTIONS)
    if answered + skipped != TOTAL_QUESTIONS:
        raise ValueError(
            "作答题数 %d + 放弃题数 %d 应等于 %d"
            % (answered, skipped, TOTAL_QUESTIONS)
        )
    if not 0 <= overtime <= answered:
        raise ValueError("超时题数应在 0–作答题数之间")
    return {
        "answered": answered,
        "correct": correct,
        "skipped": skipped,
        "overtime": overtime,
        "score": correct * PER_QUESTION,
        "max_score": TOTAL_QUESTIONS * PER_QUESTION,
    }


def format_report(half, counts, day):
    lines = [
        "## %s · %s" % (day, HALF_NAMES[half]),
        "",
        "- 作答 **%d** / %d · 做对 **%d** · 放弃 **%d** · 超时 **%d**"
        % (
            counts["answered"],
            TOTAL_QUESTIONS,
            counts["correct"],
            counts["skipped"],
            counts["overtime"],
        ),
        "- 得分 **%d / %d**（每题 %d 分；合格线 60/100）"
        % (counts["score"], counts["max_score"], PER_QUESTION),
        "- 案例为过程评分：做对 = 过程与结果均正确；只有答案无过程按 0 分计。",
        "- 记录方式：人工按过程自评，不按选项字母判分。",
        "",
    ]
    return "\n".join(lines)


def append_report(section, path=REPORT):
    """Append one half's section; refuse to overwrite an existing one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "# 2025 案例基线\n\n"
                "案例是主观题：做对 = 过程与结果均正确；只有答案无过程按 0 分。\n"
                "本文件只记录四个数（作答题数 / 做对数 / 放弃数 / 超时题数），"
                "不按选项字母判分。\n\n"
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
    parser = argparse.ArgumentParser(
        description="按过程自评记录 2025 案例基线四个数；不按选项字母判分"
    )
    parser.add_argument("--half", choices=["A", "P"], required=True)
    parser.add_argument("--answered", type=int, required=True,
                        help="作答题数（0–25）")
    parser.add_argument("--correct", type=int, required=True,
                        help="做对数（过程与结果均正确，0–作答题数）")
    parser.add_argument("--skipped", type=int, default=None,
                        help="放弃题数；默认 = 25 - 作答题数")
    parser.add_argument("--overtime", type=int, default=0,
                        help="超时题数（单题 >10 分钟）")
    parser.add_argument("--write", action="store_true",
                        help="追加到 docs/coach/baseline-2025.md")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    try:
        counts = validate_counts(
            args.answered, args.correct, args.skipped, args.overtime
        )
    except ValueError as exc:
        raise SystemExit("错误：%s" % exc)

    day = args.date or datetime.date.today().isoformat()
    section = format_report(args.half, counts, day)
    print(section)
    if args.write:
        print("已写入：%s" % append_report(section))
    else:
        print("（加 --write 可写入 docs/coach/baseline-2025.md）")


if __name__ == "__main__":
    main()
