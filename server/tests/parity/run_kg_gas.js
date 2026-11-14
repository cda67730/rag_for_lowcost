// 用 apps-script 原版 kgMatch_ 比對；節點、關係、標註由測試給定
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const src = fs.readFileSync(path.join(gas, "Graph.gs"), "utf8");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = {}; vm.createContext(sb);
vm.runInContext(src + `
  let __c = null;
  function kgHasGraph_() { return true; }
  function kgNodes_() { return __c.nodes.map(n => ({ '節點ID': n.id, '名稱': n.name, '類型': n.type, '同義說法': n.aliases.join('、'), '定義': n.def, '實務要點': n.tips })); }
  function rows_(t) { if (t === 'kgEdges') return __c.edges.map(e => ({ '主詞': e.subj, '關係': e.rel, '受詞': e.obj }));
    return __c.links.map(l => ({ '段落ID': l.chunk, '節點ID': l.node, '標籤': l.tag === 'secondary' ? KG_TAG_SUB : KG_TAG_MAIN })); }
  this.__run = (c) => { __c = c; return kgMatch_(c.text, c.concepts, { kg_max_nodes: c.max }); };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
