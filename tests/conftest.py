"""把 src 目录加入模块搜索路径，兼容未安装为 site-packages 的运行方式。"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
