// 用 apps-script 的去識別化函式跑一組輸入，輸出結果給 Python 比對
const fs = require("fs"), vm = require("vm"), path = require("path");
const gas = path.join(__dirname, "..", "..", "..", "apps-script");
const src = fs.readFileSync(path.join(gas, "Setup.gs"), "utf8").replace("function setup()", "function setupX__()") + "\n" + fs.readFileSync(path.join(gas, "Chat.gs"), "utf8");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = { console, __cfg: {} }; vm.createContext(sb);
vm.runInContext(src + `
  const __base = {}; SETTINGS_DEFAULT.forEach(r => __base[r[0]] = r[1]);
  function cfgAll_() { return __cfg; } function cfg_(k) { return __cfg[k]; } function stabNote_() {}
  this.__run = (c) => { __cfg = Object.assign({}, __base, c.cfg || {});
    if (c.fn === 'scrub') return deidScrub_(c.text);
    if (c.fn === 'rule') return deidRule_(c.text, c.map);
    if (c.fn === 'items') return deidFromItems_(c.text, c.map, c.items);
    if (c.fn === 'instr') return deidInstr_(c.known);
  };`, sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
