/* 左側選單：工作區功能、機器人、知識庫（目前這個展開它的分頁）、系統與帳號。
   沿用各頁的 A.crumbs／A.tabs：記下目前工作區與分頁，換頁後重畫選單；寬螢幕把頁內分頁收進選單 */
(function () {
  let wsId = null, tabs = null;
  const cache = {};            // wsId → { bots, kbs, at }
  const crumbs0 = A.crumbs, tabs0 = A.tabs;
  A.crumbs = (items) => {
    crumbs0(items);
    const m = items[0] && items[0][1] && String(items[0][1]).match(/^#\/ws\/([^/]+)/);
    wsId = m ? m[1] : null;
    tabs = null;
  };
  A.tabs = (base, list, cur) => {
    if (!tabs) tabs = { base, list, cur };       // 只收每頁第一組（主分頁）
    return tabs0(base, list, cur);
  };

  const ICON = {
    bots: "M4 7h16v11H4z M9 12h.01 M15 12h.01 M12 3v4", kbs: "M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3z M5 17a3 3 0 013-3h11",
    members: "M8 11a3 3 0 100-6 3 3 0 000 6z M2 20a6 6 0 0112 0 M17 11a3 3 0 100-6 M16 14a6 6 0 016 6", audit: "M6 3h9l4 4v14H6z M9 12h7 M9 16h7 M9 8h4",
    system: "M12 15a3 3 0 100-6 3 3 0 000 6z M19 12a7 7 0 00-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 00-2-1.2L14 3h-4l-.5 2.6a7 7 0 00-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 005 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 002 1.2L10 21h4l.5-2.6a7 7 0 002-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z",
    account: "M12 12a4 4 0 100-8 4 4 0 000 8z M4 21a8 8 0 0116 0", home: "M3 11l9-7 9 7v9a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1z",
  };
  const ico = (k) => k === "dot" ? `<i class="ndot"></i>` : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICON[k] || ICON.home}"/></svg>`;
  const link = (href, text, icon, on, extra = "") => `<a href="${href}" class="${on ? "on" : ""}">${icon ? ico(icon) : ""}<span>${A.esc(text)}</span>${extra}</a>`;

  async function lists(id) {
    const c = cache[id];
    if (c && Date.now() - c.at < 30000) return c;
    const [b, k] = await Promise.all([A.api("GET", `/workspaces/${id}/bots`).catch(() => ({ items: [] })), A.api("GET", `/workspaces/${id}/kbs`).catch(() => ({ items: [] }))]);
    return (cache[id] = { bots: b.items || [], kbs: k.items || [], at: Date.now() });
  }
  A.nav = {
    invalidate() { Object.keys(cache).forEach((k) => delete cache[k]); },
    async render() {
      const nav = A.$("#nav");
      if (!nav) return;
      const h = location.hash || "#/";
      document.body.classList.toggle("hasnav", !!A.me);
      if (!A.me) { nav.innerHTML = ""; return; }
      const all = A.me.workspaces || [];
      const ws = all.find((w) => w.id === wsId) || (all.length === 1 ? all[0] : null);
      const sub = (prefix) => tabs && tabs.base === prefix ? `<div class="nsub">${tabs.list.map(([k, t]) => `<a href="${tabs.base}/${k}" class="${k === tabs.cur ? "on" : ""}">${A.esc(t)}</a>`).join("")}</div>` : "";
      let html = `<a class="nbrand" href="#/"><i></i><span>後台</span></a>`;
      if (all.length > 1) html += `<select class="nws" aria-label="切換工作區">${ws ? "" : `<option value="">選擇工作區…</option>`}${all.map((w) => `<option value="${w.id}" ${ws && w.id === ws.id ? "selected" : ""}>${A.esc(w.name)}</option>`).join("")}</select>`;
      else if (ws) html += `<div class="nwsname">${A.esc(ws.name)}</div>`;
      if (ws) {
        const base = "#/ws/" + ws.id, onWs = h.startsWith(base);
        const owner = A.can(ws.id, []);
        html += `<div class="ngrp">工作區</div>` + link(base + "/bots", "機器人", "bots", onWs && /\/bots$|^#\/ws\/[^/]+$/.test(h)) + link(base + "/kbs", "知識庫", "kbs", onWs && h.endsWith("/kbs"))
          + (owner ? link(base + "/members", "成員", "members", onWs && h.endsWith("/members")) + link(base + "/audit", "稽核紀錄", "audit", onWs && h.endsWith("/audit")) : "");
        const { bots, kbs } = await lists(ws.id);
        if (bots.length) html += `<div class="ngrp">機器人</div>` + bots.map((b) => { const p = "#/bot/" + b.id, on = h.startsWith(p); return `<div class="nitem ${on ? "open" : ""}">${link(p, b.name, "dot", on && !tabs, b.disabled ? `<em>停用</em>` : "")}${on ? sub(p) : ""}</div>`; }).join("");
        if (kbs.length) html += `<div class="ngrp">知識庫</div>` + kbs.map((k) => { const p = "#/kb/" + k.id, on = h.startsWith(p); return `<div class="nitem ${on ? "open" : ""}">${link(p, k.name, "dot", on && !tabs)}${on ? sub(p) : ""}</div>`; }).join("");
      }
      html += `<div class="nfoot">${A.me.is_superadmin ? link("#/system", "系統管理", "system", h === "#/system") : ""}${link("#/account", A.me.display_name || A.me.login, "account", h === "#/account")}<button class="btn sm" type="button" id="navOut">登出</button></div>`;
      nav.innerHTML = html;
      const sel = A.$(".nws", nav);
      if (sel) sel.onchange = () => sel.value && A.go("#/ws/" + sel.value);
      A.$("#navOut", nav).onclick = () => { const b = A.$("#logout"); if (b) b.click(); };
      const on = A.$(".nsub a.on, a.on", nav);
      if (on) on.scrollIntoView({ block: "nearest" });
      // 這頁的主分頁已經在選單裡，頁內那一排就收起來（窄螢幕仍顯示）
      const first = A.$("#view > nav.tabs");
      if (first) first.classList.toggle("innav", !!(tabs && (A.$(".nsub", nav) || tabs.base.startsWith("#/ws/"))));
    },
  };
  // 新增／改名後清快取：任何寫入 API 都讓下次重抓
  const api0 = A.api;
  A.api = (method, ...rest) => { if (method !== "GET") A.nav.invalidate(); return api0(method, ...rest); };
})();
