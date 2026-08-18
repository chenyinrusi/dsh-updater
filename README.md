# dsh-updater

一键更新 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的独立更新器（纯 Python 标准库，零第三方依赖）。

**解决的核心问题：在 Windows 上更新 dsh version 时崩溃** —— pnpm install 报 `ERR_PNPM_EPERM ... symlink ... operation not permitted`，构建到一半失败。

## 问题背景

DeepSeek Harness 升级（拉取上游 + pnpm install + 重新构建）在 Windows 上容易失败，原因有两个：

1. **pnpm 需要符号链接权限**：pnpm install 要在 node_modules 里创建数千个符号链接/junction。Windows 创建符号链接需要 SeCreateSymbolicLinkPrivilege（管理员），或开启开发者模式（需重启才生效）。普通权限下会报 EPERM，导致更新崩溃。
2. **DSH 进程自身受限**：在 DSH 内部运行的子进程连 junction 都无法创建，所以 DSH 无法“自己更新自己”——依赖安装必须在外部终端完成。

dsh-updater 把整个更新流程搬到**外部终端**执行，并自动申请管理员权限，从而稳定完成更新。

## 特性

- 一条命令完成：git fetch → 合并上游 → pnpm install → pnpm run build → typecheck/测试
- 自动 UAC 提权（符号链接权限），也可 --no-elevate 跳过
- 符号链接能力预检：权限不足时提前拦下并给出解决办法，而不是装到一半崩溃
- 保留本地未提交改动（merge 不覆盖）；冲突时自动回滚并列出冲突文件
- 全程日志 update-dsh.log，失败时给出阶段与建议
- 纯 Python 标准库，零第三方依赖

## 环境要求

| 依赖 | 版本 |
| --- | --- |
| Windows | 10 / 11 |
| Python | 3.9+（运行更新器本体） |
| Node.js | ^22.19 或 >=24 |
| Git | 任意可用版本 |
| pnpm | 任意（找不到时自动用 %LOCALAPPDATA%/dsh-bin） |

## 快速开始

1. 把 update-dsh.py 和 update-dsh.bat 放到你的 DeepSeek Harness 仓库根目录（与 start-dsh.bat 同级）。
2. **双击 update-dsh.bat**，UAC 提示时点「是」。
3. 更新器自动完成：合并上游 → 装依赖 → 构建 → 类型检查。
4. 完成后关闭旧 “dsh web server” 窗口，重新双击 start-dsh.bat（或直接运行 update-dsh.bat --restart）。

## 参数

| 参数 | 说明 |
| --- | --- |
| --check | 只检查现状（上游提交数/工具链/符号链接能力），不改任何东西 |
| --clean | 安装前删除 node_modules（修复残缺依赖） |
| --no-merge | 跳过 git merge，只做 install/build/测试 |
| --branch 名 | 合并指定分支（默认 origin/master） |
| --test | 额外运行单元测试 pnpm run test |
| --no-typecheck | 跳过 pnpm run typecheck |
| --restart | 成功后自动重启 3080 服务器（会短暂断开当前 DSH 会话） |
| --force | 符号链接检查失败也继续（不推荐） |
| --no-elevate | 不自动申请管理员权限 |
| --version | 显示更新器版本 |

命令行方式：python update-dsh.py --check 等。

## 更新流程

```
预检（符号链接能力 / 工具链 / 服务器状态）
  → git fetch origin
  → git merge origin/master（有冲突自动回滚并报告）
  → pnpm install（CI 模式，避免交互确认）
  → pnpm run build
  → pnpm run typecheck（可选 --test 加单元测试）
  → 产物与版本验证，提示重启服务器
```

## 故障排查

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| EPERM ... symlink / 符号链接检查失败 | 无符号链接权限 | 用 update-dsh.bat（自动提权）运行；或重启电脑让开发者模式生效；或管理员 PowerShell 里 python update-dsh.py |
| 找不到 pnpm | pnpm 不在 PATH | 更新器自动查找 %LOCALAPPDATA%/dsh-bin；或 npm install -g pnpm |
| 合并冲突 | 上游与本地改动冲突 | 更新器已自动 git merge --abort，按提示手动处理 |
| ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY | pnpm 需确认清空 node_modules | 已内置 CI=true 规避 |

## 许可

MIT © 2026 chenyinrusi
