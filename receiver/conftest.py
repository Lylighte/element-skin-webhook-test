"""pytest 配置：确保 receiver 包可被导入。"""
from __future__ import annotations

import os
import sys

# 把 receiver 的父目录加入 path，使 `from receiver.app import ...` 可用
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
