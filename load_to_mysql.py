"""
把 v2 数据 (Erlangshen 情感分析) 灌入 MySQL
v3 修复: 写库前对 bvid/rpid 主键做 dropDuplicates, 避免重复键冲突

依赖: pip install pymysql
用法:
  spark-submit \
    --jars /home/hadoop/bili_project/jars/mysql-connector-j-8.4.0.jar \
    --master "local[*]" \
    load_to_mysql.py
"""
import pymysql
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

# ========== 配置 ==========
MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_DB   = "bili_analysis"
MYSQL_USER = "bili"
MYSQL_PASS = "040306"   # ← 改成你的实际密码

MYSQL_URL  = (f"jdbc:mysql://{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
              "?useSSL=false&serverTimezone=Asia/Shanghai"
              "&characterEncoding=utf8&allowPublicKeyRetrieval=true")
MYSQL_DRIVER = "com.mysql.cj.jdbc.Driver"

VIDEOS_PATH            = "file:///home/hadoop/bili_project/data/cleaned/videos"
SCORES_PATH            = "file:///home/hadoop/bili_project/data/scored_v2/videos_scored"
COMMENTS_CLEANED_PATH  = "file:///home/hadoop/bili_project/data/cleaned/comments"
COMMENTS_ANALYZED_PATH = "file:///home/hadoop/bili_project/data/analyzed_v2/comments"


def get_jdbc_props():
    return {
        "user":     MYSQL_USER,
        "password": MYSQL_PASS,
        "driver":   MYSQL_DRIVER,
        "rewriteBatchedStatements": "true",
        "useServerPrepStmts":       "true",
    }


def truncate_tables_via_pymysql():
    print("[预处理] 清空 MySQL 中的旧数据...")
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT,
        user=MYSQL_USER, password=MYSQL_PASS,
        database=MYSQL_DB, charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in ["comments", "video_scores", "videos"]:
                cur.execute(f"DELETE FROM {table}")
                print(f"  ✓ 已清空 {table}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
    finally:
        conn.close()


def append_table(df, table_name):
    n = df.count()
    print(f"  待写入 {n} 行 -> {table_name}")
    (df.write
       .mode("append")
       .option("batchsize", 5000)
       .jdbc(MYSQL_URL, table_name, properties=get_jdbc_props()))
    print(f"  ✓ {table_name} 完成")


def write_videos(spark):
    print("[1/3] videos 表...")
    df = spark.read.parquet(VIDEOS_PATH).select(
        F.col("bvid"),
        F.col("aid"),
        F.col("category"),
        F.col("title"),
        F.col("desc").alias("description"),
        F.col("tname"),
        F.col("tid"),
        F.col("duration"),
        F.col("pubdate"),
        F.col("owner_uid"),
        F.col("owner_name"),
        F.col("view_count"),
        F.col("like_count"),
        F.col("coin_count"),
        F.col("favorite_count"),
        F.col("share_count"),
        F.col("reply_count"),
        F.col("danmaku_count"),
    )
    df = (df
        .withColumn("title", F.substring(F.col("title"), 1, 250))
        .withColumn("description", F.substring(F.col("description"), 1, 5000)))

    # 关键: 按主键 bvid 去重
    raw_count = df.count()
    df = df.dropDuplicates(["bvid"])
    dedup_count = df.count()
    if raw_count != dedup_count:
        print(f"  ⚠ 检测到重复 bvid: {raw_count} -> {dedup_count} (去重 {raw_count - dedup_count} 条)")

    append_table(df, "videos")


def write_scores(spark):
    print("[2/3] video_scores 表...")
    df = spark.read.parquet(SCORES_PATH).select(
        F.col("bvid"),
        F.col("S").alias("s_score"),
        F.col("C").alias("c_score"),
        F.col("D").alias("d_score"),
        F.col("Q").alias("quality_q"),
        F.col("comment_count_used"),
        F.col("score_static"),
        F.col("score_dynamic"),
        F.col("rank_static"),
        F.col("rank_dynamic"),
        F.col("rank_change"),
    )
    raw_count = df.count()
    df = df.dropDuplicates(["bvid"])
    dedup_count = df.count()
    if raw_count != dedup_count:
        print(f"  ⚠ 检测到重复 bvid: {raw_count} -> {dedup_count}")

    append_table(df, "video_scores")


def write_comments(spark):
    print("[3/3] comments 表 (join 完整字段 + v2 情感分析)...")
    cleaned = spark.read.parquet(COMMENTS_CLEANED_PATH).select(
        F.col("rpid"),
        F.col("bvid"),
        F.col("aid"),
        F.col("uid"),
        F.col("uname"),
        F.col("level").alias("user_level"),
        F.to_timestamp(F.col("ctime")).alias("ctime"),
        F.col("like_count"),
        F.col("reply_count"),
        F.col("message"),
    )

    analyzed = spark.read.parquet(COMMENTS_ANALYZED_PATH).select(
        F.col("rpid"),
        F.col("sentiment_label"),
        F.col("sentiment_score"),
        F.col("confidence"),
    )

    df = cleaned.join(analyzed, on="rpid", how="inner")
    df = df.withColumn("message", F.substring(F.col("message"), 1, 1000))
    df = df.select(
        "rpid", "bvid", "aid", "uid", "uname", "user_level",
        "ctime", "like_count", "reply_count", "message",
        "sentiment_label", "sentiment_score", "confidence",
    )

    # 关键: 按主键 rpid 去重
    raw_count = df.count()
    df = df.dropDuplicates(["rpid"])
    dedup_count = df.count()
    if raw_count != dedup_count:
        print(f"  ⚠ 检测到重复 rpid: {raw_count} -> {dedup_count} (去重 {raw_count - dedup_count} 条)")

    df = df.repartition(8)
    append_table(df, "comments")


def main():
    truncate_tables_via_pymysql()

    spark = (SparkSession.builder
             .appName("BiliLoadMySQL_v2")
             .config("spark.driver.memory", "4g")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    write_videos(spark)
    write_scores(spark)
    write_comments(spark)

    spark.stop()
    print("\n=== 全部灌库完成 (v2 数据) ===")
    print("\n登录 MySQL 验证:")
    print("  mysql -u bili -p bili_analysis")
    print("  SELECT COUNT(*) FROM videos;")
    print("  SELECT COUNT(*) FROM comments;")
    print("  SELECT AVG(quality_q) FROM video_scores;")


if __name__ == "__main__":
    main()
