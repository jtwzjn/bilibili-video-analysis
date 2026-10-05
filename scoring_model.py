"""
动态权重综合评分模型 v2 - 基于 Erlangshen 情感分析结果
输入: cleaned/videos + analyzed_v2/comments
输出: scored_v2/videos_scored

用法:
  spark-submit --master local[*] scoring_model.py --local
"""
import argparse
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# ========== 路径配置 ==========
LOCAL_VIDEOS    = "file:///home/hadoop/bili_project/data/cleaned/videos"
LOCAL_COMMENTS  = "file:///home/hadoop/bili_project/data/analyzed_v2/comments"   # ← v2
LOCAL_OUTPUT    = "file:///home/hadoop/bili_project/data/scored_v2/videos_scored"

HDFS_VIDEOS     = "hdfs:///bili/cleaned/videos"
HDFS_COMMENTS   = "hdfs:///bili/analyzed_v2/comments"
HDFS_OUTPUT     = "hdfs:///bili/scored_v2/videos_scored"

# ========== 模型超参 ==========
W_VIEW, W_LIKE, W_COIN, W_FAV, W_SHARE = 0.30, 0.25, 0.20, 0.15, 0.10
ALPHA, BETA, GAMMA = 0.50, 0.25, 0.25
DYN_VIEW_PENALTY = 0.30
DYN_LIKE_BOOST   = 0.10
DYN_COIN_BOOST   = 0.20
DYN_FAV_BOOST    = 0.15
DYN_SHARE_BOOST  = 0.15
W_QUALITY_BASE   = 0.20


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--local", action="store_true")
    return p.parse_args()


def get_paths(use_local):
    if use_local:
        return LOCAL_VIDEOS, LOCAL_COMMENTS, LOCAL_OUTPUT
    return HDFS_VIDEOS, HDFS_COMMENTS, HDFS_OUTPUT


def compute_quality(comments_df):
    df = (comments_df
        .withColumn("weight", F.log1p(F.coalesce(F.col("like_count"), F.lit(0))))
        .withColumn("weighted_score", F.col("sentiment_score") * F.col("weight"))
        .withColumn("high_conf", (F.col("confidence") > 0.7).cast("int"))
    )

    grouped = (df.groupBy("bvid").agg(
        F.count("rpid").alias("comment_count_used"),
        F.sum("weight").alias("sum_weight"),
        F.sum("weighted_score").alias("sum_weighted_score"),
        F.stddev("sentiment_score").alias("std_score"),
        F.avg("high_conf").alias("mean_high_conf"),
    ))

    grouped = grouped.withColumn(
        "S",
        F.when(F.col("sum_weight") > 1e-9,
               F.col("sum_weighted_score") / F.col("sum_weight"))
         .otherwise(F.lit(0.0))
    )
    grouped = grouped.withColumn(
        "C",
        F.greatest(F.lit(0.0),
                   F.least(F.lit(1.0),
                           F.lit(1.0) - F.coalesce(F.col("std_score"), F.lit(0.0))))
    )
    grouped = grouped.withColumn("D", F.coalesce(F.col("mean_high_conf"), F.lit(0.0)))

    grouped = grouped.withColumn(
        "S_norm",
        F.lit(1.0) / (F.lit(1.0) + F.exp(-2 * F.col("S")))
    ).withColumn(
        "Q",
        F.greatest(F.lit(0.0),
                   F.least(F.lit(1.0),
                           ALPHA * F.col("S_norm")
                           + BETA  * F.col("C")
                           + GAMMA * F.col("D")))
    )
    return grouped.select("bvid", "S", "C", "D", "Q", "comment_count_used")


def add_log_minmax(df, col_name, out_name):
    log_col = F.log1p(F.coalesce(F.col(col_name), F.lit(0)))
    df = df.withColumn(f"_log_{col_name}", log_col)
    stats = df.agg(
        F.min(f"_log_{col_name}").alias("lo"),
        F.max(f"_log_{col_name}").alias("hi"),
    ).collect()[0]
    lo, hi = float(stats["lo"]), float(stats["hi"])
    span = hi - lo if (hi - lo) > 1e-9 else 1.0
    df = df.withColumn(out_name, (F.col(f"_log_{col_name}") - F.lit(lo)) / F.lit(span))
    return df.drop(f"_log_{col_name}")


def add_static_score(df):
    df = add_log_minmax(df, "view_count",     "norm_view")
    df = add_log_minmax(df, "like_count",     "norm_like")
    df = add_log_minmax(df, "coin_count",     "norm_coin")
    df = add_log_minmax(df, "favorite_count", "norm_fav")
    df = add_log_minmax(df, "share_count",    "norm_share")

    df = df.withColumn(
        "score_static",
        W_VIEW  * F.col("norm_view")  +
        W_LIKE  * F.col("norm_like")  +
        W_COIN  * F.col("norm_coin")  +
        W_FAV   * F.col("norm_fav")   +
        W_SHARE * F.col("norm_share")
    )
    return df


def add_dynamic_score(df):
    Q = F.coalesce(F.col("Q"), F.lit(0.5))

    w_view  = W_VIEW  * (F.lit(1.0) - DYN_VIEW_PENALTY * Q)
    w_like  = W_LIKE  * (F.lit(1.0) + DYN_LIKE_BOOST   * Q)
    w_coin  = W_COIN  * (F.lit(1.0) + DYN_COIN_BOOST   * Q)
    w_fav   = W_FAV   * (F.lit(1.0) + DYN_FAV_BOOST    * Q)
    w_share = W_SHARE * (F.lit(1.0) + DYN_SHARE_BOOST  * Q)
    w_q     = W_QUALITY_BASE * Q

    w_sum = w_view + w_like + w_coin + w_fav + w_share + w_q
    df = (df
        .withColumn("_w_view",  w_view  / w_sum)
        .withColumn("_w_like",  w_like  / w_sum)
        .withColumn("_w_coin",  w_coin  / w_sum)
        .withColumn("_w_fav",   w_fav   / w_sum)
        .withColumn("_w_share", w_share / w_sum)
        .withColumn("_w_q",     w_q     / w_sum)
    )

    df = df.withColumn(
        "score_dynamic",
        F.col("_w_view")  * F.col("norm_view")  +
        F.col("_w_like")  * F.col("norm_like")  +
        F.col("_w_coin")  * F.col("norm_coin")  +
        F.col("_w_fav")   * F.col("norm_fav")   +
        F.col("_w_share") * F.col("norm_share") +
        F.col("_w_q")     * F.coalesce(F.col("Q"), F.lit(0.5))
    )
    return df.drop("_w_view", "_w_like", "_w_coin", "_w_fav", "_w_share", "_w_q")


def add_rankings(df):
    w_static  = Window.orderBy(F.col("score_static").desc())
    w_dynamic = Window.orderBy(F.col("score_dynamic").desc())
    df = (df
        .withColumn("rank_static",  F.dense_rank().over(w_static))
        .withColumn("rank_dynamic", F.dense_rank().over(w_dynamic))
        .withColumn("rank_change",  F.col("rank_static") - F.col("rank_dynamic"))
    )
    return df


def main():
    args = parse_args()
    videos_path, comments_path, output_path = get_paths(args.local)

    spark = (SparkSession.builder
             .appName("BiliScoringV2")
             .config("spark.sql.session.timeZone", "Asia/Shanghai")
             .config("spark.driver.memory", "4g")
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    print(f"[读取] videos: {videos_path}")
    videos = spark.read.parquet(videos_path)
    print(f"  {videos.count()} 个视频")

    print(f"[读取] comments (v2/Erlangshen): {comments_path}")
    comments = spark.read.parquet(comments_path).select(
        "bvid", "rpid", "like_count", "sentiment_score", "confidence"
    )
    print(f"  {comments.count()} 条已分析评论")

    print("[Q] 聚合评论指标...")
    quality = compute_quality(comments)
    quality.cache()

    print("[Q] Q 描述统计:")
    quality.select("Q").describe().show()

    df = videos.join(quality, on="bvid", how="left")
    print("[静态] 计算...")
    df = add_static_score(df)
    print("[动态] 计算...")
    df = add_dynamic_score(df)
    df = add_rankings(df)
    df.cache()

    (df.coalesce(1)
       .write.mode("overwrite")
       .parquet(output_path))
    print(f"\n[输出] 已写入 {output_path}")

    print("\n=== 排名上升最多 (反流量党) ===")
    df.orderBy(F.col("rank_change").desc()).select(
        "bvid", "title", "category", "view_count", "Q",
        "rank_static", "rank_dynamic", "rank_change"
    ).show(10, truncate=40)

    print("\n=== 排名下降最多 (流量党) ===")
    df.orderBy(F.col("rank_change").asc()).select(
        "bvid", "title", "category", "view_count", "Q",
        "rank_static", "rank_dynamic", "rank_change"
    ).show(10, truncate=40)

    print("\n=== 各分类的平均 Q ===")
    (df.groupBy("category")
       .agg(F.avg("Q").alias("avg_Q"),
            F.count("*").alias("n_videos"))
       .orderBy(F.col("avg_Q").desc())
       .show())

    spark.stop()
    print("=== 完成 ===")


if __name__ == "__main__":
    main()
