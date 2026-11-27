"""
潜意识 LLM 调用模块
负责分析用户消息并返回情绪数值的调整建议，包括当前值增量、基线值增量和场景强度。

本版本修复要点：
- provider 回退链：配置 provider -> 当前会话 provider -> 默认；全部失败走中性零增量兜底。
- _default_response 改为全零：旧版 aggression +0.05 会在 provider 长期失效时把攻击性单向推满。
- _parse_json 支持 markdown 代码围栏，并对非 dict JSON 做防御。
- _ensure_non_zero_current_deltas 不再把 LLM 明确给出的 0 强制改写为非零值。
"""

import json
import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.conversation_mgr import Conversation


class UnconsciousAdjuster:
    def __init__(self, context, config, self_storage):
        self.context = context
        self.config = config
        self.self_storage = self_storage

    async def analyze_and_adjust(
        self, event: AstrMessageEvent, current_data: dict, turn_count: int
    ) -> dict:
        # 对话历史获取失败时降级为空历史，避免整轮更新失败
        history_text = ""
        try:
            conv_mgr = self.context.conversation_manager
            umo = event.unified_msg_origin
            curr_cid = await conv_mgr.get_curr_conversation_id(umo)
            conversation: Conversation = await conv_mgr.get_conversation(umo, curr_cid)
            history_text = conversation.history if conversation else ""
        except Exception as e:
            logger.warning(f"[Unconscious] 获取对话历史失败，降级为空历史: {e}")

        # 构建提示词；失败直接中性兜底，不让整轮更新失败
        try:
            self_data = self.self_storage.get()
            prompt = self._build_prompt(
                current_data, self_data, history_text, event.message_str, turn_count
            )
        except Exception as e:
            logger.error(f"[Unconscious] 构建提示词失败: {e}")
            return self._default_response()

        # provider 回退链：配置 provider -> 当前会话 provider -> 默认
        provider_chain = await self._resolve_provider_chain(event)

        last_error = None
        for provider_id in provider_chain:
            try:
                resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    contexts=[],
                    system_prompt="你是一个情绪数值调节器，只输出 JSON，不添加任何解释。",
                )
                text = getattr(resp, "completion_text", None)
                if not text or not str(text).strip():
                    # 空响应视为该 provider 失效，继续回退下一个
                    raise ValueError("LLM 返回为空")
                logger.debug(f"[Unconscious] LLM 原始返回: {text}")
                deltas = self._parse_json(str(text))
                deltas = self._clamp_deltas(deltas, turn_count)
                deltas = self._ensure_non_zero_current_deltas(deltas, current_data)
                return deltas
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[Unconscious] provider {provider_id} 调用失败: {e}，尝试回退下一个 provider"
                )

        logger.error(
            f"[Unconscious] 全部 provider 失败（链: {provider_chain}），"
            f"使用中性零增量兜底: {last_error}"
        )
        return self._default_response()

    async def _resolve_provider_chain(self, event: AstrMessageEvent) -> list:
        """构建 provider 回退链：配置 provider -> 当前会话 provider -> 默认 provider。"""
        chain: list = []
        llm_config = self.config.get("unconscious_llm") or {}
        if isinstance(llm_config, dict):
            configured = str(llm_config.get("provider_id") or "").strip()
            if configured:
                chain.append(configured)
        # 当前会话 provider（可能因未配置默认 provider 而获取失败，容忍）
        try:
            session_provider = await self.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )
            if session_provider and session_provider not in chain:
                chain.append(session_provider)
        except Exception as e:
            logger.warning(f"[Unconscious] 获取会话 provider 失败: {e}")
        if "default" not in chain:
            chain.append("default")
        return chain

    async def analyze_idle(self, uid: str, elapsed_hours: float) -> dict | None:
        prompt = self._build_idle_prompt(elapsed_hours)
        # 空闲分析没有事件上下文，回退链为：配置 provider -> 默认
        chain: list = []
        llm_config = self.config.get("unconscious_llm") or {}
        if isinstance(llm_config, dict):
            configured = str(llm_config.get("provider_id") or "").strip()
            if configured:
                chain.append(configured)
        if "default" not in chain:
            chain.append("default")
        for provider_id in chain:
            try:
                resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    contexts=[],
                    system_prompt="你是一个情绪数值调节器，只输出 JSON。",
                )
                text = getattr(resp, "completion_text", None)
                if not text or not str(text).strip():
                    raise ValueError("LLM 返回为空")
                return self._parse_json(str(text))
            except Exception as e:
                logger.warning(
                    f"[Unconscious] 空闲分析 provider {provider_id} 失败: {e}，尝试回退"
                )
        return None

    def _build_prompt(
        self,
        user_data: dict,
        self_data: dict,
        history: str,
        latest_msg: str,
        turn_count: int,
    ) -> str:
        history_snippet = history[-2000:] if len(history) > 2000 else history
        return f"""你是潜意识的数值调节器。根据用户最新消息和对话历史，分析对机器人情绪的影响。

**重要规则**：
1. 必须对"他力比多"和"他攻击性"的当前值给出调整增量（允许零值 0.0），增量方向必须符合消息情感。
2. 增量方向必须与消息情感严格一致：
   - 纯友善/关心/赞美/感谢 → 他力比多正增量，攻击性负增量或零，绝不升正。
   - 批评/指责/冷漠/拒绝/贬低 → 他力比多负增量，攻击性正增量。
   - 悲伤/无助/自我否定 → 他力比多正增量（安慰欲），攻击性负增量；除非用户攻击机器人则攻击性正增量。
   - 调侃/玩笑无恶意 → 他力比多微升或持平，攻击性微升（+0.05~+0.1）。
   - 长时间未互动且无合理理由（如白天无故消失）→ 攻击性正增量（微恼），他力比多负增量。
   - 道别但语气温暖、表达美好祝愿 → 他力比多大幅正增量，攻击性大幅负增量。
3. 同时评估本次互动是否影响长期印象（基线值）：
   - 对他人的基线（原他力比多/原他攻击性）：当前是第 {turn_count} 轮对话。
     * 若 turn_count <= 10，基线变化可以较明显（增量范围 -1.5 ~ +1.5）。
     * 若 turn_count > 10，基线变化必须极小（增量范围 -0.2 ~ +0.2），因为初印象已形成。
   - 对自身的基线（原自力比多/原自攻击性）：始终很难改变，增量范围 -0.2 ~ +0.2。
4. 好感度（affection_delta）增量范围 -1.0 ~ +1.0。好感是长期印象，只随互动累积、不随时间衰减：
   - 纯友善/关心/赞美/感谢 → +0.3 ~ +0.8
   - 日常中性闲聊/寒暄 → +0.05 ~ +0.15
   - 批评/冷漠/贬低 → -0.3 ~ -0.8
   - 严重侮辱/敌意攻击 → -0.8 ~ -1.0
5. 场景强度识别（intensity 字段）：
   - 高强度（2.0）：生死离别、深爱表白、极度崇拜、仇恨爆发、自毁倾诉、重大牺牲
   - 中强度（1.0）：普通争执、日常关心、常规互动
   - 低强度（0.5）：寒暄、中性闲聊、无关话题、简单应答

**常见消息类型与推荐增量（参考）**：
- "你好"/"在吗" → 中性闲聊，intensity=0.5，增量 ≈ 0.0，affection_delta ≈ +0.05
- "你还好吗？"/"加油"/"谢谢你" → 纯友善，他力比多 +0.3~+0.8，他攻击性 -0.1~0.0，affection_delta +0.3~+0.5
- "你真聪明"/"好厉害" → 赞美，他力比多 +0.5~+1.0，他攻击性 0.0~-0.2，affection_delta +0.4~+0.8
- "你今天怎么不说话" → 关心，他力比多 +0.2~+0.5，他攻击性 0.0，affection_delta +0.2~+0.4
- "你笨死了"/"滚" → 批评，他力比多 -0.5~-1.5，他攻击性 +0.3~+1.0，affection_delta -0.5~-1.0
- "哈哈你好好笑" → 调侃，他力比多 +0.1~+0.3，他攻击性 +0.05~+0.15，affection_delta +0.1~+0.3

当前状态：
- 对话轮次：第 {turn_count} 轮
- 好感度：{user_data["affection"]:.1f}/100
- 对他基线：原他力比多 {user_data["base_libido_other"]:.1f}，原他攻击性 {user_data["base_aggression_other"]:.1f}
- 对他当前：他力比多 {user_data["current_libido_other"]:.1f}，他攻击性 {user_data["current_aggression_other"]:.1f}
- 对己基线：原自力比多 {self_data["base_libido_self"]:.1f}，原自攻击性 {self_data["base_aggression_self"]:.1f}
- 对己当前：自力比多 {self_data["current_libido_self"]:.1f}，自攻击性 {self_data["current_aggression_self"]:.1f}

最近对话历史：
{history_snippet}

用户最新消息：{latest_msg}

请输出 JSON 格式（仅输出 JSON，不要任何其他文字或解释）：
{{
  "libido_other_delta": 0.0,
  "aggression_other_delta": 0.0,
  "libido_self_delta": 0.0,
  "aggression_self_delta": 0.0,
  "affection_delta": 0.0,
  "base_libido_other_delta": 0.0,
  "base_aggression_other_delta": 0.0,
  "base_libido_self_delta": 0.0,
  "base_aggression_self_delta": 0.0,
  "intensity": 1.0
}}

注意：字段名必须与上方模板完全一致，数值必须是数字（不要带引号），无变化的字段填 0.0。"""

    def _build_idle_prompt(self, elapsed_hours: float) -> str:
        return f"""用户已经 {elapsed_hours:.1f} 小时没有和机器人互动了。请分析这种情况是否会让机器人产生情绪波动。
例如：若是深夜睡觉时间则无影响；若是白天无故消失可能产生轻微不满。

输出 JSON 格式（仅输出 JSON，不要任何其他文字）：
{{
  "libido_other_delta": 0.0,
  "aggression_other_delta": 0.0,
  "libido_self_delta": 0.0,
  "aggression_self_delta": 0.0
}}

增量范围 -1.0 ~ 1.0。"""

    def _parse_json(self, text: str) -> dict:
        default = self._default_response()
        if not isinstance(text, str):
            return default
        text = text.strip()
        if not text:
            return default
        # 剥离 markdown 代码围栏（```json ... ``` 或 ``` ... ```）
        fence = re.match(r"^```[A-Za-z0-9_-]*\s*(.*?)\s*```$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else default
        except Exception:
            # 围栏外还有杂文字时，抓取第一个 { 到最后一个 } 的片段
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                    return data if isinstance(data, dict) else default
                except Exception:
                    pass
        return default

    def _clamp_deltas(self, data: dict, turn_count: int) -> dict:
        clamped = {}
        # 缩减钳制范围，防止 LLM 返回极端 delta 导致情绪暴涨暴跌
        # 数值统一经 _to_float 容错：字符串数字可解析，垃圾值按 0 处理
        clamped["libido_other_delta"] = max(
            -1.0, min(1.0, self._to_float(data.get("libido_other_delta", 0.0)))
        )
        clamped["aggression_other_delta"] = max(
            -1.0, min(1.0, self._to_float(data.get("aggression_other_delta", 0.0)))
        )
        clamped["libido_self_delta"] = max(
            -1.0, min(1.0, self._to_float(data.get("libido_self_delta", 0.0)))
        )
        clamped["aggression_self_delta"] = max(
            -1.0, min(1.0, self._to_float(data.get("aggression_self_delta", 0.0)))
        )
        clamped["affection_delta"] = max(
            -1.0, min(1.0, self._to_float(data.get("affection_delta", 0.0)))
        )

        if turn_count <= 10:
            clamped["base_libido_other_delta"] = max(
                -1.5, min(1.5, self._to_float(data.get("base_libido_other_delta", 0.0)))
            )
            clamped["base_aggression_other_delta"] = max(
                -1.5,
                min(1.5, self._to_float(data.get("base_aggression_other_delta", 0.0))),
            )
        else:
            clamped["base_libido_other_delta"] = max(
                -0.2, min(0.2, self._to_float(data.get("base_libido_other_delta", 0.0)))
            )
            clamped["base_aggression_other_delta"] = max(
                -0.2,
                min(0.2, self._to_float(data.get("base_aggression_other_delta", 0.0))),
            )

        clamped["base_libido_self_delta"] = max(
            -0.2, min(0.2, self._to_float(data.get("base_libido_self_delta", 0.0)))
        )
        clamped["base_aggression_self_delta"] = max(
            -0.2, min(0.2, self._to_float(data.get("base_aggression_self_delta", 0.0)))
        )

        intensity = self._to_float(data.get("intensity", 1.0), 1.0)
        clamped["intensity"] = max(0.5, min(2.0, intensity))
        return clamped

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        """把 LLM 返回值安全转为 float；None/字符串垃圾等失败情况返回 default。"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _ensure_non_zero_current_deltas(self, deltas: dict, data: dict) -> dict:
        """
        历史遗留接口：原先会把 LLM 明确给出的 0 强制改写为 +0.02/-0.05，
        造成情绪数值单向漂移（尤其中性消息也会推高攻击性）。
        现改为：0 表示"本项无变化"，必须原样保留；仅在字段缺失时补 0.0，
        保证下游 deltas.get(...) 一定有默认值。参数 data 仅为保持接口兼容。
        """
        for key in (
            "libido_other_delta",
            "aggression_other_delta",
            "libido_self_delta",
            "aggression_self_delta",
        ):
            if key not in deltas:
                deltas[key] = 0.0
        return deltas

    def _default_response(self) -> dict:
        # 中性兜底：所有增量一律为 0，不产生任何方向偏置。
        # （旧版 aggression_other_delta +0.05 会在 provider 长期失效时把攻击性单向推到顶格）
        return {
            "libido_other_delta": 0.0,
            "aggression_other_delta": 0.0,
            "libido_self_delta": 0.0,
            "aggression_self_delta": 0.0,
            "affection_delta": 0.0,
            "base_libido_other_delta": 0.0,
            "base_aggression_other_delta": 0.0,
            "base_libido_self_delta": 0.0,
            "base_aggression_self_delta": 0.0,
            "intensity": 1.0,
        }
