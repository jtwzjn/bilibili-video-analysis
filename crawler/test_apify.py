import os
import asyncio
from apify_client import ApifyClient

# ========== 配置区 ==========
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
VIDEO_AID = 1112985577          # 测试用的视频 aid，可换成你抓到的任意热门视频
MAX_COMMENTS = 50               # 测试只抓 50 条，确认能跑通

async def main():
    client = ApifyClient(APIFY_TOKEN)
    
    # 调用 Bilibili Comments Actor (已弃用但先测试能否跑)
    actor = client.actor("bishi/bilibili-comments")
    
    run_input = {
        "aid": VIDEO_AID,
        "maxResults": MAX_COMMENTS,
    }
    
    print(f"正在获取 aid={VIDEO_AID} 的评论...")
    run = actor.call(run_input=run_input)
    
    # 获取结果
    comments = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        comments.append(item)
    
    print(f"成功获取 {len(comments)} 条评论")
    
    if comments:
        print("\n前 3 条评论预览：")
        for i, c in enumerate(comments[:3]):
            content = c.get("content", c.get("text", str(c)))[:100]
            print(f"  {i+1}. {content}")
    else:
        print("警告：未获取到任何评论，该 Actor 可能已失效")
    
    # 保存结果到本地
    import json
    with open("test_apify_result.json", "w", encoding="utf-8") as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到 test_apify_result.json")

asyncio.run(main())
