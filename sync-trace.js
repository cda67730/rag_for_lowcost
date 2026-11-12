// 把 web/chat/trace.js 包成 Apps Script 後台可以引入的 TraceView.html（兩邊用同一份呈現程式）
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "web/chat/trace.js"), "utf8");
fs.writeFileSync(path.join(__dirname, "gas/TraceView.html"), "<script>\n" + src + "</script>\n");
console.log("已更新 gas/TraceView.html");
