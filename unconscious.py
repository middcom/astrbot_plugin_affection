"""
潜意识 LLM 调用模块
负责分析用户消息并返回情绪数值的调整建议，包括当前值增量、基线值增量和场景强度。
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
        conv_mgr = self.context.conversation_manager
        umo = event.unified_msg_origin
        curr_cid = await conv_mgr.get_curr_conversation_id(umo)
        conversation: Conversation = await conv_mgr.get_conversation(umo, curr_cid)
        history_text = conversation.history if conversation else ""

        self_data = self.self_storage.get()
        prompt = self._build_prompt(
            current_data, self_data, history_text, event.message_str, turn_count
        )
        llm_config = self.config.get("unconscious_llm", {})
        provider_id = llm_config.get("provider_id")
        if not provider_id:
            provider_id = await self.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )

        try:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个情绪数值调节器，只输出 JSON，不添加任何解释。",
            )
            text = resp.completion_text
            logger.debug(f"[Unconscious] LLM 原始返回: {text}")
            deltas = self._parse_json(text)
            deltas = self._clamp_deltas(deltas, turn_count)
            deltas = self._ensure_non_zero_current_deltas(deltas, current_data)
            return deltas
        except Exception as e:
            logger.error(f"[Unconscious] LLM 调用失败: {e}")
            return self._default_response()

    async def analyze_idle(self, uid: str, elapsed_hours: float) -> dict | None:
        prompt = self._build_idle_prompt(elapsed_hours)
        llm_config = self.config.get("unconscious_llm", {})
        provider_id = llm_config.get("provider_id")
        if not provider_id:
            provider_id = "default"
        try:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                contexts=[],
                system_prompt="你是一个情绪数值调节器，只输出 JSON。",
            )
            text = resp.completion_text
            return self._parse_json(text)
        except Exception:
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
4. 好感度变化范围 -0.5 ~ +0.5。
5. 场景强度识别（intensity 字段）：
   - 高强度（2.0）：生死离别、深爱表白、极度崇拜、仇恨爆发、自毁倾诉、重大牺牲
   - 中强度（1.0）：普通争执、日常关心、常规互动
   - 低强度（0.5）：寒暄、中性闲聊、无关话题、简单应答

**常见消息类型与推荐增量（参考）**：
- "你好"/"在吗" → 中性闲聊，intensity=0.5，增量 ≈ 0.0
- "你还好吗？"/"加油"/"谢谢你" → 纯友善，他力比多 +0.3~+0.8，他攻击性 -0.1~0.0
- "你真聪明"/"好厉害" → 赞美，他力比多 +0.5~+1.0，他攻击性 0.0~-0.2
- "你今天怎么不说话" → 关心，他力比多 +0.2~+0.5，他攻击性 0.0
- "你笨死了"/"滚" → 批评，他力比多 -0.5~-1.5，他攻击性 +0.3~+1.0
- "哈哈你好好笑" → 调侃，他力比多 +0.1~+0.3，他攻击性 +0.05~+0.15

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
"""

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

增量范围 -1.0 ~ 1.0。
"""

    def _parse_json(self, text: str) -> dict:
        default = self._default_response()
        try:
            return json.loads(text.strip())
        except Exception:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
        return default

    def _clamp_deltas(self, data: dict, turn_count: int) -> dict:
        clamped = {}
        clamped["libido_other_delta"] = max(
            -2.0, min(2.0, data.get("libido_other_delta", 0.0))
        )
        clamped["aggression_other_delta"] = max(
            -2.0, min(2.0, data.get("aggression_other_delta", 0.0))
        )
        clamped["libido_self_delta"] = max(
            -2.0, min(2.0, data.get("libido_self_delta", 0.0))
        )
        clamped["aggression_self_delta"] = max(
            -2.0, min(2.0, data.get("aggression_self_delta", 0.0))
        )
        clamped["affection_delta"] = max(
            -0.5, min(0.5, data.get("affection_delta", 0.0))
        )

        if turn_count <= 10:
            clamped["base_libido_other_delta"] = max(
                -1.5, min(1.5, data.get("base_libido_other_delta", 0.0))
            )
            clamped["base_aggression_other_delta"] = max(
                -1.5, min(1.5, data.get("base_aggression_other_delta", 0.0))
            )
        else:
            clamped["base_libido_other_delta"] = max(
                -0.2, min(0.2, data.get("base_libido_other_delta", 0.0))
            )
            clamped["base_aggression_other_delta"] = max(
                -0.2, min(0.2, data.get("base_aggression_other_delta", 0.0))
            )

        clamped["base_libido_self_delta"] = max(
            -0.2, min(0.2, data.get("base_libido_self_delta", 0.0))
        )
        clamped["base_aggression_self_delta"] = max(
            -0.2, min(0.2, data.get("base_aggression_self_delta", 0.0))
        )

        intensity = data.get("intensity", 1.0)
        try:
            intensity = float(intensity)
        except Exception:
            intensity = 1.0
        clamped["intensity"] = max(0.5, min(2.0, intensity))
        return clamped

    def _ensure_non_zero_current_deltas(self, deltas: dict, data: dict) -> dict:
        """
        若 LLM 返回的力比多/攻击性增量为零，赋予微小增量避免全零。
        方向依据好感度趋势：高好感偏正向，低好感偏负向。
        注意：仅在 LLM 未给出方向时使用，不覆盖 LLM 明确给出的非零值。
        """
        for key in ["libido_other_delta", "aggression_other_delta"]:
            if abs(deltas.get(key, 0.0)) < 0.001:
                affection = data.get("affection", 50.0)
                if affection > 60:
                    deltas[key] = 0.05
                elif affection < 40:
                    deltas[key] = -0.05
                else:
                    deltas[key] = 0.02
        return deltas

    def _default_response(self) -> dict:
        return {
            "libido_other_delta": 0.05,
            "aggression_other_delta": 0.05,
            "libido_self_delta": 0.0,
            "aggression_self_delta": 0.0,
            "affection_delta": 0.0,
            "base_libido_other_delta": 0.0,
            "base_aggression_other_delta": 0.0,
            "base_libido_self_delta": 0.0,
            "base_aggression_self_delta": 0.0,
            "intensity": 1.0,
        }
