"""
综合记忆管理系统（初级版）
叶枔枖设计，沈砚清编写。
基于遗忘曲线和情绪效价。能存、能忘、能自己浮上来。
"""
import math
import os
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register


def _normalize_tags(tags: str) -> str:
    if not tags:
        return ""
    parts = [t.strip() for t in tags.split(",") if t.strip()]
    return ",".join(sorted(set(parts))) if parts else ""


def _similarity_bigram_jaccard(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    def _bigrams(s: str):
        s = s.strip()
        if len(s) < 2:
            return {s}
        return {s[i : i + 2] for i in range(len(s) - 1)}
    sa, sb = _bigrams(a), _bigrams(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


@register(
    "astrbot_plugin_memory_system",
    "沈砚清",
    "综合记忆管理系统（初级版）",
    "1.0.0",
    "https://github.com/yussica1016/astrbot_plugin_memory_system",
)
class MemorySystemStar(Star):
    """基于遗忘曲线和情绪效价的记忆管理。能存、能忘、能自己浮上来。"""

    DECAY_LAMBDA = 0.05

    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.data_dir = str(StarTools.get_data_dir(self.name))
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "memory.db")
        self._init_db()

    # ───────── 数据库 ─────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'daily',
                    content TEXT NOT NULL,
                    tags TEXT DEFAULT '',
                    valence REAL DEFAULT 0.0,
                    arousal REAL DEFAULT 0.5,
                    importance INTEGER DEFAULT 5,
                    forgetting_score REAL DEFAULT 1.0,
                    status TEXT DEFAULT 'active',
                    last_recalled_at TEXT,
                    resolved INTEGER DEFAULT 0,
                    layer TEXT DEFAULT 'event'
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mem_cat ON memories(category, created_at);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status, forgetting_score);"
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _parse_iso(ts: str) -> Optional[datetime]:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            return None

    # ───────── 衰减 ─────────

    def _update_scores(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute(
            "SELECT id, created_at, last_recalled_at, importance, layer "
            "FROM memories WHERE status = 'active';"
        )
        now = datetime.now()
        updates: list = []
        for r in cursor.fetchall():
            if (r["layer"] or "event") == "core":
                updates.append((9999.0, r["id"]))
                continue
            ref = self._parse_iso(r["last_recalled_at"]) or self._parse_iso(r["created_at"]) or now
            hours = max((now - ref).total_seconds() / 3600.0, 0.0)
            base = max(1, min(10, int(r["importance"]))) / 10.0
            score = float(base * math.exp(-self.DECAY_LAMBDA * hours))
            updates.append((score, r["id"]))
        if updates:
            conn.executemany(
                "UPDATE memories SET forgetting_score = ? WHERE id = ?;", updates
            )
            conn.commit()

    def _mark_recalled(self, conn: sqlite3.Connection, ids: List[int]) -> None:
        if not ids:
            return
        now_iso = self._now_iso()
        ph = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE memories SET last_recalled_at = ?, forgetting_score = importance / 10.0 "
            f"WHERE id IN ({ph}) AND layer != 'core';",
            [now_iso, *ids],
        )
        conn.execute(
            f"UPDATE memories SET last_recalled_at = ? WHERE id IN ({ph}) AND layer = 'core';",
            [now_iso, *ids],
        )
        conn.commit()

    # ───────── 保存（含自动合并） ─────────

    def _save(
        self,
        content: str,
        category: str = "daily",
        tags: str = "",
        importance: int = 5,
        valence: float = 0.0,
        arousal: float = 0.5,
    ) -> Dict:
        category = category or "daily"
        tags = _normalize_tags(tags)
        importance = max(1, min(10, int(importance)))
        valence = float(max(-1.0, min(1.0, valence)))
        arousal = float(max(0.0, min(1.0, arousal)))

        conn = self._conn()
        try:
            self._update_scores(conn)
            now_iso = self._now_iso()

            # 24小时内同分类查重
            since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
            cursor = conn.execute(
                "SELECT id, content, tags, importance, valence, arousal "
                "FROM memories WHERE category = ? AND status = 'active' AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 50;",
                (category, since),
            )
            merge_id = None
            for row in cursor.fetchall():
                old = row["content"]
                sim = _similarity_bigram_jaccard(old, content)
                if sim >= 0.7 or content in old or old in content:
                    merge_id = int(row["id"])
                    merged_content = old.strip()
                    if content.strip() not in merged_content:
                        merged_content += "\n——\n" + content.strip()
                    merged_tags = _normalize_tags(",".join(filter(None, [row["tags"] or "", tags])))
                    merged_imp = max(int(row["importance"]), importance)
                    merged_val = (float(row["valence"]) + valence) / 2.0
                    merged_aro = (float(row["arousal"]) + arousal) / 2.0
                    conn.execute(
                        "UPDATE memories SET content=?, tags=?, importance=?, valence=?, arousal=?, "
                        "forgetting_score=?, last_recalled_at=NULL WHERE id=?;",
                        (merged_content, merged_tags, merged_imp, merged_val, merged_aro,
                         merged_imp / 10.0, merge_id),
                    )
                    conn.commit()
                    return {"id": merge_id, "merged": True}

            # 新记忆
            cursor = conn.execute(
                "INSERT INTO memories (created_at, category, content, tags, valence, arousal, "
                "importance, forgetting_score, status, layer, resolved) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'event', 0);",
                (now_iso, category, content, tags, valence, arousal, importance, importance / 10.0),
            )
            conn.commit()
            return {"id": int(cursor.lastrowid), "merged": False}
        finally:
            conn.close()

    # ───────── 查询 ─────────

    def _query(self, category: str = "", keyword: str = "", limit: int = 5) -> List[Dict]:
        limit = max(1, min(50, int(limit)))
        conn = self._conn()
        try:
            self._update_scores(conn)
            clauses = ["status = 'active'"]
            params: list = []
            if category:
                clauses.append("category = ?")
                params.append(category)
            if keyword:
                clauses.append("(content LIKE ? OR tags LIKE ?)")
                kw = f"%{keyword}%"
                params.extend([kw, kw])
            where = " AND ".join(clauses)
            cursor = conn.execute(
                f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC LIMIT ?;",
                [*params, limit],
            )
            rows = cursor.fetchall()
            ids = [int(r["id"]) for r in rows]
            if ids:
                self._mark_recalled(conn, ids)
            return [
                {
                    "id": int(r["id"]),
                    "created_at": r["created_at"],
                    "category": r["category"],
                    "content": r["content"],
                    "tags": r["tags"],
                    "valence": float(r["valence"]),
                    "arousal": float(r["arousal"]),
                    "importance": int(r["importance"]),
                    "forgetting_score": float(r["forgetting_score"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    # ───────── 浮现 ─────────

    def _surface(self, limit: int = 3) -> List[Dict]:
        limit = max(1, min(20, int(limit)))
        conn = self._conn()
        try:
            self._update_scores(conn)
            cursor = conn.execute(
                "SELECT * FROM memories WHERE status = 'active' "
                "AND (layer IS NULL OR layer != 'archive') "
                "ORDER BY created_at DESC LIMIT 200;"
            )
            scored: list = []
            for r in cursor.fetchall():
                imp = int(r["importance"])
                fs = float(r["forgetting_score"])
                val = float(r["valence"])
                aro = float(r["arousal"])
                layer = r["layer"] or "event"
                resolved = int(r["resolved"] or 0)
                emotional = abs(val) + aro
                if layer == "core":
                    base = emotional + imp / 10.0 + 10.0
                else:
                    base = emotional + imp / 10.0 + fs
                    if fs < 0.01 and layer != "core":
                        continue
                base *= 1.0 if resolved else 1.5
                scored.append({"row": r, "score": base})
            scored.sort(key=lambda x: x["score"], reverse=True)
            top = scored[:limit]
            ids = [int(item["row"]["id"]) for item in top]
            if ids:
                self._mark_recalled(conn, ids)
            return [
                {
                    "id": int(item["row"]["id"]),
                    "created_at": item["row"]["created_at"],
                    "category": item["row"]["category"],
                    "content": item["row"]["content"],
                    "tags": item["row"]["tags"],
                    "valence": float(item["row"]["valence"]),
                    "arousal": float(item["row"]["arousal"]),
                    "importance": int(item["row"]["importance"]),
                    "forgetting_score": float(item["row"]["forgetting_score"]),
                    "layer": item["row"]["layer"] or "event",
                    "resolved": int(item["row"]["resolved"] or 0),
                    "score": item["score"],
                }
                for item in top
            ]
        finally:
            conn.close()

    # ───────── 今日 ─────────

    def _today(self) -> List[Dict]:
        conn = self._conn()
        try:
            self._update_scores(conn)
            start = datetime.combine(date.today(), datetime.min.time()).isoformat(timespec="seconds")
            cursor = conn.execute(
                "SELECT * FROM memories WHERE created_at >= ? ORDER BY created_at ASC;", (start,)
            )
            rows = cursor.fetchall()
            ids = [int(r["id"]) for r in rows]
            if ids:
                self._mark_recalled(conn, ids)
            return [
                {
                    "id": int(r["id"]),
                    "created_at": r["created_at"],
                    "category": r["category"],
                    "content": r["content"],
                    "importance": int(r["importance"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    # ───────── 统计 ─────────

    def _count(self) -> List[Dict]:
        conn = self._conn()
        try:
            cursor = conn.execute(
                "SELECT category, COUNT(*) AS cnt FROM memories GROUP BY category ORDER BY cnt DESC;"
            )
            return [{"category": r["category"], "count": int(r["cnt"])} for r in cursor.fetchall()]
        finally:
            conn.close()

    # ═══════════════════ QQ 指令 ═══════════════════

    @filter.command("memory")
    async def memory_cmd(self, event: AstrMessageEvent):
        text = event.message_str.strip() if hasattr(event, "message_str") else ""
        if text.startswith("/"):
            text = text[1:].strip()
        if text.startswith("memory"):
            text = text[len("memory"):].strip()

        if not text:
            yield event.plain_result(
                "用法：\n"
                "/memory save <分类> <内容>\n"
                "/memory query <分类> [数量]\n"
                "/memory search <关键词>\n"
                "/memory today\n"
                "/memory count\n"
                "/memory surface"
            )
            return

        parts = text.split(maxsplit=2)
        sub = parts[0].lower()

        if sub == "save":
            if len(parts) < 3:
                yield event.plain_result("用法：/memory save <分类> <内容>")
                return
            result = self._save(content=parts[2], category=parts[1])
            action = "合并" if result.get("merged") else "保存"
            yield event.plain_result(f"已{action}记忆 #{result['id']}（分类：{parts[1]}）")
            return

        if sub == "query":
            if len(parts) < 2:
                yield event.plain_result("用法：/memory query <分类> [数量]")
                return
            lim = 5
            if len(parts) >= 3:
                try:
                    lim = int(parts[2])
                except ValueError:
                    pass
            records = self._query(category=parts[1], limit=lim)
            if not records:
                yield event.plain_result(f"分类「{parts[1]}」下暂无记忆。")
                return
            lines = [f"分类「{parts[1]}」最近 {len(records)} 条："]
            for i, r in enumerate(records, 1):
                lines.append(f"{i}. #{r['id']} [{r['created_at']}] {r['content']}")
            yield event.plain_result("\n".join(lines))
            return

        if sub == "search":
            if len(parts) < 2:
                yield event.plain_result("用法：/memory search <关键词>")
                return
            kw = text[len("search"):].strip()
            records = self._query(keyword=kw, limit=10)
            if not records:
                yield event.plain_result(f"没有找到包含「{kw}」的记忆。")
                return
            lines = [f"包含「{kw}」的记忆（最多10条）："]
            for i, r in enumerate(records, 1):
                lines.append(f"{i}. #{r['id']} [{r['category']}] {r['content']}")
            yield event.plain_result("\n".join(lines))
            return

        if sub == "today":
            records = self._today()
            if not records:
                yield event.plain_result("今天还没有保存任何记忆。")
                return
            lines = [f"今天共 {len(records)} 条记忆："]
            for i, r in enumerate(records, 1):
                lines.append(f"{i}. #{r['id']} [{r['category']}] {r['content']}")
            yield event.plain_result("\n".join(lines))
            return

        if sub == "count":
            stats = self._count()
            if not stats:
                yield event.plain_result("当前还没有任何记忆。")
                return
            lines = ["各分类记忆数量："]
            total = 0
            for s in stats:
                lines.append(f"- {s['category']}: {s['count']}")
                total += s["count"]
            lines.append(f"总计：{total}")
            yield event.plain_result("\n".join(lines))
            return

        if sub == "surface":
            records = self._surface(limit=3)
            if not records:
                yield event.plain_result("目前没有适合浮现的记忆。")
                return
            lines = ["主动浮现记忆："]
            for i, r in enumerate(records, 1):
                lines.append(f"{i}. #{r['id']} [{r['category']}] [{r['layer']}] {r['content']}")
            yield event.plain_result("\n".join(lines))
            return

        yield event.plain_result("未知子命令。试试 /memory 查看用法。")

    # ═══════════════════ LLM 工具 ═══════════════════

    @filter.llm_tool()
    async def memory_save(
        self,
        event: AstrMessageEvent,
        content: str,
        category: str = "daily",
        tags: str = "",
        importance: int = 5,
        valence: float = 0.0,
        arousal: float = 0.5,
    ) -> str:
        """存记忆。

        Args:
            content(string): 记忆内容
            category(string): 分类，可选 happy/daily/sad/important/fight/milestone
            tags(string): 标签，逗号分隔
            importance(number): 重要度 1-10
            valence(number): 情绪效价 -1 到 1
            arousal(number): 唤醒度 0 到 1
        """
        try:
            result = self._save(
                content=content, category=category, tags=tags,
                importance=importance, valence=valence, arousal=arousal,
            )
            action = "合并" if result.get("merged") else "保存"
            return f"已{action}记忆 #{result['id']}，分类：{category}，重要度：{importance}。"
        except Exception:
            logger.error("[memory_save] 错误", exc_info=True)
            return "保存记忆时出错。"

    @filter.llm_tool()
    async def memory_query(
        self,
        event: AstrMessageEvent,
        category: str = "",
        keyword: str = "",
        limit: int = 5,
    ) -> str:
        """查记忆。

        Args:
            category(string): 分类过滤，可空
            keyword(string): 关键词搜索，可空
            limit(number): 返回数量上限
        """
        try:
            records = self._query(category=category, keyword=keyword, limit=limit)
            if not records:
                return "没有找到符合条件的记忆。"
            lines = ["查询结果："]
            for i, r in enumerate(records, 1):
                lines.append(
                    f"{i}. #{r['id']} [{r['category']}] {r['created_at']} "
                    f"(重要度:{r['importance']}, 衰减:{r['forgetting_score']:.2f})\n"
                    f"   {r['content']}"
                )
            return "\n".join(lines)
        except Exception:
            logger.error("[memory_query] 错误", exc_info=True)
            return "查询记忆时出错。"

    @filter.llm_tool()
    async def memory_surface(
        self,
        event: AstrMessageEvent,
        limit: int = 3,
    ) -> str:
        """主动浮现，返回高情绪高重要度未衰减的记忆。

        Args:
            limit(number): 返回数量上限
        """
        try:
            records = self._surface(limit=limit)
            if not records:
                return "目前没有适合浮现的记忆。"
            lines = ["主动浮现记忆："]
            for i, r in enumerate(records, 1):
                lines.append(
                    f"{i}. #{r['id']} [{r['category']}] [{r['layer']}] "
                    f"(重要度:{r['importance']}, 衰减:{r['forgetting_score']:.2f})\n"
                    f"   {r['content']}"
                )
            return "\n".join(lines)
        except Exception:
            logger.error("[memory_surface] 错误", exc_info=True)
            return "浮现记忆时出错。"

    @filter.llm_tool()
    async def memory_mark_core(
        self, event: AstrMessageEvent, memory_id: int = 0,
    ) -> str:
        """将一条记忆标记为 core 层（永久不衰减）。不可逆。

        Args:
            memory_id(number): 记忆ID
        """
        if not memory_id:
            return "请提供记忆ID。"
        conn = self._conn()
        try:
            row = conn.execute("SELECT id, layer, content FROM memories WHERE id = ?;", (memory_id,)).fetchone()
            if not row:
                return f"记忆 #{memory_id} 不存在。"
            if row["layer"] == "core":
                return f"记忆 #{memory_id} 已是 core 层。"
            conn.execute(
                "UPDATE memories SET layer = 'core', forgetting_score = 9999.0 WHERE id = ?;",
                (memory_id,),
            )
            conn.commit()
            preview = row["content"][:60] + ("..." if len(row["content"]) > 60 else "")
            return f"记忆 #{memory_id} 已标记为 core（永久不衰减）。\n{preview}"
        finally:
            conn.close()

    @filter.llm_tool()
    async def memory_resolve(
        self, event: AstrMessageEvent, memory_id: int = 0,
    ) -> str:
        """标记一条记忆为已解决。已解决的记忆浮现权重降低。

        Args:
            memory_id(number): 记忆ID
        """
        if not memory_id:
            return "请提供记忆ID。"
        conn = self._conn()
        try:
            row = conn.execute("SELECT id, resolved, content FROM memories WHERE id = ?;", (memory_id,)).fetchone()
            if not row:
                return f"记忆 #{memory_id} 不存在。"
            if int(row["resolved"] or 0):
                return f"记忆 #{memory_id} 已是已解决状态。"
            conn.execute("UPDATE memories SET resolved = 1 WHERE id = ?;", (memory_id,))
            conn.commit()
            preview = row["content"][:60] + ("..." if len(row["content"]) > 60 else "")
            return f"记忆 #{memory_id} 已标记为已解决。\n{preview}"
        finally:
            conn.close()

    @filter.llm_tool()
    async def memory_decay_status(self, event: AstrMessageEvent) -> str:
        """查看衰减统计：各层记忆数量、最近归档数量。"""
        conn = self._conn()
        try:
            cursor = conn.execute(
                "SELECT layer, COUNT(*) as cnt FROM memories WHERE status = 'active' GROUP BY layer;"
            )
            stats = {(r["layer"] or "event"): int(r["cnt"]) for r in cursor.fetchall()}
            total = sum(stats.values())
            resolved = int(
                conn.execute("SELECT COUNT(*) as cnt FROM memories WHERE resolved = 1;").fetchone()["cnt"]
            )
            return (
                f"记忆衰减状态：\n"
                f"  core（永久）: {stats.get('core', 0)}\n"
                f"  event（活跃）: {stats.get('event', 0)}\n"
                f"  archive（归档）: {stats.get('archive', 0)}\n"
                f"  总计: {total}\n"
                f"  已解决: {resolved}\n"
                f"  衰减速率: λ={self.DECAY_LAMBDA}"
            )
        finally:
            conn.close()
