import json
import os
from pathlib import Path

DATA_FILE = Path("bot_data.json")


class Storage:
    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"users": [], "total_analyses": 0}

    def _save(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def add_user(self, user_id: int):
        if user_id not in self._data["users"]:
            self._data["users"].append(user_id)
            self._save()

    def get_all_users(self) -> list[int]:
        return list(self._data["users"])

    def increment_analyses(self):
        self._data["total_analyses"] += 1
        self._save()

    def get_total_analyses(self) -> int:
        return self._data.get("total_analyses", 0)


storage = Storage()
