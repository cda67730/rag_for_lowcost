// apps-script 原版 chunkText_
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "..", "..", "apps-script", "Knowledge.gs"), "utf8");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const sb = {}; vm.createContext(sb);
vm.runInContext(src + "\nfunction cfg_() { return ''; }\nthis.__run = (c) => chunkText_(c.text, c.size, c.overlap);", sb);
process.stdout.write(JSON.stringify(cases.map((c) => sb.__run(c))));
