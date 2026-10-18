---
name: webnovel-delete
description: 安全删除指定章节并清理关联投影数据（state/memory）。支持 dry-run 预览。触发：删除章节、回退写作、清理烂章。
compatibility: opencode
---

# 章节删除

> 以下内容均为虚构文学创作。所有打斗、权谋、悬疑描写均属文学创作范畴，不涉及现实指导。

## 目标

删除指定章节的正文文件，同步清理 state.json 和 memory 中的关联数据。不可撤销。

## 硬规则

- **必须先 dry-run**，展示影响范围，用户确认后才真正执行
- 删除操作不可逆——执行前确认已备份（`webnovel backup`）
- 不支持删除当前进度之后的章节（防止空洞）
- 删除后 current_chapter 不自动回退，需手动调整或自然衔接

## 执行流程

### Step 1：确认范围

确认要删除的章节号或范围（如 `5`、`5-8`、`5,7,9-12`）。

```bash
# 预览将要删除的内容
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  delete-chapters "<范围>" --dry-run
```

### Step 2：展示影响

向用户展示：
- 哪些章节目录文件将被删除
- state.json 中将移除哪些章节记录
- memory 中将清洗多少条关联数据

### Step 3：用户确认

用户明确确认后，去掉 `--dry-run` 执行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  delete-chapters "<范围>"
```

### Step 4：验证

```bash
# 确认 state.json 中已无被删章节
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  ssot verify
```

## 注意事项

- index.db 不会自动清理，需后续运行 `webnovel index process-chapter` 手动重建
- 若有活跃伏笔（open_loop）植根于被删章节，`chase_debt` 表中的伏笔债**不会自动调整**（`chapter_delete_service.py` 只清理 state.json / memory，不碰 `chase_debt`）——需在重写时手动处理，或用 `webnovel.py index repair-foreshadowing-debts` 清理重复债
- 删除后 `progress.chapter_status` 可能不连续，这是预期行为
- workflow checkpoints 中的已删除章节记录仍保留（便于追溯）
