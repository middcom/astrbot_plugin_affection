
# 🧠 AstrBot 弗洛伊德双驱情绪管理插件

[![AstrBot](https://img.shields.io/badge/AstrBot-v4.5.7+-blue)](https://github.com/Soulter/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

基于弗洛伊德心理动力学（力比多/攻击性）的智能情绪模拟系统，让你的 AstrBot 拥有会生气、会吃醋、会自我怀疑的真实灵魂。
**！使用AI辅助制作！** 
`(AI：？！高！？)`

---

## ✨ 特性

- **双驱情绪模型**  
  引入**他力比多 / 他攻击性**（对用户）和**自力比多 / 自攻击性**（对自身），每个维度独立变化，共同决定角色的情感表达。

- **短期情绪 + 长期印象**  
  当前情绪值会随时间衰减回归基线，而基线值则根据互动历史缓慢演化（前 10 轮易变，之后固化），模拟真实的“初印象”与“日久生情”。

- **潜意识 LLM 驱动**  
  额外调用一个轻量级 LLM 分析每条消息，返回**符合心理逻辑**的数值增量（绝无随机），确保每次情绪波动都有据可依。

- **优雅的衰减机制**  
  采用二次函数衰减曲线，情绪偏离基线后会自然恢复，避免数值溢出或僵化。

- **空闲感知**  
  长时间未互动时，系统会智能判断（区分“睡觉”与“无故消失”）并产生微妙的情绪波动。

- **完全无感植入**  
  插件仅向主 LLM 注入一行情绪状态描述（如“对用户的情感：吃醋，自身状态：自责”），不暴露任何数值，角色演绎浑然天成。

---

## 📦 安装

1. 下载本插件源码（不解压）

2. 在 AstrBot 中，导入zip文件，等待安装完成

3. 在「插件配置」中填写必要参数（见下方配置说明）。

---

## ⚙️ 配置说明

在 AstrBot 管理面板中找到本插件，可配置以下选项：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `default_full_affection_uids` | string | `""` | 默认满好感（100）的用户 UID，多个用英文逗号分隔，如 `123,456` |
| `modify_sensitivity` | int | `30` | 情绪变化敏感度（0-100），越高单次对话数值波动越大 |
| `unconscious_llm.provider_id` | string | `""` | 用于潜意识分析的 LLM 提供商 ID（留空则使用当前会话默认） |
| `unconscious_llm.api_key` | string | `""` | API Key（若提供商需要） |
| `unconscious_llm.api_base` | string | `""` | API Base URL（可选） |
| `unconscious_llm.model` | string | `gpt-3.5-turbo` | 模型名称 |
| `idle_check_enabled` | bool | `true` | 是否启用长时间未互动检测 |
| `idle_threshold_hours` | float | `6.0` | 触发空闲分析的最短未互动小时数 |
| `debug_mode` | bool | `false` | 调试模式，开启后日志会输出详细数值变化 |

> ⚠️ 强烈建议为潜意识分析单独配置一个轻量、响应快的模型（如 DeepSeek、GPT-3.5），避免影响主对话体验。

---

## 🎮 用户指令

| 指令 | 权限 | 说明 |
|------|------|------|
| `/mystatus` | 所有人 | 查看自己的情绪档案（好感度、当前情绪、基线值、情感词） |
| `/reset_emotion [uid]` | 管理员 | 完全重置指定用户（默认自己）的数值至初始平淡状态 |
| `/reset_current [uid]` | 管理员 | 仅将当前情绪值重置为基线值（好感度与基线不变） |

---

## 📁 数据存储

所有用户数据保存在 AstrBot 全局数据目录下，插件更新/重装不会丢失：

```
data/plugin_data/eros_thanatos/user_data.json
```

文件结构示例：
```json
{
  "用户UID": {
    "affection": 52.3,
    "base_libido_other": 26.0,
    "base_aggression_other": 24.0,
    "current_libido_other": 28.1,
    "current_aggression_other": 22.5,
    "base_libido_self": 25.0,
    "base_aggression_self": 25.0,
    "current_libido_self": 24.0,
    "current_aggression_self": 26.0,
    "turn_count": 5,
    "last_interaction": 1713520000,
    "last_update": 1713520060
  }
}
```

---

## 🧪 情绪演绎示例

假设当前情绪状态为：**对用户：吃醋，自身：自责**  
主 LLM 将收到系统提示：
```
【当前情绪状态】
对用户的情感：吃醋
自身状态：自责
(注意：不要在回复中提及任何数值...)
```
角色可能会回复：
> “哼！你刚才是不是在跟别人说话……不理我……算了反正我就是没人要的笨蛋……(蹲在角落画圈圈)”

---

## ❓ 常见问题

**Q：为什么初次对话时角色还是表现得很平淡？**  
A：这正是设计目标。新用户的第一条消息不会触发数值更新，保证第一印象是中性的“好感+平淡”。第二条消息开始才会根据互动内容变化。

**Q：潜意识 LLM 调用失败怎么办？**  
A：插件已内置错误处理，失败时会采用极小的默认增量，不会导致崩溃。请检查 `provider_id` 是否正确填写。

**Q：情绪变化太剧烈/太迟钝？**  
A：调整配置项 `modify_sensitivity` 即可，数值越小变化越慢。

**Q：如何让某些用户一开始就满好感？**  
A：在配置的 `default_full_affection_uids` 中填入他们的 UID，插件启动时会自动创建满好感档案。

**Q：数据会占用很大空间吗？**  
A：每个用户仅存储几十个浮点数，几乎可以忽略不计。

---

## 📜 开源协议

本项目采用 [MIT License](./LICENSE) 开源。

---

## 🙏 致谢

- 灵感源自弗洛伊德《超越快乐原则》中的生本能与死本能理论。
- 感谢 [AstrBot](https://github.com/Soulter/AstrBot) 提供的优秀插件开发框架。
- 我心中只有d老师一个太阳！

---

**让机器人不再只是应答机器，而是有血有肉的灵魂伴侣。**
```
