"""
B 站热门视频与评论爬虫 (412 优化版)
关键改动:
1. 自动获取游客 Cookie (buvid3)
2. 并发降到 1, 间隔拉长
3. 遇到 412 自动暂停 5-10 分钟
4. 完整请求头模拟真实浏览器
"""
import asyncio
import aiohttp
import json
import random
import time
import logging
from pathlib import Path

# ========== 配置区 ==========
BASE_DIR = Path(__file__).parent
VIDEO_DIR = BASE_DIR / "data" / "raw" / "videos"
COMMENT_DIR = BASE_DIR / "data" / "raw" / "comments"
LOG_DIR = BASE_DIR / "logs"
DONE_FILE = BASE_DIR / "logs" / "done_bvids.txt"

CATEGORIES = [
    (188, "科技"),
    (36,  "知识"),
    (4,   "游戏"),
    (211, "美食"),
    (1,   "动画"),
    (181, "影视"),
    (160, "生活"),
    (3,   "音乐"),
]
COMMENT_PAGES_PER_VIDEO = 10
CONCURRENT = 1                       # 并发改成 1, 串行最稳
SLEEP_RANGE = (3.0, 5.0)             # 间隔拉长到 3-5 秒
LONG_PAUSE_AFTER_412 = (300, 600)    # 触发 412 后暂停 5-10 分钟

# 完整请求头, 模拟真实 Chrome
BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
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
        logging.FileHandler(LOG_DIR / "crawler.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger()


# ========== 工具函数 ==========
def load_done_bvids():
    if not DONE_FILE.exists():
        return set()
    return set(DONE_FILE.read_text(encoding="utf-8").strip().split("\n"))

def mark_done(bvid):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(bvid + "\n")


async def init_cookies(session):
    """访问 B 站首页, 让 server 下发游客 Cookie (buvid3 等)"""
    log.info("初始化游客 Cookie...")
    try:
        async with session.get("https://www.bilibili.com/", headers=BASE_HEADERS, timeout=15) as r:
            await r.text()
            cookies = {k: v.value for k, v in session.cookie_jar.filter_cookies("https://www.bilibili.com").items()}
            log.info(f"已获取 Cookie 字段: {list(cookies.keys())}")
            if "buvid3" not in cookies:
                log.warning("⚠ 未拿到 buvid3, 可能依然会触发 412")
    except Exception as e:
        log.warning(f"Cookie 初始化失败: {e}")


async def fetch_json(session, url, retry=3):
    """带 412 处理的 GET"""
    for i in range(retry):
        try:
            async with session.get(url, headers=BASE_HEADERS, timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("code") == 0:
                        return data["data"]
                    log.warning(f"API code={data.get('code')} msg={data.get('message')} url={url}")
                    # code=-412 也是风控
                    if data.get("code") == -412:
                        pause = random.randint(*LONG_PAUSE_AFTER_412)
                        log.warning(f"⛔ 触发 -412 风控, 暂停 {pause} 秒...")
                        await asyncio.sleep(pause)
                elif r.status == 412:
                    pause = random.randint(*LONG_PAUSE_AFTER_412)
                    log.warning(f"⛔ HTTP 412 风控, 暂停 {pause} 秒...")
                    await asyncio.sleep(pause)
                else:
                    log.warning(f"HTTP {r.status}: {url}")
        except Exception as e:
            log.warning(f"请求异常 (第{i+1}次): {url} -> {e}")
        await asyncio.sleep(3 + i * 3)
    return None


# ========== 爬取逻辑 ==========
async def get_ranking_list(session, rid):
    url = f"https://api.bilibili.com/x/web-interface/ranking/v2?rid={rid}&type=all"
    data = await fetch_json(session, url)
    if not data or "list" not in data:
        return []
    return [{"bvid": v["bvid"], "aid": v["aid"]} for v in data["list"]]

async def get_video_detail(session, bvid):
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    return await fetch_json(session, url)

async def get_comments(session, aid, pages):
    all_comments = []
    for pn in range(1, pages + 1):
        url = f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&next={pn}"
        data = await fetch_json(session, url)
        if not data or not data.get("replies"):
            break
        for r in data["replies"]:
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
        await asyncio.sleep(random.uniform(*SLEEP_RANGE))
    return all_comments

async def process_video(session, sem, video_info, category_name, done_set):
    bvid = video_info["bvid"]
    aid = video_info["aid"]

    if bvid in done_set:
        return

    async with sem:
        try:
            detail = await get_video_detail(session, bvid)
            if not detail:
                log.warning(f"详情失败: {bvid}")
                return
            await asyncio.sleep(random.uniform(*SLEEP_RANGE))

            comments = await get_comments(session, aid, COMMENT_PAGES_PER_VIDEO)

            video_record = {
                "bvid": bvid,
                "aid": aid,
                "category": category_name,
                "title": detail.get("title"),
                "desc": detail.get("desc"),
                "pubdate": detail.get("pubdate"),
                "duration": detail.get("duration"),
                "owner": detail.get("owner", {}),
                "stat": detail.get("stat", {}),
                "tname": detail.get("tname"),
                "tid": detail.get("tid"),
            }
            VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            COMMENT_DIR.mkdir(parents=True, exist_ok=True)

            with open(VIDEO_DIR / f"{bvid}.json", "w", encoding="utf-8") as f:
                json.dump(video_record, f, ensure_ascii=False, indent=2)

            with open(COMMENT_DIR / f"{bvid}.json", "w", encoding="utf-8") as f:
                json.dump({"bvid": bvid, "aid": aid, "comments": comments},
                          f, ensure_ascii=False, indent=2)

            mark_done(bvid)
            log.info(f"✓ {category_name} {bvid}  评论={len(comments)}  "
                     f"播放={detail['stat']['view']}  点赞={detail['stat']['like']}")
        except Exception as e:
            log.error(f"处理 {bvid} 时异常: {e}")


async def main():
    done_set = load_done_bvids()
    log.info(f"已完成 {len(done_set)} 个视频, 本次跳过它们")

    sem = asyncio.Semaphore(CONCURRENT)
    connector = aiohttp.TCPConnector(limit=CONCURRENT, ssl=False)
    cookie_jar = aiohttp.CookieJar()

    async with aiohttp.ClientSession(connector=connector, cookie_jar=cookie_jar) as session:
        # 关键: 先访问首页拿 Cookie
        await init_cookies(session)
        await asyncio.sleep(2)

        for rid, name in CATEGORIES:
            log.info(f"=== 开始分区: {name} (rid={rid}) ===")
            video_list = await get_ranking_list(session, rid)
            log.info(f"  分区 {name} 排行榜共 {len(video_list)} 个视频")
            await asyncio.sleep(random.uniform(*SLEEP_RANGE))

            tasks = [process_video(session, sem, v, name, done_set) for v in video_list]
            await asyncio.gather(*tasks)

    log.info("=== 全部完成 ===")


if __name__ == "__main__":
    start = time.time()
    asyncio.run(main())
    log.info(f"总耗时 {(time.time()-start)/60:.1f} 分钟")
