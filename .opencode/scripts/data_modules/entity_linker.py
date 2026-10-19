#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entity Linker - 实体消歧辅助模块 (v5.4)

为 Data Agent 提供实体消歧的辅助功能：
- 置信度判断
- 别名索引管理 (通过 index.db aliases 表)
- 消歧结果记录

v5.1 变更（v5.4 沿用）:
- 别名存储从 state.json 迁移到 index.db aliases 表
- 使用 IndexManager 进行别名读写
- 移除对 state.json 的直接操作
"""

from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from .config import get_config
from .index_manager import IndexManager
from .observability import safe_log_tool_call


@dataclass
class DisambiguationResult:
    """消歧结果"""
    mention: str
    entity_id: Optional[str]
    confidence: float
    candidates: List[str] = field(default_factory=list)
    adopted: bool = False
    warning: Optional[str] = None


class EntityLinker:
    """实体链接器 - 辅助 Data Agent 进行实体消歧 (v5.1 SQLite，v5.4 沿用)"""

    def __init__(self, config=None):
        self.config = config or get_config()
        self._index_manager = IndexManager(self.config)

    # ==================== 别名管理 (v5.1 SQLite，v5.4 沿用) ====================

    def register_alias(self, entity_id: str, alias: str, entity_type: str = "角色") -> bool:
        """注册新别名（v5.1 引入：写入 index.db aliases 表）"""
        if not alias or not entity_id:
            return False
        return self._index_manager.register_alias(alias, entity_id, entity_type)

    def lookup_alias(self, mention: str, entity_type: str = None) -> Optional[str]:
        """查找别名对应的实体 ID（返回**排序后的第一个**匹配，可选按类型过滤）。

        注意：本方法不区分「唯一匹配」与「同类型多匹配」。若需要知道是否
        存在歧义，用 `lookup_alias_detailed()`。

        返回顺序由 `get_entities_by_alias` 的 `ORDER BY` 保证（归档靠后、
        主角优先、tier 排序），因此结果可复现。
        """
        detail = self.lookup_alias_detailed(mention, entity_type=entity_type)
        return detail["entity_id"]

    def lookup_alias_detailed(
        self, mention: str, entity_type: str = None
    ) -> Dict[str, Any]:
        """带歧义标记的别名查询。

        为什么需要它：`aliases` 表的主键是 `(alias, entity_id, entity_type)`，
        允许**同一别名映射多个实体**——跨类型同名是设计支持的
        （如「天云宗」既是地点又是势力）。但**同一类型内**的同名不是正常
        情况，那是真歧义：两个都是「角色」时，到底指谁？

        此前唯一的查询入口 `lookup_alias()` 对这种情况静默取第一条。用户
        从未被告知存在歧义，等于被分配了「tier 排序靠前的那个实体」。
        这与本项目在别处的 fail-closed 取向不一致（`review_pipeline` 对
        形状异常载荷合成 critical 阻断项，理由是「无法判断有没有问题 ≠
        没有问题」）。

        本方法把选择与判断分开：
        - `entity_id`：仍是排序后的第一个（保持既有调用方行为不变）
        - `ambiguous`：同类型命中 > 1 时为 True
        - `candidates`：同类型下的全部候选 ID（多于一个才非空）

        Returns:
            {"entity_id": Optional[str], "ambiguous": bool,
             "candidates": List[str], "entity_type": Optional[str],
             "matches": int}
        """
        entries = self._index_manager.get_entities_by_alias(mention)
        result: Dict[str, Any] = {
            "entity_id": None,
            "ambiguous": False,
            "candidates": [],
            "entity_type": entity_type,
            "matches": len(entries),
        }
        if not entries:
            return result

        if entity_type:
            pool = [e for e in entries if e.get("type") == entity_type]
        else:
            pool = entries

        if not pool:
            return result

        result["entity_id"] = pool[0].get("id")

        # 同类型歧义：把「类型与首选相同」的其他候选列出来
        chosen_type = pool[0].get("type")
        same_type = [e for e in pool if e.get("type") == chosen_type]
        if len(same_type) > 1:
            result["ambiguous"] = True
            result["candidates"] = [
                str(e.get("id")) for e in same_type if e.get("id")
            ]
        return result

    def lookup_alias_all(self, mention: str) -> List[Dict]:
        """查找别名对应的所有实体（一对多）"""
        entries = self._index_manager.get_entities_by_alias(mention)
        return [{"type": e.get("type"), "id": e.get("id")} for e in entries]

    def get_all_aliases(self, entity_id: str, entity_type: str = None) -> List[str]:
        """获取实体的所有别名"""
        return self._index_manager.get_entity_aliases(entity_id)

    # ==================== 置信度判断 ====================

    def evaluate_confidence(self, confidence: float) -> Tuple[str, bool, Optional[str]]:
        """
        评估置信度，返回 (action, adopt, warning)

        - action: "auto" | "warn" | "manual"
        - adopt: 是否采用
        - warning: 警告信息
        """
        if confidence >= self.config.extraction_confidence_high:
            return ("auto", True, None)
        elif confidence >= self.config.extraction_confidence_medium:
            return ("warn", True, f"中置信度匹配 (confidence: {confidence:.2f})")
        else:
            return ("manual", False, f"需人工确认 (confidence: {confidence:.2f})")

    def process_uncertain(
        self,
        mention: str,
        candidates: List[str],
        suggested: str,
        confidence: float,
        context: str = ""
    ) -> DisambiguationResult:
        """
        处理不确定的实体匹配

        返回消歧结果，包含是否采用、警告信息等
        """
        action, adopt, warning = self.evaluate_confidence(confidence)

        result = DisambiguationResult(
            mention=mention,
            entity_id=suggested if adopt else None,
            confidence=confidence,
            candidates=candidates,
            adopted=adopt,
            warning=warning
        )

        return result

    # ==================== 批量处理 ====================

    def process_extraction_result(
        self,
        uncertain_items: List[Dict]
    ) -> Tuple[List[DisambiguationResult], List[str]]:
        """
        处理 AI 提取结果中的 uncertain 项

        返回 (results, warnings)
        """
        results = []
        warnings = []

        for item in uncertain_items:
            result = self.process_uncertain(
                mention=item.get("mention", ""),
                candidates=item.get("candidates", []),
                suggested=item.get("suggested", ""),
                confidence=item.get("confidence", 0.0),
                context=item.get("context", "")
            )
            results.append(result)

            if result.warning:
                warnings.append(f"{result.mention} → {result.entity_id}: {result.warning}")

        return results, warnings

    def register_new_entities(
        self,
        new_entities: List[Dict]
    ) -> List[str]:
        """
        注册新实体的别名 (v5.1 引入，v5.4 沿用)

        返回注册的实体ID列表
        """
        registered = []

        for entity in new_entities:
            entity_id = entity.get("suggested_id") or entity.get("id")
            if not entity_id or entity_id == "NEW":
                continue

            entity_type = entity.get("type", "角色")

            # 注册主名称
            name = entity.get("name", "")
            if name:
                self.register_alias(entity_id, name, entity_type)

            # 注册提及方式
            for mention in entity.get("mentions", []):
                if mention and mention != name:
                    self.register_alias(entity_id, mention, entity_type)

            registered.append(entity_id)

        return registered


# ==================== CLI 接口 ====================

def main():
    import argparse
    import sys
    from .cli_output import print_success, print_error
    from .cli_args import normalize_global_project_root
    from .index_manager import IndexManager

    parser = argparse.ArgumentParser(description="Entity Linker CLI (v5.4 SQLite)")
    parser.add_argument("--project-root", type=str, help="项目根目录")

    subparsers = parser.add_subparsers(dest="command")

    # 注册别名
    register_parser = subparsers.add_parser("register-alias")
    register_parser.add_argument("--entity", required=True, help="实体ID")
    register_parser.add_argument("--alias", required=True, help="别名")
    register_parser.add_argument("--type", default="角色", help="实体类型（默认：角色）")

    # 查找别名
    lookup_parser = subparsers.add_parser("lookup")
    lookup_parser.add_argument("--mention", required=True, help="提及文本")
    lookup_parser.add_argument("--type", help="按类型过滤")

    # 查找所有匹配（一对多）
    lookup_all_parser = subparsers.add_parser("lookup-all")
    lookup_all_parser.add_argument("--mention", required=True, help="提及文本")

    # 列出别名
    list_parser = subparsers.add_parser("list-aliases")
    list_parser.add_argument("--entity", required=True, help="实体ID")
    list_parser.add_argument("--type", help="实体类型")

    argv = normalize_global_project_root(sys.argv[1:])
    args = parser.parse_args(argv)

    # 初始化
    config = None
    if args.project_root:
        # 允许传入“工作区根目录”，统一解析到真正的 book project_root（必须包含 .webnovel/state.json）
        from project_locator import resolve_project_root
        from .config import DataModulesConfig

        resolved_root = resolve_project_root(args.project_root)
        config = DataModulesConfig.from_project_root(resolved_root)

    linker = EntityLinker(config)
    logger = IndexManager(config)
    tool_name = f"entity_linker:{args.command or 'unknown'}"

    def emit_success(data=None, message: str = "ok"):
        print_success(data, message=message)
        safe_log_tool_call(logger, tool_name=tool_name, success=True)

    def emit_error(code: str, message: str, suggestion: str | None = None):
        print_error(code, message, suggestion=suggestion)
        safe_log_tool_call(
            logger,
            tool_name=tool_name,
            success=False,
            error_code=code,
            error_message=message,
        )

    if args.command == "register-alias":
        entity_type = getattr(args, "type", "角色")
        success = linker.register_alias(args.entity, args.alias, entity_type)
        if success:
            emit_success({"entity": args.entity, "alias": args.alias, "type": entity_type}, message="alias_registered")
        else:
            emit_error("ALIAS_EXISTS", "注册失败或已存在")

    elif args.command == "lookup":
        entity_type = getattr(args, "type", None)
        entity_id = linker.lookup_alias(args.mention, entity_type)
        if entity_id:
            emit_success({"mention": args.mention, "entity": entity_id}, message="lookup")
        else:
            emit_error("NOT_FOUND", f"未找到别名: {args.mention}")

    elif args.command == "lookup-all":
        matches = linker.lookup_alias_all(args.mention)
        emit_success(matches, message="lookup_all")

    elif args.command == "list-aliases":
        entity_type = getattr(args, "type", None)
        aliases = linker.get_all_aliases(args.entity, entity_type)
        emit_success(aliases, message="aliases")

    else:
        emit_error("UNKNOWN_COMMAND", "未指定有效命令", suggestion="请查看 --help")


if __name__ == "__main__":
    main()
