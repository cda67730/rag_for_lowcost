#!/usr/bin/env bash
# 一鍵建立 Apps Script 專案並推上程式（需要 Node.js）。執行後照畫面提示做剩下的兩步：設定金鑰、部署。
set -e
cd "$(dirname "$0")"
TITLE="${1:-AI 知識問答}"
command -v npx >/dev/null || { echo "需要 Node.js（含 npx）"; exit 1; }
echo "⓪ 請先確認已在 https://script.google.com/home/usersettings 開啟「Google Apps Script API」（只需一次），否則下一步建立專案會被拒絕。"
read -r -p "   已開啟的話按 Enter 繼續… "
echo "① 登入 Google（會開瀏覽器，用你的 Google 帳號授權 clasp）"; npx -y @google/clasp login
echo "② 建立試算表與綁定的 Apps Script 專案：$TITLE"
npx -y @google/clasp create --type sheets --title "$TITLE" --rootDir .
echo "③ 推上程式"; npx -y @google/clasp push -f
echo
echo "完成。接下來在瀏覽器裡做："
echo "  1. npx @google/clasp open-container  →  開啟剛建立的試算表，重新整理後選單「AI 助手後台 → 設定 API 金鑰」貼上 Gemini 金鑰"
echo "  2. 選單「AI 助手後台 → 初始化工作表（setup）」（第一次會要求授權）"
echo "  3. npx @google/clasp open  →  Apps Script 編輯器：部署 → 新增部署作業 → 網頁應用程式（執行身分：我／存取：所有人），複製 /exec 網址"
echo "  4. 把網址填進 web/chat/config.js 的 GAS_URL"
