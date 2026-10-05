"""
检查 BERT 情感分析结果
用法: python check_sentiment.py
"""
import pandas as pd

ANALYZED_PATH = "/home/hadoop/bili_project/data/analyzed/comments"

print(f"读取: {ANALYZED_PATH}")
df = pd.read_parquet(ANALYZED_PATH, engine="pyarrow")

print(f"\n总数: {len(df)}")

print("\n=== 情感分布 ===")
print(df["sentiment_label"].value_counts(normalize=True).round(3))

print("\n=== 情感分数描述 ===")
print(df["sentiment_score"].describe())

print("\n=== 置信度描述 ===")
print(df["confidence"].describe())

print("\n=== 最正向的 5 条 ===")
print(df.nlargest(5, "sentiment_score")[["message", "sentiment_score", "confidence"]].to_string(index=False))

print("\n=== 最负向的 5 条 ===")
print(df.nsmallest(5, "sentiment_score")[["message", "sentiment_score", "confidence"]].to_string(index=False))

print("\n=== 中性样本 5 条 ===")
neutral = df[df["sentiment_label"] == "neutral"]
if len(neutral) >= 5:
    print(neutral.sample(5)[["message", "sentiment_score", "confidence"]].to_string(index=False))
else:
    print(f"中性样本只有 {len(neutral)} 条")

print("\n=== 高赞评论的情感分布 (like_count > 100) ===")
hot = df[df["like_count"] > 100]
print(f"高赞评论数: {len(hot)}")
if len(hot) > 0:
    print(hot["sentiment_label"].value_counts(normalize=True).round(3))
