"""
工具函数模块
"""

import time
import traceback

from astrbot.api import logger


def clamp(value: float, lo: float, hi: float) -> float:
    """将值限制在 [lo, hi] 区间内。"""
    return max(lo, min(hi, value))


def run_unconscious_update(
    bot_id: str,
    event,
    uid: str,
    user_storage,
    self_storage,
    adjuster,
    config,
):
    """
    返回一个 coroutine：调用潜意识 LLM 获取增量，更新情绪数值。
    主文件通过 asyncio.create_task() 启动。
    """

    async def _coro():
        try:
            user_data = user_storage.get(uid)
            if not user_data:
                user_data = user_storage.create_user(
                    uid,
                    affection=float(config.get("initial_affection", 50)),
                    base_libido_other=float(config.get("initial_libido_other", 25)),
                    base_aggression_other=float(
                        config.get("initial_aggression_other", 25)
                    ),
                )

            now = time.time()
            turn = user_data.get("turn_count", 0)
            is_first = turn == 0

            if is_first:
                logger.info(
                    f"[affection] 机器人 {bot_id} 用户 {uid} 初次互动，保持平淡"
                )
                user_data["last_interaction"] = now
                user_data["turn_count"] = 1
                user_storage.save_user(uid, user_data)
                return

            deltas = await adjuster.analyze_and_adjust(event, user_data, turn)

            base_sensitivity = config.get("modify_sensitivity", 30) / 100.0
            intensity = deltas.get("intensity", 1.0)
            sensitivity = base_sensitivity * intensity

            if config.get("debug_mode"):
                logger.info(
                    f"[affection] 机器人 {bot_id} 用户 {uid} 场景强度: {intensity}, 有效敏感度: {sensitivity:.2f}"
                )

            # 更新用户当前值
            user_data["current_libido_other"] = clamp(
                0.0,
                50.0,
                user_data["current_libido_other"]
                + deltas["libido_other_delta"] * sensitivity,
            )
            user_data["current_aggression_other"] = clamp(
                0.0,
                50.0,
                user_data["current_aggression_other"]
                + deltas["aggression_other_delta"] * sensitivity,
            )
            user_data["affection"] = clamp(
                0.0,
                100.0,
                user_data["affection"] + deltas["affection_delta"] * sensitivity,
            )

            # 基线值（初印象规则）
            base_coef_other = 1.0 if turn <= 10 else 0.2
            user_data["base_libido_other"] = clamp(
                0.0,
                50.0,
                user_data["base_libido_other"]
                + deltas.get("base_libido_other_delta", 0.0) * base_coef_other,
            )
            user_data["base_aggression_other"] = clamp(
                0.0,
                50.0,
                user_data["base_aggression_other"]
                + deltas.get("base_aggression_other_delta", 0.0) * base_coef_other,
            )

            user_data["turn_count"] = turn + 1
            user_data["last_interaction"] = now
            user_data["last_update"] = now
            user_data["idle_triggered"] = False
            user_storage.save_user(uid, user_data)

            # 更新自身数据
            self_data = self_storage.get()
            self_data["current_libido_self"] = clamp(
                0.0,
                50.0,
                self_data["current_libido_self"]
                + deltas["libido_self_delta"] * sensitivity,
            )
            self_data["current_aggression_self"] = clamp(
                0.0,
                50.0,
                self_data["current_aggression_self"]
                + deltas["aggression_self_delta"] * sensitivity,
            )
            self_data["base_libido_self"] = clamp(
                0.0,
                50.0,
                self_data["base_libido_self"]
                + deltas.get("base_libido_self_delta", 0.0) * 0.2,
            )
            self_data["base_aggression_self"] = clamp(
                0.0,
                50.0,
                self_data["base_aggression_self"]
                + deltas.get("base_aggression_self_delta", 0.0) * 0.2,
            )
            self_data["last_update"] = now
            self_storage.save(self_data)

            if config.get("debug_mode"):
                logger.info(
                    f"[affection] 机器人 {bot_id} 用户 {uid} 轮次{turn} 更新: {deltas}"
                )

        except Exception as e:
            logger.error(
                f"[affection] 机器人 {bot_id} 用户 {uid} 更新失败: {e}\n{traceback.format_exc()}"
            )

    return _coro()
