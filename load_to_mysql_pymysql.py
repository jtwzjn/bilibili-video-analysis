"""
直接用 pymysql 灌库, 完全绕开 Spark JDBC
关键: INSERT IGNORE - 遇到重复主键自动跳过, 不报错

依赖: pip install pymysql pandas pyarrow
用法: python load_to_mysql_pymysql.py    (不需要 spark-submit)
"""
import time
import pymysql
import pandas as pd

# ========== 配置 ==========
MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_DB   = "bili_analysis"
MYSQL_USER = "bili"
MYSQL_PASS = "040306"   # ← 改成你的实际密码

VIDEOS_PATH            = "/home/hadoop/bili_project/data/cleaned/videos"
SCORES_PATH            = "/home/hadoop/bili_project/data/scored_v2/videos_scored"
COMMENTS_CLEANED_PATH  = "/home/hadoop/bili_project/data/cleaned/comments"
COMMENTS_ANALYZED_PATH = "/home/hadoop/bili_project/data/analyzed_v2/comments"

BATCH_SIZE = 2000


def get_conn():
    return pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASS,
        database=MYSQL_DB, charset="utf8mb4",
        autocommit=False,
    )


def truncate_all():
    print("[预处理] 清空 MySQL 中的旧数据...")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in ["comments", "video_scores", "videos"]:
                cur.execute(f"TRUNCATE TABLE {table}")
                print(f"  ✓ 已 TRUNCATE {table}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
    finally:
        conn.close()


def batch_insert_ignore(conn, sql, rows, batch=BATCH_SIZE):
    """批量 INSERT IGNORE, 遇到重复键跳过"""
    total = len(rows)
    inserted = 0
    with conn.cursor() as cur:
        for i in range(0, total, batch):
            chunk = rows[i:i+batch]
            cur.executemany(sql, chunk)
            inserted += cur.rowcount
            if (i // batch) % 5 == 0:
                print(f"    {i+len(chunk):>7d}/{total} 已插入 (实际写入 {inserted})")
    conn.commit()
    return inserted


def write_videos():
    print("[1/3] videos 表...")
    df = pd.read_parquet(VIDEOS_PATH, engine="pyarrow")
    print(f"  Parquet 原始: {len(df)} 行")

    df = df.drop_duplicates(subset=["bvid"], keep="first")
    print(f"  pandas 去重: {len(df)} 行")

    # 字段规整
    df["title"]       = df["title"].fillna("").astype(str).str.slice(0, 250)
    df["desc"]        = df["desc"].fillna("").astype(str).str.slice(0, 5000)
    df["category"]    = df["category"].fillna("").astype(str)
    df["tname"]       = df["tname"].fillna("").astype(str)
    df["owner_name"]  = df["owner_name"].fillna("").astype(str)
    # pubdate 在 Parquet 里是 string, 转成 datetime
    df["pubdate"] = pd.to_datetime(df["pubdate"], errors="coerce")

    cols = ["bvid", "aid", "category", "title", "desc", "tname", "tid",
            "duration", "pubdate", "owner_uid", "owner_name",
            "view_count", "like_count", "coin_count", "favorite_count",
            "share_count", "reply_count", "danmaku_count"]
    rows = []
    for r in df[cols].itertuples(index=False, name=None):
        # pandas NaT/NaN -> None
        rows.append(tuple(None if (pd.isna(x) if not isinstance(x, str) else False) else x for x in r))

    sql = """INSERT IGNORE INTO videos
        (bvid, aid, category, title, description, tname, tid,
         duration, pubdate, owner_uid, owner_name,
         view_count, like_count, coin_count, favorite_count,
         share_count, reply_count, danmaku_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

    conn = get_conn()
    try:
        n = batch_insert_ignore(conn, sql, rows)
        print(f"  ✓ videos 完成: 实际写入 {n} 行")
    finally:
        conn.close()


def write_scores():
    print("[2/3] video_scores 表...")
    df = pd.read_parquet(SCORES_PATH, engine="pyarrow")
    print(f"  Parquet 原始: {len(df)} 行")

    df = df.drop_duplicates(subset=["bvid"], keep="first")
    print(f"  pandas 去重: {len(df)} 行")

    cols_map = {
        "bvid": "bvid", "S": "s_score", "C": "c_score", "D": "d_score",
        "Q": "quality_q", "comment_count_used": "comment_count_used",
        "score_static": "score_static", "score_dynamic": "score_dynamic",
        "rank_static": "rank_static", "rank_dynamic": "rank_dynamic",
        "rank_change": "rank_change",
    }
    df2 = df[list(cols_map.keys())].rename(columns=cols_map)

    rows = []
    for r in df2.itertuples(index=False, name=None):
        rows.append(tuple(None if (pd.isna(x) if not isinstance(x, str) else False) else x for x in r))

    sql = """INSERT IGNORE INTO video_scores
        (bvid, s_score, c_score, d_score, quality_q, comment_count_used,
         score_static, score_dynamic, rank_static, rank_dynamic, rank_change)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

    conn = get_conn()
    try:
        n = batch_insert_ignore(conn, sql, rows)
        print(f"  ✓ video_scores 完成: 实际写入 {n} 行")
    finally:
        conn.close()


def write_comments():
    print("[3/3] comments 表 (join 完整字段 + v2 情感分析)...")
    print("  读取 cleaned/comments...")
    cleaned = pd.read_parquet(COMMENTS_CLEANED_PATH, engine="pyarrow")
    print(f"    {len(cleaned)} 行")

    print("  读取 analyzed_v2/comments...")
    analyzed = pd.read_parquet(COMMENTS_ANALYZED_PATH, engine="pyarrow",
                                columns=["rpid", "sentiment_label",
                                         "sentiment_score", "confidence"])
    print(f"    {len(analyzed)} 行")

    print("  按 rpid 内联...")
    df = cleaned.merge(analyzed, on="rpid", how="inner")
    print(f"    join 后: {len(df)} 行")

    df = df.drop_duplicates(subset=["rpid"], keep="first")
    print(f"  按 rpid 去重: {len(df)} 行")

    # 字段规整
    df["message"] = df["message"].fillna("").astype(str).str.slice(0, 1000)
    df["uname"]   = df["uname"].fillna("").astype(str)
    df["sentiment_label"] = df["sentiment_label"].fillna("neutral").astype(str)
    df["ctime"]   = pd.to_datetime(df["ctime"], errors="coerce")

    # 整理列顺序: rpid, bvid, aid, uid, uname, level, ctime, like, reply, message, sent_label, sent_score, conf
    df = df.rename(columns={"level": "user_level"})

    cols = ["rpid", "bvid", "aid", "uid", "uname", "user_level", "ctime",
            "like_count", "reply_count", "message",
            "sentiment_label", "sentiment_score", "confidence"]

    rows = []
    for r in df[cols].itertuples(index=False, name=None):
        rows.append(tuple(None if (pd.isna(x) if not isinstance(x, str) else False) else x for x in r))

    sql = """INSERT IGNORE INTO comments
        (rpid, bvid, aid, uid, uname, user_level, ctime,
         like_count, reply_count, message,
         sentiment_label, sentiment_score, confidence)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

    print(f"  开始批量写入 ({len(rows)} 行)...")
    conn = get_conn()
    try:
        n = batch_insert_ignore(conn, sql, rows, batch=5000)
        print(f"  ✓ comments 完成: 实际写入 {n} 行")
    finally:
        conn.close()


def main():
    t0 = time.time()
    truncate_all()
    write_videos()
    write_scores()
    write_comments()
    print(f"\n=== 全部灌库完成, 耗时 {(time.time()-t0)/60:.1f} 分钟 ===")
    print("\n登录 MySQL 验证:")
    print("  mysql -u bili -p bili_analysis")
    print("  SELECT COUNT(*) FROM videos;")
    print("  SELECT COUNT(*) FROM video_scores;")
    print("  SELECT COUNT(*) FROM comments;")


if __name__ == "__main__":
    main()
