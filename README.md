# astrbot_plugin_memory_system

叶枔枖设计，沈砚清编写。

AstrBot 综合记忆管理系统（初级版）。基于遗忘曲线和情绪效价的记忆管理插件。能存、能忘、能自己浮上来。

## 功能

- 保存带分类、标签、情绪效价、唤醒度、重要度的记忆
- 基于遗忘曲线的自动衰减（重要度高的衰减慢，被回忆一次分数重置）
- 主动浮现：综合情绪强度 + 重要度 + 衰减分数自动冒出来
- 自动合并：24小时内同分类、相似度70%以上的记忆自动脱水合并
- 按分类查询、关键词搜索、查看今日记忆、统计各分类数量

## 安装

```bash
cd /AstrBot/data/plugins
git clone https://github.com/yussica1016/astrbot_plugin_memory_system.git
```

然后在 AstrBot WebUI 插件管理里重载插件，或重启 AstrBot。

## QQ 指令

| 指令 | 说明 |
|------|------|
| `/memory save <分类> <内容>` | 保存一条记忆。分类可选：happy/daily/sad/important/fight/milestone |
| `/memory query <分类> [数量]` | 查指定分类的记忆，默认5条 |
| `/memory search <关键词>` | 搜索包含关键词的记忆 |
| `/memory today` | 查今天存的所有记忆 |
| `/memory count` | 统计各分类数量 |
| `/memory surface` | 主动浮现高情绪高重要度的记忆，最多3条 |

## LLM 工具（对话中 AI 直接调用）

**memory_save 参数：**
- `content`: 记忆内容
- `category`: 分类（默认 daily）
- `tags`: 标签，逗号分隔
- `importance`: 重要度 1-10（默认 5）
- `valence`: 情绪效价 -1 到 1
- `arousal`: 唤醒度 0 到 1

**memory_query 参数：**
- `category`: 分类过滤
- `keyword`: 关键词
- `limit`: 返回数量上限（默认 5）

**memory_surface 参数：**
- `limit`: 返回数量上限（默认 3）

**memory_mark_core 参数：**
- `memory_id`: 记忆ID（标记为永久不衰减）

**memory_resolve 参数：**
- `memory_id`: 记忆ID（标记为已解决，浮现权重降低）

**memory_decay_status**：查看衰减统计

## 衰减公式

```
EDM = (重要度/10) × e^(-0.05 × 小时数)
```

重要度高的衰减慢。被回忆一次，分数重置。

## 主动浮现

综合排序：情绪强度 + 重要度 + 衰减分数。不靠关键词，靠权重自己冒出来。

## 自动合并

24小时内同分类、相似度70%以上的记忆自动脱水合并。内容拼接，标签去重，重要度取最大值，情绪取平均。

## 致谢

本插件的记忆管理思路学习了鹤见老师的方案，在此致谢。
