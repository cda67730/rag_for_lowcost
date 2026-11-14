// 用 apps-script/Llm.gs 組請求，輸出 url 與 body，給 Python 比對（不送出）
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "..", "apps-script", "Llm.gs"), "utf8");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console, PropertiesService: { getScriptProperties: () => ({ getProperty: () => "K" }) }, __cfg: {} };
vm.createContext(sb);
vm.runInContext(src + "\nfunction cfg_(k){ return __cfg[k]; }\nthis.__gen = (m,o) => genReq_(m,[{text:'P'}],o); this.__emb = (t,tt) => embedReqs_(t,tt);", sb);
const out = cases.map((c) => {
  if (c.kind === "gen") { const r = sb.__gen(c.model, c.opts); return { url: r.url, body: JSON.parse(r.payload) }; }
  sb.__cfg = { model_embed: c.model, embed_dim: c.dim };
  return sb.__emb(c.texts, c.task).map((r) => ({ url: r.url, body: JSON.parse(r.payload) }));
});
process.stdout.write(JSON.stringify(out));
