---
name: webnovel-publish
description: 将小说章节自动发布到国内主流小说平台（番茄等）。触发条件："发布小说"、"发布章节"、"上传到番茄"、"自动发布"、"清理草稿"、"发布草稿"。
compatibility: opencode
---

# 小说自动发布

## 目标

将已完成小说章节自动发布到目标平台。首次需手动扫码登录，后续全自动运行。

## 支持平台

| 平台 | 标识 | 认证方式 | 状态 |
|------|------|---------|------|
| 番茄小说 | fanqie | 扫码登录（一次） | 可用 |
| 七猫小说 | qimao | 扫码登录（一次） | 实验性 |

## 环境设置

```bash
export SCRIPTS_DIR="${PWD}/.opencode/scripts"
test -d "${SCRIPTS_DIR}" || { echo "错误: 未找到 ${SCRIPTS_DIR}，请确保当前目录是 webnovel-writer 仓库根目录"; exit 1; }

export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PWD}" where)"
test -n "$PROJECT_ROOT" && test -f "${PROJECT_ROOT}/.webnovel/state.json" || { echo "错误: PROJECT_ROOT 解析失败，请用 --project-root 显式指定"; exit 1; }
echo "项目路径: ${PROJECT_ROOT}"

python -c "import playwright" 2>/dev/null || { echo "请先安装: pip install playwright && playwright install chromium"; exit 1; }
```

## 交互式流程

### Step 0：确认参数

若用户指令模糊，按优先级询问：

| 优先级 | 参数 | 何时必须询问 | 选项 |
|--------|------|-------------|------|
| 1 | 平台 | 未指定平台 | fanqie / qimao |
| 2 | 操作 | 未指定操作 | 首次配置 / 创建新书 / 上传章节 |
| 3 | book_id | 上传时未绑定书籍 | 输入 ID 或书名 |
| 4 | 范围 | 未指定章节范围 | 全部 / 指定范围 |
| 5 | 模式 | 未指定 | 当前仅支持草稿（draft） |

**可直接执行**：
- "发布到番茄" → 平台明确，进入菜单流程
- "上传第 90-95 章" → 操作和范围明确
- "发布全部到番茄" → 平台和范围明确

### Step 1：首次配置（登录）

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish setup-auth --platform fanqie
```

弹出浏览器窗口，扫码登录后自动保存认证状态。后续无需重复。  
认证存储在 `~/.webnovel-publish/browser_data/{platform}/`。

### Step 2：查看书单

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish list-books --platform fanqie
```

确认目标书籍是否存在，获取 book_id。

### Step 3：创建新书

```bash
# 交互式（会询问书名、简介）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish create-book \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}"

# 非交互式（跳过确认）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish create-book \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}" \
  --yes \
  --abstract "自定义简介"
```

自动从 `state.json` 读取：书名、题材、简介、主角名。  
创建成功后自动绑定 book_id 到项目（写入 `.webnovel/publish_config.json`）。

### Step 4：上传章节

```bash
# 上传全部（使用项目绑定的 book_id）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish upload \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}"

# 指定 book_id 或书名（首次上传自动绑定）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish upload \
  --platform fanqie \
  --book 7631548393474493502 \
  --project-root "${PROJECT_ROOT}"

# 指定范围
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish upload \
  --platform fanqie \
  --range 90-95 \
  --project-root "${PROJECT_ROOT}"

# 跳过交互确认（脚本/CI 环境）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish upload \
  --platform fanqie \
  --yes \
  --project-root "${PROJECT_ROOT}"
```

已上传章节自动跳过，支持断点续传。  
上传日志存储在 `~/.webnovel-publish/upload_log/{project_name}/{platform}_{book_id}.json`。

### Step 5：草稿管理（新增功能）

#### 5.1 清理重复草稿

```bash
# 预览模式（不实际删除，查看哪些章节有重复）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish clean-drafts \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}" \
  --dry-run

# 清理重复草稿（同章节号保留最新修改时间的，删除旧版本）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish clean-drafts \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}"

# 跳过确认
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish clean-drafts \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}" \
  -y
```

**适用场景**：多次上传同一章导致草稿箱有重复，想保留最新版本删除旧版本。

#### 5.2 清空草稿箱

```bash
# 清空草稿箱所有章节（跳过已发布的）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish clear-drafts \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}"
```

**适用场景**：想清空草稿箱重新开始，但不删除已正式发布的章节。

#### 5.3 删除全部草稿

```bash
# 删除草稿箱全部章节（包括已发布的，需二次确认 yes）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish delete-drafts \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}"

# 跳过确认（危险操作）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish delete-drafts \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}" \
  -y
```

**危险操作**：会删除所有草稿，包括已发布状态的章节，并清除上传记录。

#### 5.4 发布草稿

```bash
# 发布草稿箱中的章节（将草稿改为已发布状态）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish publish-drafts \
  --platform fanqie \
  --range 1-10 \
  --project-root "${PROJECT_ROOT}"

# 发布全部草稿
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" publish publish-drafts \
  --platform fanqie \
  --project-root "${PROJECT_ROOT}"
```

**适用场景**：章节已在草稿箱，想批量发布到正式站。  
**注意**：发布时会用草稿箱现有的 item_id，标题和内容从本地文件读取。

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--platform` | 平台标识 | fanqie |
| `--book` | 书籍 ID 或书名 | 从项目绑定读取 |
| `--range` | 章节范围（1-50 / 1,3,5 / all） | all |
| `--mode` | 发布模式（当前仅 draft） | draft |
| `--yes` / `-y` | 跳过交互确认 | — |
| `--dry-run` | 预览模式（仅 clean-drafts 支持） | — |
| `--abstract` | 书籍简介（create-book） | 自动生成 |
| `--project-root` | 书项目根目录 | 自动探测 |

## 子命令列表

| 命令 | 功能 | 典型用法 |
|------|------|---------|
| `setup-auth` | 扫码登录平台 | 首次使用 |
| `list-books` | 列出已有书籍 | 查询 book_id |
| `create-book` | 创建新书 | 自动从项目元数据生成 |
| `upload` | 上传章节到草稿箱 | 日常更新 |
| `clean-drafts` | 清理重复草稿 | 多次上传同章后去重 |
| `clear-drafts` | 清空草稿箱（跳过已发布） | 重新开始前清理 |
| `delete-drafts` | 删除全部草稿（危险） | 完全清空草稿箱 |
| `publish-drafts` | 发布草稿到正式站 | 批量上架 |

## 自动绑定机制

- 首次 `create-book` 成功后自动绑定 book_id 到 `.webnovel/publish_config.json`
- 首次 `upload --book <id>` 成功后自动持久化绑定
- 后续 `upload` 无需传 `--book`，自动从绑定读取
- 多书项目：用 `--book` 指定目标即可

## 卸载与重置

```bash
# 清除认证状态
rm -rf ~/.webnovel-publish/browser_data/

# 清除上传日志
rm -rf ~/.webnovel-publish/upload_log/

# 清除项目绑定
rm "${PROJECT_ROOT}/.webnovel/publish_config.json"
```

## 充分性闸门

- [ ] Playwright 已安装且 Chromium 可用
- [ ] 平台认证状态有效
- [ ] 目标书籍 book_id 已知或在书单中
- [ ] 上传日志一致（不会重复上传）
- [ ] 章节文件存在且非空

## 常见问题

| 问题 | 解决 |
|------|------|
| 登录超时 | 重新运行 setup-auth，3 分钟内扫码 |
| 认证过期 | 重新运行 setup-auth（认证目录存在不代表有效） |
| 浏览器不弹 | 检查显示器配置，确保非 headless |
| Playwright 未安装 | `pip install playwright && playwright install chromium` |
| 项目未绑定 | 传 `--book <id>` 或先 `create-book` |
| book_id 不匹配 | 检查是否误用了其他书的 ID |
| 非交互环境报错 | 添加 `--yes` 跳过确认 |
| 草稿箱有重复章节 | 运行 `clean-drafts --dry-run` 预览，再去掉 `--dry-run` 执行 |
| 想重新上传所有章节 | 先 `delete-drafts` 删除草稿和上传记录，再重新 `upload` |
| 草稿上传了但想发布 | 运行 `publish-drafts --range <范围>` |
