import asyncio
import re

import httpx
from bs4 import BeautifulSoup

async def main() -> None:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
        hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.85po.com/"}
        r = await c.get("https://www.85po.com/", headers=hdr)
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href]"):
            h = (a.get("href") or "").strip()
            t = a.get_text(strip=True)
            if h.startswith("/") and len(t) < 40 and t and "login" not in h.lower():
                if any(x in h for x in ("latest", "top", "popular", "4k", "tag", "random", "most")):
                    print(t[:30], h)


if __name__ == "__main__":
    asyncio.run(main())
