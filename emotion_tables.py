"""
情绪标签映射表 + 线性插值混合标签计算。

核心设计：
- 基础档位（brackets）：0 / 12.5 / 25 / 37.5 / 50
- 当数值恰好落在档位上时，返回单一标签
- 当数值落在两个档位之间时，返回线性插值混合标签，例如：
    - 他力比多 18.0（12.5 与 25 之间，占 60%）
      → "好感(60%)/竞争(40%)" 或 "好感/竞争（偏向好感）"

插值策略（简化版双线性插值）：
1. 找到 libido 的上下档位及权重
2. 找到 aggression 的上下档位及权重
3. 取出 4 个角标签，按双线性权重混合
"""


# ====================================================================
# 基础档位
# ====================================================================

BRACKETS = [0.0, 12.5, 25.0, 37.5, 50.0]

# ====================================================================
# 好感度分档
# ====================================================================


def _get_affection_level(affection: float) -> int:
    if affection < 12.5:
        return 0
    elif affection < 37.5:
        return 25
    elif affection < 62.5:
        return 50
    elif affection < 87.5:
        return 75
    else:
        return 100


# ====================================================================
# 档位查找
# ====================================================================


def _find_bracket_info(value: float) -> tuple:
    """
    找到 value 所在的档位区间。
    如果恰好等于某档位，返回 (val, val, 0.0, 0.0)
    如果在两档之间，返回 (lower, upper, lower_weight, upper_weight)
    """
    for i in range(len(BRACKETS)):
        b = BRACKETS[i]
        if value <= b:
            if value == b:
                return (b, b, 1.0, 0.0)
            if i == 0:
                return (b, b, 1.0, 0.0)
            lower = BRACKETS[i - 1]
            upper = b
            gap = upper - lower
            if gap == 0:
                return (upper, upper, 1.0, 0.0)
            upper_weight = (value - lower) / gap  # 0~1
            return (lower, upper, 1.0 - upper_weight, upper_weight)
    # 超过最大值
    last = BRACKETS[-1]
    return (last, last, 1.0, 0.0)


def _format_blend(label1: str, pct1: int, label2: str, pct2: int) -> str:
    """格式化混合标签"""
    pct1 = max(0, min(100, pct1))
    pct2 = 100 - pct1
    # 标签完全相同时直接返回
    if label1 == label2:
        return label1
    if pct1 < 15:
        return f"{label2}（偏{label1}）"
    elif pct1 > 85:
        return f"{label1}（偏{label2}）"
    elif pct1 >= 40 and pct2 >= 40:
        # 差距不大，显示百分比
        return f"{label1}({pct1}%)/{label2}({pct2}%)"
    elif pct1 >= pct2:
        return f"{label1}（偏{label2}）"
    else:
        return f"{label2}（偏{label1}）"


# ====================================================================
# 情绪映射表
# ====================================================================

TOWARDS_USER_TABLE = {
    0: {
        (50.0, 0.0): "痴迷(病态)",
        (50.0, 12.5): "纠缠(偏执)",
        (50.0, 25.0): "憎恨(爱转恨)",
        (50.0, 37.5): "毁灭性恨",
        (50.0, 50.0): "同归于尽",
        (37.5, 0.0): "依赖(绝望)",
        (37.5, 12.5): "烦躁",
        (37.5, 25.0): "厌恶",
        (37.5, 37.5): "仇恨",
        (37.5, 50.0): "残暴",
        (25.0, 0.0): "冷淡",
        (25.0, 12.5): "无聊",
        (25.0, 25.0): "轻蔑",
        (25.0, 37.5): "蔑视",
        (25.0, 50.0): "冷酷",
        (12.5, 0.0): "回避",
        (12.5, 12.5): "疏离",
        (12.5, 25.0): "嫌弃",
        (12.5, 37.5): "恶心",
        (12.5, 50.0): "憎恶",
        (0.0, 0.0): "无视",
        (0.0, 12.5): "不存在",
        (0.0, 25.0): "否定",
        (0.0, 37.5): "驱逐",
        (0.0, 50.0): "湮灭",
    },
    25: {
        (50.0, 0.0): "执着",
        (50.0, 12.5): "猜疑",
        (50.0, 25.0): "嫉妒",
        (50.0, 37.5): "报复欲",
        (50.0, 50.0): "毁灭欲",
        (37.5, 0.0): "渴求(卑微)",
        (37.5, 12.5): "试探(不安)",
        (37.5, 25.0): "敌意",
        (37.5, 37.5): "愤怒",
        (37.5, 50.0): "仇恨",
        (25.0, 0.0): "普通",
        (25.0, 12.5): "不耐烦",
        (25.0, 25.0): "竞争",
        (25.0, 37.5): "攻击性玩笑",
        (25.0, 50.0): "讽刺",
        (12.5, 0.0): "礼貌",
        (12.5, 12.5): "无聊",
        (12.5, 25.0): "烦躁",
        (12.5, 37.5): "厌恶",
        (12.5, 50.0): "憎恨",
        (0.0, 0.0): "冷漠",
        (0.0, 12.5): "沉默",
        (0.0, 25.0): "回避",
        (0.0, 37.5): "拒绝",
        (0.0, 50.0): "驱赶",
    },
    50: {
        (50.0, 0.0): "迷恋",
        (50.0, 12.5): "占有",
        (50.0, 25.0): "嫉妒",
        (50.0, 37.5): "施虐倾向",
        (50.0, 50.0): "毁灭性爱",
        (37.5, 0.0): "依恋",
        (37.5, 12.5): "激情",
        (37.5, 25.0): "纠缠",
        (37.5, 37.5): "报复",
        (37.5, 50.0): "仇恨",
        (25.0, 0.0): "喜欢",
        (25.0, 12.5): "渴望",
        (25.0, 25.0): "竞争",
        (25.0, 37.5): "愤怒",
        (25.0, 50.0): "残暴",
        (12.5, 0.0): "好感",
        (12.5, 12.5): "无聊",
        (12.5, 25.0): "烦躁",
        (12.5, 37.5): "厌恶",
        (12.5, 50.0): "憎恨",
        (0.0, 0.0): "冷漠",
        (0.0, 12.5): "疏离",
        (0.0, 25.0): "轻蔑",
        (0.0, 37.5): "蔑视",
        (0.0, 50.0): "冷酷",
    },
    75: {
        (50.0, 0.0): "痴迷",
        (50.0, 12.5): "占有欲",
        (50.0, 25.0): "吃醋",
        (50.0, 37.5): "霸道",
        (50.0, 50.0): "毁灭性占有",
        (37.5, 0.0): "依恋(甜)",
        (37.5, 12.5): "热情",
        (37.5, 25.0): "撒娇式纠缠",
        (37.5, 37.5): "管教欲",
        (37.5, 50.0): "因爱生恨",
        (25.0, 0.0): "欣赏",
        (25.0, 12.5): "心动",
        (25.0, 25.0): "争宠",
        (25.0, 37.5): "着急",
        (25.0, 50.0): "暴躁(但会后悔)",
        (12.5, 0.0): "友善",
        (12.5, 12.5): "小无聊",
        (12.5, 25.0): "小烦躁",
        (12.5, 37.5): "恼火",
        (12.5, 50.0): "气话(很快哄好)",
        (0.0, 0.0): "平淡",
        (0.0, 12.5): "安静",
        (0.0, 25.0): "冷一下",
        (0.0, 37.5): "生闷气",
        (0.0, 50.0): "冷战",
    },
    100: {
        (50.0, 0.0): "崇拜",
        (50.0, 12.5): "完全占有",
        (50.0, 25.0): "吃醋到失控",
        (50.0, 37.5): "施虐(play)",
        (50.0, 50.0): "共依存(病态)",
        (37.5, 0.0): "依恋到离不开",
        (37.5, 12.5): "热情似火",
        (37.5, 25.0): "黏人到烦人",
        (37.5, 37.5): "调教欲",
        (37.5, 50.0): "相爱相杀",
        (25.0, 0.0): "喜欢到溺爱",
        (25.0, 12.5): "渴望融合",
        (25.0, 25.0): "撒娇争夺",
        (25.0, 37.5): "炸毛(可爱型)",
        (25.0, 50.0): "虐恋",
        (12.5, 0.0): "安心",
        (12.5, 12.5): "小撒娇",
        (12.5, 25.0): "小赌气",
        (12.5, 37.5): "假生气",
        (12.5, 50.0): "闹别扭",
        (0.0, 0.0): "平静幸福",
        (0.0, 12.5): "沉默但有爱",
        (0.0, 25.0): "闷气但心软",
        (0.0, 37.5): "委屈",
        (0.0, 50.0): "冷战但等你哄",
    },
}

SELF_TABLE = {
    (50.0, 0.0): "自恋",
    (50.0, 12.5): "自满",
    (50.0, 25.0): "自傲",
    (50.0, 37.5): "自大",
    (50.0, 50.0): "自毁冲动",
    (37.5, 0.0): "自爱",
    (37.5, 12.5): "自怜",
    (37.5, 25.0): "自责",
    (37.5, 37.5): "自卑",
    (37.5, 50.0): "自我仇恨",
    (25.0, 0.0): "自信",
    (25.0, 12.5): "平淡",
    (25.0, 25.0): "内疚",
    (25.0, 37.5): "自我厌恶",
    (25.0, 50.0): "自残欲",
    (12.5, 0.0): "自保",
    (12.5, 12.5): "空虚",
    (12.5, 25.0): "羞愧",
    (12.5, 37.5): "自贬",
    (12.5, 50.0): "自毁欲",
    (0.0, 0.0): "无我",
    (0.0, 12.5): "麻木",
    (0.0, 25.0): "自我否定",
    (0.0, 37.5): "自我毁灭",
    (0.0, 50.0): "湮灭",
}


# ====================================================================
# 核心查询函数
# ====================================================================


def _bilinear_interpolate(table: dict, lib_val: float, agg_val: float) -> str:
    """
    双线性插值：根据 lib 和 agg 各自的档位权重，对 4 个角的标签进行加权混合。
    """
    lib_info = _find_bracket_info(lib_val)
    agg_info = _find_bracket_info(agg_val)

    lib_lower, lib_upper, w_lib_lower, w_lib_upper = lib_info
    agg_lower, agg_upper, w_agg_lower, w_agg_upper = agg_info

    # 获取 4 个角标签
    corners = [
        (table.get((lib_lower, agg_lower), ""), w_lib_lower * w_agg_lower),
        (table.get((lib_lower, agg_upper), ""), w_lib_lower * w_agg_upper),
        (table.get((lib_upper, agg_lower), ""), w_lib_upper * w_agg_lower),
        (table.get((lib_upper, agg_upper), ""), w_lib_upper * w_agg_upper),
    ]

    # 过滤掉空标签
    valid = [(label, weight) for label, weight in corners if label]

    if not valid:
        return "平淡"

    if len(valid) == 1:
        return valid[0][0]

    # 归一化权重
    total_weight = sum(w for _, w in valid)
    if total_weight > 0:
        valid = [(label, weight / total_weight) for label, weight in valid]

    if len(valid) == 2:
        valid.sort(key=lambda x: x[1], reverse=True)
        label1, pct1 = valid[0]
        label2, _ = valid[1]
        pct1 = int(pct1 * 100)
        return _format_blend(label1, pct1, label2, 100 - pct1)

    # 3-4 个有效角，取权重最高的两个
    valid.sort(key=lambda x: x[1], reverse=True)
    label1, pct1 = valid[0]
    label2, _ = valid[1]
    pct1 = int(pct1 * 100)
    return _format_blend(label1, pct1, label2, 100 - pct1)


def _get_interpolated_towards_user(
    libido: float, aggression: float, aff_level: int
) -> tuple:
    """
    对他力比多和攻击性进行双线性插值，返回 (混合标签, 插值信息字典)。
    插值信息包含：lower_label, upper_label, weights 等，方便调试和显示。
    """
    table = TOWARDS_USER_TABLE.get(aff_level, {})
    if not table:
        return ("未知情感(好感档缺失)", {})

    interpolated = _bilinear_interpolate_with_info(table, libido, aggression)
    return interpolated


def _bilinear_interpolate_with_info(
    table: dict, lib_val: float, agg_val: float
) -> tuple:
    """
    双线性插值，返回 (标签, 插值信息)。
    插值信息包含角标签和权重，用于外部展示。
    """
    lib_info = _find_bracket_info(lib_val)
    agg_info = _find_bracket_info(agg_val)

    lib_lower, lib_upper, w_lib_lower, w_lib_upper = lib_info
    agg_lower, agg_upper, w_agg_lower, w_agg_upper = agg_info

    corners = [
        (table.get((lib_lower, agg_lower), ""), w_lib_lower * w_agg_lower),
        (table.get((lib_lower, agg_upper), ""), w_lib_lower * w_agg_upper),
        (table.get((lib_upper, agg_lower), ""), w_lib_upper * w_agg_lower),
        (table.get((lib_upper, agg_upper), ""), w_lib_upper * w_agg_upper),
    ]

    valid = [(label, weight) for label, weight in corners if label]

    if not valid:
        return ("平淡", {"info": "无匹配标签"})

    if len(valid) == 1:
        return (valid[0][0], {"info": f"{valid[0][0]} (精确匹配)"})

    total_weight = sum(w for _, w in valid)
    if total_weight > 0:
        valid = [(label, weight / total_weight) for label, weight in valid]

    valid.sort(key=lambda x: x[1], reverse=True)
    label1, pct1 = valid[0]
    label2, _ = valid[1]
    pct1 = int(pct1 * 100)

    info = {
        "labels": f"{label1}({pct1}%)/{label2}({100 - pct1}%)",
        "lib_range": f"{lib_lower}-{lib_upper}",
        "agg_range": f"{agg_lower}-{agg_upper}",
    }

    return (_format_blend(label1, pct1, label2, 100 - pct1), info)


def _get_interpolated_self_state(libido: float, aggression: float) -> tuple:
    """对自我状态进行双线性插值，返回 (标签, 插值信息)"""
    return _bilinear_interpolate_with_info(SELF_TABLE, libido, aggression)


def get_emotion_description(
    affection: float,
    libido_other: float,
    aggression_other: float,
    libido_self: float,
    aggression_self: float,
) -> dict:
    """
    返回混合情绪标签和原始数值，供 LLM 精确判断。
    包含插值区间信息，让角色明确知道自己落在哪两个标签之间。
    """
    aff_level = _get_affection_level(affection)
    towards_user, towards_info = _get_interpolated_towards_user(
        libido_other, aggression_other, aff_level
    )
    self_state, self_info = _get_interpolated_self_state(libido_self, aggression_self)

    return {
        "towards_user": towards_user,
        "self_state": self_state,
        "towards_info": towards_info,
        "self_info": self_info,
        "affection": round(affection, 2),
        "libido_other": round(libido_other, 2),
        "aggression_other": round(aggression_other, 2),
        "libido_self": round(libido_self, 2),
        "aggression_self": round(aggression_self, 2),
        "aff_level": aff_level,
    }
