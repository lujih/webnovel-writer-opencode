#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from reference_search import (
    CSV_CONFIG,
    GENRE_CANONICAL,
    resolve_genre,
    search as search_reference,
    split_multi_value,
)

from .story_contracts import merge_anti_patterns

logger = logging.getLogger(__name__)


ANTI_PATTERN_SOURCE_FIELDS = {
    "场景写法": ["毒点"],
    "写作技法": ["毒点"],
    "爽点与节奏": ["毒点"],
    "人设与关系": ["毒点"],
    "桥段套路": ["毒点"],
    "题材与调性推理": ["毒点"],
    "命名规则": ["毒点"],
    "金手指与设定": ["毒点"],
}

_TEXT_TOKEN_RE = re.compile(r"[\s|,，、/；;：:（）()【】\[\]<>《》\"'!?！？。…]+")
_PLACEHOLDER_QUERY_RE = re.compile(r"^\s*(\{[^{}]*章纲目标[^{}]*\}|第\s*\d+\s*章\s*章纲目标)\s*$")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")


def is_placeholder_query(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return True
    return bool(_PLACEHOLDER_QUERY_RE.match(text))


class StorySystemRoutingError(ValueError):
    """Raised when story-system cannot select a route row."""


def _validate_explicit_genre_source(genre: Optional[str]) -> Optional[str]:
    normalized = str(genre or "").strip()
    if not normalized:
        return None
    if _ASCII_LETTER_RE.search(normalized):
        raise StorySystemRoutingError(
            "story-system 题材参数必须使用中文名称，不能使用英文 profile key "
            f"'{normalized}'。不会生成 .story-system contracts。"
            "例如：规则怪谈、悬疑、玄幻。"
        )
    return normalized


class StorySystemEngine:
    def __init__(self, csv_dir: str | Path):
        self.csv_dir = Path(csv_dir)
        self._csv_cache: Dict[str, List[Dict[str, Any]]] = {}
        #: 每张 CSV 的读取诊断（仅在有畸形行/空行时出现）。
        #: 键为表名，值为 {rows_read, rows_kept, dropped_blank_rows, normalized_rows}。
        #: CSV 是知识层最上游，静默丢行会让「路由不到正确题材」无从排查。
        self.csv_diagnostics: Dict[str, Dict[str, int]] = {}

    def build(
        self,
        query: str,
        genre: Optional[str],
        chapter: Optional[int],
        chapter_directive: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        chapter_directive = chapter_directive or {}
        genre = _validate_explicit_genre_source(genre)
        route = self._route(query=query, genre=genre)
        search_query = self._expand_query(
            query,
            route.get("default_query", ""),
            self._directive_query_text(chapter_directive),
        )
        # Boost dynamic context with strand keywords.
        # Doubling strand raises BM25 term frequency so strand-relevant
        # reference rows rank above generic genre matches for this chapter.
        strand = str(chapter_directive.get("strand") or "").strip()
        dynamic_query = f"{search_query} {strand} {strand}" if strand else search_query
        base_context = self._collect_tables(
            search_query,
            route["recommended_base_tables"],
            genre=route["genre_filter"],
            top_k=1,
        )
        dynamic_context = self._collect_tables(
            dynamic_query,
            route["recommended_dynamic_tables"],
            genre=route["genre_filter"],
            top_k=2,
        )

        # Reasoning layer — try routed genre first, then original genre
        canonical_genre = str(route.get("meta", {}).get("canonical_genre", "") or "").strip()
        reasoning = self._load_reasoning(canonical_genre)
        if not reasoning and genre:
            fallback_genre = self._primary_resolved_genre(genre) or genre
            if fallback_genre != canonical_genre:
                reasoning = self._load_reasoning(fallback_genre)
        ranked = self._apply_reasoning(reasoning, base_context, dynamic_context, chapter_directive)

        source_trace = route["source_trace"] + self._build_source_trace_with_reasoning(ranked, reasoning)

        raw_anti = merge_anti_patterns(
            route["route_anti_patterns"],
            self._extract_anti_patterns(base_context),
            self._extract_anti_patterns(dynamic_context),
        )
        anti_patterns = self._rank_anti_patterns(reasoning, raw_anti)

        return {
            "meta": {"query": query, "chapter": chapter, "explicit_genre": genre or ""},
            "master_setting": {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "MASTER_SETTING",
                    "generator_version": "phase1",
                    "query": query,
                },
                "route": route["meta"],
                "master_constraints": {
                    "core_tone": route["core_tone"],
                    "pacing_strategy": route["pacing_strategy"],
                },
                "base_context": [r for r in ranked if r.get("_priority_rank", 999) < 999],
                "source_trace": source_trace,
                "override_policy": {
                    "locked": ["route.primary_genre", "master_constraints.core_tone"],
                    "append_only": ["anti_patterns"],
                    "override_allowed": [],
                },
            },
            "chapter_brief": (
                {
                    "meta": {
                        "schema_version": "story-system/v1",
                        "contract_type": "CHAPTER_BRIEF",
                        "generator_version": "phase1",
                        "chapter": chapter,
                    },
                    "override_allowed": {
                        "chapter_focus": self._suggest_chapter_focus(query, chapter_directive),
                    },
                    "chapter_directive": chapter_directive,
                    "dynamic_context": ranked,
                    "source_trace": source_trace,
                    "reasoning": (
                        {
                            "genre": reasoning.get("题材", ""),
                            "inject_target": self._reasoning_inject_target(reasoning),
                            "style_priority": reasoning.get("风格优先级", ""),
                            "pacing_strategy": reasoning.get("节奏默认策略", ""),
                        }
                        if reasoning
                        else {}
                    ),
                }
                if chapter is not None
                else None
            ),
            "anti_patterns": anti_patterns,
        }

    def _route(self, query: str, genre: Optional[str]) -> Dict[str, Any]:
        route_rows = self._load_csv_rows("题材与调性推理")
        query_text = self._normalize_text(" ".join([query or "", genre or ""]))
        inferred_canonical = "" if genre else self._infer_genre_from_text(query)

        matched = None
        route_source = ""
        for row in route_rows:
            aliases = (
                split_multi_value(row.get("关键词"))
                + split_multi_value(row.get("意图与同义词"))
                + split_multi_value(row.get("题材别名"))
            )
            if any(alias and self._normalize_text(alias) in query_text for alias in aliases):
                matched = row
                route_source = "keyword_or_alias_match"
                break
        if matched is None and genre:
            matched = self._fallback_row_for_genre(route_rows, genre)
            if matched is not None:
                route_source = "explicit_genre_fallback"
        if matched is None and inferred_canonical:
            matched = self._fallback_row_for_genre(route_rows, inferred_canonical)
            if matched is not None:
                route_source = "inferred_genre_fallback"
        if matched is None:
            raise self._routing_error(query=query, genre=genre, route_rows=route_rows)

        primary_genre = str(matched.get("题材/流派") or genre or "").strip()
        explicit_canonical = self._primary_resolved_genre(genre)
        canonical_genre = str(matched.get("canonical_genre") or "").strip()
        row_canonicals = [
            resolved
            for raw in split_multi_value(matched.get("适用题材"))
            for resolved in [resolve_genre(raw) or str(raw or "").strip()]
            if resolved and resolved != "全部"
        ]
        if explicit_canonical and explicit_canonical != "全部":
            if not row_canonicals or explicit_canonical in row_canonicals or canonical_genre in ("", "全部"):
                canonical_genre = explicit_canonical
        elif inferred_canonical and inferred_canonical != "全部":
            if not row_canonicals or inferred_canonical in row_canonicals or canonical_genre in ("", "全部"):
                canonical_genre = inferred_canonical
        if not canonical_genre:
            resolved_primary = resolve_genre(primary_genre)
            if resolved_primary in GENRE_CANONICAL:
                canonical_genre = resolved_primary
            elif explicit_canonical and explicit_canonical != "全部":
                canonical_genre = explicit_canonical
        genre_filter = canonical_genre if canonical_genre not in ("", "全部") else ""
        return {
            "meta": {
                "primary_genre": primary_genre,
                "canonical_genre": canonical_genre,
                "route_source": route_source,
                "genre_filter": genre_filter,
                "recommended_base_tables": split_multi_value(matched.get("推荐基础检索表")),
                "recommended_dynamic_tables": split_multi_value(matched.get("推荐动态检索表")),
            },
            "core_tone": str(matched.get("核心调性") or "").strip(),
            "pacing_strategy": str(matched.get("节奏策略") or "").strip(),
            "route_anti_patterns": self._extract_route_anti_patterns(matched),
            "recommended_base_tables": split_multi_value(matched.get("推荐基础检索表")),
            "recommended_dynamic_tables": split_multi_value(matched.get("推荐动态检索表")),
            "genre_filter": genre_filter,
            "default_query": str(matched.get("默认查询词") or "").strip(),
            "source_trace": [{"table": "题材与调性推理", "id": matched.get("编号", ""), "reason": route_source}],
        }

    def _collect_tables(self, query: str, tables: List[str], genre: str, top_k: int) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for table_name in tables:
            result = search_reference(
                csv_dir=self.csv_dir,
                skill="write",
                query=query,
                table=table_name,
                genre=genre or None,
                max_results=top_k,
            )
            raw_rows = {str(row.get("编号") or ""): row for row in self._load_csv_rows(table_name)}
            for item in result.get("data", {}).get("results", []):
                row_id = str(item.get("编号") or "")
                full_row = dict(raw_rows.get(row_id) or {})
                full_row["_table"] = str(item.get("表") or table_name)
                full_row["编号"] = row_id
                full_row["核心摘要"] = str(
                    full_row.get("核心摘要")
                    or item.get("内容摘要")
                    or item.get("核心摘要")
                    or ""
                ).strip()
                rows.append(full_row)
        return rows

    def _extract_anti_patterns(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        extracted: List[Dict[str, Any]] = []
        for row in rows:
            table_name = str(row.get("_table") or "")
            for field_name in ANTI_PATTERN_SOURCE_FIELDS.get(table_name, []):
                for text in self._split_multi_value(row.get(field_name)):
                    extracted.append(
                        {
                            "text": text,
                            "source_table": table_name,
                            "source_id": row.get("编号", ""),
                        }
                    )
        return extracted

    def _suggest_chapter_focus(self, query: str, chapter_directive: Optional[Dict[str, Any]] = None) -> str:
        directive = chapter_directive or {}
        goal = str(directive.get("goal") or "").strip()
        if goal:
            return goal
        query_text = str(query or "").strip()
        if query_text and not is_placeholder_query(query_text):
            return query_text
        return ""

    def _build_source_trace(self, *groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        trace: List[Dict[str, Any]] = []
        for group in groups:
            for row in group:
                trace.append(
                    {
                        "table": row.get("_table", ""),
                        "id": row.get("编号", ""),
                        "summary": row.get("核心摘要", ""),
                    }
                )
        return trace

    def _load_csv_rows(self, table_name: str) -> List[Dict[str, Any]]:
        """读取知识层 CSV，剔除畸形行并记录诊断。

        CSV 是六层数据流的最上游（题材路由 / 反模式 / 裁决规则）。读坏了
        后面每一层都跟着偏，而且不会报错——只会"路由到错误的题材"。
        `csv.DictReader` 对畸形行有三种默认行为，都会污染下游：

        1. 列数多于表头 → 多出的值进 `None` 键
           `{'题材': '仙侠', None: ['多一列']}`
        2. 列数少于表头 → 缺的列取 `None`
           `{'题材': '科幻', '关键词': None}`
        3. 全空行 → 保留成一行全空字符串

        `None` 键最危险：`row.get("关键词")` 看着正常，但任何
        `row.items()` 遍历或 `dict(row)` 比较都会撞上它；而 `None` 值会让
        下游 `.strip()` / 拼接直接抛 `AttributeError`。全空行则让「文件存在
        但没内容」看起来像"有数据"，掩盖配置缺失。

        这里统一归一：多余列丢弃、缺列补空字符串、全空行剔除，并把被处理
        的行数记进 `csv_diagnostics` 供排查。
        """
        if table_name in self._csv_cache:
            return list(self._csv_cache[table_name])
        csv_path = self.csv_dir / f"{table_name}.csv"
        if not csv_path.is_file():
            self._csv_cache[table_name] = []
            return []

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            raw_rows = list(reader)

        rows: List[Dict[str, Any]] = []
        dropped_blank = 0
        normalized = 0
        for raw_row in raw_rows:
            # 归一：只保留表头声明的列，缺列补 ""，None 值补 ""
            row = {name: ("" if raw_row.get(name) is None else raw_row.get(name))
                   for name in fieldnames}
            # 全空行（所有声明列都是空字符串）→ 剔除
            if not any(str(v).strip() for v in row.values()):
                dropped_blank += 1
                continue
            if len(raw_row) != len(fieldnames) or None in raw_row:
                normalized += 1
            rows.append(row)

        if dropped_blank or normalized:
            self.csv_diagnostics[table_name] = {
                "rows_read": len(raw_rows),
                "rows_kept": len(rows),
                "dropped_blank_rows": dropped_blank,
                "normalized_rows": normalized,
            }
            logger.warning(
                "story_system CSV %s: 读入 %d 行，保留 %d 行"
                "（剔除 %d 个空行，归一 %d 个畸形行）",
                table_name, len(raw_rows), len(rows), dropped_blank, normalized,
            )

        self._csv_cache[table_name] = rows
        return list(rows)

    def _normalize_text(self, text: str) -> str:
        return str(text or "").strip().lower()

    _GENRE_COMPOSITE_RE = re.compile(r"[+＋]")

    _NARROW_SPLIT_RE = re.compile(r"[|；;]+")

    def _split_multi_value(self, raw: Any) -> List[str]:
        """Split on | ； ; only — for fields whose content may contain natural-language commas."""
        return [item.strip() for item in self._NARROW_SPLIT_RE.split(str(raw or "")) if item.strip()]

    def _resolve_genre_values(self, raw: Any) -> List[str]:
        tokens = []
        for part in split_multi_value(raw):
            tokens.extend(self._GENRE_COMPOSITE_RE.split(part))
        return [
            resolved
            for token in tokens
            if token
            for resolved in [resolve_genre(token)]
            if resolved
        ]

    def _primary_resolved_genre(self, raw: Any) -> Optional[str]:
        resolved_values = self._resolve_genre_values(raw)
        for value in resolved_values:
            if value in GENRE_CANONICAL or value == "全部":
                return value
        return resolved_values[0] if resolved_values else None

    def _expand_query(self, query: str, default_query: str, chapter_query: str = "") -> str:
        items: List[str] = []
        for candidate in [query, chapter_query, *split_multi_value(default_query)]:
            text = str(candidate or "").strip()
            if text and text not in items:
                items.append(text)
        return " ".join(items)

    def _directive_query_text(self, chapter_directive: Dict[str, Any]) -> str:
        parts: List[str] = []
        for key in ("goal", "strand", "antagonist_tier"):
            value = str(chapter_directive.get(key) or "").strip()
            if value:
                parts.append(value)
        for key in ("key_entities", "must_cover_nodes"):
            for value in chapter_directive.get(key) or []:
                text = str(value or "").strip()
                if text:
                    parts.append(text)
        return " ".join(parts)

    def _fallback_row_for_genre(self, rows: List[Dict[str, Any]], genre: str) -> Dict[str, Any] | None:
        genre_texts = {
            self._normalize_text(value)
            for value in self._resolve_genre_values(genre)
            if value
        }
        if not genre_texts:
            return None
        for row in rows:
            candidates = (
                split_multi_value(row.get("适用题材"))
                + split_multi_value(row.get("题材/流派"))
                + split_multi_value(row.get("canonical_genre"))
            )
            resolved_candidates = {
                self._normalize_text(resolve_genre(candidate) or candidate)
                for candidate in candidates
                if candidate
            }
            if genre_texts.intersection(resolved_candidates):
                return row
        return None

    def _infer_genre_from_text(self, text: str) -> str:
        """Infer a canonical genre from plain query text before default routing."""
        raw_text = str(text or "")
        tokens = [token.strip() for token in _TEXT_TOKEN_RE.split(raw_text) if token.strip()]
        for candidate in tokens:
            resolved = resolve_genre(candidate)
            if resolved in GENRE_CANONICAL:
                return resolved or ""

        normalized = self._normalize_text(raw_text)
        for canonical in sorted(GENRE_CANONICAL, key=len, reverse=True):
            if self._normalize_text(canonical) in normalized:
                return canonical
        return ""

    def _extract_route_anti_patterns(self, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"text": text, "source_table": "题材与调性推理", "source_id": row.get("编号", "")}
            for text in self._split_multi_value(row.get("毒点"))
        ]

    # ------------------------------------------------------------------
    # Reasoning / 裁决 layer
    # ------------------------------------------------------------------

    def _load_reasoning(self, genre: str) -> Dict[str, Any]:
        """Load matching row from 裁决规则.csv for *genre*."""
        rows = self._load_csv_rows("裁决规则")
        genre_norm = self._normalize_text(genre)
        if not genre_norm:
            return {}
        for row in rows:
            if self._normalize_text(row.get("题材")) == genre_norm:
                return row
            aliases = (
                split_multi_value(row.get("关键词"))
                + split_multi_value(row.get("意图与同义词"))
            )
            if any(genre_norm == self._normalize_text(a) for a in aliases):
                return row
        return {}

    def _apply_reasoning(
        self,
        reasoning: Dict[str, Any],
        base_context: List[Dict[str, Any]],
        dynamic_context: List[Dict[str, Any]],
        chapter_directive: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Rank *base_context* + *dynamic_context* rows using 冲突裁决 priority."""
        combined = [dict(r) for r in base_context] + [dict(r) for r in dynamic_context]
        chapter_terms = self._chapter_keyword_terms(chapter_directive or {})
        if not reasoning and not chapter_terms:
            return combined

        priority_order = [
            s.strip()
            for s in str(reasoning.get("冲突裁决") or "").split(">")
            if s.strip()
        ]
        priority_map = {name: idx for idx, name in enumerate(priority_order)}

        genre_label = reasoning.get("题材", "")
        ranked_priorities = sorted(priority_map.values())
        max_priority = max(ranked_priorities) if ranked_priorities else 0
        max_chapter_score = 0
        for row in combined:
            table = str(row.get("_table") or "")
            row["_priority_rank"] = priority_map.get(table, 999)
            row["_reasoning_rule"] = genre_label
            row["_chapter_keyword_score"] = self._chapter_keyword_score(row, chapter_terms)
            max_chapter_score = max(max_chapter_score, int(row.get("_chapter_keyword_score") or 0))

        for row in combined:
            row["_combined_rank_score"] = self._combined_rank_score(
                int(row.get("_priority_rank") or 999),
                int(row.get("_chapter_keyword_score") or 0),
                max_priority=max_priority,
                max_chapter_score=max_chapter_score,
                has_reasoning=bool(priority_map),
                has_chapter_terms=bool(chapter_terms),
            )

        combined.sort(
            key=lambda r: (
                -float(r.get("_combined_rank_score") or 0.0),
                r["_priority_rank"],
                -int(r.get("_chapter_keyword_score") or 0),
            )
        )
        return combined

    def _combined_rank_score(
        self,
        priority_rank: int,
        chapter_score: int,
        *,
        max_priority: int,
        max_chapter_score: int,
        has_reasoning: bool,
        has_chapter_terms: bool,
    ) -> float:
        priority_component = 0.0
        if has_reasoning:
            if priority_rank >= 999:
                priority_component = 0.0
            elif max_priority <= 0:
                priority_component = 1.0
            else:
                priority_component = 1.0 - (priority_rank / float(max_priority + 1))

        chapter_component = 0.0
        if has_chapter_terms and max_chapter_score > 0:
            chapter_component = chapter_score / float(max_chapter_score)

        if has_reasoning and has_chapter_terms:
            return round((priority_component * 0.4) + (chapter_component * 0.6), 6)
        if has_chapter_terms:
            return round(chapter_component, 6)
        return round(priority_component, 6)

    def _chapter_keyword_terms(self, chapter_directive: Dict[str, Any]) -> List[str]:
        raw_items: List[str] = []
        for key in ("goal", "strand", "antagonist_tier"):
            value = str(chapter_directive.get(key) or "").strip()
            if value:
                raw_items.append(value)
        for key in ("key_entities", "must_cover_nodes"):
            raw_items.extend(str(item or "") for item in chapter_directive.get(key) or [])

        terms: List[str] = []
        for item in raw_items:
            for token in _TEXT_TOKEN_RE.split(item):
                token = token.strip().lower()
                if len(token) >= 2 and token not in terms:
                    terms.append(token)
        return terms

    def _chapter_keyword_score(self, row: Dict[str, Any], terms: List[str]) -> int:
        if not terms:
            return 0
        haystack = " ".join(
            str(row.get(field) or "")
            for field in ("关键词", "意图与同义词", "适用场景", "核心摘要", "详细展开")
        ).lower()
        return sum(1 for term in terms if term and term in haystack)

    def _rank_anti_patterns(
        self,
        reasoning: Dict[str, Any],
        anti_patterns: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Sort *anti_patterns* by 毒点权重 and append reasoning 反模式."""
        if not reasoning:
            return anti_patterns

        weight_order = [
            s.strip()
            for s in str(reasoning.get("毒点权重") or "").split(">")
            if s.strip()
        ]

        def _sort_key(item: Dict[str, Any]) -> int:
            text = str(item.get("text") or "")
            for idx, keyword in enumerate(weight_order):
                if keyword in text:
                    return idx
            return len(weight_order)

        sorted_anti = sorted(anti_patterns, key=_sort_key)

        # Append 反模式 entries from reasoning row
        existing_texts = {str(a.get("text") or "") for a in sorted_anti}
        for text in self._split_multi_value(reasoning.get("反模式")):
            if text and text not in existing_texts:
                sorted_anti.append(
                    {"text": text, "source_table": "裁决规则", "source_id": reasoning.get("编号", "")}
                )
                existing_texts.add(text)

        return sorted_anti

    def _build_source_trace_with_reasoning(
        self,
        ranked: List[Dict[str, Any]],
        reasoning: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Build source trace entries enriched with reasoning metadata."""
        inject_target = self._reasoning_inject_target(reasoning)
        trace: List[Dict[str, Any]] = []
        for row in ranked:
            trace.append(
                {
                    "table": row.get("_table", ""),
                    "id": row.get("编号", ""),
                    "summary": row.get("核心摘要", ""),
                    "reasoning_rule": row.get("_reasoning_rule", ""),
                    "priority_rank": row.get("_priority_rank", 999),
                    "chapter_keyword_score": row.get("_chapter_keyword_score", 0),
                    "combined_rank_score": row.get("_combined_rank_score", 0),
                    "inject_target": inject_target,
                }
            )
        return trace

    def _reasoning_inject_target(self, reasoning: Dict[str, Any]) -> str:
        if reasoning:
            explicit = str(reasoning.get("contract注入层") or "").strip()
            if explicit:
                return explicit
        cfg = CSV_CONFIG.get("裁决规则") or {}
        return str(cfg.get("contract_inject") or "")

    def _routing_error(
        self,
        *,
        query: str,
        genre: Optional[str],
        route_rows: List[Dict[str, Any]],
    ) -> StorySystemRoutingError:
        query_text = str(query or "").strip()
        genre_text = str(genre or "").strip()
        if not route_rows:
            detail = "题材与调性推理.csv 没有可用路由行"
        else:
            detail = f"query={query_text!r}, genre={genre_text!r} 未命中任何路由行"
        return StorySystemRoutingError(
            f"无法匹配 story-system 题材路由：{detail}。"
            "不会生成 .story-system contracts。"
            "请使用中文题材/流派（例如：规则怪谈、玄幻、仙侠），"
            "或先在 题材与调性推理.csv 添加路由行。"
        )
