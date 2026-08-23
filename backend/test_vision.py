import asyncio
import base64
from pathlib import Path
from agent.vision import analyze_screenshot

async def main():
    img_path = Path("debug_sessions/20260619_025131/step_01.png")
    if not img_path.exists():
        print("Image not found")
        return
    img_b64 = base64.b64encode(img_path.read_bytes()).decode("utf-8")
    
    accounts = [{"label": "Duck.ai Chat", "url": "https://duck.ai/chat", "username": "", "password": ""}]
    
    res = await analyze_screenshot(img_b64, "свежие новости", accounts=accounts)
    print("VISION RESULT:", res)

asyncio.run(main())
