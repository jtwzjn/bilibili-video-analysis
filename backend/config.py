"""
Flask 应用配置
密码从 .env 文件读取, 避免硬编码
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    # MySQL
    MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", 3306))
    MYSQL_DB   = os.getenv("MYSQL_DATABASE", "bili_analysis")
    MYSQL_USER = os.getenv("MYSQL_USER", "bili")
    MYSQL_PASS = os.getenv("MYSQL_PASSWORD", "")

    # Flask
    DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    PORT  = int(os.getenv("FLASK_PORT", 5000))
    JSON_AS_ASCII = False    # 让 jsonify 输出中文不转义

    # 业务参数
    MAX_PAGE_SIZE = 1000      # 防止前端要求一次返回太多
