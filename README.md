# dsh-updater

一键更新 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的独立更新器（纯 Python 标准库，零第三方依赖）。

**解决的核心问题：在 Windows 上更新 dsh version 时崩溃** —— `pnpm install` 报 `ERR_PNPM_EPERM ... symlink ... operation not permitted`，构建到一半失败。

## 问题背景

DeepSeek Harness 升级（拉取上游 + `pnpm install` + 重新构建）在 Windows 上容易失败，原因有两个：

1. **pnpm 需要符号链接权限**：`pnpm install` 要在 `node_modules` 里创建数千个符号链接/junction。Windows 创建符号链接需要 `SeCreateSymbolicLinkPrivilege`（管理员），或开启开发者模式（需重启才生效）。普通权限下会报 `EPERM`，导致更新崩溃。
2. **DSH 进程自身受限**：在 DSH 内部运行的子进程连 junction 都无法创建，所以 DSH 无法“自己更新自己”——依赖安装必须在外部终端完成。

`dsh-updater` 把整个更新流程搬到**外部终端**执行，并自动定位/提权，从而稳定完成更新。

## 安装

两种方式任选：

### 方式 A：pip 安装（推荐）

```bash
pip install dsh-updater
```

然后从任意目录运行（自动从当前目录向上定位 DeepSeek Harness 仓库）：

```bash
dsh-updater --check      # 先看现状
dsh-updater              # 完整更新
dsh-updater --elevate    # Windows 下以管理员权限运行（符号链接权限）
```

### 方式 B：仓库本地版

把 `update-dsh.py`、`update-dsh.bat`、`dsh_updater/` 放进 DeepSeek Harness 仓库根目录（与 `start-dsh.bat` 同级），然后：

```bash
python update-dsh.py --check
```

或直接**双击 `update-dsh.bat`**（自动 UAC 提权）。

## 参数

| 参数 | 说明 |
| --- | --- |
| `--check` | 只检查现状（上游提交数/工具链/符号链接能力），不改任何东西 |
| `--repo 路径` | 指定 DeepSeek Harness 仓库根目录（默认自动定位） |
| `--elevate` | Windows 下以管理员权限重新启动自身 |
| `--clean` | 安装前删除 node_modules（修复残缺依赖） |
| `--no-merge` | 跳过 git merge，只做 install/build/测试 |
| `--branch 名` | 合并指定分支（默认 `origin/master`） |
| `--test` | 额外运行单元测试 `pnpm run test` |
| `--no-typecheck` | 跳过 `pnpm run typecheck` |
| `--restart` | 成功后自动重启 3080 服务器（会短暂断开当前 DSH 会话） |
| `--force` | 符号链接检查失败也继续（不推荐） |
| `--version` | 显示更新器版本 |

## 更新流程

```
定位仓库 → 预检（符号链接能力 / 工具链 / 服务器状态）
  → git fetch origin
  → git merge origin/master（有冲突自动回滚并报告）
  → pnpm install（CI 模式，避免交互确认）
  → pnpm run build
  → pnpm run typecheck（可选 --test 加单元测试）
  → 产物与版本验证，提示重启服务器
```

## 环境要求

| 依赖 | 版本 |
| --- | --- |
| Windows | 10 / 11 |
| Python | 3.9+ |
| Node.js | ^22.19 或 >=24 |
| Git | 任意可用版本 |
| pnpm | 任意（找不到时自动用 `%LOCALAPPDATA%/dsh-bin`） |

## 故障排查

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `EPERM ... symlink` / 符号链接检查失败 | 无符号链接权限 | 用 `update-dsh.bat`（自动提权）或 `dsh-updater --elevate` 运行；或重启电脑让开发者模式生效 |
| 找不到 pnpm | pnpm 不在 PATH | 更新器自动查找 `%LOCALAPPDATA%/dsh-bin`；或 `npm install -g pnpm` |
| 找不到仓库 | 不在仓库目录运行 | `cd` 到仓库目录，或 `--repo <仓库根目录>` |
| 合并冲突 | 上游与本地改动冲突 | 更新器已自动 `git merge --abort`，按提示手动处理 |
| `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY` | pnpm 需确认清空 node_modules | 已内置 `CI=true` 规避 |

## 许可

MIT © 2026 chenyinrusi
