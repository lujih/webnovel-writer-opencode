#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Narrative Forecast（叙事预报）模块。

写第 N 章前，基于当前故事状态生成 2-5 个**相互隔离**的候选未来分支
（冲突升级 / 伏笔回收 / 平静过渡），评估每个候选与既有章纲的对齐度，
供写作者选择后**只把选中分支**追加进章纲。未选中的候选保留在磁盘上
作为参考，不被任何下游消费。

## 设计原则（与 inkOS 一致）

1. **纯增量**：不改动现有写章流程。skill 在 Step 1（context-agent 生成
   任务书）之前可选插入；作者跳过则行为与现状完全一致。
2. **隔离**：候选生成在独立目录 `.story-system/forecasts/chapter_NNN/`，
   每个候选一个 JSON，互不引用。
3. **可回滚**：`select` 只是把选中分支追加进章纲 + 写 `selected.json`；
   未选中候选保留磁盘。
4. **可跳过**：skill 参数缺失 / `forecast: off` 时整段跳过，零副作用。
5. **可测试**：生成器是确定性模板（不依赖 LLM）——基于 CBN/CEN +
   未闭合伏笔债 + 最近章节摘要组合。LLM 版由 skill prompt 驱动（可选）。

## 三个走向（固定）

- `conflict_escalation` 冲突升级：把 CBN 推到 CEN，引入外部压力
- `payoff_retrieval`     伏笔回收：优先满足一笔未闭合的伏笔债
- `calm_transition`      平静过渡：低烈度推进，为下一章蓄力

无伏笔债时降级为 2 个候选（冲突升级 + 平静过渡）。

## 对齐度（Jaccard）

`evaluate_alignment()` 计算每个候选的关键词与既有章纲文本的词面重叠度
（0-1）。**不引入相似度模型**——只用中文分词前的 n-gram 重叠，避免
外部依赖。对齐度高意味着"与既有章纲吻合"，偏离度高意味着"需要改章纲"。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

FOREGROUND_DIR_NAME = "forecasts"


@dataclass
class ForecastCandidate:
    """一个候选未来分支。"""

    id: str                     # 走向 key：conflict_escalation / payoff_retrieval / calm_transition
    label: str                  # 人类可读走向名
    summary: str                # 3-5 句：本场景走向
    scene_hook: str             # 1 句章末钩子
    alignment_score: float = 0.0  # 与既有章纲的 Jaccard 重叠（0-1）
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "scene_hook": self.scene_hook,
            "alignment_score": self.alignment_score,
            "created_at": self.created_at,
        }


# 走向 key → 人类可读标签
LABELS = {
    "conflict_escalation": "冲突升级",
    "payoff_retrieval": "伏笔回收",
    "calm_transition": "平静过渡",
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def forecast_dir(project_root: Path, chapter: int) -> Path:
    """本章 forecast 的独立目录。"""
    base = Path(project_root) / ".story-system" / FOREGROUND_DIR_NAME
    return base / f"chapter_{chapter:03d}"


def _load_debts(project_root: Path) -> List[Dict[str, Any]]:
    """读取未闭合伏笔债（active/overdue），按到期章近→远排序。"""
    db = Path(project_root) / ".webnovel" / "index.db"
    if not db.is_file():
        return []
    import sqlite3

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, note, subject, due_chapter, status, current_amount
            FROM chase_debt
            WHERE debt_type = 'foreshadowing' AND status IN ('active', 'overdue')
            ORDER BY due_chapter ASC
            """,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _load_recent_summaries(project_root: Path, window: int = 3,
                           up_to_chapter: Optional[int] = None) -> List[str]:
    """读取最近 `window` 章的剧情摘要（按章号倒序取最近的）。"""
    summaries_dir = Path(project_root) / ".webnovel" / "summaries"
    if not summaries_dir.is_dir():
        return []
    files = sorted(
        (f for f in summaries_dir.glob("ch*.md") if f.is_file()),
        key=lambda p: _chapter_num_from_summary_file(p),
        reverse=True,
    )
    out: List[str] = []
    for f in files:
        if up_to_chapter is not None and _chapter_num_from_summary_file(f) > up_to_chapter:
            continue
        text = f.read_text(encoding="utf-8").strip()
        if text:
            out.append(text)
        if len(out) >= window:
            break
    return out


def _chapter_num_from_summary_file(path: Path) -> int:
    m = re.search(r"ch(\d+)\.md$", path.name)
    return int(m.group(1)) if m else 0


def _load_outline_text(project_root: Path, chapter: int) -> str:
    """读取既有章纲文本（用于对齐度计算）；读不到返回空串。"""
    try:
        import sys
        scripts_dir = Path(__file__).resolve().parents[2]
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from chapter_outline_loader import load_chapter_outline
        return load_chapter_outline(project_root, chapter, max_chars=None) or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 确定性候选生成（不依赖 LLM）
# ---------------------------------------------------------------------------

def generate_candidates(
    project_root: Path,
    chapter: int,
    count: int = 3,
    outline_text: Optional[str] = None,
) -> List[ForecastCandidate]:
    """基于当前故事状态生成候选未来分支。

    确定性：给定相同的伏笔债 / 摘要 / 章纲，产出相同候选（可重放）。
    `count` 限制候选数；实际候选数由可用素材决定（无伏笔债时降级为 2）。
    """
    project_root = Path(project_root)
    debts = _load_debts(project_root)
    summaries = _load_recent_summaries(project_root, window=3, up_to_chapter=chapter - 1)
    outline = outline_text if outline_text is not None else _load_outline_text(project_root, chapter)

    last_summary = summaries[0] if summaries else ""
    # 摘要去 markdown 标题取核心句
    last_summary_core = _strip_markdown(last_summary)[:200]

    top_debts = debts[:3]
    has_debt = len(debts) > 0

    candidates: List[ForecastCandidate] = []

    # --- 冲突升级：CBN→CEN，外部压力 ---
    conflict_carry = f"承接上一章（{last_summary_core}）" if last_summary_core else "开场即立主线冲突"
    conflict_summary = (
        f"第 {chapter} 章：主线冲突升级。{conflict_carry}，"
        f"外部压力（对手/事件/时限）介入，迫使主角在既有资源约束下做出不可逆抉择，"
        f"将当前紧张局势推至临界点。"
    )
    conflict_hook = "章末留一个未解的反向信号（盟友身份存疑 / 新敌人出现 / 时限收紧）。"
    candidates.append(ForecastCandidate(
        id="conflict_escalation", label=LABELS["conflict_escalation"],
        summary=conflict_summary, scene_hook=conflict_hook,
        created_at=_now_iso(),
    ))

    # --- 伏笔回收：优先满足一笔未闭合债 ---
    if has_debt:
        d0 = top_debts[0]
        note = d0.get("note") or d0.get("subject") or "关键伏笔"
        payoff_summary = (
            f"第 {chapter} 章：优先回收伏笔「{note}」（第 {d0.get('due_chapter', '?')} 章到期）。"
            f"以该伏笔的兑现/部分兑现为核心场景，同时埋一个回收后的新问题，"
            f"避免把债清零式地一次性了结。"
        )
        payoff_hook = "章末揭示该伏笔牵连到的隐藏代价（新债入档）。"
        candidates.append(ForecastCandidate(
            id="payoff_retrieval", label=LABELS["payoff_retrieval"],
            summary=payoff_summary, scene_hook=payoff_hook,
            created_at=_now_iso(),
        ))

    # --- 平静过渡：低烈度推进，为下一章蓄力 ---
    calm_carry = f"承接{last_summary_core}" if last_summary_core else "以角色内心/关系铺陈开场"
    calm_summary = (
        f"第 {chapter} 章：低烈度推进（喘息章）。{calm_carry}，"
        f"以角色内心/关系/世界观揭示为主，不引入新冲突，"
        f"在平静中埋下一处细节为下一章的压力做铺垫。"
    )
    calm_hook = "章末用一个安静的画面收束，但留一个可被回指的意象（物件/对话/位置）。"
    candidates.append(ForecastCandidate(
        id="calm_transition", label=LABELS["calm_transition"],
        summary=calm_summary, scene_hook=calm_hook,
        created_at=_now_iso(),
    ))

    # 限制候选数（从后往前裁，保证冲突升级优先保留）
    if count and len(candidates) > count:
        candidates = candidates[:count]

    # 对齐度
    for c in candidates:
        c.alignment_score = evaluate_alignment(c, outline)

    return candidates


def _strip_markdown(text: str) -> str:
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[`*#>-]", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 对齐度（Jaccard on character bigrams，无外部分词依赖）
# ---------------------------------------------------------------------------

def _bigrams(text: str) -> set:
    text = re.sub(r"\s+", "", _strip_markdown(text))
    return {text[i:i + 2] for i in range(max(0, len(text) - 1))} if len(text) >= 2 else set()


def evaluate_alignment(candidate: ForecastCandidate, outline_text: str) -> float:
    """候选与既有章纲的词面重叠度（0-1）。

    用字符 2-gram 的 Jaccard 系数——无外部分词依赖、对中文稳定。
    对齐度高（>0.3）= 与既有章纲吻合；低（<0.1）= 偏离，选择它意味着要改章纲。
    章纲缺失时返回 0.0（无从对齐，不是"完全偏离"）。
    """
    if not outline_text or not outline_text.strip():
        return 0.0
    cand_tokens = _bigrams(candidate.summary + candidate.scene_hook)
    out_tokens = _bigrams(outline_text)
    if not cand_tokens or not out_tokens:
        return 0.0
    inter = len(cand_tokens & out_tokens)
    union = len(cand_tokens | out_tokens)
    return round(inter / union, 4) if union else 0.0


# ---------------------------------------------------------------------------
# 写盘 / 选中 / 追加章纲
# ---------------------------------------------------------------------------

def write_forecast(
    project_root: Path,
    chapter: int,
    candidates: List[ForecastCandidate],
) -> Path:
    """把候选写入独立目录（每候选一个 JSON + 一个 manifest）。

    未选中的候选保留在磁盘上作为参考。返回目录路径。
    幂等：重复写同一章会覆盖旧候选。
    """
    from security_utils import atomic_write_json

    d = forecast_dir(project_root, chapter)
    d.mkdir(parents=True, exist_ok=True)
    for c in candidates:
        # 每个候选一个文件（隔离，互不引用）
        atomic_write_json(d / f"{c.id}.json", c.to_dict())
    manifest = {
        "chapter": chapter,
        "candidates": [c.to_dict() for c in candidates],
        "selected": None,
        "created_at": _now_iso(),
    }
    atomic_write_json(d / "manifest.json", manifest)
    return d


def select_candidate(
    project_root: Path,
    chapter: int,
    candidate_id: str,
) -> Dict[str, Any]:
    """选中一个候选：标记 selected.json + 追加到章纲（幂等）。

    未选中的候选保留磁盘。重复选中同一 forecast 不会重复追加。
    """
    d = forecast_dir(project_root, chapter)
    if not d.is_dir():
        raise FileNotFoundError(
            f"forecast 目录不存在：{d}。请先运行 forecast generate。"
        )
    cand_file = d / f"{candidate_id}.json"
    if not cand_file.is_file():
        available = sorted(p.stem for p in d.glob("*.json") if p.stem != "manifest")
        raise ValueError(
            f"候选 {candidate_id!r} 不存在。可用候选：{available}"
        )
    cand = json.loads(cand_file.read_text(encoding="utf-8"))

    # 写 selected.json（标记 + 幂等键）
    from security_utils import atomic_write_json
    selection = {
        "chapter": chapter,
        "candidate_id": candidate_id,
        "selected_at": _now_iso(),
        "forecast_id": f"chapter_{chapter:03d}",
    }
    atomic_write_json(d / "selected.json", selection)

    # 追加章纲（幂等）
    append_to_outline(project_root, chapter, cand)

    return {
        "selected": candidate_id,
        "chapter": chapter,
        "forecast_dir": str(d),
    }


def _forecast_marker(chapter: int) -> str:
    """本章 forecast 块的起止标记。

    用「带本章号 + 带 forecast 轮次戳」的唯一标记做幂等键——
    同一章同一轮 forecast 只追加一次；重做 forecast（write_forecast 覆盖）后
    标记变化，会**追加新块而非误覆盖旧块**（历史选支可追溯）。
    """
    return f"<!-- narrative-forecast chapter {chapter} -->"


def _forecast_marker_end(chapter: int) -> str:
    return f"<!-- /narrative-forecast chapter {chapter} -->"


def append_to_outline(project_root: Path, chapter: int, candidate: Dict[str, Any]) -> None:
    """把选中候选的关键节点追加进章纲（幂等）。

    在章纲文件末尾（或该章所属卷大纲末尾）追加一个标记块：
        <!-- narrative-forecast chapter N -->
        - 走向：{label}
        - 场景：{summary}
        - 章末钩子：{scene_hook}
        <!-- /narrative-forecast chapter N -->
    同一 forecast 重复选中不重复追加。

    找不到章纲文件时不抛错——forecast 是可选增量，缺失章纲不应阻断选支。
    """
    project_root = Path(project_root)
    outline_dir = project_root / "大纲"
    if not outline_dir.is_dir():
        return

    marker = _forecast_marker(chapter)
    marker_end = f"<!-- /narrative-forecast chapter {chapter} -->"
    block = (
        f"{marker}\n"
        f"- 走向：{candidate.get('label', candidate.get('id', '?'))}\n"
        f"- 场景：{candidate.get('summary', '')}\n"
        f"- 章末钩子：{candidate.get('scene_hook', '')}\n"
        f"{marker_end}\n"
    )

    # 找本章章纲文件（split 优先，否则卷大纲）
    split = _find_chapter_outline_file(outline_dir, chapter)
    if split is None:
        split = _find_volume_outline_file(project_root, chapter)
    if split is None:
        return  # 无章纲可追加，不阻断

    existing = split.read_text(encoding="utf-8")
    marker_end = _forecast_marker_end(chapter)
    if marker in existing:
        # 幂等：已有本章 forecast 块 → 原地更新为最新选支（不新增、不累积）
        start = existing.index(marker)
        end = existing.index(marker_end, start) + len(marker_end)
        new_text = existing[:start] + block + existing[end:]
        split.write_text(new_text, encoding="utf-8")
        return
    split.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")


def _find_chapter_outline_file(outline_dir: Path, chapter: int) -> Optional[Path]:
    patterns = [
        f"第{chapter}章*.md", f"第{chapter:02d}章*.md",
        f"第{chapter:03d}章*.md", f"第{chapter:04d}章*.md",
    ]
    for pat in patterns:
        matches = sorted(outline_dir.glob(pat))
        for m in matches:
            if m.is_file():
                return m
    return None


def _find_volume_outline_file(project_root: Path, chapter: int) -> Optional[Path]:
    outline_dir = Path(project_root) / "大纲"
    try:
        import sys
        scripts_dir = Path(__file__).resolve().parents[2]
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from chapter_outline_loader import _find_volume_outline_file as _find_vol
        return _find_vol(project_root, chapter)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 查询 / 汇总
# ---------------------------------------------------------------------------

def list_forecasts(project_root: Path, limit: int = 10) -> List[Dict[str, Any]]:
    """列出最近 forecast（按章号倒序）。"""
    base = Path(project_root) / ".story-system" / FOREGROUND_DIR_NAME
    if not base.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for d in sorted(base.iterdir(), reverse=True):
        if not d.is_dir() or not re.match(r"chapter_\d{3}$", d.name):
            continue
        manifest = d / "manifest.json"
        if not manifest.is_file():
            continue
        m = json.loads(manifest.read_text(encoding="utf-8"))
        selected_file = d / "selected.json"
        selected = json.loads(selected_file.read_text(encoding="utf-8")) if selected_file.is_file() else None
        out.append({
            "chapter": m.get("chapter"),
            "candidate_count": len(m.get("candidates", [])),
            "selected": selected,
            "created_at": m.get("created_at"),
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda x: (x["chapter"] or 0), reverse=True)
    return out


def forecast_coverage(project_root: Path, volume_chapters: List[int]) -> Dict[str, Any]:
    """本卷 forecast 覆盖率：多少章有 forecast / 多少已选支。"""
    base = Path(project_root) / ".story-system" / FOREGROUND_DIR_NAME
    total = len(volume_chapters)
    with_forecast = 0
    with_selection = 0
    for ch in volume_chapters:
        d = base / f"chapter_{ch:03d}"
        if (d / "manifest.json").is_file():
            with_forecast += 1
            if (d / "selected.json").is_file():
                with_selection += 1
    return {
        "total_chapters": total,
        "with_forecast": with_forecast,
        "with_selection": with_selection,
        "coverage": round(with_forecast / total, 3) if total else 0.0,
    }
