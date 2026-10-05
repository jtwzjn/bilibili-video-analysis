"""
B 站热门视频与评论爬虫 (Cookie 登录版)
使用前: 把浏览器登录后的完整 Cookie 粘贴到 LOGIN_COOKIE
"""
import os
import asyncio
import aiohttp
import json
import random
import time
import logging
from pathlib import Path

# ==========【必填】粘贴你的登录 Cookie ==========
# 浏览器登录小号 -> F12 -> Network -> 刷新 -> 找 api.bilibili.com 请求 -> 复制 cookie 整串
LOGIN_COOKIE = os.environ.get("BILI_COOKIE", "")  # 从环境变量读取；也可改为读取 cookies/cookie.txt
# ================================================

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
CONCURRENT = 1
SLEEP_RANGE = (2.0, 4.0)              # 登录态可以稍快
LONG_PAUSE_AFTER_412 = (600, 1200)    # 仍然保留, 万一中途又被风控

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
    "Cookie": LOGIN_COOKIE,
}

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


def load_done_bvids():
    if not DONE_FILE.exists():
        return set()
    return set(DONE_FILE.read_text(encoding="utf-8").strip().split("\n"))

def mark_done(bvid):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(bvid + "\n")


async def fetch_json(session, url, retry=3):
    for i in range(retry):
        try:
            async with session.get(url, headers=BASE_HEADERS, timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("code") == 0:
                        return data["data"]
                    log.warning(f"API code={data.get('code')} msg={data.get('message')} url={url}")
                    if data.get("code") == -412:
                        pause = random.randint(*LONG_PAUSE_AFTER_412)
                        log.warning(f"⛔ -412 风控, 暂停 {pause}s")
                        await asyncio.sleep(pause)
                    elif data.get("code") == -101:
                        log.error("⛔ Cookie 失效, 请重新获取!")
                        return None
                elif r.status == 412:
                    pause = random.randint(*LONG_PAUSE_AFTER_412)
                    log.warning(f"⛔ HTTP 412, 暂停 {pause}s")
                    await asyncio.sleep(pause)
                else:
                    log.warning(f"HTTP {r.status}: {url}")
        except Exception as e:
            log.warning(f"请求异常 (第{i+1}次): {url} -> {e}")
        await asyncio.sleep(3 + i * 3)
    return None


async def check_login(session):
    """开始抓之前先验证 Cookie 是否有效"""
    url = "https://api.bilibili.com/x/web-interface/nav"
    async with session.get(url, headers=BASE_HEADERS, timeout=15) as r:
        data = await r.json()
        if data.get("code") == 0 and data["data"].get("isLogin"):
            uname = data["data"].get("uname", "?")
            log.info(f"✓ Cookie 有效, 当前用户: {uname}")
            return True
        else:
            log.error(f"✗ Cookie 无效或未登录: {data.get('message')}")
            return False


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
    if "SESSDATA" not in LOGIN_COOKIE:
        log.error("⛔ LOGIN_COOKIE 还没填! 请按文档说明粘贴登录 Cookie")
        return

    done_set = load_done_bvids()
    log.info(f"已完成 {len(done_set)} 个视频")

    sem = asyncio.Semaphore(CONCURRENT)
    connector = aiohttp.TCPConnector(limit=CONCURRENT, ssl=False)

    async with aiohttp.ClientSession(connector=connector) as session:
        # 验证登录
        if not await check_login(session):
            return
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
