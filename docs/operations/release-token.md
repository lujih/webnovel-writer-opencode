# 自动发版开关：`SEMVER_PUSH_TOKEN`

面向维护者。讲清这个 secret 是什么、为什么需要、怎么建、怎么验证、怎么撤销。

## 它是什么

**不是产品，也不是要申请的东西**——只是一个 GitHub Actions Secret 的**名字**，
由 `.github/workflows/semver.yml` 引用：

```yaml
TOKEN: ${{ secrets.SEMVER_PUSH_TOKEN }}
```

值必须是你在 GitHub 上创建的 **Personal Access Token（PAT）**，且带
`contents: write` 权限。

## 为什么需要它

`semver.yml` 负责创建 tag，`manifest.yml`（建 GitHub Release）与
`npm-publish.yml`（发 npm）靠 **tag push 事件**触发。而 GitHub 有一条防递归
规则：

> 用 Actions 内置的 `GITHUB_TOKEN` 推送 tag，**不会触发其它 workflow**。

实测（v3.1.1）：tag 建出来了、零个 tag 触发的 run、npm 停在旧版本。
换成你自己的 PAT 后，这次推送被当作「真人操作」，下游正常触发。

**为什么没配就干脆不建 tag**：留一个「tag 有、npm 没发」的悬空版本会让
`npm-publish.yml` 的非 tag 路径直接 `exit 1`（它要求 npm 最新版与 git tag
同 base），把静默问题变成下一次必然报错的阻塞。宁可这次不动。

## 怎么建

### 方案 A：Fine-grained PAT（推荐，权限最小）

1. <https://github.com/settings/tokens?type=beta> → **Generate new token**
2. Token name：`webnovel-writer-semver`
3. Expiration：**90 天**或 1 年（到期续，比永久安全）
4. **Repository access** → **Only select repositories** → 只勾 `webnovel-writer-opencode`
5. **Repository permissions** 只开一项：
   - **Contents → Read and write**
   - Metadata 会自动带上只读，其余全部 `No access`
6. Generate → 复制 `github_pat_...`（关掉页面就看不到）

### 方案 B：Classic PAT（快，权限范围大）

1. <https://github.com/settings/tokens/new>
2. Note：`webnovel-writer-semver`，Expiration 同上
3. Scopes 只勾 **`public_repo`**（本仓库公开，够写代码/推 tag；不要勾整个 `repo`）
4. Generate → 复制 `ghp_...`

## 怎么配进仓库

1. <https://github.com/lujih/webnovel-writer-opencode/settings/secrets/actions>
2. **New repository secret**
3. **Name** 必须逐字为 `SEMVER_PUSH_TOKEN`
4. **Secret** 粘贴 token
5. Add secret

## 怎么验证生效

推一个 `fix:` 或 `feat:` 提交，看 `Semver Tag` 的日志：

```
SEMVER_PUSH_TOKEN 已配置 → 可自动建 tag 并发版。
```

随后应依次出现：新 tag → `Version & Release` 建 Release → `npm-publish` 发新版。
用这三条交叉核对一致性：

```bash
git describe --tags --abbrev=0          # 最近 tag
npm view @cszx/webnovel-writer-opencode version   # npm 最新版
git show origin/master:manifest.json | grep '"tag"'
```

三者应指向同一个版本号。

## 没配 / 临时撤销时的行为

`semver.yml` 会**跳过建 tag** 并打 `::warning::`，step summary 写明
「推算出的是哪个版本、为什么没建、怎么启用」。此时发版回到手动：

```bash
git tag -a v3.2.0 -F release-notes.md
git push origin v3.2.0
```

撤销：删掉该 secret（Settings → Secrets），并在
<https://github.com/settings/tokens> 里 Revoke 那个 token。不影响其它任何流程
——`grep -rn SEMVER_PUSH_TOKEN .github/` 只有 `semver.yml` 一个文件用到它。

## 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `Semver Tag` 解析阶段即失败、没走任何 step | `secrets` 被写进了 step 级 `if:`（secrets 不可用在该求值阶段） | 已在 workflow 里改成「先读进 env、再输出 step output」；若手动改回去会复发 |
| tag 建了但 npm 没发 | 用的是 `GITHUB_TOKEN` 而非 PAT | 检查 secret 是否叫对名字、是否真的配在**本仓库** |
| `npm-publish` 报「npm 最新版与 tag 不在同一 base」 | 存在悬空 tag（tag 有、npm 没发） | 删掉悬空 tag：`git push origin --delete <tag>` |
| `npm-publish` 报 `EPUBLISHCONFLICT` | 目标版本已存在于 npm | 换版本号；勿靠重跑解决 |
