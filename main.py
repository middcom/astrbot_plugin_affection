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

from .storage import UserDataStorage, SelfDataStorage
from .unconscious import UnconsciousAdjuster
from .decay import DecayManager
from .emotion_tables import get_emotion_description


@register("eros_thanatos", "Soulter", "弗洛伊德双驱情绪管理插件", "2.1.0")
class ErosThanatosPlugin(Star):
    """
    弗洛伊德双驱情绪管理插件主类
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.data_path = Path(get_astrbot_data_path()) / "plugin_data" / "eros_thanatos"
        self.data_path.mkdir(parents=True, exist_ok=True)

        # 自身数据独立存储
        self.self_storage = SelfDataStorage(self.data_path / "self_data.json")
        self._init_self_data()

        # 用户数据存储（传入自身存储用于迁移）
        self.storage = UserDataStorage(self.data_path / "user_data.json", self.self_storage)

        self.adjuster = UnconsciousAdjuster(self.context, self.config, self.self_storage)
        self.decay_manager = DecayManager(self.storage, self.self_storage, self.adjuster, self.context, self.config)

        self._init_default_users()
        asyncio.create_task(self.decay_manager.start())

    def _init_self_data(self):
        """初始化全局自身数据（若不存在则根据配置创建）"""
        if not self.self_storage.exists():
            base_libido = float(self.config.get("initial_libido_self", 25))
            base_aggression = float(self.config.get("initial_aggression_self", 25))
            self.self_storage.create(base_libido, base_aggression)
            logger.info(f"[ErosThanatos] 初始化全局自身数据: 自力比多基线 {base_libido}, 自攻击性基线 {base_aggression}")

    def _init_default_users(self):
        uids_raw = self.config.get("default_full_affection_uids", [])
        uids = []
        if isinstance(uids_raw, str):
            uids = [uid.strip() for uid in uids_raw.split(",") if uid.strip()]
        elif isinstance(uids_raw, list):
            uids = [str(uid).strip() for uid in uids_raw if str(uid).strip()]
        for uid in uids:
            if not self.storage.exists(uid):
                self.storage.create_user(
                    uid,
                    affection=100.0,
                    base_libido_other=float(self.config.get("initial_libido_other", 25)),
                    base_aggression_other=float(self.config.get("initial_aggression_other", 25))
                )
                logger.info(f"[ErosThanatos] 初始化满好感用户: {uid}")

    # ---------- 钩子：注入情绪状态 ----------
    @filter.on_llm_request(priority=10)
    async def inject_emotion_prompt(self, event: AstrMessageEvent, req):
        uid = event.get_sender_id()
        user_data = self.storage.get(uid)
        if not user_data:
            user_data = self.storage.create_user(
                uid,
                affection=float(self.config.get("initial_affection", 50)),
                base_libido_other=float(self.config.get("initial_libido_other", 25)),
                base_aggression_other=float(self.config.get("initial_aggression_other", 25))
            )

        self_data = self.self_storage.get()
        emotion_desc = get_emotion_description(
            affection=user_data["affection"],
            libido_other=user_data["current_libido_other"],
            aggression_other=user_data["current_aggression_other"],
            libido_self=self_data["current_libido_self"],
            aggression_self=self_data["current_aggression_self"]
        )

        append_prompt = (
            f"\n\n【当前情绪状态】\n"
            f"对用户的情感：{emotion_desc['towards_user']}\n"
            f"自身状态：{emotion_desc['self_state']}\n"
            f"(注意：不要在回复中提及任何数值或显示数值面板，只需根据上述情绪状态自然演绎角色。)"
        )
        req.system_prompt += append_prompt

    # ---------- 钩子：触发潜意识更新 ----------
    @filter.on_waiting_llm_request(priority=10)
    async def trigger_unconscious_analysis(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        asyncio.create_task(self._run_unconscious_update(event, uid))

    async def _run_unconscious_update(self, event: AstrMessageEvent, uid: str):
        try:
            user_data = self.storage.get(uid)
            if not user_data:
                user_data = self.storage.create_user(
                    uid,
                    affection=float(self.config.get("initial_affection", 50)),
                    base_libido_other=float(self.config.get("initial_libido_other", 25)),
                    base_aggression_other=float(self.config.get("initial_aggression_other", 25))
                )

            now = time.time()
            is_first = user_data.get("last_interaction", 0) == 0

            if is_first:
                logger.info(f"[ErosThanatos] {uid} 初次互动，保持平淡")
                user_data["last_interaction"] = now
                user_data["turn_count"] = 1
                self.storage.save_user(uid, user_data)
                return

            turn = user_data.get("turn_count", 1)
            deltas = await self.adjuster.analyze_and_adjust(event, user_data, turn)

            sensitivity = self.config.get("modify_sensitivity", 30) / 100.0

            # 更新用户当前值
            user_data["current_libido_other"] = max(0.0, min(50.0, user_data["current_libido_other"] + deltas["libido_other_delta"] * sensitivity))
            user_data["current_aggression_other"] = max(0.0, min(50.0, user_data["current_aggression_other"] + deltas["aggression_other_delta"] * sensitivity))
            user_data["affection"] = max(0.0, min(100.0, user_data["affection"] + deltas["affection_delta"] * sensitivity))

            # 更新用户基线值
            base_coef_other = 1.0 if turn <= 10 else 0.2
            user_data["base_libido_other"] = max(0.0, min(50.0, user_data["base_libido_other"] + deltas.get("base_libido_other_delta", 0.0) * base_coef_other))
            user_data["base_aggression_other"] = max(0.0, min(50.0, user_data["base_aggression_other"] + deltas.get("base_aggression_other_delta", 0.0) * base_coef_other))

            user_data["turn_count"] = turn + 1
            user_data["last_interaction"] = now
            user_data["last_update"] = now
            user_data["idle_triggered"] = False

            self.storage.save_user(uid, user_data)

            # 更新全局自身数据
            self_data = self.self_storage.get()
            self_data["current_libido_self"] = max(0.0, min(50.0, self_data["current_libido_self"] + deltas["libido_self_delta"] * sensitivity))
            self_data["current_aggression_self"] = max(0.0, min(50.0, self_data["current_aggression_self"] + deltas["aggression_self_delta"] * sensitivity))
            self_data["base_libido_self"] = max(0.0, min(50.0, self_data["base_libido_self"] + deltas.get("base_libido_self_delta", 0.0) * 0.2))
            self_data["base_aggression_self"] = max(0.0, min(50.0, self_data["base_aggression_self"] + deltas.get("base_aggression_self_delta", 0.0) * 0.2))
            self_data["last_update"] = now
            self.self_storage.save(self_data)

            if self.config.get("debug_mode"):
                logger.info(f"[ErosThanatos] {uid} 轮次{turn} 更新: {deltas}")

        except Exception as e:
            logger.error(f"[ErosThanatos] 更新失败: {e}")

    # ---------- 指令 ----------
    @filter.command("mystatus")
    async def cmd_status(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        user_data = self.storage.get(uid)
        if not user_data:
            user_data = self.storage.create_user(
                uid,
                affection=float(self.config.get("initial_affection", 50)),
                base_libido_other=float(self.config.get("initial_libido_other", 25)),
                base_aggression_other=float(self.config.get("initial_aggression_other", 25))
            )
        self_data = self.self_storage.get()
        emotion = get_emotion_description(
            user_data["affection"],
            user_data["current_libido_other"],
            user_data["current_aggression_other"],
            self_data["current_libido_self"],
            self_data["current_aggression_self"]
        )
        msg = (
            f"【情绪档案】\n"
            f"好感度：{user_data['affection']:.1f}/100\n"
            f"对他：当前力比多 {user_data['current_libido_other']:.1f} (基线 {user_data['base_libido_other']:.1f}) | 攻击性 {user_data['current_aggression_other']:.1f} (基线 {user_data['base_aggression_other']:.1f})\n"
            f"对己：当前力比多 {self_data['current_libido_self']:.1f} (基线 {self_data['base_libido_self']:.1f}) | 攻击性 {self_data['current_aggression_self']:.1f} (基线 {self_data['base_aggression_self']:.1f})\n"
            f"对话轮次：{user_data.get('turn_count', 0)}\n"
            f"对你情感：{emotion['towards_user']}\n"
            f"自身状态：{emotion['self_state']}"
        )
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_emotion")
    async def cmd_reset(self, event: AstrMessageEvent, target_uid: str = None):
        if not target_uid:
            target_uid = event.get_sender_id()
        self.storage.create_user(
            target_uid,
            affection=float(self.config.get("initial_affection", 50)),
            base_libido_other=float(self.config.get("initial_libido_other", 25)),
            base_aggression_other=float(self.config.get("initial_aggression_other", 25))
        )
        yield event.plain_result(f"已重置用户 {target_uid} 的所有数值至初始状态。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_current")
    async def cmd_reset_current(self, event: AstrMessageEvent, target_uid: str = None):
        if not target_uid:
            target_uid = event.get_sender_id()
        user_data = self.storage.get(target_uid)
        if not user_data:
            user_data = self.storage.create_user(
                target_uid,
                affection=float(self.config.get("initial_affection", 50)),
                base_libido_other=float(self.config.get("initial_libido_other", 25)),
                base_aggression_other=float(self.config.get("initial_aggression_other", 25))
            )
        user_data["current_libido_other"] = user_data["base_libido_other"]
        user_data["current_aggression_other"] = user_data["base_aggression_other"]
        self.storage.save_user(target_uid, user_data)
        # 同时重置自身当前值到基线
        self_data = self.self_storage.get()
        self_data["current_libido_self"] = self_data["base_libido_self"]
        self_data["current_aggression_self"] = self_data["base_aggression_self"]
        self.self_storage.save(self_data)
        yield event.plain_result(f"已重置用户 {target_uid} 的当前情绪至基线。")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_all_emotions")
    async def cmd_reset_all(self, event: AstrMessageEvent):
        """
        重置所有用户数据及机器人自身情绪至初始状态。
        危险操作，仅管理员可用。
        """
        # 1. 清空用户数据存储
        self.storage.data.clear()
        self.storage._save()
        
        # 2. 重新初始化自身数据（使用配置中的初始值）
        base_libido_self = float(self.config.get("initial_libido_self", 25))
        base_aggression_self = float(self.config.get("initial_aggression_self", 25))
        self.self_storage.create(base_libido_self, base_aggression_self)
        
        # 3. 重新创建默认满好感用户（如果配置了）
        self._init_default_users()
        
        logger.warning(f"[ErosThanatos] 管理员 {event.get_sender_id()} 执行了全局重置！")
        yield event.plain_result("⚠️ 已重置所有用户的情绪档案及机器人自身情绪至初始状态。")

    async def terminate(self):
        await self.decay_manager.stop()
        logger.info("[ErosThanatos] 插件已卸载")