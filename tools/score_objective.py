"""Score a knowledge paper (objective single/multiple choice).

This is deliberately separate from `score_baseline.py`: case questions are
subjective and are never scored from option letters.

Key JSON shape:
    {
      "paper": "K2025-A",
      "questions": [
        {"qid": "K2025-A-1", "type": "single", "answer": "B",
         "points": 1, "category": "勘察"},
        {"qid": "K2025-A-41", "type": "multiple", "answer": ["A","D"],
         "points": 2, "category": "浅基础"}
      ]
    }

Answers JSON can be either {qid: "B"} / {qid: "AD"} or a list in key order.

Usage:
    py -3 tools/score_objective.py --key key.json --answers answers.json
    py -3 tools/score_objective.py --key key.json --answers answers.json --json
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.objective import score_objective  # noqa: E402


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def format_report(result):
    lines = [
        "客观题得分：**%d / %d** · 答对 %d / %d 题"
        % (result["score"], result["max_score"], result["correct"], result["n"]),
        "",
        "| 类别 | 题数 | 作答 | 答对 | 得分 |",
        "|---|---|---|---|---|",
    ]
    for name, row in sorted(result["by_category"].items()):
        lines.append(
            "| %s | %d | %d | %d | %d / %d |"
            % (name, row["n"], row["answered"], row["correct"],
               row["points"], row["max_points"])
        )
    wrong = [row for row in result["questions"] if not row["correct"]]
    if wrong:
        lines.extend(["", "错题：" + "、".join(
            "%s（你 %s / 答案 %s）"
            % (row["qid"], row["gave"] or "未答", row["expected"])
            for row in wrong
        )])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="知识题客观判分（单选/多选）；不用于案例主观题"
    )
    parser.add_argument("--key", required=True, help="答案键 JSON")
    parser.add_argument("--answers", required=True, help="作答 JSON")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    try:
        result = score_objective(load_json(args.key), load_json(args.answers))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("错误：%s" % exc)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result))


if __name__ == "__main__":
    main()
