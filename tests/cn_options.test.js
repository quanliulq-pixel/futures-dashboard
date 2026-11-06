// node tests/cn_options.test.js
const assert = require("assert");
const c = require("../static/cn_options.js");
const loc = (h, q = "") => ({ hostname: h, search: q });
assert.strictEqual(c.resolveUrl("put", loc("quanliulq-pixel.github.io"), {}), "./api/cn_short_put.json");
assert.strictEqual(c.resolveUrl("call", loc("x.onrender.com", "?static=1"), {}), "./api/cn_short_call.json");
assert.strictEqual(c.resolveUrl("call", loc("x.onrender.com"), { STATIC_MODE: true }), "./api/cn_short_call.json");
assert.strictEqual(c.resolveUrl("put", loc("futures-dashboard-5e5b.onrender.com"), {}), "/api/cn-short-put");
assert.throws(() => c.resolveUrl("x", loc("a"), {}));
const now = new Date("2026-11-06T07:00:00Z"); // 15:00 CST
assert.ok(c.isExpiredRow({ market: "cn_index", expiry: "2026-11-05" }, now));
assert.ok(!c.isExpiredRow({ market: "cn_index", expiry: "2026-11-06" }, now));
assert.ok(c.isExpiredRow({ market: "cn_index" }, now));
assert.ok(c.isExpiredRow({ market: "cn_commodity", contract_month: "cu2611" }, now));
assert.ok(!c.isExpiredRow({ market: "cn_commodity", contract_month: "cu2612" }, now));
const f = { ...c.UI_DEFAULTS, product: "all", includeStale: false };
const base = { market: "cn_index", expiry: "2026-11-20", dte: 14, otm_pct: 5, oi: 500, product: "IO", freshness: "last_session" };
const rows = [base, { ...base, expiry: "2026-10-16" }, { ...base, freshness: "stale" }, { ...base, freshness: "unknown" },
  { ...base, otm_pct: 20 }, { ...base, oi: 10 }, { ...base, dte: 90 }, { ...base, product: "HO" }];
assert.strictEqual(c.filterRows(rows, f, "cn_index", now).length, 2);
assert.strictEqual(c.filterRows(rows, { ...f, product: "HO" }, "cn_index", now).length, 1);
assert.strictEqual(c.filterRows(rows, { ...f, includeStale: true }, "cn_index", now).length, 4);
const com = { market: "cn_commodity", contract_month: "m2701", otm_pct: 5, oi: 500, product: "m", freshness: "delayed", dte: null };
assert.strictEqual(c.filterRows([com], f, "cn_commodity", now).length, 1); // 商品不按 DTE 过滤
console.log("cn_options.test.js ok");
