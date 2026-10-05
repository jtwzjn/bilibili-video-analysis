"""
单视频按需分析服务
负责: 抓取 (B站API) + 清洗 + BERT推理 + 计算评分 + 写库

复用项目现有的:
  - cookies (从 crawler/cookies/ 读)
  - Erlangshen 模型 (常驻内存)
  - MySQL 连接池 (db.py)
  - 评分公式 (与 scoring_model.py 一致)
"""
import re
import time
import json
import asyncio
import aiohttp
import logging
from pathlib import Path
import math
import torch
import numpy as np
from transformers import AutoTokenizer, AutoModelForSequenceClassification

import db
from config import Config
from tid_map import tid_to_category as _tid_lookup
from tid_map import tid_to_category as _tid_lookup

log = logging.getLogger(__name__)

# ========== 常量 ==========
COOKIES_DIR  = Path("/home/hadoop/bili_project/crawler/cookies")
MODEL_PATH   = "/home/hadoop/bili_project/models/erlangshen-sentiment"
COMMENT_PAGES = 15      # 单视频按需分析时, 抓 ~300 条评论 (足够计算 Q, 不用全量)

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

# 同评分模型常数 (与 scoring_model.py 完全一致)
W_VIEW, W_LIKE, W_COIN, W_FAV, W_SHARE = 0.30, 0.25, 0.20, 0.15, 0.10
ALPHA, BETA, GAMMA = 0.50, 0.25, 0.25
DYN_VIEW_PENALTY = 0.30
DYN_LIKE_BOOST   = 0.10
DYN_COIN_BOOST   = 0.20
DYN_FAV_BOOST    = 0.15
DYN_SHARE_BOOST  = 0.15
W_QUALITY_BASE   = 0.20

# 评论清洗正则 (与 clean_data.py 一致)
EMOJI_TAG_RE    = re.compile(r"\[[^\[\]]{1,15}\]")
URL_RE          = re.compile(r"https?://\S+|www\.\S+")
AT_USER_RE      = re.compile(r"@[\u4e00-\u9fa5\w\-]{1,30}")
REPLY_PREFIX_RE = re.compile(r"^回复\s*@[^\s:：]+\s*[:：]\s*")
WHITESPACE_RE   = re.compile(r"\s+")


# ========== 全局: BERT 模型 (启动时加载一次) ==========
_tokenizer = None
_model = None
_device = None


def init_bert():
    """Flask 启动时调一次, 加载 BERT 模型常驻"""
    global _tokenizer, _model, _device
    log.info(f"[BERT] 加载模型: {MODEL_PATH}")
    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    _model = AutoModelForSequenceClassification.from_pretrained(MODEL_PATH)
    _model.eval()
    _model.to(_device)
    log.info(f"[BERT] 加载完成, 设备: {_device}")


# ========== 全局: B站 Cookie ==========
_cookies = []
_cookie_idx = 0


def init_cookies():
    """加载所有 cookie"""
    global _cookies
    _cookies = []
    for f in sorted(COOKIES_DIR.glob("cookie*.txt")):
        c = f.read_text(encoding="utf-8").strip()
        if c and "SESSDATA" in c:
            _cookies.append(c)
    if not _cookies:
        log.warning(f"[Cookie] 在 {COOKIES_DIR} 没找到 cookie 文件")
    else:
        log.info(f"[Cookie] 加载了 {len(_cookies)} 个")


def next_cookie():
    global _cookie_idx
    if not _cookies:
        return None
    c = _cookies[_cookie_idx % len(_cookies)]
    _cookie_idx += 1
    return c


# ========== 工具 ==========
def parse_bvid(text):
    """从用户输入提取 bvid: 'BV1xxx' 或 B站链接"""
    text = text.strip()
    # 直接是 BV 号
    m = re.match(r"^(BV[a-zA-Z0-9]{10})$", text)
    if m:
        return m.group(1)
    # B 站链接: https://www.bilibili.com/video/BV1xxx/?xxx
    m = re.search(r"(BV[a-zA-Z0-9]{10})", text)
    if m:
        return m.group(1)
    return None


def clean_text(s):
    if not s:
        return None
    s = REPLY_PREFIX_RE.sub("", s)
    s = EMOJI_TAG_RE.sub("", s)
    s = URL_RE.sub("", s)
    s = AT_USER_RE.sub("", s)
    s = WHITESPACE_RE.sub(" ", s).strip()
    return s if s and len(s) >= 3 else None


# ========== B站 API 调用 ==========
async def _fetch_json(session, url, retry=3):
    headers = dict(BASE_HEADERS)
    cookie = next_cookie()
    if cookie:
        headers["Cookie"] = cookie

    for i in range(retry):
        try:
            async with session.get(url, headers=headers, timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("code") == 0:
                        return data["data"]
                    if data.get("code") in (-412, -101):
                        return None
                elif r.status == 412:
                    return None
        except Exception as e:
            log.warning(f"请求异常: {e}")
        await asyncio.sleep(1 + i)
    return None


async def fetch_video_detail(bvid):
    async with aiohttp.ClientSession() as session:
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        return await _fetch_json(session, url)


async def fetch_video_with_comments(bvid):
    """抓视频详情 + 评论, 一次性返回"""
    async with aiohttp.ClientSession() as session:
        # 详情
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        detail = await _fetch_json(session, url)
        if not detail:
            return None, []

        aid = detail["aid"]
        await asyncio.sleep(0.5)

        # 评论
        comments = []
        for pn in range(1, COMMENT_PAGES + 1):
            curl = f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&next={pn}"
            data = await _fetch_json(session, curl)
            if not data or not data.get("replies"):
                break
            for r in data["replies"]:
                try:
                    comments.append({
                        "rpid": r["rpid"],
                        "uid": r["mid"],
                        "uname": r["member"]["uname"],
                        "level": r["member"]["level_info"]["current_level"],
                        "ctime": r["ctime"],
                        "like": r["like"],
                        "rcount": r.get("rcount", 0),
                        "message": r["content"]["message"],
                    })
                except (KeyError, TypeError):
                    continue
            await asyncio.sleep(0.8)

        return detail, comments


# ========== BERT 推理 ==========
@torch.no_grad()
def bert_predict_batch(texts, batch_size=64):
    """对一批文本做 BERT 情感分析"""
    if not texts:
        return []

    all_results = []
    for bs in range(0, len(texts), batch_size):
        batch = texts[bs : bs + batch_size]
        enc = _tokenizer(
            batch, padding=True, truncation=True,
            max_length=128, return_tensors="pt"
        ).to(_device)
        logits = _model(**enc).logits
        probs = torch.softmax(logits, dim=-1)
        pred = probs.argmax(dim=-1).cpu().tolist()
        conf = probs.max(dim=-1).values.cpu().tolist()
        pos_prob = probs[:, 1].cpu().tolist()

        for p, c, pp in zip(pred, conf, pos_prob):
            score = (pp - 0.5) * 2  # [-1, 1]
            label = "neutral" if c < 0.65 else ("positive" if p == 1 else "negative")
            all_results.append({
                "label": label,
                "score": float(score),
                "confidence": float(c),
            })
    return all_results


# ========== Q 计算 (与评分模型一致) ==========
def compute_q(comments_with_sentiment):
    """计算单视频的 Q 系数"""
    if not comments_with_sentiment:
        return {"S": 0, "C": 1, "D": 0, "Q": 0.5, "comment_count_used": 0}

    weights = []
    weighted_scores = []
    scores = []
    high_confs = []
    for c in comments_with_sentiment:
        w = math.log1p(c["like_count"] or 0)
        weights.append(w)
        weighted_scores.append(c["sentiment_score"] * w)
        scores.append(c["sentiment_score"])
        high_confs.append(1 if c["confidence"] > 0.7 else 0)

    sum_w = sum(weights)
    S = sum(weighted_scores) / sum_w if sum_w > 1e-9 else 0.0

    if len(scores) < 2:
        std = 0
    else:
        std = float(np.std(scores))
    C = max(0.0, min(1.0, 1.0 - std))

    D = sum(high_confs) / len(high_confs) if high_confs else 0

    S_norm = 1.0 / (1.0 + math.exp(-2 * S))
    Q = max(0.0, min(1.0, ALPHA * S_norm + BETA * C + GAMMA * D))

    return {
        "S": float(S),
        "C": float(C),
        "D": float(D),
        "Q": float(Q),
        "comment_count_used": len(comments_with_sentiment),
    }


# ========== 评分计算 (单视频版, 用全局 min/max 归一化) ==========
def compute_score_for_new_video(video_stats, q_value):
    """
    给新视频算分数. 因为是单视频, 没法用 min-max 归一化,
    用全库的 min/max 作为归一化基准 (从 MySQL 查)
    """
    # 查全库的 log(stat) 范围
    sql = """
        SELECT
          LOG(GREATEST(view_count, 1) + 1) AS log_view,
          LOG(GREATEST(like_count, 1) + 1) AS log_like,
          LOG(GREATEST(coin_count, 1) + 1) AS log_coin,
          LOG(GREATEST(favorite_count, 1) + 1) AS log_fav,
          LOG(GREATEST(share_count, 1) + 1) AS log_share
        FROM videos
    """
    rows = db.query(sql)
    if not rows:
        return None

    def col_range(col):
        vals = [r[col] for r in rows if r[col] is not None]
        return min(vals), max(vals)

    lo_v, hi_v = col_range("log_view")
    lo_l, hi_l = col_range("log_like")
    lo_c, hi_c = col_range("log_coin")
    lo_f, hi_f = col_range("log_fav")
    lo_s, hi_s = col_range("log_share")

    def norm(value, lo, hi):
        x = math.log1p(value or 0)
        if hi - lo < 1e-9:
            return 0.0
        return max(0.0, min(1.0, (x - lo) / (hi - lo)))

    n_view  = norm(video_stats.get("view", 0),     lo_v, hi_v)
    n_like  = norm(video_stats.get("like", 0),     lo_l, hi_l)
    n_coin  = norm(video_stats.get("coin", 0),     lo_c, hi_c)
    n_fav   = norm(video_stats.get("favorite", 0), lo_f, hi_f)
    n_share = norm(video_stats.get("share", 0),    lo_s, hi_s)

    # 静态评分
    score_static = (W_VIEW*n_view + W_LIKE*n_like + W_COIN*n_coin
                    + W_FAV*n_fav + W_SHARE*n_share)

    # 动态评分
    Q = q_value if q_value is not None else 0.5
    w_view  = W_VIEW  * (1 - DYN_VIEW_PENALTY * Q)
    w_like  = W_LIKE  * (1 + DYN_LIKE_BOOST   * Q)
    w_coin  = W_COIN  * (1 + DYN_COIN_BOOST   * Q)
    w_fav   = W_FAV   * (1 + DYN_FAV_BOOST    * Q)
    w_share = W_SHARE * (1 + DYN_SHARE_BOOST  * Q)
    w_q     = W_QUALITY_BASE * Q
    w_sum = w_view + w_like + w_coin + w_fav + w_share + w_q

    score_dynamic = (
        (w_view  / w_sum) * n_view  +
        (w_like  / w_sum) * n_like  +
        (w_coin  / w_sum) * n_coin  +
        (w_fav   / w_sum) * n_fav   +
        (w_share / w_sum) * n_share +
        (w_q     / w_sum) * Q
    )

    return {
        "score_static":  float(score_static),
        "score_dynamic": float(score_dynamic),
    }


def estimate_rank(score, mode="dynamic"):
    """估计在现有视频中的排名 (按百分位)"""
    col = "score_dynamic" if mode == "dynamic" else "score_static"
    rank_row = db.query_one(
        f"SELECT COUNT(*) + 1 AS rank_pos FROM video_scores WHERE {col} > %s",
        [score],
    )
    return rank_row["rank_pos"] if rank_row else None


# ========== tid 映射 (使用完整映射表, 与 fix_category.py 一致) ==========
def tid_to_category(tid, tname=None):
    """根据 tid 查一级分类名 (动画/科技/...). 与离线脚本完全一致"""
    main_name, _sub_name = _tid_lookup(tid, tname)
    return main_name

# ========== 主入口 ==========
async def analyze_video(bvid):
    """
    主流程: 抓取 -> 分析 -> 写库 -> 返回结果

    返回:
      {
        "success": bool,
        "msg": str,
        "data": {bvid, title, ..., score_static, score_dynamic, Q, est_rank_dynamic, ...}
      }
    """
    t0 = time.time()
    log.info(f"[analyze] 开始处理 {bvid}")

    # 1. 检查是否已存在
    existing = db.query_one(
        """SELECT v.*, s.s_score, s.c_score, s.d_score, s.quality_q,
                  s.comment_count_used, s.score_static, s.score_dynamic,
                  s.rank_static, s.rank_dynamic, s.rank_change
           FROM videos v LEFT JOIN video_scores s ON v.bvid=s.bvid
           WHERE v.bvid = %s""",
        [bvid],
    )
    if existing:
        log.info(f"[analyze] {bvid} 已存在, 直接返回")
        return {"success": True, "msg": "已存在", "data": existing, "from_cache": True}

    # 2. 抓取
    detail, raw_comments = await fetch_video_with_comments(bvid)
    if not detail:
        return {"success": False, "msg": "视频不存在或抓取失败", "data": None}

    # 3. 清洗评论
    cleaned_comments = []
    seen_msgs = set()
    for c in raw_comments:
        msg = clean_text(c["message"])
        if not msg or msg in seen_msgs:
            continue
        if 3 <= len(msg) <= 500:
            seen_msgs.add(msg)
            cleaned_comments.append({**c, "message": msg})

    log.info(f"[analyze] 清洗后评论: {len(cleaned_comments)}")

    # 4. BERT 推理
    if cleaned_comments:
        texts = [c["message"] for c in cleaned_comments]
        results = bert_predict_batch(texts)
        for c, r in zip(cleaned_comments, results):
            c["sentiment_label"] = r["label"]
            c["sentiment_score"] = r["score"]
            c["confidence"] = r["confidence"]
            c["like_count"] = c.pop("like")  # 字段重命名

    # 5. 算 Q
    q_data = compute_q(cleaned_comments)
    log.info(f"[analyze] Q={q_data['Q']:.3f}")

    # 6. 算评分
    stat = detail["stat"]
    scores = compute_score_for_new_video(stat, q_data["Q"])

    # 7. 估计排名
    est_rank_static  = estimate_rank(scores["score_static"], "static")
    est_rank_dynamic = estimate_rank(scores["score_dynamic"], "dynamic")

    # 8. 写入 MySQL
    _save_to_db(detail, q_data, scores, est_rank_static, est_rank_dynamic, cleaned_comments)

    elapsed = time.time() - t0
    log.info(f"[analyze] {bvid} 完成, 耗时 {elapsed:.1f}s")

    return {
        "success": True,
        "msg": "分析完成",
        "from_cache": False,
        "elapsed_seconds": round(elapsed, 1),
        "data": {
            "bvid": bvid,
            "title": detail.get("title"),
            "category": tid_to_category(detail.get("tid"), detail.get("tname")),
            "owner_name": detail.get("owner", {}).get("name"),
            "view_count":  stat.get("view", 0),
            "like_count":  stat.get("like", 0),
            "coin_count":  stat.get("coin", 0),
            **q_data,
            **scores,
            "est_rank_static":  est_rank_static,
            "est_rank_dynamic": est_rank_dynamic,
            "rank_change":      (est_rank_static or 0) - (est_rank_dynamic or 0),
            "comment_count_used": q_data["comment_count_used"],
        },
    }


def _save_to_db(detail, q_data, scores, est_rank_s, est_rank_d, comments):
    """把抓取+分析的结果写入三张表"""
    import pymysql
    from datetime import datetime

    conn = pymysql.connect(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASS,
        database=Config.MYSQL_DB, charset="utf8mb4",
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            stat = detail["stat"]
            owner = detail.get("owner", {})

            # videos
            cur.execute("""
                INSERT INTO videos
                (bvid, aid, category, title, description, tname, tid, duration,
                 pubdate, owner_uid, owner_name,
                 view_count, like_count, coin_count, favorite_count,
                 share_count, reply_count, danmaku_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s)
            """, (
                detail["bvid"], detail["aid"],
                tid_to_category(detail.get("tid"), detail.get("tname")),
                detail.get("title", "")[:250],
                detail.get("desc", "")[:5000],
                detail.get("tname", ""),
                detail.get("tid"),
                detail.get("duration"),
                datetime.fromtimestamp(detail["pubdate"]) if detail.get("pubdate") else None,
                owner.get("mid"),
                owner.get("name", "")[:100],
                stat.get("view"), stat.get("like"), stat.get("coin"),
                stat.get("favorite"), stat.get("share"),
                stat.get("reply"), stat.get("danmaku"),
            ))

            # video_scores
            cur.execute("""
                INSERT INTO video_scores
                (bvid, s_score, c_score, d_score, quality_q, comment_count_used,
                 score_static, score_dynamic, rank_static, rank_dynamic, rank_change)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                detail["bvid"],
                q_data["S"], q_data["C"], q_data["D"], q_data["Q"],
                q_data["comment_count_used"],
                scores["score_static"], scores["score_dynamic"],
                est_rank_s, est_rank_d,
                (est_rank_s or 0) - (est_rank_d or 0),
            ))

            # comments
            comment_rows = []
            for c in comments:
                comment_rows.append((
                    c["rpid"], detail["bvid"], detail["aid"],
                    c["uid"], c["uname"][:100],
                    c["level"],
                    datetime.fromtimestamp(c["ctime"]),
                    c["like_count"], c.get("rcount", 0),
                    c["message"][:1000],
                    c["sentiment_label"], c["sentiment_score"], c["confidence"],
                ))
            if comment_rows:
                cur.executemany("""
                    INSERT IGNORE INTO comments
                    (rpid, bvid, aid, uid, uname, user_level, ctime,
                     like_count, reply_count, message,
                     sentiment_label, sentiment_score, confidence)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, comment_rows)

        conn.commit()
        log.info(f"[DB] {detail['bvid']} 写入成功 (评论 {len(comments)} 条)")
    except Exception as e:
        conn.rollback()
        log.error(f"[DB] 写入失败: {e}")
        raise
    finally:
        conn.close()
