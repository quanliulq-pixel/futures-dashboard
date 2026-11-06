# 期货看板 · 高低位与成本

本地 Flask 投研看板：国内 + 北美商品期货行情、历史高低位、成本带、产业供需周报、美股涨跌榜、天然气 NG 九因子。

## 运行

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

浏览器打开 http://127.0.0.1:5050

Windows 也可双击 `start_dashboard.bat`；周报数据用 `fetch_supply.bat`。

## 说明

静态快照可走 GitHub Pages；要实时刷新请部署 Flask（见下方 Render）。


## GitHub Pages（静态快照）

定时导出在 `docs/`，站点：[https://quanliulq-pixel.github.io/futures-dashboard/](https://quanliulq-pixel.github.io/futures-dashboard/)

## Render（动态站）

仓库已含 `render.yaml` / `Procfile`。在 [Render Dashboard](https://dashboard.render.com) 用 GitHub 登录后：

1. **New → Blueprint**，选本仓库，按 `render.yaml` 创建；或 **New → Web Service**，连 `quanliulq-pixel/futures-dashboard`，Build `pip install -r requirements.txt`，Start 用 Procfile。
2. 免费档闲置约 15 分钟会休眠，首次打开可能要等几十秒。
3. 部署成功后打开 `https://<服务名>.onrender.com`，前端会自动走实时 `/api/*`（非 github.io 即动态模式）。

卖 Put 全市场扫描较慢，点「重新扫描」可能要 1–2 分钟。
