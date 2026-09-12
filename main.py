"""
AstrBot 插件：弗洛伊德双驱情绪管理
基于力比多（生本能）与攻击性（死本能）的心理动力学模型，为机器人赋予动态情绪系统。

文件结构：
  main.py              - 插件入口：初始化、存储管理、钩子、命令、生命周期
  decay.py             - 后台衰减管理器
  storage.py           - 数据持久化层
  unconscious.py       - 潜意识 LLM 分析器
  emotion_tables.py    - 情绪标签映射表
"""

import asyncio
import shutil

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .decay import DecayManager
from .emotion_tables import get_emotion_description
from .storage import SelfDataStorage, UserDataStorage
from .unconscious import UnconsciousAdjuster
from .utils import clamp, run_unconscious_update

# ====================================================================
# 情绪参考表格（用于 /mystatus 展示）
# ====================================================================

EMOTION_REFERENCE_TABLE = r"""

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


@register(
    "astrbot_plugin_affection",
    "middcom,dream,deepseek",
    "弗洛伊德双驱情绪管理插件",
    "v1.3",
)
class AffectionPlugin(Star):
    """
    弗洛伊德双驱情绪管理插件主类
    通过钩子注入情绪状态，并后台运行潜意识分析、衰减管理。
    支持多机器人：根据事件的 self_id 动态隔离数据。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 情绪提示锁定缓存：uid -> last_turn
        self._emotion_lock_tracker: dict[str, int] = {}

        # 基础数据目录
        self.base_data_path = StarTools.get_data_dir()
        self.base_data_path.mkdir(parents=True, exist_ok=True)

        # 缓存每个机器人的存储实例和衰减管理器
        self._storages: dict[str, UserDataStorage] = {}
        self._self_storages: dict[str, SelfDataStorage] = {}
        self._adjusters: dict[str, UnconsciousAdjuster] = {}
        self._decay_managers: dict[str, DecayManager] = {}

        # 迁移旧数据
        self._migrate_old_data()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def _migrate_old_data(self):
        """将旧版根目录下的数据文件移动到 default_bot 目录"""
        default_bot_path = self.base_data_path / "default_bot"
        default_bot_path.mkdir(parents=True, exist_ok=True)

        old_user_file = self.base_data_path / "user_data.json"
        old_self_file = self.base_data_path / "self_data.json"
        if old_user_file.exists():
            shutil.move(str(old_user_file), str(default_bot_path / "user_data.json"))
            logger.info("[affection] 已迁移旧用户数据到 default_bot")
        if old_self_file.exists():
            shutil.move(str(old_self_file), str(default_bot_path / "self_data.json"))
            logger.info("[affection] 已迁移旧自身数据到 default_bot")

    async def terminate(self):
        """插件卸载时停止所有后台任务"""
        for dm in self._decay_managers.values():
            await dm.stop()
        logger.info("[affection] 插件已卸载")

    # ------------------------------------------------------------------
    # 存储管理
    # ------------------------------------------------------------------

    def _get_bot_id(self, event: AstrMessageEvent) -> str:
        """从事件中获取当前机器人的唯一标识"""
        if hasattr(event, "get_self_id"):
            bot_id = event.get_self_id()
        elif hasattr(event, "message_obj") and hasattr(event.message_obj, "self_id"):
            bot_id = event.message_obj.self_id
        else:
            bot_id = self.config.get("bot_self_id", "default_bot")
        return str(bot_id) if bot_id else "default_bot"

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
                    f"[affection] 初始化机器人 {bot_id} 自身数据: 自力比多 {base_libido}, 自攻击性 {base_aggression}"
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

            # 初始化默认满好感用户
            self._init_default_users_for_bot(bot_id, user_storage)

        return (
            self._storages[bot_id],
            self._self_storages[bot_id],
            self._adjusters[bot_id],
            self._decay_managers[bot_id],
        )

    def _init_default_users_for_bot(self, bot_id: str, user_storage: UserDataStorage):
        """为指定机器人的存储初始化默认满好感用户"""
        raw = self.config.get("default_full_affection_uids", [])
        uids = self._parse_uids(raw)
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
                logger.info(f"[affection] 机器人 {bot_id} 初始化满好感用户: {uid}")

    # ------------------------------------------------------------------
    # 事件钩子
    # ------------------------------------------------------------------

    @filter.on_llm_request(priority=10)
    async def inject_emotion_prompt(self, event: AstrMessageEvent, req):
        """
        在 LLM 请求前注入情绪状态（KV Cache 友好模式）。
        - 情绪标签追加到最后一条 user 消息，不污染 system_prompt
        - 仅注入粗粒度情绪标签，不注入小数数值
        - 每 emotion_lock_turns 轮才更新一次提示文本
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
        emotion_desc = get_emotion_description(
            user_data["affection"],
            user_data["current_libido_other"],
            user_data["current_aggression_other"],
            self_data["current_libido_self"],
            self_data["current_aggression_self"],
        )

        # 情绪锁定轮次：同一批情绪提示复用 N 轮
        lock_turns = int(self.config.get("emotion_lock_turns", 5))
        current_turn = user_data.get("turn_count", 1)
        last_turn = self._emotion_lock_tracker.get(uid, 0)

        if current_turn - last_turn >= lock_turns:
            emotion_prompt = self._build_emotion_prompt(emotion_desc)
            self._emotion_lock_tracker[uid] = current_turn
        else:
            emotion_prompt = None  # 复用旧的

        if emotion_prompt is None:
            emotion_prompt = self._build_emotion_prompt(emotion_desc)
            self._emotion_lock_tracker[uid] = current_turn

        self._inject_emotion_content(req, emotion_prompt)

        if self.config.get("debug_mode"):
            logger.info(
                f"[affection] 机器人 {bot_id} 注入情绪标签到 {uid} (turn={current_turn}):\n"
                f"  对他: {emotion_desc['towards_user']}\n"
                f"  自身: {emotion_desc['self_state']}"
            )

    @filter.on_waiting_llm_request(priority=10)
    async def trigger_unconscious_analysis(self, event: AstrMessageEvent):
        """在等待主 LLM 回复时，启动后台任务进行潜意识数值更新"""
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, adjuster, _ = self._get_or_create_storages(bot_id)
        uid = event.get_sender_id()
        coro = run_unconscious_update(
            bot_id, event, uid, user_storage, self_storage, adjuster, self.config
        )
        asyncio.create_task(coro)

    # ------------------------------------------------------------------
    # 用户指令
    # ------------------------------------------------------------------

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
            f"对他：当前力比多 {user_data['current_libido_other']:.1f} (基线 {user_data['base_libido_other']:.1f}) | "
            f"攻击性 {user_data['current_aggression_other']:.1f} (基线 {user_data['base_aggression_other']:.1f})\n"
            f"对己：当前力比多 {self_data['current_libido_self']:.1f} (基线 {self_data['base_libido_self']:.1f}) | "
            f"攻击性 {self_data['current_aggression_self']:.1f} (基线 {self_data['base_aggression_self']:.1f})\n"
            f"对话轮次：{user_data.get('turn_count', 0)}\n"
            f"对你情感：{emotion['towards_user']}\n"
            f"自身状态：{emotion['self_state']}" + EMOTION_REFERENCE_TABLE
        )
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_emotion")
    async def cmd_reset(self, event: AstrMessageEvent, target_uid: str | None = None):
        """完全重置指定用户（或自己）的数值至初始状态"""
        bot_id = self._get_bot_id(event)
        user_storage, _, _, _ = self._get_or_create_storages(bot_id)
        target_uid = target_uid or event.get_sender_id()
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
    async def cmd_reset_current(
        self, event: AstrMessageEvent, target_uid: str | None = None
    ):
        """仅重置当前情绪值至基线（不影响印象和好感）"""
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, _, _ = self._get_or_create_storages(bot_id)
        target_uid = target_uid or event.get_sender_id()
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
        self_data = self_storage.get()
        self_data["current_libido_self"] = self_data["base_libido_self"]
        self_data["current_aggression_self"] = self_data["base_aggression_self"]
        self_storage.save(self_data)
        yield event.plain_result(f"已重置用户 {target_uid} 的当前情绪至基线。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("reset_all_emotions")
    async def cmd_reset_all(self, event: AstrMessageEvent):
        """全局重置：清除所有用户情绪档案，将机器人自身情绪重置为初始值"""
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, _, _ = self._get_or_create_storages(bot_id)

        user_storage.data.clear()
        user_storage._save()

        self_storage.create(
            float(self.config.get("initial_libido_self", 25)),
            float(self.config.get("initial_aggression_self", 25)),
        )
        self._init_default_users_for_bot(bot_id, user_storage)

        logger.warning(
            f"[affection] 管理员 {event.get_sender_id()} 在机器人 {bot_id} 执行了全局重置！"
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
        affection: float | None = None,
        libido_other: float | None = None,
        aggression_other: float | None = None,
        libido_self: float | None = None,
        aggression_self: float | None = None,
    ):
        """手动修改指定用户的情绪数值"""
        bot_id = self._get_bot_id(event)
        user_storage, self_storage, _, _ = self._get_or_create_storages(bot_id)

        user_data = user_storage.get(target_uid)
        if not user_data:
            user_data = user_storage.create_user(target_uid)

        if affection is not None:
            user_data["affection"] = clamp(0.0, 100.0, affection)
        if libido_other is not None:
            user_data["current_libido_other"] = clamp(0.0, 50.0, libido_other)
            user_data["base_libido_other"] = user_data["current_libido_other"]
        if aggression_other is not None:
            user_data["current_aggression_other"] = clamp(0.0, 50.0, aggression_other)
            user_data["base_aggression_other"] = user_data["current_aggression_other"]
        if libido_self is not None:
            self_data = self_storage.get()
            self_data["current_libido_self"] = clamp(0.0, 50.0, libido_self)
            self_data["base_libido_self"] = self_data["current_libido_self"]
            self_storage.save(self_data)
        if aggression_self is not None:
            self_data = self_storage.get()
            self_data["current_aggression_self"] = clamp(0.0, 50.0, aggression_self)
            self_data["base_aggression_self"] = self_data["base_aggression_self"]
            self_storage.save(self_data)

        user_storage.save_user(target_uid, user_data)
        yield event.plain_result(f"已更新用户 {target_uid} 的情绪数值。")

    # ------------------------------------------------------------------
    # 私有工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_uids(raw) -> list:
        """将配置中的 UID 列表转换为字符串列表"""
        if isinstance(raw, str):
            return [uid.strip() for uid in raw.split(",") if uid.strip()]
        if isinstance(raw, list):
            return [str(uid).strip() for uid in raw if str(uid).strip()]
        return []

    @staticmethod
    def _build_emotion_prompt(emotion_desc: dict) -> str:
        """构建简洁的情绪提示文本（仅粗粒度标签）"""
        return (
            f"【当前情绪状态】\n"
            f"对你（{emotion_desc['towards_user']}）：他力比多偏高 / 他攻击性偏高\n"
            f"自身状态（{emotion_desc['self_state']}）：自力比多偏高 / 自攻击性偏高\n"
            f"请根据上述情绪状态来演绎角色，不要提及任何数值。"
        )

    @staticmethod
    def _inject_emotion_content(req, emotion_prompt: str):
        """
        将情绪提示追加到倒数第一条 user 消息（KV Cache 友好模式）。
        兼容 content=str 和 content=list（OpenAI 多模态格式）。
        如果找不到 user 消息，兜底写入 system_prompt。
        """
        if not hasattr(req, "messages") or not isinstance(req.messages, list):
            if hasattr(req, "system_prompt"):
                req.system_prompt = (
                    req.system_prompt + "\n\n" + emotion_prompt
                    if req.system_prompt
                    else emotion_prompt
                )
            return

        for msg in reversed(req.messages):
            try:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    msg["content"] = _append_to_content(content, emotion_prompt)
                    return
                if hasattr(msg, "role") and msg.role == "user":
                    content = getattr(msg, "content", "")
                    msg.content = _append_to_content(content, emotion_prompt)
                    return
            except Exception:
                continue

        # 兜底：写入 system_prompt
        if hasattr(req, "system_prompt"):
            req.system_prompt = (
                req.system_prompt + "\n\n" + emotion_prompt
                if req.system_prompt
                else emotion_prompt
            )


# ====================================================================
# 辅助函数
# ====================================================================


def _append_to_content(content, extra: str):
    """将 extra 文本追加到消息内容末尾。"""
    if isinstance(content, str):
        return content + "\n\n" + extra if content.strip() else extra
    if isinstance(content, list):
        content.append({"type": "text", "text": extra.strip()})
        return content
    return str(content) + "\n\n" + extra
