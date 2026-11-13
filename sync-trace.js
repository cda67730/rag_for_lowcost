// 把 web/chat/ 的共用呈現程式包成 Apps Script 後台可以引入的 HTML（兩邊用同一份）：trace.js → TraceView.html、answer.js → AnswerView.html
const fs = require("fs"), path = require("path");
[["trace.js", "TraceView.html"], ["answer.js", "AnswerView.html"]].forEach(([src, dst]) => {
  const code = fs.readFileSync(path.join(__dirname, "web/chat", src), "utf8");
  fs.writeFileSync(path.join(__dirname, "apps-script", dst), "<script>\n" + code + "</script>\n");
  console.log("已更新 apps-script/" + dst);
});
