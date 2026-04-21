"""
AstrBot 插件：弗洛伊德双驱情绪管理
基于力比多（生本能）与攻击性（死本能）的心理动力学模型，为机器人赋予动态情绪系统。
支持多机器人数据隔离、动态敏感度。
"""

import asyncio
import shutil
import time
import traceback
from typing import Dict

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger, AstrBotConfig

from .storage import UserDataStorage, SelfDataStorage
from .unconscious import UnconsciousAdjuster
from .decay import DecayManager
from .emotion_tables import get_emotion_description

# 情绪参考表格（用于 /mystatus 展示）
EMOTION_REFERENCE_TABLE = """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
情绪档案参考
好感参考：
0：强烈厌恶或仇恨  25：非常厌恶  40：有些负面  50：陌生人
60：些许好感  75：普通朋友  90：喜欢  100：生命最重要

好感 = 0
他力比多\他攻击性    0       12.5     25       37.5     50
50                    痴迷    纠缠     憎恨     毁灭性恨 同归于尽
37.5                  依赖    烦躁     厌恶     仇恨     残暴
25                    冷淡    无聊     轻蔑     蔑视     冷酷
12.5                  回避    疏离     嫌弃     恶心     憎恶
0                     无视    不存在   否定     驱逐     湮灭

好感 = 25
他力比多\他攻击性    0       12.5     25       37.5     50
50                    执着    猜疑     嫉妒     报复欲   毁灭欲
37.5                  渴求    试探     敌意     愤怒     仇恨
25                    普通    不耐烦   竞争     攻击玩笑 讽刺
12.5                  礼貌    无聊     烦躁     厌恶     憎恨
0                     冷漠    沉默     回避     拒绝     驱赶

好感 = 50
他力比多\他攻击性    0       12.5     25       37.5     50
50                    迷恋    占有     嫉妒     施虐倾向 毁灭性爱
37.5                  依恋    激情     纠缠     报复     仇恨
25                    喜欢    渴望     竞争     愤怒     残暴
12.5                  好感    无聊     烦躁     厌恶     憎恨
0                     冷漠    疏离     轻蔑     蔑视     冷酷

好感 = 75
他力比多\他攻击性    0       12.5     25       37.5     50
50                    痴迷    占有欲   吃醋     霸道     毁灭占有
37.5                  依恋甜  热情     撒娇纠缠 管教欲   因爱生恨
25                    欣赏    心动     争宠     着急     暴躁后悔
12.5                  友善    小无聊   小烦躁   恼火     气话哄好
0                     平淡    安静     冷一下   生闷气   冷战

好感 = 100
他力比多\他攻击性    0       12.5     25       37.5     50
50                    崇拜    完全占有 吃醋失控 施虐play 共依存
37.5                  离不开  热情似火 黏人烦   调教欲   相爱相杀
25                    溺爱    渴望融合 撒娇争夺 炸毛     虐恋
12.5                  安心    小撒娇   小赌气   假生气   闹别扭
0                     平静幸福 沉默有爱 闷气心软 委屈     冷战等你哄

对自身的情绪表（自力比多 × 自攻击性）
自力比多\自攻击性    0       12.5     25       37.5     50
50                    自恋    自满     自傲     自大     自毁冲动
37.5                  自爱    自怜     自责     自卑     自我仇恨
25                    自信    平淡     内疚     自我厌恶 自残欲
12.5                  自保    空虚     羞愧     自贬     自毁欲
0                     无我    麻木     自我否定 自我毁灭 湮灭
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


@register("astrbot_plugin_affection", "middcom,dream,deepseek", "弗洛伊德双驱情绪管理插件", "v1.2")
class ErosThanatosPlugin(Star):
    """
    弗洛伊德双驱情绪管理插件主类
    通过钩子注入情绪状态，并后台运行潜意识分析、衰减管理。
    支持多机器人：根据事件的 self_id 动态隔离数据。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 基础数据目录
        self.base_data_path = StarTools.get_data_dir()
        self.base_data_path.mkdir(parents=True, exist_ok=True)

        # 缓存每个机器人的存储实例和衰减管理器
        self._storages: Dict[str, UserDataStorage] = {}
        self._self_storages: Dict[str, SelfDataStorage] = {}
        self._adjusters: Dict[str, UnconsciousAdjuster] = {}
        self._decay_managers: Dict[str, DecayManager] = {}

        # 迁移旧数据（根目录下的旧文件）到默认机器人目录，避免丢失
        self._migrate_old_data()

    def _migrate_old_data(self):
        """将旧版根目录下的数据文件移动到 default_bot 目录"""
        default_bot_path = self.base_data_path / "default_bot"
        default_bot_path.mkdir(parents=True, exist_ok=True)

        old_user_file = self.base_data_path / "user_data.json"
        old_self_file = self.base_data_path / "self_data.json"
        if old_user_file.exists():
            shutil.move(str(old_user_file), str(default_bot_path / "user_data.json"))
            logger.info(f"[ErosThanatos] 已迁移旧用户数据到 default_bot")
        if old_self_file.exists():
            shutil.move(str(old_self_file), str(default_bot_path / "self_data.json"))
            logger.info(f"[ErosThanatos] 已迁移旧自身数据到 default_bot")

    def _get_bot_id(self, event: AstrMessageEvent) -> str:
        """从事件中获取当前机器人的唯一标识"""
        # 尝试从 event 中获取 self_id
        if hasattr(event, "get_self_id"):
            bot_id = event.get_self_id()
        elif hasattr(event, "message_obj") and hasattr(event.message_obj, "self_id"):
            bot_id = event.message_obj.self_id
        else:
            # 回退到配置中的 bot_self_id
            bot_id = self.config.get("bot_self_id", "default_bot")
        if not bot_id:
            bot_id = "default_bot"
        return str(bot_id)

    def _get_or_create_storages(self, bot_id: str):
        """获取或创建指定机器人的存储实例和调节器"""
        if bot_id not in self._storages:
            bot_data_path = self.base_data_path / bot_id
            bot_data_path.mkdir(parents=True, exist_ok=True)

            self_storage = SelfDataStorage(bot_data_path / "self_data.json")
            self._self_storages[bot_id] = self_storage

            # 初始化自身数据
            if not self_storage.exists():
                base_libido = float(self.config.get("initial_libido_self", 25))
                base_aggression = float(self.config.get("initial_aggression_self", 25))
                self_storage.create(base_libido, base_aggression)
                logger.info(
                    f"[ErosThanatos] 初始化机器人 {bot_id} 自身数据: 自力比多 {base_libido}, 自攻击性 {base_aggression}"
                )

            user_storage = UserDataStorage(
                bot_data_path / "user_data.json", self_storage
            )
            self._storages[bot_id] = user_storage

            adjuster = UnconsciousAdjuster(self.context, self.config, self_storage)
            self._adjusters[bot_id] = adjuster

            decay_manager = DecayManager(
                user_storage, self_storage, adjuster, self.context, self.config
            )
            self._decay_managers[bot_id] = decay_manager

            # 启动该机器人的后台衰减任务
            asyncio.create_task(decay_manager.start())

            # 初始化该机器人的默认满好感用户
            self._init_default_users_for_bot(bot_id, user_storage)

        return (
            self._storages[bot_id],
            self._self_storages[bot_id],
            self._adjusters[bot_id],
            self._decay_managers[bot_id],
        )

    def _init_default_users_for_bot(self, bot_id: str, user_storage: UserDataStorage):
        """为指定机器人的存储初始化默认满好感用户"""
        uids_raw = self.config.get("default_full_affection_uids", [])
        uids = []
        if isinstance(uids_raw, str):
            uids = [uid.strip() for uid in uids_raw.split(",") if uid.strip()]
        elif isinstance(uids_raw, list):
            uids = [str(uid).strip() for uid in uids_raw if str(uid).strip()]
        for uid in uids:
            if not user_storage.exists(uid):
                user_storage.create_user(
                    uid,
                    affection=100.0,
                    base_libido_other=float(
                        self.config.get("initial_libido_other", 25)
                    ),
                    base_aggression_other=float(
                        self.config.get("initial_aggression_other", 25)
                    ),
                )
                logger.info(f"[ErosThanatos] 机器人 {bot_id} 初始化满好感用户: {uid}")

    # ---------- 钩子：注入情绪状态到主 LLM 的系统提示 ----------
    @filter.on_llm_request(priority=10)
    async def inject_emotion_prompt(self, event: AstrMessageEvent, req):
        """
        在主 LLM 调用前，注入当前情绪数值面板。
        具体演绎规则由用户在人设中自行定义（见 README）。
        """
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, _, _ = self._get_or_create_storages(bot_id)

        uid = event.get_sender_id()
        user_data = user_storage.get(uid)
        if not user_data:
            user_data = user_storage.create_user(
                uid,
                affection=float(self.config.get("initial_affection", 50)),
                base_libido_other=float(self.config.get("initial_libido_other", 25)),
                base_aggression_other=float(
                    self.config.get("initial_aggression_other", 25)
                ),
            )

        self_data = self_storage.get()

        # 获取情感标签作为简写参考
        emotion_desc = get_emotion_description(
            affection=user_data["affection"],
            libido_other=user_data["current_libido_other"],
            aggression_other=user_data["current_aggression_other"],
            libido_self=self_data["current_libido_self"],
            aggression_self=self_data["current_aggression_self"],
        )

        # 极简数值面板（仅提供数字和基础定义，不包含任何演绎指导）
        values_text = (
            f"【当前情绪数值】\n"
            f"他力比多：{user_data['current_libido_other']:.1f}/50（亲近/给予温暖的欲望）\n"
            f"他攻击性：{user_data['current_aggression_other']:.1f}/50（推开/伤害的冲动）\n"
            f"好感度：{user_data['affection']:.1f}/100\n"
            f"自力比多：{self_data['current_libido_self']:.1f}/50（自爱/珍视自己）\n"
            f"自攻击性：{self_data['current_aggression_self']:.1f}/50（自责/自我毁灭）\n"
            f"参考标签：对用户「{emotion_desc['towards_user']}」，自身「{emotion_desc['self_state']}」\n"
        )

        # 仅追加一行提示，引导 LLM 遵循人设中的规则
        reminder = "（请根据上述数值和你在人设中定义的「情绪驱动规则」来演绎角色，不要提及数值。）"

        emotion_prompt = values_text + reminder
        if req.system_prompt:
            req.system_prompt += "\n\n" + emotion_prompt
        else:
            req.system_prompt = emotion_prompt

        if self.config.get("debug_mode"):
            logger.info(
                f"[ErosThanatos] 机器人 {bot_id} 注入情绪数值到 {uid}:\n{values_text}"
            )

    # ---------- 钩子：触发潜意识分析（后台异步） ----------
    @filter.on_waiting_llm_request(priority=10)
    async def trigger_unconscious_analysis(self, event: AstrMessageEvent):
        """
        在等待主 LLM 回复时，启动后台任务进行潜意识数值更新。
        """
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, adjuster, _ = self._get_or_create_storages(bot_id)
        uid = event.get_sender_id()
        asyncio.create_task(
            self._run_unconscious_update(
                bot_id, event, uid, user_storage, self_storage, adjuster
            )
        )

    async def _run_unconscious_update(
        self,
        bot_id: str,
        event: AstrMessageEvent,
        uid: str,
        user_storage: UserDataStorage,
        self_storage: SelfDataStorage,
        adjuster: UnconsciousAdjuster,
    ):
        """
        后台任务：调用潜意识 LLM 获取增量，更新用户情绪数值。
        支持动态敏感度（根据场景强度调整变化幅度）。
        """
        try:
            user_data = user_storage.get(uid)
            if not user_data:
                user_data = user_storage.create_user(
                    uid,
                    affection=float(self.config.get("initial_affection", 50)),
                    base_libido_other=float(
                        self.config.get("initial_libido_other", 25)
                    ),
                    base_aggression_other=float(
                        self.config.get("initial_aggression_other", 25)
                    ),
                )

            now = time.time()
            is_first = user_data.get("last_interaction", 0) == 0

            # 初次互动：仅记录时间戳和轮次，不更新数值（保持平淡）
            if is_first:
                logger.info(
                    f"[ErosThanatos] 机器人 {bot_id} 用户 {uid} 初次互动，保持平淡"
                )
                user_data["last_interaction"] = now
                user_data["turn_count"] = 1
                user_storage.save_user(uid, user_data)
                return

            turn = user_data.get("turn_count", 1)
            deltas = await adjuster.analyze_and_adjust(event, user_data, turn)

            base_sensitivity = self.config.get("modify_sensitivity", 30) / 100.0
            intensity = deltas.get("intensity", 1.0)
            sensitivity = base_sensitivity * intensity

            if self.config.get("debug_mode"):
                logger.info(
                    f"[ErosThanatos] 机器人 {bot_id} 用户 {uid} 场景强度: {intensity}, 有效敏感度: {sensitivity:.2f}"
                )

            # 更新用户当前值
            user_data["current_libido_other"] = max(
                0.0,
                min(
                    50.0,
                    user_data["current_libido_other"]
                    + deltas["libido_other_delta"] * sensitivity,
                ),
            )
            user_data["current_aggression_other"] = max(
                0.0,
                min(
                    50.0,
                    user_data["current_aggression_other"]
                    + deltas["aggression_other_delta"] * sensitivity,
                ),
            )
            user_data["affection"] = max(
                0.0,
                min(
                    100.0,
                    user_data["affection"] + deltas["affection_delta"] * sensitivity,
                ),
            )

            # 更新用户基线值（初印象规则）
            base_coef_other = 1.0 if turn <= 10 else 0.2
            user_data["base_libido_other"] = max(
                0.0,
                min(
                    50.0,
                    user_data["base_libido_other"]
                    + deltas.get("base_libido_other_delta", 0.0) * base_coef_other,
                ),
            )
            user_data["base_aggression_other"] = max(
                0.0,
                min(
                    50.0,
                    user_data["base_aggression_other"]
                    + deltas.get("base_aggression_other_delta", 0.0) * base_coef_other,
                ),
            )

            user_data["turn_count"] = turn + 1
            user_data["last_interaction"] = now
            user_data["last_update"] = now
            user_data["idle_triggered"] = False

            user_storage.save_user(uid, user_data)

            # 更新全局自身数据
            self_data = self_storage.get()
            self_data["current_libido_self"] = max(
                0.0,
                min(
                    50.0,
                    self_data["current_libido_self"]
                    + deltas["libido_self_delta"] * sensitivity,
                ),
            )
            self_data["current_aggression_self"] = max(
                0.0,
                min(
                    50.0,
                    self_data["current_aggression_self"]
                    + deltas["aggression_self_delta"] * sensitivity,
                ),
            )
            self_data["base_libido_self"] = max(
                0.0,
                min(
                    50.0,
                    self_data["base_libido_self"]
                    + deltas.get("base_libido_self_delta", 0.0) * 0.2,
                ),
            )
            self_data["base_aggression_self"] = max(
                0.0,
                min(
                    50.0,
                    self_data["base_aggression_self"]
                    + deltas.get("base_aggression_self_delta", 0.0) * 0.2,
                ),
            )
            self_data["last_update"] = now
            self_storage.save(self_data)

            if self.config.get("debug_mode"):
                logger.info(
                    f"[ErosThanatos] 机器人 {bot_id} 用户 {uid} 轮次{turn} 更新: {deltas}"
                )

        except Exception as e:
            logger.error(f"[ErosThanatos] 机器人 {bot_id} 用户 {uid} 更新失败: {e}\n{traceback.format_exc()}")

    # ---------- 用户指令 ----------
    @filter.command("mystatus")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看自己的情绪档案（含完整参考表格）"""
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, _, _ = self._get_or_create_storages(bot_id)

        uid = event.get_sender_id()
        user_data = user_storage.get(uid)
        if not user_data:
            user_data = user_storage.create_user(
                uid,
                affection=float(self.config.get("initial_affection", 50)),
                base_libido_other=float(self.config.get("initial_libido_other", 25)),
                base_aggression_other=float(
                    self.config.get("initial_aggression_other", 25)
                ),
            )
        self_data = self_storage.get()
        emotion = get_emotion_description(
            user_data["affection"],
            user_data["current_libido_other"],
            user_data["current_aggression_other"],
            self_data["current_libido_self"],
            self_data["current_aggression_self"],
        )
        msg = (
            f"【情绪档案】\n"
            f"好感度：{user_data['affection']:.1f}/100\n"
            f"对他：当前力比多 {user_data['current_libido_other']:.1f} (基线 {user_data['base_libido_other']:.1f}) | 攻击性 {user_data['current_aggression_other']:.1f} (基线 {user_data['base_aggression_other']:.1f})\n"
            f"对己：当前力比多 {self_data['current_libido_self']:.1f} (基线 {self_data['base_libido_self']:.1f}) | 攻击性 {self_data['current_aggression_self']:.1f} (基线 {self_data['base_aggression_self']:.1f})\n"
            f"对话轮次：{user_data.get('turn_count', 0)}\n"
            f"对你情感：{emotion['towards_user']}\n"
            f"自身状态：{emotion['self_state']}" + EMOTION_REFERENCE_TABLE
        )
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_emotion")
    async def cmd_reset(self, event: AstrMessageEvent, target_uid: str = None):
        """管理员指令：完全重置指定用户（或自己）的数值至初始状态"""
        bot_id = self._get_bot_id(event)
        user_storage, _, _, _ = self._get_or_create_storages(bot_id)

        if not target_uid:
            target_uid = event.get_sender_id()
        user_storage.create_user(
            target_uid,
            affection=float(self.config.get("initial_affection", 50)),
            base_libido_other=float(self.config.get("initial_libido_other", 25)),
            base_aggression_other=float(
                self.config.get("initial_aggression_other", 25)
            ),
        )
        yield event.plain_result(f"已重置用户 {target_uid} 的所有数值至初始状态。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_current")
    async def cmd_reset_current(self, event: AstrMessageEvent, target_uid: str = None):
        """管理员指令：仅重置当前情绪值至基线（不影响印象和好感）"""
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, _, _ = self._get_or_create_storages(bot_id)

        if not target_uid:
            target_uid = event.get_sender_id()
        user_data = user_storage.get(target_uid)
        if not user_data:
            user_data = user_storage.create_user(
                target_uid,
                affection=float(self.config.get("initial_affection", 50)),
                base_libido_other=float(self.config.get("initial_libido_other", 25)),
                base_aggression_other=float(
                    self.config.get("initial_aggression_other", 25)
                ),
            )
        user_data["current_libido_other"] = user_data["base_libido_other"]
        user_data["current_aggression_other"] = user_data["base_aggression_other"]
        user_storage.save_user(target_uid, user_data)
        # 同时重置自身当前值到基线
        self_data = self_storage.get()
        self_data["current_libido_self"] = self_data["base_libido_self"]
        self_data["current_aggression_self"] = self_data["base_aggression_self"]
        self_storage.save(self_data)
        yield event.plain_result(f"已重置用户 {target_uid} 的当前情绪至基线。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_all_emotions")
    async def cmd_reset_all(self, event: AstrMessageEvent):
        """
        全局重置指令：清除当前机器人的所有用户情绪档案，并将自身情绪重置为初始值。
        危险操作，仅管理员可用。
        """
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, _, _ = self._get_or_create_storages(bot_id)

        # 1. 清空用户数据存储
        user_storage.data.clear()
        user_storage._save()

        # 2. 重新初始化自身数据
        base_libido_self = float(self.config.get("initial_libido_self", 25))
        base_aggression_self = float(self.config.get("initial_aggression_self", 25))
        self_storage.create(base_libido_self, base_aggression_self)

        # 3. 重新创建默认满好感用户
        self._init_default_users_for_bot(bot_id, user_storage)

        logger.warning(
            f"[ErosThanatos] 管理员 {event.get_sender_id()} 在机器人 {bot_id} 执行了全局重置！"
        )
        yield event.plain_result(
            f"⚠️ 已重置机器人 {bot_id} 的所有用户情绪档案及自身情绪至初始状态。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("set_emotion")
    async def cmd_set_emotion(
        self,
        event: AstrMessageEvent,
        target_uid: str,
        affection: float = None,
        libido_other: float = None,
        aggression_other: float = None,
        libido_self: float = None,
        aggression_self: float = None,
    ):
        """
        管理员指令：修改指定用户的情绪数值。
        用法：/set_emotion <uid> [affection] [libido_other] [aggression_other] [libido_self] [aggression_self]
        未指定的参数保持不变；修改的是当前值，基线值同步更新。
        """
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, _, _ = self._get_or_create_storages(bot_id)

        user_data = user_storage.get(target_uid)
        if not user_data:
            user_data = user_storage.create_user(target_uid)

        if affection is not None:
            user_data["affection"] = max(0.0, min(100.0, affection))
        if libido_other is not None:
            user_data["current_libido_other"] = max(0.0, min(50.0, libido_other))
            user_data["base_libido_other"] = user_data["current_libido_other"]
        if aggression_other is not None:
            user_data["current_aggression_other"] = max(
                0.0, min(50.0, aggression_other)
            )
            user_data["base_aggression_other"] = user_data["current_aggression_other"]
        if libido_self is not None:
            self_data = self_storage.get()
            self_data["current_libido_self"] = max(0.0, min(50.0, libido_self))
            self_data["base_libido_self"] = self_data["current_libido_self"]
            self_storage.save(self_data)
        if aggression_self is not None:
            self_data = self_storage.get()
            self_data["current_aggression_self"] = max(0.0, min(50.0, aggression_self))
            self_data["base_aggression_self"] = self_data["current_aggression_self"]
            self_storage.save(self_data)

        user_storage.save_user(target_uid, user_data)
        yield event.plain_result(f"已更新用户 {target_uid} 的情绪数值。")

    async def terminate(self):
        """插件卸载时停止所有后台任务"""
        for bot_id, dm in self._decay_managers.items():
            await dm.stop()
        logger.info("[ErosThanatos] 插件已卸载")
