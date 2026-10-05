"""
MySQL 连接管理
使用 DBUtils 的连接池, 避免每次请求都开关连接
"""
import pymysql
from dbutils.pooled_db import PooledDB
from config import Config


# 全局连接池, Flask 启动时初始化一次
_pool = None


def init_pool():
    global _pool
    _pool = PooledDB(
        creator=pymysql,
        maxconnections=10,        # 最大并发连接
        mincached=2,
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASS,
        database=Config.MYSQL_DB,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,   # 关键: 让查询返回 dict 不是 tuple
    )
    print(f"[DB] 连接池已初始化: {Config.MYSQL_USER}@{Config.MYSQL_HOST}/{Config.MYSQL_DB}")


def query(sql, params=None):
    """执行 SELECT, 返回 list[dict]"""
    conn = _pool.connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def query_one(sql, params=None):
    """执行 SELECT, 返回单条 dict 或 None"""
    rows = query(sql, params)
    return rows[0] if rows else None
