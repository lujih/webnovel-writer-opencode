#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`entities_v3` 形状归一 — single source of truth.

state.json 里的 `entities_v3` 有两种历史形状：

    嵌套（NESTED，权威形状）: {"角色": {"xiaoyan": {...}}, "地点": {...}, ...}
    扁平（FLAT，旧 rebuild 产物）: {"xiaoyan": {...}, "xiaoyao": {...}, ...}

嵌套是权威的：`state_manager.add_entity()`、`sql_state_manager.
export_to_entities_v3_format()`、`migrate_state_to_sqlite.py` 三处生产/消费方
都按 `entities_v3[entity_type][entity_id]` 读写，`ENTITY_TYPES` 那五个中文
类型名就是第一层键。

扁平是 `ssot_enforcer.rebuild_state_json()` 早前直接
`state.setdefault("entities_v3", {})[eid] = {...}` 写出来的意外产物。它造成
两个问题：

1. `verify_consistency()` 拿两边的**键集**比较，嵌套态的第一层键是类型名、
   扁平态是实体 id，于是恒不相等 —— 健康项目被判漂移，而文档给出的补救
   办法正是 `ssot rebuild`，偏偏 rebuild 写出的是另一种形状。
2. `sql_state_manager` 迁移按 `for entity_type, entities in entities_v3.items()`
   读，扁平态会把实体 id 当类型名，写进 index.db 后回读全错。

本模块只做形状判断与归一，不落盘、不依赖 state_manager（避免循环导入）。
写入方（rebuild、verify）读入任意形状后统一转嵌套；读取方（renderer、
settler、migrate）用 `flatten_entities()` 拿扁平的 id → 实体视图，从而两种
形状都能正确工作、也为将来读到自己写出的嵌套态做好准备。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

__all__ = [
    "ENTITY_TYPES",
    "normalize_entities",
    "flatten_entities",
    "coerce_entity_type",
    "detect_shape",
]

# 与 `state_manager.StateManager.ENTITY_TYPES` 保持一致的五个实体类型。
# 这里复制一份而不是 import，是为了让本模块能被 ssot_enforcer /
# state_projection_renderer / observer_settler 引用而不引入 state_manager
# 的重量级依赖链（state_manager 会 import sqlite3、filelock 等）。
ENTITY_TYPES = ("角色", "地点", "物品", "势力", "招式")

# 判定扁平实体"看起来像实体记录"的标志键。实体 id 本身无从反推类型，
# 因此扁平态归一必须依赖记录内的 entity_type / type 字段。
_ENTITY_TYPE_KEYS = ("entity_type", "type")
_DEFAULT_TYPE = "角色"


def detect_shape(entities: Any) -> str:
    """返回 ``"nested"`` / ``"flat"`` / ``"empty"`` / ``"invalid"``。

    判定规则：空 → empty；顶层值里任一键属于 `ENTITY_TYPES` → nested；
    其余非空 dict → flat。混合形状按 nested 处理（nested 优先，因为它是
    权威形状，混合态本身就是需要被重建修掉的脏数据）。
    """
    if not isinstance(entities, dict):
        return "invalid"
    if not entities:
        return "empty"
    if any(key in ENTITY_TYPES for key in entities):
        return "nested"
    return "flat"


def coerce_entity_type(value: Any) -> str:
    """把实体记录里的类型字段收敛到 `ENTITY_TYPES` 之一。"""
    text = str(value or "").strip()
    return text if text in ENTITY_TYPES else _DEFAULT_TYPE


def flatten_entities(entities: Any) -> Dict[str, Dict[str, Any]]:
    """把任意形状的 `entities_v3` 视图转成 ``{entity_id: record}``。

    嵌套态下类型名即记录的 entity_type，回填进记录里（不覆盖已有字段）；
    扁平态原样返回实体记录。非 dict 输入、非 dict 记录一律跳过——renderer
    早前就为 legacy 脏值做过同样的防御，这里统一收口。
    """
    if not isinstance(entities, dict):
        return {}

    shape = detect_shape(entities)
    flat: Dict[str, Dict[str, Any]] = {}
    if shape in ("empty", "invalid"):
        return flat

    if shape == "nested":
        for entity_type, bucket in entities.items():
            if not isinstance(bucket, dict):
                continue
            type_name = coerce_entity_type(entity_type)
            for eid, record in bucket.items():
                if not isinstance(record, dict):
                    continue
                if not any(k in record for k in _ENTITY_TYPE_KEYS):
                    record = {**record, "entity_type": type_name}
                flat[str(eid)] = record
        return flat

    # flat：记录里必须自己带类型，缺省归到角色（与原 add_entity 的兜底一致）
    for eid, record in entities.items():
        if not isinstance(record, dict):
            continue
        if not any(k in record for k in _ENTITY_TYPE_KEYS):
            record = {**record, "entity_type": _DEFAULT_TYPE}
        flat[str(eid)] = record
    return flat


def normalize_entities(
    entities: Any,
) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], bool]:
    """把任意形状归一为嵌套态，返回 ``(nested, migrated)``。

    ``migrated`` 为 True 表示输入是扁平（或混合）形状、结果已改写——调用方
    据此决定要不要提示用户「重建产物已修正」。传入空/非法输入时返回带五个
    空类型桶的嵌套骨架，与 `_empty_state()` 的形状一致。
    """
    skeleton: Dict[str, Dict[str, Dict[str, Any]]] = {t: {} for t in ENTITY_TYPES}
    if not isinstance(entities, dict):
        return skeleton, bool(entities)

    shape = detect_shape(entities)
    if shape in ("empty", "invalid"):
        return skeleton, False

    migrated = shape == "flat"
    nested: Dict[str, Dict[str, Dict[str, Any]]] = {t: {} for t in ENTITY_TYPES}
    if shape == "nested":
        for entity_type, bucket in entities.items():
            if not isinstance(bucket, dict):
                continue
            type_name = coerce_entity_type(entity_type)
            migrated = migrated or entity_type not in ENTITY_TYPES
            for eid, record in bucket.items():
                if isinstance(record, dict):
                    nested.setdefault(type_name, {})[str(eid)] = record
        return nested, migrated

    for eid, record in flatten_entities(entities).items():
        type_name = coerce_entity_type(
            record.get("entity_type") or record.get("type") or _DEFAULT_TYPE
        )
        nested.setdefault(type_name, {})[eid] = record
    return nested, migrated
