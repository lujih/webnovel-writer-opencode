"""章节文件名编号统一工具。

将 `第060章-标题.md` 或 `第060章.md` 统一为 `第0060章-标题.md` / `第0060章.md`（四位数编号）。
也处理 `chapter_60.json` → `chapter_0060.json` 格式。

用法:
  python chapter_rename.py --project-root /path/to/project --dry-run
  python chapter_rename.py --project-root /path/to/project
"""
import argparse
import re
import sys
from pathlib import Path


# 匹配 "第060章-标题.md" 或 "第060章.md"（无标题）
_RE_CHINESE = re.compile(r"^第(\d{1,3})章(-.+)?(\.md)$")
# 匹配 "chapter_60.json" 或 "chapter_60.md" 格式
_RE_CHAPTER = re.compile(r"^chapter_(\d{1,3})\.(json|md)$")
# 识别"像章节文件但编号位数超出预期"的畸形名，用于报警而不是静默跳过
_RE_CHINESE_ANY = re.compile(r"^第(\d+)章(-.+)?(\.md)$")
_RE_CHAPTER_ANY = re.compile(r"^chapter_(\d+)\.(json|md)$")

#: 标准编号宽度
_PAD_WIDTH = 4

#: 最近一次 `scan_chapters()` / `scan_story_system()` 产生的警告。
#:
#: 用模块级列表是因为这两个函数的签名被既有调用方（CLI、测试）依赖，
#: 加返回值会破坏兼容。CLI 在扫描后读它并打印。
last_warnings: list[str] = []


def _pad_num(num_str: str, width: int = 4) -> str:
    """将数字字符串补齐到指定位数。"""
    return num_str.zfill(width)


def scan_chapters(story_dir: Path, recursive: bool = False) -> list[tuple[Path, Path]]:
    """扫描需要重命名的章节文件。返回 [(旧路径, 新路径), ...]

    ``story_dir`` 应当传**正文目录**（``<project>/正文``）——那是
    `chapter_paths.find_chapter_file()` 的权威位置。传 ``story/`` 是错的：
    那里面是 markdown 投影（世界观状态.md / 伏笔面板.md / 章节摘要.md），
    没有章节正文。

    畸形编号（位数超出 `_PAD_WIDTH`）不再静默跳过，而是记进 `last_warnings`。
    """
    renames: list[tuple[Path, Path]] = []
    last_warnings.clear()

    if not story_dir.is_dir():
        return renames

    pattern = "**/*" if recursive else "*"
    for f in sorted(story_dir.glob(pattern)):
        if not f.is_file():
            continue

        m = _RE_CHINESE.match(f.name)
        if m:
            num, title_part, ext = m.groups()
            if len(num) < _PAD_WIDTH:
                new_name = f"第{_pad_num(num)}章{title_part or ''}{ext}"
                renames.append((f, f.parent / new_name))
            continue

        m = _RE_CHAPTER.match(f.name)
        if m:
            num, ext = m.groups()
            if len(num) < _PAD_WIDTH:
                new_name = f"chapter_{_pad_num(num)}.{ext}"
                renames.append((f, f.parent / new_name))
            continue

        # 走到这里说明没被前两个正则命中。再宽松匹配一次：若它"像"章节文件
        # 但编号位数超标（如 第12345章.md），报警而不是当作无关文件。
        loose = _RE_CHINESE_ANY.match(f.name) or _RE_CHAPTER_ANY.match(f.name)
        if loose:
            last_warnings.append(
                f"{f.name}: 编号位数超过 {_PAD_WIDTH} 位，本工具不处理——"
                f"请手工确认这是否是笔误"
            )

    return renames


def scan_story_system(ss_dir: Path) -> list[tuple[Path, Path]]:
    """扫描 .story-system/chapters/ 下需要重命名的文件。"""
    renames = []
    chapters_dir = ss_dir / "chapters"

    if not chapters_dir.is_dir():
        return renames

    for f in sorted(chapters_dir.iterdir()):
        if not f.is_file():
            continue

        m = _RE_CHAPTER.match(f.name)
        if m:
            num, ext = m.groups()
            if len(num) < 4:
                new_name = f"chapter_{_pad_num(num)}.{ext}"
                renames.append((f, f.parent / new_name))

    return renames


def apply_renames(renames: list[tuple[Path, Path]], dry_run: bool = True) -> int:
    """执行重命名。返回成功数。"""
    count = 0
    for old, new in renames:
        if dry_run:
            print(f"  [DRY-RUN] {old.name} → {new.name}")
        else:
            if new.exists():
                print(f"  [SKIP] 目标已存在: {new.name}")
                continue
            old.rename(new)
            print(f"  [OK] {old.name} → {new.name}")
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="章节文件名编号统一工具")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录（如按卷组织）")
    args = parser.parse_args()

    root = Path(args.project_root)
    # 正文目录是 正文/，不是 story/。
    #
    # story/ 是 markdown **投影**目录（state_projection_renderer 输出
    # 世界观状态.md / 伏笔面板.md / 章节摘要.md 等），里面没有章节正文。
    # chapter_paths.find_chapter_file() 的权威位置同样是 project_root / "正文"。
    # 此前这里写 root / "story"，导致本工具永远找不到用户的章节文件，反而会
    # 把 story/ 里形如 chapter_1.md 的投影误改名。
    body_dir = root / "正文"
    ss_dir = root / ".story-system"

    print(f"项目: {root}")
    print(f"正文: {body_dir}")
    print(f"模式: {'预览' if args.dry_run else '执行'}\n")

    if not body_dir.is_dir():
        print(
            f"ERROR: 未找到正文目录（{body_dir}）。\n"
            f"   本工具只处理 正文/ 下的章节文件。若你的项目用别的目录名，"
            f"请确认 --project-root 指向的是书项目根。",
            file=sys.stderr,
        )
        return 1

    if not args.dry_run:
        print("⚠ 注意：重命名操作非原子性。中途失败会导致部分文件已重命名。")
        print("  建议先用 --dry-run 预览，确认无误后再执行。\n")

    all_renames = []

    # 正文目录
    body_renames = scan_chapters(body_dir, recursive=args.recursive)
    if body_renames:
        print(f"正文/ 目录 ({len(body_renames)} 个文件):")
        all_renames.extend(body_renames)
        apply_renames(body_renames, args.dry_run)
    else:
        print("正文/ 目录: 无需重命名")

    for warning in last_warnings:
        print(f"  ⚠ {warning}", file=sys.stderr)

    print()

    # .story-system/chapters/ 目录
    ss_renames = scan_story_system(ss_dir)
    if ss_renames:
        print(f".story-system/chapters/ 目录 ({len(ss_renames)} 个文件):")
        all_renames.extend(ss_renames)
        apply_renames(ss_renames, args.dry_run)
    else:
        print(".story-system/chapters/ 目录: 无需重命名")

    print(f"\n{'将' if args.dry_run else '已'}处理 {len(all_renames)} 个文件")
    if args.dry_run and all_renames:
        print("去掉 --dry-run 参数执行实际重命名")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
