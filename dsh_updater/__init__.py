#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsh-updater — 一键更新 DeepSeek Harness 的独立更新器。

解决的核心问题：Windows 上更新 dsh version 时，pnpm install 因符号链接权限报
ERR_PNPM_EPERM (symlink ... operation not permitted) 导致崩溃；且 DSH 内部进程
无法创建 junction，无法自行更新。本工具在【外部终端】运行并自动定位仓库。

它替你完成:  git fetch -> 合并上游 -> pnpm install -> pnpm run build -> typecheck/测试

用法（两种方式等价）:
  仓库本地版:  把 update-dsh.py / update-dsh.bat / dsh_updater/ 放进仓库根目录,
              然后 python update-dsh.py 或双击 update-dsh.bat
  已安装版:   pip install dsh-updater, 然后 dsh-updater 命令（自动从当前目录向上定位仓库）

参数:
  --check          只检查现状（上游提交数/工具链/符号链接能力），不改任何东西
  --repo 路径      指定 DeepSeek Harness 仓库根目录（默认自动定位）
  --elevate        Windows 下以管理员权限重新启动自身（符号链接权限）
  --clean          安装前删除 node_modules（修复残缺依赖时用）
  --no-merge       跳过合并, 只做 install/build/测试
  --branch X       合并指定分支 (默认 origin/master)
  --test           额外运行单元测试 (pnpm run test)
  --no-typecheck   跳过 typecheck（只 install + build）
  --restart        全部成功后自动重启 3080 服务器（会短暂断开当前 DSH 会话!）
  --force          符号链接检查失败也继续（大概率会在 install 失败）
  --version        显示版本

日志:  <仓库根目录>/update-dsh.log
"""
import argparse
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

UPDATER_VERSION = "1.1.0"
DEFAULT_BRANCH = "origin/master"

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"

REPO = None  # 由 main() 解析后赋值


# ---------- 仓库定位 ----------

def is_harness_root(p):
    try:
        pkg = p / "package.json"
        if not pkg.is_file():
            return False
        text = pkg.read_text(encoding="utf-8", errors="replace")
        return "@deepseek-ai/dsh-root" in text
    except Exception:
        return False


def resolve_repo(explicit):
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not is_harness_root(p):
            fail("preflight", f"指定目录不是 DeepSeek Harness 仓库根目录: {p}")
        return p
    here = Path(__file__).resolve().parent
    if is_harness_root(here):
        return here
    cwd = Path.cwd().resolve()
    for d in [cwd, *cwd.parents]:
        if is_harness_root(d):
            return d
    fail("preflight",
         "找不到 DeepSeek Harness 仓库。请先 cd 到仓库目录再运行，或用 --repo <仓库根目录> 指定。")


def _log_path():
    return (REPO or Path.cwd()) / "update-dsh.log"


# ---------- 基础工具 ----------

def color(text, code):
    if not sys.stdout.isatty():
        return text
    return code + text + RESET


def log(msg="", raw=False, color_code=None):
    if raw:
        line = msg
    else:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
    if color_code:
        print(color(line, color_code), flush=True)
    else:
        print(line, flush=True)
    with open(_log_path(), "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


def fail(stage, msg, rc=1):
    log(f"[FAIL] {stage}: {msg}", color_code=RED)
    log("完整日志见: " + str(_log_path()), color_code=DIM)
    sys.exit(rc)


def run_stream(cmd, cwd=None, env=None, echo=True):
    """运行命令, 实时把输出打印到控制台并写日志。返回 returncode。"""
    cwd = cwd or REPO
    pretty = " ".join(str(c) for c in cmd)
    log(f"$ {pretty}", color_code=CYAN)
    e = os.environ.copy()
    if env:
        e.update(env)
    try:
        p = subprocess.Popen(
            cmd, cwd=str(cwd), env=e,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
    except OSError as ex:
        fail("run", f"无法启动命令 {pretty}: {ex}")
    out = []
    if p.stdout:
        for line in p.stdout:
            line = line.rstrip("\r\n")
            out.append(line)
            log(line, raw=True)
    p.wait()
    if echo:
        log("")
    return p.returncode, out


def run_quiet(cmd, cwd=None):
    cwd = cwd or REPO
    e = os.environ.copy()
    try:
        r = subprocess.run(cmd, cwd=str(cwd), env=e, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except OSError:
        return -1, ""


def git(*args, cwd=None):
    return run_quiet(["git", *args], cwd=cwd)


def pnpm(*args, cwd=None, env=None, echo=True):
    """在 Windows 上用 cmd 执行 pnpm(.cmd)；非 Windows 直接执行。"""
    if os.name == "nt":
        cmd = ["cmd", "/c", "pnpm", *args]
    else:
        cmd = ["pnpm", *args]
    return run_stream(cmd, cwd=cwd, env=env, echo=echo)


def tool_path(name):
    p = shutil.which(name)
    return p or "MISSING"


def ensure_pnpm_on_path():
    """pnpm 可能装在 %LOCALAPPDATA%/dsh-bin（start-dsh.bat 自己加 PATH, 交互终端没有）。"""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        d = Path(local) / "dsh-bin"
        if d.is_dir():
            cur = os.environ.get("PATH", "")
            if str(d) not in cur.split(os.pathsep):
                os.environ["PATH"] = str(d) + os.pathsep + cur


def symlink_capable():
    """Windows 下探测能否创建 junction（pnpm 工作区链接的前提, 不需要符号链接特权）。"""
    if os.name != "nt":
        return True, None
    tmp = Path(tempfile.mkdtemp(prefix="dsh-upd-"))
    try:
        a, b = tmp / "a", tmp / "b"
        a.mkdir(); b.mkdir()
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(b / "j"), str(a)],
                           capture_output=True, text=True)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def server_listening(port=3080):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def is_admin():
    if os.name != "nt":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def self_elevate():
    """Windows 下以管理员权限重新启动自身（仅提权启动，参数去掉 --elevate）。"""
    if os.name != "nt":
        return False
    try:
        import ctypes
        argv = [a for a in sys.argv[1:] if a != "--elevate"]
        cmdline = "-m dsh_updater " + " ".join(argv)
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, cmdline, str(REPO or Path.cwd()), 1)
        return ret > 32
    except Exception:
        return False


# ---------- 仓库状态 ----------

def pkg_version():
    try:
        data = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
        return data.get("version", "?")
    except Exception:
        return "?"


def head_short():
    rc, out = git("rev-parse", "--short", "HEAD")
    return out.strip() if rc == 0 else "?"


def behind_ahead():
    rc, out = git("rev-list", "--left-right", "--count", f"{DEFAULT_BRANCH}...HEAD")
    if rc != 0:
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    return int(parts[0]), int(parts[1])  # behind, ahead


def restart_server():
    log("正在重启 3080 服务器...")
    rc, out = run_quiet(["netstat", "-ano"])
    pids = set()
    for line in out.splitlines():
        if ":3080" in line and "LISTENING" in line:
            toks = line.split()
            if toks:
                pids.add(toks[-1])
    for pid in pids:
        run_quiet(["taskkill", "/F", "/PID", pid])
    time.sleep(1.5)
    subprocess.Popen(
        'start "dsh web server" cmd /k "pnpm dsh web"',
        shell=True, cwd=str(REPO),
    )
    log("已启动新服务器窗口。等待几秒后浏览器刷新 http://127.0.0.1:3080 即可。", color_code=GREEN)


# ---------- 检查模式 ----------

def cmd_check():
    log("========== DeepSeek Harness 状态检查 ==========", color_code=CYAN)
    log(f"仓库:   {REPO}")
    log(f"版本:   {pkg_version()}  (HEAD {head_short()})")

    node = tool_path("node")
    g = tool_path("git")
    log(f"Node:   {node}")
    log(f"Git:    {g}")
    ensure_pnpm_on_path()
    pn = shutil.which("pnpm")
    log(f"pnpm:   {pn or 'MISSING (检查 PATH, 通常位于 %LOCALAPPDATA%/dsh-bin)'}")

    log("正在 fetch 上游（仅更新远端引用，不改变工作区）...")
    rc, out = git("fetch", "origin")
    if rc != 0:
        log("fetch 失败（网络？）: " + out.strip()[-300:], color_code=RED)
        ba = None
    else:
        ba = behind_ahead()
    if ba:
        log(f"上游:   {ba[0]} 个新提交可合并, 本地领先 {ba[1]} 个提交")
    else:
        log("上游:   无法确定（fetch 失败）")

    ok, detail = symlink_capable()
    if ok:
        log("符号链接: OK（可以正常 pnpm install）", color_code=GREEN)
    else:
        log("符号链接: 当前进程无法创建 junction/链接！", color_code=RED)
        log("          解决办法: 用 update-dsh.bat（自动提权）运行, 或 dsh-updater --elevate,", color_code=YELLOW)
        log("          或重启电脑让开发者模式生效。", color_code=YELLOW)
        if detail:
            log("          细节: " + detail, color_code=DIM)

    if server_listening():
        log("服务器:   3080 端口正在运行（更新后需重启才会用上新版本）", color_code=YELLOW)
    else:
        log("服务器:   3080 未运行")

    dirty_rc, dirty = git("status", "--porcelain")
    dirty_files = [ln for ln in dirty.splitlines() if ln] if dirty_rc == 0 else []
    if dirty_files:
        log(f"工作区:   {len(dirty_files)} 个未提交改动（merge 会原样保留，不覆盖）", color_code=YELLOW)
        for ln in dirty_files[:8]:
            log("          " + ln, color_code=DIM)
        if len(dirty_files) > 8:
            log(f"          ... 共 {len(dirty_files)} 条", color_code=DIM)
    else:
        log("工作区:   干净")

    log("==============================================", color_code=CYAN)
    log("下一步: 运行 python update-dsh.py（或直接双击 update-dsh.bat）开始更新。")
    sys.exit(0)


# ---------- 主流程 ----------

def main(argv=None):
    global REPO
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="dsh-updater",
        description="DeepSeek Harness 更新器（在外部终端运行；自动定位仓库）")
    parser.add_argument("--check", action="store_true", help="只检查现状，不做任何改动")
    parser.add_argument("--repo", help="DeepSeek Harness 仓库根目录（默认自动定位）")
    parser.add_argument("--elevate", action="store_true", help="以管理员权限重新启动自身（Windows）")
    parser.add_argument("--clean", action="store_true", help="install 前删除 node_modules")
    parser.add_argument("--no-merge", action="store_true", help="跳过 git merge")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="要合并的分支 (默认 origin/master)")
    parser.add_argument("--test", action="store_true", help="额外运行单元测试 pnpm run test")
    parser.add_argument("--no-typecheck", action="store_true", help="跳过 pnpm run typecheck")
    parser.add_argument("--restart", action="store_true", help="成功后重启 3080 服务器（会断开当前会话）")
    parser.add_argument("--force", action="store_true", help="符号链接检查失败也继续")
    parser.add_argument("--version", action="store_true", help="显示更新器版本并退出")
    args = parser.parse_args(argv)

    if args.version:
        print(f"dsh-updater v{UPDATER_VERSION}")
        sys.exit(0)

    REPO = resolve_repo(args.repo)

    if args.elevate and os.name == "nt" and not is_admin():
        log("正在申请管理员权限...", color_code=YELLOW)
        if self_elevate():
            sys.exit(0)
        log("[WARN] 提权启动失败，继续以当前权限运行（install 可能因符号链接权限失败）", color_code=YELLOW)

    if args.check:
        cmd_check()
        return

    _log_path().open("a", encoding="utf-8").write(
        "\n========== " + datetime.datetime.now().isoformat() + " ==========\n")

    log("========== DeepSeek Harness 更新 ==========", color_code=CYAN)
    log(f"版本 {pkg_version()} (HEAD {head_short()}) -> 目标分支 {args.branch}")

    # ---- 预检 ----
    node = tool_path("node")
    if node == "MISSING":
        fail("preflight", "未找到 Node.js (需要 ^22.19 || >=24), 请先安装 https://nodejs.org/")
    ensure_pnpm_on_path()
    if shutil.which("pnpm") is None:
        fail("preflight", "未找到 pnpm。请执行: npm install -g pnpm@11.7.0")
    log(f"Node {node}  |  pnpm {shutil.which('pnpm')}", color_code=DIM)

    ok, detail = symlink_capable()
    if not ok:
        msg = ("当前进程无法创建 junction/符号链接，pnpm install 必然失败。\n"
               "  请用 update-dsh.bat 双击运行（自动申请管理员权限），或 dsh-updater --elevate，\n"
               "  或重启电脑让开发者模式生效。若坚持继续: dsh-updater --force")
        if not args.force:
            fail("preflight", msg)
        log("[WARN] 符号链接不可用，--force 继续（install 大概率失败）", color_code=YELLOW)

    server_up = server_listening()
    if server_up:
        log("[WARN] 3080 服务器正在运行。更新期间它会继续服务旧版本；完成后需要重启。", color_code=YELLOW)

    # ---- fetch + merge ----
    log("阶段 1/4: 拉取上游...")
    rc, out = git("fetch", "origin")
    if rc != 0:
        fail("git fetch", out.strip()[-300:] or "网络错误")

    ba = behind_ahead()
    if ba:
        log(f"上游 {ba[0]} 个新提交, 本地领先 {ba[1]} 个")
    if ba and ba[0] > 0 and not args.no_merge:
        log("阶段 2/4: 合并 " + args.branch + " ...")
        rc, out = run_stream(["git", "merge", args.branch, "--no-edit"])
        if rc != 0:
            mer, _ = git("rev-parse", "-q", "--verify", "MERGE_HEAD")
            if mer == 0:
                c_rc, c_out = git("diff", "--name-only", "--diff-filter=U")
                if c_out.strip():
                    git("merge", "--abort")
                    fail("git merge", "与本地改动冲突，已自动回滚 (git merge --abort)。\n冲突文件:\n" + c_out.strip())
                rc2, _ = run_stream(["git", "commit", "--no-edit"])
                if rc2 != 0:
                    fail("git merge", "合并已暂存但自动提交失败（可能是 hook 问题）。请手动执行: git merge --continue")
            else:
                fail("git merge", "合并失败（无 MERGE_HEAD，可能有 hook 或文件锁问题）")
    elif args.no_merge:
        log("--no-merge 已指定，跳过合并。", color_code=DIM)
    else:
        log("已经是最新，无需合并。", color_code=GREEN)

    # ---- install ----
    if args.clean:
        log("--clean 已指定: 删除 node_modules ...", color_code=YELLOW)
        shutil.rmtree(REPO / "node_modules", ignore_errors=True)

    log("阶段 3/4: pnpm install ...")
    env_install = os.environ.copy()
    env_install["CI"] = "true"  # 避免无 TTY 时的确认提示
    rc, out = pnpm("install", env=env_install)
    if rc != 0:
        tail = "\n".join(out[-30:])
        hint = ""
        if any("EPERM" in ln or "symlink" in ln.lower() for ln in out):
            hint = ("\n看起来是符号链接权限问题: 请用 update-dsh.bat（管理员）运行, "
                    "或 dsh-updater --elevate, 或重启电脑让开发者模式生效。")
        fail("pnpm install", tail + hint)

    # ---- build ----
    log("阶段 4/4: pnpm run build ...")
    env_build = os.environ.copy()
    env_build["npm_config_verify_deps_before_run"] = "false"  # 避免 build 时触发自动重装
    rc, out = pnpm("run", "build", env=env_build)
    if rc != 0:
        fail("pnpm run build", "\n".join(out[-40:]))

    # ---- typecheck / test ----
    if not args.no_typecheck:
        log("附加: pnpm run typecheck ...")
        rc, out = pnpm("run", "typecheck", env=env_build)
        if rc != 0:
            fail("pnpm run typecheck", "\n".join(out[-40:]))
    if args.test:
        log("附加: pnpm run test ...")
        rc, out = pnpm("run", "test", env=env_build)
        if rc != 0:
            fail("pnpm run test", "\n".join(out[-40:]))

    # ---- 验证与总结 ----
    core_lib = (REPO / "packages" / "core" / "lib").is_dir()
    web_dist = (REPO / "apps" / "web" / "dist").is_dir()
    new_head = head_short()
    log("========== 更新完成 ==========", color_code=GREEN)
    log(f"版本:   {pkg_version()}   HEAD: {new_head}")
    log(f"产物:   packages/core/lib: {'OK' if core_lib else 'MISSING'} | apps/web/dist: {'OK' if web_dist else 'MISSING'}")
    log(f"日志:   {_log_path()}", color_code=DIM)
    if server_up:
        if args.restart:
            restart_server()
        else:
            log("服务器: 3080 还在跑旧版本。重启方式: 关闭 'dsh web server' 窗口后重新双击 start-dsh.bat；"
                "或下次直接 dsh-updater --restart。", color_code=YELLOW)
    elif args.restart:
        restart_server()
    else:
        log("服务器: 未运行。启动: 双击 start-dsh.bat 或 pnpm dsh web。", color_code=YELLOW)
    sys.exit(0)
