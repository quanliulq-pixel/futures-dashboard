# AI 应用落地研究快照

- 唯一维护源：`data/ai_apps.json`，`schema_version: 1`；页面从 `/api/ai-apps` 按需读取。
- `as_of` 是人工复核截止日（`timezone: Asia/Tokyo`），不是行情时间，也不是所有公司的报告期。
- `sources` 保存标题、原文 URL、发布日期、核验日期和来源类型；公司、板块及政策均通过 `source_ids` 引用。事实、主观判断、反面风险和下一步验证分栏显示。
- 资料优先为公司公告/正式报告、政策原文；阿里公告为 Business Wire 的 Morningstar 转载。公司自报指标不等于独立审计的 AI 增量收入。
- 更新公司证据时同步核对报告期、收入统计口径、实际值/目标值和链接日期。不用总收入、云收入、广告收入或 ARR/ACV 冒充独立 AI 收入；没有明确数据填写“无数据”。不要推算胜率、价格或目标价。
- 排名与分层是人工商业化研究判断，不是自动打分、不影响既有交易/基本面评分，也不是投资建议。A股政策用于评估场景、国产适配和验收回款，不代表收益保证。
- 新财报、重大事故或商业模式改变后应人工复核；页面不自动抓取或定时更新。

仅同步 UI 与本研究快照（**不要运行会强制重抓行情的 `main()`**）：

```sh
/tmp/yfv/bin/python -c 'from export_static_pages import export_html; export_html()'
/tmp/yfv/bin/python -m pytest -q -p no:cacheprovider tests/test_ai_apps.py tests/test_ui_polish.py
node tests/ai_apps_view.test.js
```

静态页对应 `docs/api/ai_apps.json`，脚本和模板由 `export_html()` 同步；两个站点显示同一份研究。
