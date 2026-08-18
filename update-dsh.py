#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek Harness 更新器（仓库本地版入口）。

把本文件、update-dsh.bat 和 dsh_updater/ 目录一起放进 DeepSeek Harness
仓库根目录（与 start-dsh.bat 同级），然后:
    python update-dsh.py          # 或
    双击 update-dsh.bat           # 自动申请管理员权限
也可以直接:  python -m dsh_updater

详细用法:  python update-dsh.py --help
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dsh_updater import main  # noqa: E402

if __name__ == "__main__":
    main()
