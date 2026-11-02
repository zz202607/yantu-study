"""Small, validated study settings that live beside the user database.

Only `data/settings.json` is written; the exam date is a user decision, not a
catalog fact, so it must not go into `index/`.  A corrupt file falls back to
defaults instead of breaking the dashboard.
"""

import json
import os
import tempfile
from datetime import date
from pathlib import Path

DEFAULTS = {
    # The study plan says 2027-11; the exact day is not official yet, so the
    # dashboard shows this as an estimate until _ming edits it.
    "exam_date": "2027-11-01",
    "exam_label": "2027 注册岩土 · 专业考试",
    "plan_start": "2026-11-01",
    "daily_target": 20,
    "baseline_due": "2026-11-04",
}

DATE_KEYS = ("exam_date", "plan_start", "baseline_due")


class Settings:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "settings.json"

    def load(self):
        data = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                for key in DEFAULTS:
                    if key in stored:
                        data[key] = stored[key]
        except (OSError, ValueError):
            pass
        try:
            return self._validate(data)
        except ValueError:
            return dict(DEFAULTS)

    def update(self, patch):
        """Merge a partial payload, validate the result, write atomically."""
        if not isinstance(patch, dict):
            raise ValueError("设置必须是对象")
        unknown = set(patch) - set(DEFAULTS)
        if unknown:
            raise ValueError("不认识的设置项：%s" % "、".join(sorted(unknown)))
        data = self.load()
        data.update({key: patch[key] for key in patch})
        data = self._validate(data)
        self._write(data)
        return data

    @staticmethod
    def _validate(data):
        clean = {}
        for key in DATE_KEYS:
            value = str(data.get(key, DEFAULTS[key])).strip()
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("%s 不是有效日期（YYYY-MM-DD）" % key)
            clean[key] = value
        if clean["exam_date"] <= clean["plan_start"]:
            raise ValueError("考试日期必须晚于备考起点")
        try:
            target = int(data.get("daily_target", DEFAULTS["daily_target"]))
        except (TypeError, ValueError):
            raise ValueError("每日复习目标必须是整数")
        if not 1 <= target <= 60:
            raise ValueError("每日复习目标应在 1–60 之间")
        clean["daily_target"] = target
        label = str(data.get("exam_label", DEFAULTS["exam_label"])).strip()
        if not label or len(label) > 60:
            raise ValueError("考试名称应为 1–60 个字符")
        clean["exam_label"] = label
        return clean

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".settings-", suffix=".json", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
