"""
Flask 应用主入口 - B 站视频分析系统 API
启动: python app.py

新增功能:
  - POST /api/analyze       提交新视频分析 (BV号 或 链接)
  - POST /api/admin/rerank  全库重算排名 (新视频累积一些后调用)
"""
import asyncio
import threading
from flask import Flask, jsonify, request
from flask_cors import CORS
from config import Config
import db
import analyzer


app = Flask(__name__)
app.config.from_object(Config)
CORS(app)


# ============= 全局: 单一并发锁 (一次只允许 1 个分析任务) =============
_analyze_lock = threading.Lock()


# ============= 工具函数 =============
def ok(data, **extra):
    resp = {"code": 0, "msg": "ok", "data": data}
    resp.update(extra)
    return jsonify(resp)


def err(msg, code=1, http_status=400):
    return jsonify({"code": code, "msg": msg, "data": None}), http_status


# ============= 1. 健康检查 =============
@app.route("/api/health")
def health():
    try:
        row = db.query_one("SELECT 1 AS ok")
        return ok({"status": "up", "db": bool(row)})
    except Exception as e:
        return err(f"DB error: {e}", http_status=500)


# ============= 2. 分类列表 =============
@app.route("/api/categories")
def categories():
    sql = """
        SELECT v.category,
               COUNT(*) AS video_count,
               AVG(s.quality_q) AS avg_q,
               AVG(s.score_dynamic) AS avg_score
        FROM videos v
        LEFT JOIN video_scores s ON v.bvid = s.bvid
        GROUP BY v.category
        ORDER BY video_count DESC
    """
    return ok(db.query(sql))


# ============= 3. 视频排行榜 =============
@app.route("/api/videos/rank")
def videos_rank():
    category = request.args.get("category")
    mode     = request.args.get("mode", "dynamic")
    limit    = min(int(request.args.get("limit", 20)), Config.MAX_PAGE_SIZE)
    offset   = int(request.args.get("offset", 0))

    if mode not in ("dynamic", "static"):
        return err("mode must be 'dynamic' or 'static'")

    order_col = "s.score_dynamic" if mode == "dynamic" else "s.score_static"
    rank_col  = "s.rank_dynamic"  if mode == "dynamic" else "s.rank_static"

    where = "WHERE 1=1"
    params = []
    if category:
        where += " AND v.category = %s"
        params.append(category)

    sql = f"""
        SELECT v.bvid, v.title, v.category, v.owner_name,
               v.view_count, v.like_count, v.coin_count, v.favorite_count,
               s.quality_q, s.score_static, s.score_dynamic,
               s.rank_static, s.rank_dynamic, s.rank_change,
               {rank_col} AS current_rank
        FROM videos v
        JOIN video_scores s ON v.bvid = s.bvid
        {where}
        ORDER BY {order_col} DESC
        LIMIT %s OFFSET %s
    """
    rows = db.query(sql, params + [limit, offset])

    total = db.query_one(
        f"SELECT COUNT(*) AS c FROM videos v JOIN video_scores s ON v.bvid=s.bvid {where}",
        params
    )["c"]

    return ok(rows, total=total, limit=limit, offset=offset)


# ============= 4. 单视频详情 =============
@app.route("/api/video/<bvid>")
def video_detail(bvid):
    sql = """
        SELECT v.*, s.s_score, s.c_score, s.d_score, s.quality_q,
               s.comment_count_used,
               s.score_static, s.score_dynamic,
               s.rank_static, s.rank_dynamic, s.rank_change
        FROM videos v
        LEFT JOIN video_scores s ON v.bvid = s.bvid
        WHERE v.bvid = %s
    """
    row = db.query_one(sql, [bvid])
    if not row:
        return err("Video not found", code=404, http_status=404)
    return ok(row)


# ============= 5. 单视频情感分布 =============
@app.route("/api/video/<bvid>/sentiment")
def video_sentiment(bvid):
    label_sql = """
        SELECT sentiment_label AS label, COUNT(*) AS count,
               AVG(sentiment_score) AS avg_score
        FROM comments
        WHERE bvid = %s
        GROUP BY sentiment_label
    """
    score_sql = """
        SELECT FLOOR((sentiment_score + 1) * 5) AS bucket, COUNT(*) AS count
        FROM comments WHERE bvid = %s
        GROUP BY bucket ORDER BY bucket
    """
    return ok({
        "labels":     db.query(label_sql, [bvid]),
        "score_dist": db.query(score_sql, [bvid]),
    })


# ============= 6. 单视频评论列表 =============
@app.route("/api/video/<bvid>/comments")
def video_comments(bvid):
    sort  = request.args.get("sort", "hot")
    label = request.args.get("label")
    limit = min(int(request.args.get("limit", 20)), Config.MAX_PAGE_SIZE)
    offset= int(request.args.get("offset", 0))

    order_map = {
        "hot":           "like_count DESC",
        "latest":        "ctime DESC",
        "sentiment_pos": "sentiment_score DESC",
        "sentiment_neg": "sentiment_score ASC",
    }
    order_by = order_map.get(sort, "like_count DESC")

    where = "WHERE bvid = %s"
    params = [bvid]
    if label in ("positive", "negative", "neutral"):
        where += " AND sentiment_label = %s"
        params.append(label)

    sql = f"""
        SELECT rpid, uname, user_level, ctime, like_count, message,
               sentiment_label, sentiment_score, confidence
        FROM comments
        {where}
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
    """
    rows = db.query(sql, params + [limit, offset])

    total = db.query_one(
        f"SELECT COUNT(*) AS c FROM comments {where}", params
    )["c"]

    return ok(rows, total=total, limit=limit, offset=offset)


# ============= 7. 评分对比 =============
@app.route("/api/compare")
def compare():
    direction = request.args.get("direction", "up")
    limit = min(int(request.args.get("limit", 20)), Config.MAX_PAGE_SIZE)

    if direction == "up":
        order = "s.rank_change DESC"
    elif direction == "down":
        order = "s.rank_change ASC"
    else:
        return err("direction must be 'up' or 'down'")

    sql = f"""
        SELECT v.bvid, v.title, v.category, v.owner_name, v.view_count,
               s.quality_q, s.rank_static, s.rank_dynamic, s.rank_change,
               s.score_static, s.score_dynamic
        FROM videos v
        JOIN video_scores s ON v.bvid = s.bvid
        ORDER BY {order}
        LIMIT %s
    """
    return ok(db.query(sql, [limit]))


# ============= 8. 全局统计 =============
@app.route("/api/stats/overview")
def stats_overview():
    overview = db.query_one("""
        SELECT
          (SELECT COUNT(*) FROM videos) AS total_videos,
          (SELECT COUNT(*) FROM comments) AS total_comments,
          (SELECT COUNT(DISTINCT category) FROM videos) AS total_categories,
          (SELECT AVG(quality_q) FROM video_scores) AS avg_q,
          (SELECT AVG(view_count) FROM videos) AS avg_view
    """)

    sentiment = db.query("""
        SELECT sentiment_label AS label, COUNT(*) AS count
        FROM comments
        GROUP BY sentiment_label
    """)

    return ok({"overview": overview, "sentiment": sentiment})


# ============= 9. 【新】提交新视频分析 =============
@app.route("/api/analyze", methods=["POST"])
def analyze_new_video():
    """
    入参: { "input": "BV1xxx" 或 "https://www.bilibili.com/video/BV1xxx" }
    返回: 分析结果 (含 Q、评分、估计排名)
    """
    data = request.get_json(silent=True) or {}
    user_input = data.get("input", "").strip()
    if not user_input:
        return err("请输入 BV 号或视频链接")

    bvid = analyzer.parse_bvid(user_input)
    if not bvid:
        return err("无法识别为有效的 BV 号或 B 站视频链接")

    # 并发锁: 同时只允许 1 个分析任务
    if not _analyze_lock.acquire(blocking=False):
        return err("当前有其他分析任务进行中, 请稍后重试", code=429, http_status=429)

    try:
        # asyncio 跑爬虫部分
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(analyzer.analyze_video(bvid))
        finally:
            loop.close()

        if not result["success"]:
            return err(result["msg"])

        return ok(result["data"], from_cache=result.get("from_cache", False),
                  elapsed_seconds=result.get("elapsed_seconds"))
    except Exception as e:
        app.logger.exception(f"分析 {bvid} 异常")
        return err(f"分析失败: {e}", http_status=500)
    finally:
        _analyze_lock.release()


# ============= 10. 【新】管理员重算所有排名 =============
@app.route("/api/admin/rerank", methods=["POST"])
def rerank_all():
    """重算所有视频的精确排名 (按 score_static / score_dynamic 全库排序)"""
    import pymysql
    conn = pymysql.connect(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASS,
        database=Config.MYSQL_DB, charset="utf8mb4",
        autocommit=False,
    )
    try:
        with conn.cursor() as cur:
            # MySQL 8 的 dense_rank 用窗口函数
            cur.execute("""
                UPDATE video_scores vs
                JOIN (
                    SELECT bvid,
                           DENSE_RANK() OVER (ORDER BY score_static  DESC) AS rs,
                           DENSE_RANK() OVER (ORDER BY score_dynamic DESC) AS rd
                    FROM video_scores
                ) ranked ON vs.bvid = ranked.bvid
                SET vs.rank_static  = ranked.rs,
                    vs.rank_dynamic = ranked.rd,
		    vs.rank_change  = CAST(ranked.rs AS SIGNED) - CAST(ranked.rd AS SIGNED)
            """)
            affected = cur.rowcount
        conn.commit()
        return ok({"updated_rows": affected})
    except Exception as e:
        conn.rollback()
        return err(f"重算失败: {e}", http_status=500)
    finally:
        conn.close()


# ============= 启动 =============
if __name__ == "__main__":
    db.init_pool()
    analyzer.init_bert()
    analyzer.init_cookies()
    print(f"[Flask] starting on http://0.0.0.0:{Config.PORT}")
    # debug 模式下 reloader 会重复加载 BERT, 关掉
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG, use_reloader=False)
