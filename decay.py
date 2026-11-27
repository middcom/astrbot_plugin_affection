"""
衰减管理器
负责后台定时任务：基于二次函数衰减使当前情绪值逐渐回归基线，并检测长时间未互动触发空闲分析。

本版本修复要点：
- 衰减时间锚点固定为 last_update（上次互动/更新时刻），tick() 不再改写它，
  保证 compute_decay 的 elapsed_hours 持续累积，偏离量能真正回归基线。
- tick() 通过簿记字段 last_decay（仅在衰减实际写入时更新）反推锚点时刻的初始偏离，
  使多次 tick 的结果与一次性计算二次曲线完全一致，避免复利式过衰减。
- 空闲分析产生非零偏移时才落地，并把它视为一次"更新"重设锚点（last_update），
  防止空闲分析结果在下一次 tick 中被旧锚点的满周期衰减瞬间抹掉。
"""

import asyncio
import time

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


def _safe_float(value, default: float = 0.0) -> float:
    """安全转 float；None/字符串垃圾等失败情况返回 default。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
        self._task: asyncio.Task | None = None

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

    def _decay_field(
        self, record: dict, cur_field: str, base_field: str, elapsed_hours: float
    ) -> float | None:
        """
        按二次衰减曲线计算字段新值。

        以 last_update（上次互动/更新）为锚点：val(e) = base + dev0 * (1 - (e/d)^2)，
        dev0 为锚点时刻的初始偏离。由于 tick 每次只写入曲线上的当前点，
        通过上次衰减写入点 last_decay（仅簿记，不是锚点）反推 dev0，
        保证多次 tick 与一次性计算的结果完全一致。

        Args:
            record: 用户或自身数据 dict
            cur_field / base_field: 当前值与基线字段名
            elapsed_hours: 距锚点（last_update）经过的小时数

        Returns:
            新值；None 表示无需更新（无偏离或变化量过小）。
        """
        duration = _safe_float(self.config.get("decay_duration_hours", 2.0), 2.0)
        if duration <= 0:
            duration = 0.5
        base = _safe_float(record.get(base_field), 0.0)
        current = _safe_float(record.get(cur_field), 0.0)
        dev = current - base
        if abs(dev) < 0.001:
            return None
        if elapsed_hours >= duration:
            # 超过衰减周期：完全回归基线
            return base
        # 反推锚点初始偏离 dev0：current = base + dev0 * (1 - (e_prev/d)^2)
        last_decay = record.get("last_decay")
        last_update = record.get("last_update")
        e_prev = 0.0
        if last_decay is not None and last_update is not None and last_decay > last_update:
            e_prev = (last_decay - last_update) / 3600.0
            # 钳到 [0, elapsed]，防时钟异常
            e_prev = min(max(0.0, e_prev), elapsed_hours)
        frac_prev = 1.0 - (e_prev / duration) ** 2
        if frac_prev < 1e-6:
            # 极端情况保护：视为已达周期末端
            return base
        dev0 = dev / frac_prev
        new_val = base + dev0 + compute_decay(elapsed_hours, dev0, duration)
        new_val = max(0.0, min(50.0, new_val))
        if abs(new_val - current) <= 0.0001:
            return None
        return new_val

    async def tick(self):
        now = time.time()

        # ---------- 全局自身数据衰减 ----------
        self_data = self.self_storage.get()
        if self_data:
            # 衰减锚点 = 上次互动/更新时刻；tick 只读不改，保证 elapsed 持续累积
            anchor_self = self_data.get("last_update")
            if anchor_self is None:
                anchor_self = self_data.get("last_interaction", now)
            elapsed_self = max(0.0, (now - _safe_float(anchor_self, now)) / 3600.0)
            updated_self = False
            fields_self = [
                ("current_libido_self", "base_libido_self"),
                ("current_aggression_self", "base_aggression_self"),
            ]
            for cur_field, base_field in fields_self:
                new_val = self._decay_field(
                    self_data, cur_field, base_field, elapsed_self
                )
                if new_val is not None:
                    self_data[cur_field] = new_val
                    updated_self = True
            if updated_self:
                # 仅写簿记时间戳（衰减推进点），绝不改写 last_update 锚点
                self_data["last_decay"] = now
                self.self_storage.save(self_data)

        # ---------- 用户数据衰减与空闲检查 ----------
        uids = self.storage.get_all_uids()
        for uid in uids:
            user = self.storage.get(uid)
            if not user:
                continue

            last_interact = user.get("last_interaction", now)
            elapsed_seconds = now - _safe_float(last_interact, now)
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

            # 衰减锚点 = 上次互动/更新时刻（旧数据缺 last_update 时回退 last_interaction）
            anchor = user.get("last_update")
            if anchor is None:
                anchor = user.get("last_interaction", now)
            elapsed_user = max(0.0, (now - _safe_float(anchor, now)) / 3600.0)

            updated = False
            fields = [
                ("current_libido_other", "base_libido_other"),
                ("current_aggression_other", "base_aggression_other"),
            ]
            for cur_field, base_field in fields:
                new_val = self._decay_field(user, cur_field, base_field, elapsed_user)
                if new_val is not None:
                    user[cur_field] = new_val
                    updated = True

            if updated:
                # 簿记：记录本次衰减推进时间供下次反推曲线；不改写 last_update 锚点
                user["last_decay"] = now
                self.storage.save_user(uid, user)

    async def _trigger_idle_analysis(self, uid: str, elapsed_hours: float):
        logger.info(
            f"[DecayManager] 用户 {uid} 空闲 {elapsed_hours:.1f}h，触发空闲分析"
        )
        try:
            deltas = await self.unconscious.analyze_idle(uid, elapsed_hours)
            if not deltas:
                return
            now = time.time()
            keys = [
                "libido_other_delta",
                "aggression_other_delta",
                "libido_self_delta",
                "aggression_self_delta",
            ]
            values = {k: _safe_float(deltas.get(k, 0.0)) for k in keys}
            # 全零结果不落地：避免空结果无谓地重置衰减锚点
            if all(abs(v) < 1e-9 for v in values.values()):
                return
            sensitivity = 0.3
            user = self.storage.get(uid)
            if user:
                user["current_libido_other"] = max(
                    0.0,
                    min(
                        50.0,
                        user["current_libido_other"]
                        + values["libido_other_delta"] * sensitivity,
                    ),
                )
                user["current_aggression_other"] = max(
                    0.0,
                    min(
                        50.0,
                        user["current_aggression_other"]
                        + values["aggression_other_delta"] * sensitivity,
                    ),
                )
                # 空闲分析产生的偏移同样是一次"更新"，重设衰减锚点
                user["last_update"] = now
                user.pop("last_decay", None)
                self.storage.save_user(uid, user)
            # 自身数据仅在自身增量非零时才落地，避免无谓重置自身衰减锚点
            if abs(values["libido_self_delta"]) > 1e-9 or (
                abs(values["aggression_self_delta"]) > 1e-9
            ):
                self_data = self.self_storage.get()
                if self_data:
                    self_data["current_libido_self"] = max(
                        0.0,
                        min(
                            50.0,
                            self_data["current_libido_self"]
                            + values["libido_self_delta"] * sensitivity,
                        ),
                    )
                    self_data["current_aggression_self"] = max(
                        0.0,
                        min(
                            50.0,
                            self_data["current_aggression_self"]
                            + values["aggression_self_delta"] * sensitivity,
                        ),
                    )
                    self_data["last_update"] = now
                    self_data.pop("last_decay", None)
                    self.self_storage.save(self_data)
        except Exception as e:
            logger.error(f"[DecayManager] 空闲分析失败: {e}")
