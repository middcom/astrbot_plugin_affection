"""
用户数据存储模块
负责将每个用户的情绪数值持久化到 JSON 文件中，并提供安全的读写接口。
数据存储路径：data/plugin_data/eros_thanatos/user_data.json
"""

import json
from pathlib import Path
from typing import Dict, Optional
from copy import deepcopy
import asyncio
import time


class UserDataStorage:
    """
    用户数据存储器，管理所有用户的情绪档案。
    支持并发安全的异步保存。
    """

    def __init__(self, file_path: Path):
        """
        Args:
            file_path: JSON 文件完整路径
        """
        self.file_path = file_path
        self._lock = asyncio.Lock()  # 异步锁，防止并发写入损坏文件
        self.data: Dict[str, dict] = self._load()

    def _load(self) -> dict:
        """从文件加载已有数据，若文件不存在或损坏则返回空字典"""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        """同步写入文件（内部调用，不直接暴露）"""
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    async def async_save(self):
        """异步保存，使用锁保证线程安全"""
        async with self._lock:
            self._save()

    def get(self, uid: str) -> Optional[dict]:
        """
        获取指定用户的完整数据（深拷贝，避免外部意外修改）
        """
        user = self.data.get(uid)
        return deepcopy(user) if user else None

    def exists(self, uid: str) -> bool:
        """检查用户是否已存在"""
        return uid in self.data

    def create_user(self, uid: str, affection: float = 50.0) -> dict:
        """
        为新用户创建默认情绪档案（平淡状态）
        - 好感度默认 50
        - 所有基线值（原力比多/原攻击性）均为 25
        - 当前情绪值同步基线
        - 对话轮次计数为 0
        """
        now = int(time.time())
        default = {
            # === 对用户维度 ===
            "base_libido_other": 25.0,  # 原他力比多（长期印象）
            "base_aggression_other": 25.0,  # 原他攻击性（长期印象）
            "affection": float(affection),  # 好感度（0-100，较难改变）
            "current_libido_other": 25.0,  # 他力比多（当前情绪）
            "current_aggression_other": 25.0,  # 他攻击性（当前情绪）
            # === 对自身维度 ===
            "base_libido_self": 25.0,  # 原自力比多（长期自我印象）
            "base_aggression_self": 25.0,  # 原自攻击性（长期自我印象）
            "current_libido_self": 25.0,  # 自力比多（当前心情）
            "current_aggression_self": 25.0,  # 自攻击性（当前心情）
            # === 元数据 ===
            "turn_count": 0,  # 已对话轮次（用于初印象规则）
            "last_interaction": now,  # 上次互动时间戳（秒）
            "last_update": now,  # 上次数值更新时间戳
            "idle_triggered": False,  # 是否已触发空闲分析（避免重复）
        }
        self.data[uid] = default
        self._save()
        return deepcopy(default)

    def update_user(self, uid: str, updates: dict):
        """
        部分更新用户数据（仅更新传入的字段）
        """
        if uid in self.data:
            self.data[uid].update(updates)
            self._save()

    def save_user(self, uid: str, user_data: dict):
        """
        完全覆盖用户数据
        """
        self.data[uid] = user_data
        self._save()

    def get_all_uids(self):
        """返回所有已存储用户的 UID 列表"""
        return list(self.data.keys())
