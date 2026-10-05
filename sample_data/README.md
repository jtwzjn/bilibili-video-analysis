# 小样本数据

完整数据集约 86 MB（Parquet），原始爬取数据为 1497 个 JSON 文件，都不适合放入 Git 仓库。
这里提供一份**小样本**，便于在不重建整个数据流水线的情况下快速体验系统。

## ⚠️ 隐私说明（重要）

样本中的**第三方用户身份信息已全部匿名化**：

| 处理 | 说明 |
|---|---|
| `uid` / `owner.mid` / `rpid` | 替换为 SHA-256 前 8 位的**稳定伪匿名 ID**（前缀 `U_` / `UP_` / `R_`）。同一用户得到相同结果，因此分组统计与去重能力完全保留，但**不可反查** |
| `uname` / `name` / `face` | **直接删除**（昵称、头像地址属直接身份信息） |
| 视频标题、简介、评论正文 | **保留**。这些是 B 站公开可见的内容；作者身份匿名后，不再指向具体个人 |
| `aid`（AV 号） | **保留原值**。它是公开的视频标识，不属于个人信息 |

## 文件清单

| 文件 | 内容 |
|---|---|
| `videos_scored.parquet` | 全部 1497 个视频的评分结果（含 S / C / D / Q、静态与动态排名、排名变化）。体积很小，因此提供完整文件而非抽样 |
| `comments_sample.parquet` | 评论级情感分析结果 5000 条（全量共 69 个分块，此处为第 1 块） |
| `raw_examples/videos/` | 爬虫原始视频 JSON 样例（3 个） |
| `raw_examples/comments/` | 爬虫原始评论 JSON 样例（3 个） |

## 字段与格式

### `videos_scored.parquet`

```
bvid, aid, category, title, desc, tname, tid, duration, pubdate,
view_count, like_count, coin_count, favorite_count, share_count,
reply_count, danmaku_count,
S, C, D, Q, comment_count_used,
norm_view, norm_like, norm_coin, norm_fav, norm_share,
score_static, score_dynamic, rank_static, rank_dynamic, rank_change,
owner_anon          # 原 owner_uid / owner_name 的匿名化结果
```

其中 `S` / `C` / `D` 是评论质量系数 Q 的三个维度（情感倾向 / 一致性 / 讨论深度）。

### `comments_sample.parquet`

```
bvid, like_count, message, sentiment_label, sentiment_score, confidence,
author_anon,        # 原 uid 的匿名化结果
rpid_anon           # 原 rpid 的匿名化结果
```

### 原始视频 JSON（`raw_examples/videos/`）

```json
{
  "bvid": "BV113Z7YrEKL",
  "aid": 114236869055984,
  "category": "科技",
  "title": "把视频中的人物替换教程 让你马起飞",
  "desc": "...",
  "pubdate": 1743116560,
  "duration": 112,
  "tname": "科技",
  "tid": 188,
  "owner": { "mid": "UP_8796914d" },
  "stat": { "view": 128022647, "like": 5610101, "coin": 4930265, "favorite": 3094397, "reply": 336207 }
}
```

### 原始评论 JSON（`raw_examples/comments/`）

```json
{
  "bvid": "BV113Z7YrEKL",
  "aid": 114236869055984,
  "comments": [
    {
      "rpid": "R_5c49df5b",
      "uid": "U_4c4ad465",
      "level": 4,
      "ctime": 1743144910,
      "like": 494,
      "rcount": 18,
      "message": "美国恩情课文，之马爷爷有飞行的魔力[doge][doge][doge]"
    }
  ]
}
```

## 如何使用

```python
import pandas as pd

videos = pd.read_parquet("videos_scored.parquet")
print(videos.columns.tolist())
print(videos[["title", "category", "Q", "rank_change"]].head())

comments = pd.read_parquet("comments_sample.parquet")
print(comments["sentiment_label"].value_counts())
```

> 需要在本地复现完整流水线时，请参考根目录 `README.md` 的「如何运行」章节。
