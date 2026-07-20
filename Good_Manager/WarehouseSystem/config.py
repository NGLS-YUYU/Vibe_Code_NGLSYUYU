"""
仓库管理系统 — 统一配置文件
修改此文件即可切换数据库连接、服务端口等参数
"""

# ── 数据库连接配置 ────────────────────────────────────────────────────────────────
# 当前使用 SQLite（本地文件数据库，无需安装任何服务）
# 切换到 PostgreSQL 时取消下面注释：
# DATABASE_URL = "postgresql://warehouse:warehouse123@localhost:5432/warehouse_db"
import os, sys
# 数据库文件放在 EXE/脚本 同级目录的 data 子目录下
# sys.argv[0] 在开发和 PyInstaller 打包后都指向实际工作目录
_BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
_DATA_DIR = os.path.join(_BASE_DIR, "data")
os.makedirs(_DATA_DIR, exist_ok=True)
DATABASE_URL = f"sqlite:///{os.path.join(_DATA_DIR, 'warehouse.db')}"

# ── Flask 配置 ───────────────────────────────────────────────────────────────────
SECRET_KEY = "warehouse-secret-key-2026-change-in-production"

# ── 服务配置 ─────────────────────────────────────────────────────────────────────
HOST = "0.0.0.0"     # 监听地址（0.0.0.0 即允许局域网访问）
PORT = 5000           # 监听端口
THREADS = 8           # Waitress 线程数

# ── 业务配置 ─────────────────────────────────────────────────────────────────────
AUTO_CLEANUP_DAYS = 30  # 回收站自动清理天数