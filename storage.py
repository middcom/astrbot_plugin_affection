"""
用户数据存储模块
支持多机器人数据隔离，每个机器人拥有独立的数据目录。
"""

import json
from pathlib import Path
from typing import Dict, Optional, Any
from copy import deepcopy
import asyncio
import time

from astrbot.api import logger


class SelfDataStorage:
    """机器人自身情绪数据存储器（每个机器人独立）"""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._lock = asyncio.Lock()
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> dict:
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"[SelfDataStorage] JSON 解析失败 {self.file_path}: {e}")
                return {}
            except Exception as e:
                logger.warning(f"[SelfDataStorage] 文件读取失败 {self.file_path}: {e}")
                return {}
        return {}

    def _save(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    async def async_save(self):
        async with self._lock:
            await asyncio.to_thread(self._save)

    def get(self) -> dict:
        return deepcopy(self.data)

    def exists(self) -> bool:
        return bool(self.data)

    def create(
        self, base_libido_self: float = 25.0, base_aggression_self: float = 25.0
    ) -> dict:
        now = int(time.time())
        default = {
            "base_libido_self": float(base_libido_self),
            "base_aggression_self": float(base_aggression_self),
            "current_libido_self": float(base_libido_self),
            "current_aggression_self": float(base_aggression_self),
            "last_update": now,
        }
        self.data = default
        self._save()
        return deepcopy(default)

    def update(self, updates: dict):
        self.data.update(updates)
        self._save()

    def save(self, data: dict):
        self.data = data
        self._save()


class UserDataStorage:
    """用户数据存储器（每个机器人独立）"""

    def __init__(self, file_path: Path, self_storage: SelfDataStorage = None):
        self.file_path = file_path
        self._lock = asyncio.Lock()
        self.self_storage = self_storage
        self.data: Dict[str, dict] = self._load()
        self._migrate_old_data()

    def _load(self) -> dict:
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                logger.warning(f"[UserDataStorage] JSON 解析失败 {self.file_path}: {e}")
                return {}
            except Exception as e:
                logger.warning(f"[UserDataStorage] 文件读取失败 {self.file_path}: {e}")
                return {}
        return {}

    def _save(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    async def async_save(self):
        async with self._lock:
            await asyncio.to_thread(self._save)

    def _migrate_old_data(self):
        """将旧版用户数据中的自身字段迁移到全局自身存储"""
        if not self.data or not self.self_storage:
            return
        first_user = next(iter(self.data.values()), None)
        if first_user and "base_libido_self" in first_user:
            self.self_storage.data = {
                "base_libido_self": first_user.get("base_libido_self", 25.0),
                "base_aggression_self": first_user.get("base_aggression_self", 25.0),
                "current_libido_self": first_user.get("current_libido_self", 25.0),
                "current_aggression_self": first_user.get(
                    "current_aggression_self", 25.0
                ),
                "last_update": first_user.get("last_update", int(time.time())),
            }
            self.self_storage._save()
            for uid in self.data:
                user = self.data[uid]
                for key in [
                    "base_libido_self",
                    "base_aggression_self",
                    "current_libido_self",
                    "current_aggression_self",
                ]:
                    user.pop(key, None)
            self._save()

    def get(self, uid: str) -> Optional[dict]:
        user = self.data.get(uid)
        return deepcopy(user) if user else None

    def exists(self, uid: str) -> bool:
        return uid in self.data

    def create_user(
        self,
        uid: str,
        affection: float = 50.0,
        base_libido_other: float = 25.0,
        base_aggression_other: float = 25.0,
    ) -> dict:
        now = int(time.time())
        default = {
            "base_libido_other": float(base_libido_other),
            "base_aggression_other": float(base_aggression_other),
            "affection": float(affection),
            "current_libido_other": float(base_libido_other),
            "current_aggression_other": float(base_aggression_other),
            "turn_count": 0,
            "last_interaction": now,
            "last_update": now,
            "idle_triggered": False,
        }
        self.data[uid] = default
        self._save()
        return deepcopy(default)

    def update_user(self, uid: str, updates: dict):
        if uid in self.data:
            self.data[uid].update(updates)
            self._save()

    def save_user(self, uid: str, user_data: dict):
        self.data[uid] = user_data
        self._save()

    def get_all_uids(self):
        return list(self.data.keys())
