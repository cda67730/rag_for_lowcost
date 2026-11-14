/* 後台共用：API 呼叫、登入狀態、畫面工具 */
(function () {
  const A = (window.A = {});
  const C = window.ADMIN_CONFIG || {};
  A.views = {};
  A.$ = (s, r) => (r || document).querySelector(s);
  A.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  A.esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  A.fmtTime = (t) => (t ? new Date(t).toLocaleString("zh-TW", { hour12: false }) : "");
  A.pct = (v) => (v == null ? "—" : v + "%");

  // 登入憑證只放 sessionStorage：關掉分頁就要重新登入；被擋（無痕或停用儲存）時退回記憶體
  let mem = "";
  A.token = {
    get() { try { return sessionStorage.getItem("admin_token") || mem; } catch (e) { return mem; } },
    set(t) { mem = t || ""; try { t ? sessionStorage.setItem("admin_token", t) : sessionStorage.removeItem("admin_token"); } catch (e) {} },
  };

  A.base = () => String(C.API_URL || "").replace(/\/+$/, "");

  /** 呼叫 /api/admin；錯誤丟出 Error（含 code、fields、status）。opts.raw＝回 Response；opts.body 可是 FormData 或字串 */
  A.api = async function (method, path, body, opts) {
    opts = opts || {};
    if (!A.base()) throw new Error("尚未設定 API 網址（admin/config.js）");
    const h = {};
    const t = A.token.get();
    if (t) h.Authorization = "Bearer " + t;
    let b;
    if (body instanceof FormData || typeof body === "string" || body instanceof Blob) b = body;
    else if (body !== undefined) { h["Content-Type"] = "application/json"; b = JSON.stringify(body); }
    let res;
    try { res = await fetch(A.base() + "/api/admin" + path, { method, headers: h, body: b }); }
    catch (e) { throw new Error("連不到伺服器，請確認網址與網路"); }
    if (res.status === 401 && !path.startsWith("/auth/login")) { A.token.set(""); A.go("#/login"); throw Object.assign(new Error("請重新登入"), { status: 401 }); }
    if (opts.raw && res.ok) return res;
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      let msg = d.error || (res.status === 404 ? "找不到" : "伺服器錯誤（" + res.status + "）");
      if (d.fields) msg += "：" + Object.entries(d.fields).map(([k, v]) => k + " " + (typeof v === "string" ? v : JSON.stringify(v))).join("；");
      throw Object.assign(new Error(msg), { code: d.code, fields: d.fields, status: res.status });
    }
    return d;
  };

  /** 下載檔案（帶登入憑證） */
  A.download = async function (path, name) {
    const res = await A.api("GET", path, undefined, { raw: true });
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  let toastT;
  A.toast = function (msg, kind) {
    const el = A.$("#toast");
    el.textContent = msg;
    el.className = kind === "err" ? "err" : "";
    el.style.display = "block";
    clearTimeout(toastT);
    toastT = setTimeout(() => (el.style.display = "none"), kind === "err" ? 6000 : 2600);
  };
  /** 包住按鈕動作：執行中停用按鈕、錯誤顯示 toast */
  A.act = async function (btn, fn, okMsg) {
    if (btn) btn.disabled = true;
    try { const r = await fn(); if (okMsg) A.toast(okMsg); return r; }
    catch (e) { if (e.status !== 401) A.toast(e.message, "err"); }
    finally { if (btn) btn.disabled = false; }
  };

  A.side = {
    open(title, html) { A.$("#sideTitle").textContent = title; A.$("#sideBody").innerHTML = html; A.$("#side").classList.add("open"); A.$("#side").setAttribute("aria-hidden", "false"); return A.$("#sideBody"); },
    close() { A.$("#side").classList.remove("open"); A.$("#side").setAttribute("aria-hidden", "true"); },
  };

  A.go = (h) => { if (location.hash !== h) location.hash = h; else window.dispatchEvent(new HashChangeEvent("hashchange")); };
  A.crumbs = (items) => {
    A.$("#crumbs").innerHTML = items.map((x) => (x[1] ? `<a href="${x[1]}">${A.esc(x[0])}</a>` : `<span>${A.esc(x[0])}</span>`)).join("<span>›</span>");
  };
  A.tabs = (base, list, cur) => `<nav class="tabs">${list.map(([k, t]) => `<a href="${base}/${k}" class="${k === cur ? "on" : ""}">${A.esc(t)}</a>`).join("")}</nav>`;
  A.pill = (s, label) => `<span class="pill ${A.esc(s)}">${A.esc(label || s)}</span>`;
  A.STATUS = { processing: "處理中", pending: "待審核", approved: "已核可", disabled: "停用", failed: "失敗", queued: "排隊中", running: "進行中", done: "完成", cancelled: "已取消",
    active: "啟用", draft: "草稿", retired: "停用" };
  A.st = (s) => A.pill(s, A.STATUS[s] || s);

  /** 等背景工作完成（每 1.5 秒查一次）；onTick 拿到進度 */
  A.waitJob = async function (jobId, onTick) {
    for (;;) {
      const j = await A.api("GET", "/jobs/" + jobId);
      if (onTick) onTick(j);
      if (["done", "failed", "cancelled"].includes(j.status)) return j;
      await new Promise((r) => setTimeout(r, 1500));
    }
  };

  // 目前登入者與他在各工作區的角色（/me）
  A.me = null;
  A.roleIn = (wsId, botId) => {
    const w = A.me && A.me.workspaces.find((x) => x.id === wsId);
    if (!w) return [];
    return w.roles.concat(w.bots.filter((b) => !botId || b.bot_id === botId).map((b) => b.role));
  };
  A.can = (wsId, roles, botId) => A.roleIn(wsId, botId).some((r) => r === "owner" || roles.includes(r));
})();
