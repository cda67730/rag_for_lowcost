// 聊天頁的後端設定（docs/api.md）。換後端只要改這裡
window.CHAT_CONFIG = {
  BACKEND: "apps-script",   // "apps-script" 或 "server"（Python 版）
  // Apps Script 網頁應用程式的網址（部署後的 /exec）
  GAS_URL: "https://script.google.com/macros/s/AKfycbyM3oZqfOy4vW4LwkqSRkUwYapPq_81R9z8pEeYEXdcbJpF20i1skezJkayUPw4gesZ/exec",
  // BACKEND="server" 時用：Python 版 API 的網址與預設機器人（網址的 ?bot=<slug> 優先）
  API_URL: "",
  BOT: ""
};
