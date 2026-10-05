"""
Spark 清洗: 原始 JSON -> Parquet
用法:
  本地模式 (推荐先调试):
    spark-submit --master local[*] clean_data.py --local
  HDFS 模式 (传 HDFS 后再跑):
    spark-submit --master local[*] clean_data.py
"""
import argparse
import re
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, LongType, IntegerType
from pyspark.sql.window import Window

# ========== 路径配置 (用户名 hadoop, 加 file:// 前缀避免 Spark 误认为 HDFS) ==========
LOCAL_VIDEO_PATH    = "file:///home/hadoop/bili_project/crawler/data/raw/videos/*.json"
LOCAL_COMMENT_PATH  = "file:///home/hadoop/bili_project/crawler/data/raw/comments/*.json"
LOCAL_OUT_DIR       = "file:///home/hadoop/bili_project/data/cleaned"

HDFS_VIDEO_PATH     = "hdfs:///bili/raw/videos/*/*.json"
HDFS_COMMENT_PATH   = "hdfs:///bili/raw/comments/*/*.json"
HDFS_OUT_DIR        = "hdfs:///bili/cleaned"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--local", action="store_true", help="使用本地路径而非 HDFS")
    return p.parse_args()


def get_paths(use_local):
    if use_local:
        return LOCAL_VIDEO_PATH, LOCAL_COMMENT_PATH, LOCAL_OUT_DIR
    return HDFS_VIDEO_PATH, HDFS_COMMENT_PATH, HDFS_OUT_DIR


# ========== 清洗 UDF ==========
EMOJI_TAG_RE    = re.compile(r"\[[^\[\]]{1,15}\]")
URL_RE          = re.compile(r"https?://\S+|www\.\S+")
AT_USER_RE      = re.compile(r"@[\u4e00-\u9fa5\w\-]{1,30}")
REPLY_PREFIX_RE = re.compile(r"^回复\s*@[^\s:：]+\s*[:：]\s*")
WHITESPACE_RE   = re.compile(r"\s+")


def clean_text(s):
    if s is None:
        return None
    s = REPLY_PREFIX_RE.sub("", s)
    s = EMOJI_TAG_RE.sub("", s)
    s = URL_RE.sub("", s)
    s = AT_USER_RE.sub("", s)
    s = WHITESPACE_RE.sub(" ", s).strip()
    return s if s else None


clean_text_udf = F.udf(clean_text, StringType())


# ========== 主流程 ==========
def clean_videos(spark, input_path, output_path):
    print(f"[videos] 读取: {input_path}")
    df = spark.read.option("multiline", "true").json(input_path)

    print("[videos] 原始 schema:")
    df.printSchema()

    cleaned = (df
        .select(
            F.col("bvid"),
            F.col("aid").cast(LongType()),
            F.col("category"),
            F.col("title"),
            F.col("desc"),
            F.col("tname"),
            F.col("tid").cast(IntegerType()),
            F.col("duration").cast(IntegerType()),
            F.from_unixtime(F.col("pubdate")).alias("pubdate"),
            F.col("owner.mid").cast(LongType()).alias("owner_uid"),
            F.col("owner.name").alias("owner_name"),
            F.col("stat.view").cast(LongType()).alias("view_count"),
            F.col("stat.like").cast(LongType()).alias("like_count"),
            F.col("stat.coin").cast(LongType()).alias("coin_count"),
            F.col("stat.favorite").cast(LongType()).alias("favorite_count"),
            F.col("stat.share").cast(LongType()).alias("share_count"),
            F.col("stat.reply").cast(LongType()).alias("reply_count"),
            F.col("stat.danmaku").cast(LongType()).alias("danmaku_count"),
        )
        .filter(F.col("bvid").isNotNull())
        .filter(F.col("view_count") > 0)
        .filter(F.col("title").isNotNull())
        .dropDuplicates(["bvid"])
    )

    cnt = cleaned.count()
    print(f"[videos] 清洗后: {cnt} 条")
    cleaned.show(5, truncate=60)

    cleaned.coalesce(1).write.mode("overwrite").parquet(f"{output_path}/videos")
    print(f"[videos] 已写入 {output_path}/videos")


def clean_comments(spark, input_path, output_path):
    print(f"[comments] 读取: {input_path}")
    df = spark.read.option("multiline", "true").json(input_path)

    exploded = df.select(
        F.col("bvid"),
        F.col("aid").cast(LongType()),
        F.explode("comments").alias("c")
    )

    cleaned = (exploded
        .select(
            F.col("bvid"),
            F.col("aid"),
            F.col("c.rpid").cast(LongType()).alias("rpid"),
            F.col("c.uid").cast(LongType()).alias("uid"),
            F.col("c.uname").alias("uname"),
            F.col("c.level").cast(IntegerType()).alias("level"),
            F.from_unixtime(F.col("c.ctime")).alias("ctime"),
            F.col("c.like").cast(LongType()).alias("like_count"),
            F.col("c.rcount").cast(LongType()).alias("reply_count"),
            clean_text_udf(F.col("c.message")).alias("message"),
        )
        .filter(F.col("message").isNotNull())
        .filter(F.length(F.col("message")) >= 3)
        .filter(F.length(F.col("message")) <= 500)
        .dropDuplicates(["rpid"])
    )

    # 去重 "同一用户在同一视频下重复内容"
    w = Window.partitionBy("bvid", "uid", "message").orderBy("rpid")
    cleaned = (cleaned
        .withColumn("rn", F.row_number().over(w))
        .filter(F.col("rn") == 1)
        .drop("rn"))

    cnt = cleaned.count()
    print(f"[comments] 清洗后: {cnt} 条")
    cleaned.show(5, truncate=80)

    (cleaned
        .repartition(4, "bvid")
        .write.mode("overwrite")
        .parquet(f"{output_path}/comments"))
    print(f"[comments] 已写入 {output_path}/comments")


def main():
    args = parse_args()
    video_path, comment_path, out_dir = get_paths(args.local)

    spark = (SparkSession.builder
             .appName("BiliCleanData")
             .config("spark.sql.session.timeZone", "Asia/Shanghai")
             .config("spark.driver.memory", "4g")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    clean_videos(spark, video_path, out_dir)
    clean_comments(spark, comment_path, out_dir)

    spark.stop()
    print("=== 清洗完成 ===")


if __name__ == "__main__":
    main()
