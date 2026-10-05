"""
BERT 情感分析 v2 - Erlangshen 模型 (中文专用)
对比之前的 jd-binary 模型, 在 B 站等多元语料上准确率显著更高

输出路径与 v1 不同, 方便后续做对比实验:
  v1 (旧, 京东模型): /home/hadoop/bili_project/data/analyzed/comments
  v2 (新, Erlangshen): /home/hadoop/bili_project/data/analyzed_v2/comments

使用前:
  方案 1 (推荐): 手动下载模型到 /home/hadoop/bili_project/models/erlangshen-sentiment/
  方案 2: 由 hf-mirror 自动下载 (需要网络稳定)
"""
import os
os.environ["HF_ENDPOINT"]      = "https://hf-mirror.com"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"

import time
import math
import pandas as pd
import torch
from pathlib import Path
from transformers import AutoTokenizer, AutoModelForSequenceClassification

# ========== 配置 ==========
INPUT_PARQUET   = "/home/hadoop/bili_project/data/cleaned/comments"
OUTPUT_DIR      = Path("/home/hadoop/bili_project/data/analyzed_v2/comments")
PROGRESS_FILE   = Path("/home/hadoop/bili_project/data/analyzed_v2/.done_chunks.txt")

# 二选一:
#   远程: "IDEA-CCNL/Erlangshen-Roberta-110M-Sentiment"
#   本地: "/home/hadoop/bili_project/models/erlangshen-sentiment"
MODEL_NAME      = "/home/hadoop/bili_project/models/erlangshen-sentiment"

CHUNK_SIZE      = 5000
BATCH_SIZE      = 64       # 显存不够调到 32
MAX_LENGTH      = 128

# 中性阈值: 置信度低于这个值的预测被标为 neutral
NEUTRAL_THRESHOLD = 0.65   # 比 v1 的 0.6 稍严, 减少把弱信号判成正/负的情况


def load_model():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[设备] 使用 {device}")
    if device.type == "cuda":
        print(f"[GPU] {torch.cuda.get_device_name(0)}, "
              f"显存 {torch.cuda.get_device_properties(0).total_memory/1024**3:.1f} GB")

    print(f"[模型] 加载 {MODEL_NAME} ...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    model.eval()
    model.to(device)
    print(f"[模型] 标签映射: {model.config.id2label}")
    print(f"[模型] 参数量: {sum(p.numel() for p in model.parameters())/1e6:.1f}M")
    return tokenizer, model, device


@torch.no_grad()
def infer_batch(texts, tokenizer, model, device):
    enc = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=MAX_LENGTH,
        return_tensors="pt",
    ).to(device)
    logits = model(**enc).logits
    probs  = torch.softmax(logits, dim=-1)
    pred   = probs.argmax(dim=-1)
    conf   = probs.max(dim=-1).values
    pos_prob = probs[:, 1]
    sentiment_score = (pos_prob - 0.5) * 2
    return (
        pred.cpu().tolist(),
        conf.cpu().tolist(),
        sentiment_score.cpu().tolist(),
    )


def label_id_to_str(label_id, conf):
    """带中性阈值的标签映射"""
    if conf < NEUTRAL_THRESHOLD:
        return "neutral"
    return "positive" if label_id == 1 else "negative"


def main():
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    done_chunks = set()
    if PROGRESS_FILE.exists():
        done_chunks = set(int(x) for x in PROGRESS_FILE.read_text().split() if x.strip())
        print(f"[续传] 已完成 {len(done_chunks)} 个块, 跳过它们")

    print(f"[读取] {INPUT_PARQUET}")
    t0 = time.time()
    df = pd.read_parquet(INPUT_PARQUET, engine="pyarrow",
                         columns=["bvid", "rpid", "uid", "like_count", "message"])
    print(f"[读取] {len(df)} 条评论, 耗时 {time.time()-t0:.1f}s")

    total_chunks = math.ceil(len(df) / CHUNK_SIZE)
    print(f"[分块] 共 {total_chunks} 块, 每块 {CHUNK_SIZE} 条")

    tokenizer, model, device = load_model()

    overall_start = time.time()
    for chunk_idx in range(total_chunks):
        if chunk_idx in done_chunks:
            continue

        chunk_start = time.time()
        s, e = chunk_idx * CHUNK_SIZE, (chunk_idx + 1) * CHUNK_SIZE
        chunk_df = df.iloc[s:e].copy()
        texts = chunk_df["message"].astype(str).tolist()

        all_pred, all_conf, all_score = [], [], []
        for bs in range(0, len(texts), BATCH_SIZE):
            batch = texts[bs : bs + BATCH_SIZE]
            pred, conf, score = infer_batch(batch, tokenizer, model, device)
            all_pred.extend(pred)
            all_conf.extend(conf)
            all_score.extend(score)

        chunk_df["sentiment_label"] = [
            label_id_to_str(p, c) for p, c in zip(all_pred, all_conf)
        ]
        chunk_df["sentiment_score"] = all_score
        chunk_df["confidence"]      = all_conf

        out_path = OUTPUT_DIR / f"chunk_{chunk_idx:04d}.parquet"
        chunk_df.to_parquet(out_path, engine="pyarrow", index=False)

        with open(PROGRESS_FILE, "a") as f:
            f.write(f"{chunk_idx}\n")

        elapsed = time.time() - chunk_start
        rate = len(chunk_df) / elapsed
        eta = (total_chunks - chunk_idx - 1) * elapsed / 60
        print(f"  ✓ 块 {chunk_idx+1}/{total_chunks}  "
              f"{len(chunk_df)} 条  耗时 {elapsed:.1f}s  "
              f"({rate:.0f} 条/秒)  剩余约 {eta:.1f} 分钟")

    total_min = (time.time() - overall_start) / 60
    print(f"\n=== 全部完成, 共耗时 {total_min:.1f} 分钟 ===")
    print(f"输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
