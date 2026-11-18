# 產生 App 圖示與 manifest（GitHub Actions 每次建置時執行）
import json, os
from PIL import Image, ImageDraw
os.makedirs("docs", exist_ok=True)
for n in (180, 192, 512):
    im = Image.new("RGB", (n, n), "#d2491f"); d = ImageDraw.Draw(im)
    c, r, w = n/2, n*0.34, max(3, n//28)
    d.ellipse([c-r, c-r, c+r, c+r], outline="white", width=w)
    d.line([c-r, c, c+r, c], fill="white", width=w); d.line([c, c-r, c, c+r], fill="white", width=w)
    d.arc([c-r*1.75, c-r, c-r*0.25, c+r], -60, 60, fill="white", width=w)
    d.arc([c+r*0.25, c-r, c+r*1.75, c+r], 120, 240, fill="white", width=w)
    im.save(f"docs/icon-{n}.png")
json.dump({"name": "NBA 每日情報", "short_name": "NBA 情報", "start_url": "./", "display": "standalone",
           "background_color": "#f3f4f1", "theme_color": "#d2491f",
           "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                     {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}]},
          open("docs/manifest.json", "w", encoding="utf-8"), ensure_ascii=False)
open("docs/.nojekyll", "w").close()
