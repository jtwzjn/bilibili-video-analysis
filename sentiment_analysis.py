"""
BERT 情感分析: 对清洗后的评论批量推理, 输出带情感标签的 Parquet
用法:
  python sentiment_analysis.py
特点:
  - GPU 优先 (3070 Ti 大约 15-25 分钟跑完 10 万评论)
  - 批量推理, batch_size 自适应
  - 断点续传: 分块处理, 中断后再跑会跳过已完成块
"""
import os
# 国内 HuggingFace 镜像 (必须在 import transformers 之前设置)
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import time
import math
import pandas as pd
import torch
from pathlib import Path
from transformers import AutoTokenizer, AutoModelForSequenceClassification

# ========== 配置 ==========
INPUT_PARQUET   = "/home/hadoop/bili_project/data/cleaned/comments"
OUTPUT_DIR      = Path("/home/hadoop/bili_project/data/analyzed/comments")
PROGRESS_FILE   = Path("/home/hadoop/bili_project/data/analyzed/.done_chunks.txt")

MODEL_NAME = "/home/hadoop/bili_project/models/jd-sentiment"
CHUNK_SIZE      = 5000     # 每块 5000 条, 一块处理完写一个 Parquet
BATCH_SIZE      = 64       # GPU 批大小, 显存不够就调到 32
MAX_LENGTH      = 128      # 评论一般不长, 128 足够; 超长截断


# ========== 加载模型 ==========
def load_model():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[设备] 使用 {device}")
    if device.type == "cuda":
        print(f"[GPU] {torch.cuda.get_device_name(0)}, "
              f"显存 {torch.cuda.get_device_properties(0).total_memory/1024**3:.1f} GB")

    print(f"[模型] 加载 {MODEL_NAME} (首次会下载约 400MB)...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    model.eval()
    model.to(device)
    print(f"[模型] 标签映射: {model.config.id2label}")
    return tokenizer, model, device


# ========== 批量推理 ==========
@torch.no_grad()
def infer_batch(texts, tokenizer, model, device):
    """对一批文本做推理, 返回 (label_id_list, confidence_list, sentiment_score_list)"""
    enc = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=MAX_LENGTH,
        return_tensors="pt",
    ).to(device)

    logits = model(**enc).logits          # [B, 2]
    probs  = torch.softmax(logits, dim=-1)  # [B, 2]
    pred   = probs.argmax(dim=-1)         # [B]
    conf   = probs.max(dim=-1).values     # [B] 最大概率

    # sentiment_score: 正向概率 P(positive) 映射到 [-1, 1]
    # 模型 label_0=negative, label_1=positive (uer 系列约定)
    pos_prob = probs[:, 1]                # [B]
    sentiment_score = (pos_prob - 0.5) * 2  # [-1, 1]

    return (
        pred.cpu().tolist(),
        conf.cpu().tolist(),
        sentiment_score.cpu().tolist(),
    )


def label_id_to_str(label_id, conf):
    """label_id (0/1) + 置信度 -> 字符串标签
    如果置信度 < 0.6, 算 neutral (模糊地带)"""
    if conf < 0.6:
        return "neutral"
    return "positive" if label_id == 1 else "negative"


# ========== 主流程 ==========
def main():
    # 加载已完成的块号
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    done_chunks = set()
    if PROGRESS_FILE.exists():
        done_chunks = set(int(x) for x in PROGRESS_FILE.read_text().split() if x.strip())
        print(f"[续传] 已完成 {len(done_chunks)} 个块, 跳过它们")

    # 读 Parquet (用 pandas 即可, pyarrow 引擎)
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

        # 标记完成
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
    print("可以用以下代码读取所有结果:")
    print(f'  pd.read_parquet("{OUTPUT_DIR}", engine="pyarrow")')


if __name__ == "__main__":
    main()
