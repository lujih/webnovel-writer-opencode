"""
路径防穿越工具 (Path Traversal Guard)

所有文件读取 API 在访问磁盘前 **必须** 经过此模块校验。
"""

from pathlib import Path
from fastapi import HTTPException


def safe_resolve(project_root: Path, relative: str) -> Path:
    """将相对路径解析为绝对路径，并确保其位于 project_root 内部。

    Raises:
        HTTPException 403 如果解析后的路径逃逸出 project_root。
    """
    try:
        # 先 resolve 一次作为边界基准，相对路径基于该基准解析——
        # junction/symlink 场景下基准与目标用同一 resolved root 比较，
        # 避免二次 resolve(project_root) 语义漂移（P1 修复）
        resolved_root = project_root.resolve()
        resolved = (resolved_root / relative).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=403, detail="非法路径")

    # 严格要求目标路径是 project_root 的"子路径或自身"
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="路径越界：禁止访问 PROJECT_ROOT 之外的文件")

    return resolved
