"""
AstrBot 插件：弗洛伊德双驱情绪管理
基于力比多（生本能）与攻击性（死本能）的心理动力学模型，为机器人赋予动态情绪系统。
"""

import asyncio
from pathlib import Path
import time

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .storage import UserDataStorage
from .unconscious import UnconsciousAdjuster
from .decay import DecayManager
from .emotion_tables import get_emotion_description


@register("eros_thanatos", "Soulter", "弗洛伊德双驱情绪管理插件", "2.0.0")
class ErosThanatosPlugin(Star):
    """
    弗洛伊德双驱情绪管理插件主类
    通过钩子注入情绪状态，并后台运行潜意识分析、衰减管理。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 数据存放于 AstrBot 全局 data 目录下，避免更新插件时被覆盖
        self.data_path = Path(get_astrbot_data_path()) / "plugin_data" / "eros_thanatos"
        self.data_path.mkdir(parents=True, exist_ok=True)

        # 初始化各模块
        self.storage = UserDataStorage(self.data_path / "user_data.json")
        self.adjuster = UnconsciousAdjuster(self.context, self.config)
        self.decay_manager = DecayManager(
            self.storage, self.adjuster, self.context, self.config
        )

        # 初始化配置中的默认满好感用户
        self._init_default_users()

        # 启动后台衰减任务
        asyncio.create_task(self.decay_manager.start())

    def _init_default_users(self):
        """
        解析配置项 default_full_affection_uids，
        为列表中的 UID 创建初始满好感（100）的用户档案。
        支持逗号分隔字符串或 JSON 列表。
        """
        uids_raw = self.config.get("default_full_affection_uids", [])
        uids = []
        if isinstance(uids_raw, str):
            uids = [uid.strip() for uid in uids_raw.split(",") if uid.strip()]
        elif isinstance(uids_raw, list):
            uids = [str(uid).strip() for uid in uids_raw if str(uid).strip()]
        for uid in uids:
            if not self.storage.exists(uid):
                self.storage.create_user(uid, affection=100.0)
                logger.info(f"[ErosThanatos] 初始化满好感用户: {uid}")

    # ---------- 钩子：注入情绪状态到主 LLM 的系统提示 ----------
    @filter.on_llm_request(priority=10)
    async def inject_emotion_prompt(self, event: AstrMessageEvent, req):
        """
        在主 LLM 调用前，将当前情绪状态（情感词）追加到 system_prompt 末尾。
        不包含任何具体数值，仅要求 LLM 据此演绎角色。
        """
        uid = event.get_sender_id()
        data = self.storage.get(uid)
        if not data:
            data = self.storage.create_user(uid)

        emotion_desc = get_emotion_description(
            affection=data["affection"],
            libido_other=data["current_libido_other"],
            aggression_other=data["current_aggression_other"],
            libido_self=data["current_libido_self"],
            aggression_self=data["current_aggression_self"],
        )

        append_prompt = (
            f"\n\n【当前情绪状态】\n"
            f"对用户的情感：{emotion_desc['towards_user']}\n"
            f"自身状态：{emotion_desc['self_state']}\n"
            f"(注意：不要在回复中提及任何数值或显示数值面板，只需根据上述情绪状态自然演绎角色。)"
        )
        req.system_prompt += append_prompt

    # ---------- 钩子：触发潜意识分析（后台异步） ----------
    @filter.on_waiting_llm_request(priority=10)
    async def trigger_unconscious_analysis(self, event: AstrMessageEvent):
        """
        在等待主 LLM 回复时，启动后台任务进行潜意识数值更新。
        不阻塞主流程。
        """
        uid = event.get_sender_id()
        asyncio.create_task(self._run_unconscious_update(event, uid))

    async def _run_unconscious_update(self, event: AstrMessageEvent, uid: str):
        """
        后台任务：调用潜意识 LLM 获取增量，更新用户情绪数值。
        包含初印象规则、首次互动跳过逻辑。
        """
        try:
            data = self.storage.get(uid)
            if not data:
                data = self.storage.create_user(uid)

            now = time.time()
            is_first = data.get("last_interaction", 0) == 0

            # 初次互动：仅记录时间戳和轮次，不更新数值（保持平淡）
            if is_first:
                logger.info(f"[ErosThanatos] {uid} 初次互动，保持平淡")
                data["last_interaction"] = now
                data["turn_count"] = 1
                self.storage.save_user(uid, data)
                return

            turn = data.get("turn_count", 1)
            deltas = await self.adjuster.analyze_and_adjust(event, data, turn)

            sensitivity = self.config.get("modify_sensitivity", 30) / 100.0

            # 更新当前情绪值
            data["current_libido_other"] = max(
                0.0,
                min(
                    50.0,
                    data["current_libido_other"]
                    + deltas["libido_other_delta"] * sensitivity,
                ),
            )
            data["current_aggression_other"] = max(
                0.0,
                min(
                    50.0,
                    data["current_aggression_other"]
                    + deltas["aggression_other_delta"] * sensitivity,
                ),
            )
            data["current_libido_self"] = max(
                0.0,
                min(
                    50.0,
                    data["current_libido_self"]
                    + deltas["libido_self_delta"] * sensitivity,
                ),
            )
            data["current_aggression_self"] = max(
                0.0,
                min(
                    50.0,
                    data["current_aggression_self"]
                    + deltas["aggression_self_delta"] * sensitivity,
                ),
            )
            data["affection"] = max(
                0.0,
                min(100.0, data["affection"] + deltas["affection_delta"] * sensitivity),
            )

            # 更新基线值（初印象规则）
            base_coef_other = 1.0 if turn <= 10 else 0.2
            data["base_libido_other"] = max(
                0.0,
                min(
                    50.0,
                    data["base_libido_other"]
                    + deltas.get("base_libido_other_delta", 0.0) * base_coef_other,
                ),
            )
            data["base_aggression_other"] = max(
                0.0,
                min(
                    50.0,
                    data["base_aggression_other"]
                    + deltas.get("base_aggression_other_delta", 0.0) * base_coef_other,
                ),
            )
            # 对自身基线始终难改
            data["base_libido_self"] = max(
                0.0,
                min(
                    50.0,
                    data["base_libido_self"]
                    + deltas.get("base_libido_self_delta", 0.0) * 0.2,
                ),
            )
            data["base_aggression_self"] = max(
                0.0,
                min(
                    50.0,
                    data["base_aggression_self"]
                    + deltas.get("base_aggression_self_delta", 0.0) * 0.2,
                ),
            )

            data["turn_count"] = turn + 1
            data["last_interaction"] = now
            data["last_update"] = now
            data["idle_triggered"] = False

            self.storage.save_user(uid, data)

            if self.config.get("debug_mode"):
                logger.info(f"[ErosThanatos] {uid} 轮次{turn} 更新: {deltas}")

        except Exception as e:
            logger.error(f"[ErosThanatos] 更新失败: {e}")

    # ---------- 用户指令 ----------
    @filter.command("mystatus")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看自己的情绪档案"""
        uid = event.get_sender_id()
        data = self.storage.get(uid)
        if not data:
            data = self.storage.create_user(uid)

        emotion = get_emotion_description(
            data["affection"],
            data["current_libido_other"],
            data["current_aggression_other"],
            data["current_libido_self"],
            data["current_aggression_self"],
        )
        msg = (
            f"【情绪档案】\n"
            f"好感度：{data['affection']:.1f}/100\n"
            f"对他：当前力比多 {data['current_libido_other']:.1f} (基线 {data['base_libido_other']:.1f}) | 攻击性 {data['current_aggression_other']:.1f} (基线 {data['base_aggression_other']:.1f})\n"
            f"对己：当前力比多 {data['current_libido_self']:.1f} (基线 {data['base_libido_self']:.1f}) | 攻击性 {data['current_aggression_self']:.1f} (基线 {data['base_aggression_self']:.1f})\n"
            f"对话轮次：{data.get('turn_count', 0)}\n"
            f"对你情感：{emotion['towards_user']}\n"
            f"自身状态：{emotion['self_state']}"
        )
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_emotion")
    async def cmd_reset(self, event: AstrMessageEvent, target_uid: str = None):
        """管理员指令：完全重置指定用户（或自己）的数值至平淡"""
        if not target_uid:
            target_uid = event.get_sender_id()
        self.storage.create_user(target_uid)
        yield event.plain_result(f"已重置用户 {target_uid} 的所有数值至平淡。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_current")
    async def cmd_reset_current(self, event: AstrMessageEvent, target_uid: str = None):
        """管理员指令：仅重置当前情绪值至基线（不影响印象和好感）"""
        if not target_uid:
            target_uid = event.get_sender_id()
        data = self.storage.get(target_uid)
        if not data:
            data = self.storage.create_user(target_uid)
        data["current_libido_other"] = data["base_libido_other"]
        data["current_aggression_other"] = data["base_aggression_other"]
        data["current_libido_self"] = data["base_libido_self"]
        data["current_aggression_self"] = data["base_aggression_self"]
        self.storage.save_user(target_uid, data)
        yield event.plain_result(f"已重置用户 {target_uid} 的当前情绪至基线。")

    async def terminate(self):
        """插件卸载时停止后台任务"""
        await self.decay_manager.stop()
        logger.info("[ErosThanatos] 插件已卸载")
