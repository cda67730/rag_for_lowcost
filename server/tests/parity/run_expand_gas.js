const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const src = fs.readFileSync(path.join(gas, "Knowledge.gs"), "utf8");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { __syn: [] }; vm.createContext(sb);
vm.runInContext(src + `\nfunction synonyms_() { return __syn; }
  this.__run = (c) => { __syn = c.syn; const e = expandQuery_(c.text);
    const keys = Array.from(new Set((e.concepts || []).concat(String(c.text).match(/[一-鿿]{2,4}/g) || []))).slice(0, 30);   // 與 searchDetail_ 第 149 行相同
    return { e: e, keys: keys }; };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
