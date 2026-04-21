"""
衰减管理器
负责后台定时任务：基于二次函数衰减使当前情绪值逐渐回归基线，并检测长时间未互动触发空闲分析。
"""

import asyncio
import time
from typing import Optional
from astrbot.api import logger


def compute_decay(
    elapsed_hours: float, initial_deviation: float, duration_hours: float
) -> float:
    """
    计算应向基线恢复的修正增量 delta。

    公式（二次衰减）：
        y(x) = initial_deviation * (1 - (x/n)^2)   (x 为经过时间，n 为总持续时间)
        当前值 = 基线 + y(x)
        则经过时间 x 后，当前值相较于初始时刻的变化量为 -initial_deviation * (x/n)^2

    Args:
        elapsed_hours: 自事件开始经过的小时数
        initial_deviation: 初始偏离量 = current - base
        duration_hours: 事件总持续时间（小时）

    Returns:
        应加到当前值上的修正量 delta，使得 current += delta 向基线靠拢。
        当 elapsed_hours >= duration_hours 时，完全恢复至基线。
    """
    if duration_hours <= 0:
        duration_hours = 0.5
    if elapsed_hours >= duration_hours:
        return -initial_deviation
    ratio = elapsed_hours / duration_hours
    decay_amount = initial_deviation * (ratio**2)
    return -decay_amount


class DecayManager:
    """
    衰减管理器，每分钟执行一次 tick()：
    - 对所有用户计算时间衰减，将当前情绪值拉回基线。
    - 对全局自身数据计算时间衰减。
    - 检测长时间未互动的用户，触发潜意识 LLM 分析是否产生情绪波动。
    """

    def __init__(self, storage, self_storage, unconscious_adjuster, context, config):
        self.storage = storage
        self.self_storage = self_storage
        self.unconscious = unconscious_adjuster
        self.context = context
        self.config = config
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self):
        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.error(f"[DecayManager] tick error: {e}")
            await asyncio.sleep(60)

    async def tick(self):
        now = time.time()

        # ---------- 全局自身数据衰减 ----------
        self_data = self.self_storage.get()
        if self_data:
            updated_self = False
            fields_self = [
                ("current_libido_self", "base_libido_self"),
                ("current_aggression_self", "base_aggression_self"),
            ]
            last_update_self = self_data.get("last_update", now)
            elapsed_seconds_self = now - last_update_self
            elapsed_hours_self = elapsed_seconds_self / 3600.0
            for cur_field, base_field in fields_self:
                base = self_data[base_field]
                current = self_data[cur_field]
                deviation = current - base
                if abs(deviation) < 0.001:
                    continue
                duration = self.config.get("decay_duration_hours", 2.0)
                delta = compute_decay(elapsed_hours_self, deviation, duration)
                new_val = current + delta
                new_val = max(0.0, min(50.0, new_val))
                if abs(new_val - current) > 0.0001:
                    self_data[cur_field] = new_val
                    updated_self = True
            if updated_self:
                self_data["last_update"] = now
                self.self_storage.save(self_data)

        # ---------- 用户数据衰减与空闲检查 ----------
        uids = self.storage.get_all_uids()
        for uid in uids:
            user = self.storage.get(uid)
            if not user:
                continue

            last_interact = user.get("last_interaction", now)
            elapsed_seconds = now - last_interact
            elapsed_hours = elapsed_seconds / 3600.0

            # 空闲检测
            idle_threshold = self.config.get("idle_threshold_hours", 6.0)
            if (
                elapsed_hours >= idle_threshold
                and self.config.get("idle_check_enabled", True)
                and not user.get("idle_triggered", False)
            ):
                await self._trigger_idle_analysis(uid, elapsed_hours)
                user["idle_triggered"] = True
                self.storage.update_user(uid, {"idle_triggered": True})
                continue

            updated = False
            fields = [
                ("current_libido_other", "base_libido_other"),
                ("current_aggression_other", "base_aggression_other"),
            ]
            last_update_user = user.get("last_update", now)
            elapsed_user = (now - last_update_user) / 3600.0
            for cur_field, base_field in fields:
                base = user[base_field]
                current = user[cur_field]
                deviation = current - base
                if abs(deviation) < 0.001:
                    continue
                duration = self.config.get("decay_duration_hours", 2.0)
                delta = compute_decay(elapsed_user, deviation, duration)
                new_val = current + delta
                new_val = max(0.0, min(50.0, new_val))
                if abs(new_val - current) > 0.0001:
                    user[cur_field] = new_val
                    updated = True

            if updated:
                user["last_update"] = now
                self.storage.save_user(uid, user)

    async def _trigger_idle_analysis(self, uid: str, elapsed_hours: float):
        logger.info(
            f"[DecayManager] 用户 {uid} 空闲 {elapsed_hours:.1f}h，触发空闲分析"
        )
        try:
            deltas = await self.unconscious.analyze_idle(uid, elapsed_hours)
            if deltas:
                user = self.storage.get(uid)
                sensitivity = 0.3
                user["current_libido_other"] = max(
                    0.0,
                    min(
                        50.0,
                        user["current_libido_other"]
                        + deltas.get("libido_other_delta", 0.0) * sensitivity,
                    ),
                )
                user["current_aggression_other"] = max(
                    0.0,
                    min(
                        50.0,
                        user["current_aggression_other"]
                        + deltas.get("aggression_other_delta", 0.0) * sensitivity,
                    ),
                )
                self.storage.save_user(uid, user)
                # 自身数据也可能因空闲受影响（但通常较小，此处可选择性更新）
                self_data = self.self_storage.get()
                if self_data:
                    self_data["current_libido_self"] = max(
                        0.0,
                        min(
                            50.0,
                            self_data["current_libido_self"]
                            + deltas.get("libido_self_delta", 0.0) * sensitivity,
                        ),
                    )
                    self_data["current_aggression_self"] = max(
                        0.0,
                        min(
                            50.0,
                            self_data["current_aggression_self"]
                            + deltas.get("aggression_self_delta", 0.0) * sensitivity,
                        ),
                    )
                    self.self_storage.save(self_data)
        except Exception as e:
            logger.error(f"[DecayManager] 空闲分析失败: {e}")
