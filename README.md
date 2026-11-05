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

GitHub Pages 只能托管静态文件，本项目依赖 Flask API，不能直接作为 Pages 站点运行。仓库可存放源码；线上需自备可跑 Python 的主机。
