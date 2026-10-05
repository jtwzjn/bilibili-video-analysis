import asyncio
from bilibili_api import comment, Credential

# 读取 Cookie
with open("cookie.txt", "r") as f:
    cookie_str = f.read().strip()

# 从 Cookie 中提取关键字段
cookies = {}
for item in cookie_str.split("; "):
    if "=" in item:
        k, v = item.split("=", 1)
        cookies[k] = v

# 构建凭证对象
credential = Credential(
    sessdata=cookies.get("SESSDATA", ""),
    bili_jct=cookies.get("bili_jct", ""),
    buvid3=cookies.get("buvid3", ""),
    dedeuserid=cookies.get("DedeUserID", ""),
    buvid4=cookies.get("buvid4", ""),
)

async def main():
    # 测试一个热门视频的 oid（aid）
    oid = 1112985577  # 可换成任意已知热门视频的 aid
    c = comment.Comment(
        oid=oid,
        type_=comment.CommentResourceType.VIDEO,
        credential=credential
    )
    
    page = 1
    comments = await c.get_comments(page_index=page)
    print(f"第 {page} 页获取到 {len(comments['replies'])} 条评论")
    if comments['replies']:
        for r in comments['replies'][:3]:
            print(f"  - {r['content']['message'][:80]}")

asyncio.run(main())
