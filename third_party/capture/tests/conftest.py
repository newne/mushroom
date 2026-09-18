import sys
from pathlib import Path

# 这个包用 src/ 布局且没装进任何工作区，直接把 src 挂上去，免得跑测试前还要先 pip install。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
