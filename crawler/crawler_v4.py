"""
B 站扩展爬虫 v4
特性:
  1. 多视频源: 每周必看 + 入站必刷 + 全站热门 + 分区排行榜
  2. 双 Cookie 轮换 (从 cookies/cookie1.txt, cookie2.txt 加载)
  3. 异步并发 + 自适应限速
  4. 评论扩展到 1000 条 (最多 50 页)
  5. 完善的断点续传

使用前:
  1. 准备两个 B 站小号, 把 cookie 分别保存到 cookies/cookie1.txt 和 cookies/cookie2.txt
  2. python crawler_v4.py
"""
import asyncio
import aiohttp
import json
import os
import random
import time
import logging
from pathlib import Path
from itertools import cycle

# ========== 配置 ==========
BASE_DIR     = Path(__file__).parent
COOKIES_DIR  = BASE_DIR / "cookies"
VIDEO_DIR    = BASE_DIR / "data" / "raw" / "videos"
COMMENT_DIR  = BASE_DIR / "data" / "raw" / "comments"
LOG_DIR      = BASE_DIR / "logs"
DONE_FILE    = LOG_DIR / "done_bvids.txt"
SOURCE_LOG   = LOG_DIR / "video_sources.json"   # 记录每个视频来自哪个源

# === 视频源配置 ===
# 每周必看: number 从 250 倒退抓 N 期
WEEKLY_RECENT_N    = 10          # 抓最近 50 期 (约 1 年)

# 分区排行榜: 这些分区每个都抓
CATEGORIES = [
    (188, "科技"), (36,  "知识"), (4,   "游戏"), (211, "美食"),
    (1,   "动画"), (181, "影视"), (160, "生活"), (3,   "音乐"),
    (119, "鬼畜"), (155, "时尚"), (5,   "娱乐"), (234, "运动"),
    (217, "动物圈"),
]

# 全站热门: 抓前 N 页 (每页 20 个)
POPULAR_PAGES      = 5

# === 评论抓取 ===
COMMENT_PAGES_PER_VIDEO = 50     # 1000 条 (旧接口最多翻 50 页)

# === 并发与限速 ===
CONCURRENT_DETAIL  = 3            # 视频详情并发
CONCURRENT_COMMENT = 2            # 评论并发 (相对严格)
SLEEP_DETAIL       = (1.5, 3.0)
SLEEP_COMMENT      = (2.0, 4.0)
SLEEP_LIST         = (2.0, 3.5)

# 风控触发后暂停
LONG_PAUSE_AFTER_412 = (300, 600)

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# ========== 日志 ==========
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "crawler_v4.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger()


# ========== Cookie 管理 ==========
class CookiePool:
    """轮换使用多个 Cookie"""
    def __init__(self, cookie_dir):
        self.cookies = []
        for f in sorted(cookie_dir.glob("cookie*.txt")):
            content = f.read_text(encoding="utf-8").strip()
            if content and "SESSDATA" in content:
                self.cookies.append(content)
                log.info(f"加载 Cookie: {f.name}")
        if not self.cookies:
            raise RuntimeError(f"未在 {cookie_dir} 找到任何有效 cookie 文件")
        self._cycle = cycle(self.cookies)

    def next(self):
        """轮询返回下一个 Cookie"""
        return next(self._cycle)

    def __len__(self):
        return len(self.cookies)


# ========== 工具函数 ==========
def load_done_bvids():
    if not DONE_FILE.exists():
        return set()
    return set(DONE_FILE.read_text(encoding="utf-8").strip().split("\n"))

def mark_done(bvid):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(bvid + "\n")

def make_headers(cookie):
    h = dict(BASE_HEADERS)
    h["Cookie"] = cookie
    return h


async def fetch_json(session, url, cookie_pool, retry=3):
    """带 412 处理的 GET, 自动轮换 Cookie"""
    for i in range(retry):
        cookie = cookie_pool.next()
        try:
            async with session.get(url, headers=make_headers(cookie), timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("code") == 0:
                        return data["data"]
                    code = data.get("code")
                    if code == -412:
                        pause = random.randint(*LONG_PAUSE_AFTER_412)
                        log.warning(f"⛔ -412 风控, 暂停 {pause}s")
                        await asyncio.sleep(pause)
                    elif code == -101:
                        log.error(f"⛔ Cookie 失效: {url}")
                    elif code == 12002:
                        return None  # 评论关闭
                    else:
                        log.warning(f"API code={code} {data.get('message')} {url[:80]}")
                elif r.status == 412:
                    pause = random.randint(*LONG_PAUSE_AFTER_412)
                    log.warning(f"⛔ HTTP 412, 暂停 {pause}s")
                    await asyncio.sleep(pause)
                else:
                    log.warning(f"HTTP {r.status}: {url[:80]}")
        except Exception as e:
            log.warning(f"请求异常 (第{i+1}次): {url[:80]} -> {type(e).__name__}: {e}")
        await asyncio.sleep(2 + i * 2)
    return None


# ========== 视频源接口 ==========
async def fetch_weekly(session, cp, number):
    """每周必看 第 N 期"""
    url = f"https://api.bilibili.com/x/web-interface/popular/series/one?number={number}"
    data = await fetch_json(session, url, cp)
    if not data or "list" not in data:
        return []
    return [{"bvid": v["bvid"], "aid": v["aid"], "_source": f"weekly#{number}"}
            for v in data["list"]]

async def fetch_precious(session, cp):
    """入站必刷"""
    url = "https://api.bilibili.com/x/web-interface/popular/precious"
    data = await fetch_json(session, url, cp)
    if not data or "list" not in data:
        return []
    return [{"bvid": v["bvid"], "aid": v["aid"], "_source": "precious"}
            for v in data["list"]]

async def fetch_popular(session, cp, pn):
    """全站热门 第 N 页"""
    url = f"https://api.bilibili.com/x/web-interface/popular?ps=20&pn={pn}"
    data = await fetch_json(session, url, cp)
    if not data or "list" not in data:
        return []
    return [{"bvid": v["bvid"], "aid": v["aid"], "_source": f"popular#{pn}"}
            for v in data["list"]]

async def fetch_ranking(session, cp, rid):
    """分区排行榜"""
    url = f"https://api.bilibili.com/x/web-interface/ranking/v2?rid={rid}&type=all"
    data = await fetch_json(session, url, cp)
    if not data or "list" not in data:
        return []
    return [{"bvid": v["bvid"], "aid": v["aid"], "_source": f"ranking#{rid}"}
            for v in data["list"]]


# ========== 视频详情 + 评论 ==========
async def get_video_detail(session, cp, bvid):
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    return await fetch_json(session, url, cp)

async def get_comments(session, cp, aid, max_pages):
    """获取评论, 支持深翻"""
    all_comments = []
    for pn in range(1, max_pages + 1):
        url = (f"https://api.bilibili.com/x/v2/reply/main"
               f"?type=1&oid={aid}&mode=3&next={pn}")
        data = await fetch_json(session, url, cp)
        if not data or not data.get("replies"):
            break  # 没了或被限制, 停止深翻
        for r in data["replies"]:
            try:
                all_comments.append({
                    "rpid": r["rpid"],
                    "uid": r["mid"],
                    "uname": r["member"]["uname"],
                    "level": r["member"]["level_info"]["current_level"],
                    "ctime": r["ctime"],
                    "like": r["like"],
                    "rcount": r.get("rcount", 0),
                    "message": r["content"]["message"],
                })
            except (KeyError, TypeError) as e:
                continue   # 偶尔字段缺失, 跳过单条
        await asyncio.sleep(random.uniform(*SLEEP_COMMENT))
    return all_comments


# ========== 处理单视频 ==========
async def process_video(session, cp, sem_d, sem_c, video_info, done_set):
    bvid = video_info["bvid"]
    aid = video_info["aid"]
    source = video_info.get("_source", "?")

    if bvid in done_set:
        return False

    try:
        # 详情
        async with sem_d:
            detail = await get_video_detail(session, cp, bvid)
            await asyncio.sleep(random.uniform(*SLEEP_DETAIL))
        if not detail:
            log.warning(f"详情失败: {bvid}")
            return False

        # 推断分区: 优先用 detail 里的 tname (官方分类), 不用 source 里的 ranking 信息
        category = detail.get("tname", "未分类")

        # 评论
        async with sem_c:
            comments = await get_comments(session, cp, aid, COMMENT_PAGES_PER_VIDEO)

        # 落地
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        COMMENT_DIR.mkdir(parents=True, exist_ok=True)

        video_record = {
            "bvid": bvid,
            "aid": aid,
            "category": category,
            "title": detail.get("title"),
            "desc": detail.get("desc"),
            "pubdate": detail.get("pubdate"),
            "duration": detail.get("duration"),
            "owner": detail.get("owner", {}),
            "stat": detail.get("stat", {}),
	    "tname": detail.get("tname") or "",
	    "tid": detail.get("tid"),
            "_source": source,    # 记录数据源 (论文用得上)
        }
        with open(VIDEO_DIR / f"{bvid}.json", "w", encoding="utf-8") as f:
            json.dump(video_record, f, ensure_ascii=False, indent=2)

        with open(COMMENT_DIR / f"{bvid}.json", "w", encoding="utf-8") as f:
            json.dump({"bvid": bvid, "aid": aid, "comments": comments},
                      f, ensure_ascii=False, indent=2)

        mark_done(bvid)
        log.info(f"✓ [{source}] {bvid} {category}  评论={len(comments)} 播放={detail['stat']['view']}")
        return True
    except Exception as e:
        log.error(f"处理 {bvid} 异常: {type(e).__name__}: {e}")
        return False


# ========== 收集所有视频源 ==========
async def collect_all_video_ids(session, cp):
    """从多个源收集视频 ID, 自动去重"""
    log.info("=" * 60)
    log.info("阶段1: 收集视频 ID")
    log.info("=" * 60)

    all_videos = {}   # bvid -> info

    # 源 A: 每周必看 (倒退抓 N 期)
    log.info(f"[源A] 每周必看 最近 {WEEKLY_RECENT_N} 期")
    for n in range(250, 250 - WEEKLY_RECENT_N, -1):
        videos = await fetch_weekly(session, cp, n)
        for v in videos:
            if v["bvid"] not in all_videos:
                all_videos[v["bvid"]] = v
        log.info(f"  第 {n} 期: 拿到 {len(videos)} 个 (累计去重 {len(all_videos)})")
        await asyncio.sleep(random.uniform(*SLEEP_LIST))

    # 源 B: 入站必刷
    log.info("[源B] 入站必刷")
    videos = await fetch_precious(session, cp)
    for v in videos:
        all_videos.setdefault(v["bvid"], v)
    log.info(f"  拿到 {len(videos)} 个 (累计去重 {len(all_videos)})")
    await asyncio.sleep(random.uniform(*SLEEP_LIST))

    # 源 C: 全站热门
    log.info(f"[源C] 全站热门 前 {POPULAR_PAGES} 页")
    for pn in range(1, POPULAR_PAGES + 1):
        videos = await fetch_popular(session, cp, pn)
        if not videos:
            break
        for v in videos:
            all_videos.setdefault(v["bvid"], v)
        await asyncio.sleep(random.uniform(*SLEEP_LIST))
    log.info(f"  累计去重 {len(all_videos)}")

    # 源 D: 分区排行榜
    log.info(f"[源D] {len(CATEGORIES)} 个分区排行榜")
    for rid, name in CATEGORIES:
        videos = await fetch_ranking(session, cp, rid)
        before = len(all_videos)
        for v in videos:
            all_videos.setdefault(v["bvid"], v)
        log.info(f"  {name}(rid={rid}): 新增 {len(all_videos)-before}")
        await asyncio.sleep(random.uniform(*SLEEP_LIST))

    log.info(f"=== 收集完成: 共 {len(all_videos)} 个唯一视频 ===")

    # 保存视频源 mapping (论文/答辩用)
    with open(SOURCE_LOG, "w", encoding="utf-8") as f:
        json.dump({k: v["_source"] for k, v in all_videos.items()},
                  f, ensure_ascii=False, indent=2)

    return list(all_videos.values())


# ========== 主流程 ==========
async def check_login(session, cp):
    """验证所有 Cookie 都有效"""
    for i, cookie in enumerate(cp.cookies):
        url = "https://api.bilibili.com/x/web-interface/nav"
        try:
            async with session.get(url, headers=make_headers(cookie), timeout=15) as r:
                data = await r.json()
                if data.get("code") == 0 and data["data"].get("isLogin"):
                    log.info(f"✓ Cookie {i+1} 有效, 用户: {data['data'].get('uname')}")
                else:
                    log.error(f"✗ Cookie {i+1} 无效: {data.get('message')}")
                    return False
        except Exception as e:
            log.error(f"✗ Cookie {i+1} 验证失败: {e}")
            return False
    return True


async def main():
    cp = CookiePool(COOKIES_DIR)
    log.info(f"加载了 {len(cp)} 个 Cookie")

    done_set = load_done_bvids()
    log.info(f"已完成 {len(done_set)} 个视频, 跳过它们")

    sem_d = asyncio.Semaphore(CONCURRENT_DETAIL)
    sem_c = asyncio.Semaphore(CONCURRENT_COMMENT)
    connector = aiohttp.TCPConnector(limit=10, ssl=False)
    cookie_jar = aiohttp.CookieJar()

    async with aiohttp.ClientSession(connector=connector, cookie_jar=cookie_jar) as session:
        if not await check_login(session, cp):
            log.error("Cookie 验证失败, 退出")
            return

        # 1. 收集所有视频 ID
        all_videos = await collect_all_video_ids(session, cp)

        # 2. 抓取
        log.info("=" * 60)
        log.info(f"阶段2: 抓取 {len(all_videos)} 个视频的详情和评论")
        log.info("=" * 60)
        log.info(f"预计耗时: {len(all_videos) * 30 / 60:.1f}-{len(all_videos) * 60 / 60:.1f} 分钟")

        # 过滤掉已完成的
        todo = [v for v in all_videos if v["bvid"] not in done_set]
        log.info(f"实际待抓: {len(todo)} (已跳过 {len(all_videos) - len(todo)} 个)")

        tasks = [process_video(session, cp, sem_d, sem_c, v, done_set) for v in todo]
        results = await asyncio.gather(*tasks)
        success_count = sum(1 for r in results if r)

        log.info("=" * 60)
        log.info(f"=== 全部完成 ===")
        log.info(f"成功: {success_count} / 待抓: {len(todo)} / 总收集: {len(all_videos)}")


if __name__ == "__main__":
    start = time.time()
    asyncio.run(main())
    log.info(f"总耗时 {(time.time()-start)/60:.1f} 分钟")
