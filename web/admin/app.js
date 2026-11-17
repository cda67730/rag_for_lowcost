/* 路由：#/login、#/ws/<id>/<tab>、#/bot/<id>/<tab>、#/kb/<id>/<tab> */
(function () {
  const routes = [
    [/^#\/login$/, () => A.views.login()],
    [/^#\/ws\/([^/]+)(?:\/([a-z-]+))?$/, (m) => A.views.workspace(m[1], m[2] || "bots")],
    [/^#\/bot\/([^/]+)(?:\/([a-z-]+))?$/, (m) => A.views.bot(m[1], m[2] || "settings")],
    [/^#\/kb\/([^/]+)(?:\/([a-z-]+))?$/, (m) => A.views.kb(m[1], m[2] || "docs")],
    [/^#\/system$/, () => A.views.system()],
    [/^#\/account$/, () => A.views.account()],
  ];
  async function route() {
    await route1();
    if (A.nav) A.nav.render().catch(() => {});
  }
  async function route1() {
    A.side.close();
    const h = location.hash || "#/";
    if (h !== "#/login" && !A.token.get()) return A.go("#/login");
    if (h !== "#/login" && !A.me) {
      try { A.me = await A.api("GET", "/me"); } catch (e) { if (e.status !== 401) A.$("#view").innerHTML = `<p class="err">${A.esc(e.message)}</p>`; return; }
      A.$("#me").innerHTML = (A.me.is_superadmin ? `<a href="#/system">系統管理</a> ` : "") + `<a href="#/account" title="我的帳號（改密碼）">${A.esc(A.me.display_name || A.me.login)}</a> <button class="btn sm" id="logout" type="button">登出</button>`;
      A.$("#logout").onclick = async () => { try { await A.api("POST", "/auth/logout"); } catch (e) {} A.token.set(""); A.me = null; A.$("#me").innerHTML = ""; A.go("#/login"); };
    }
    for (const [re, fn] of routes) {
      const m = h.match(re);
      if (m) {
        try { await fn(m); } catch (e) { if (e.status !== 401) A.$("#view").innerHTML = `<p class="err">${A.esc(e.message)}</p>`; }
        return;
      }
    }
    // 首頁：只有一個工作區就直接進去
    const ws = (A.me && A.me.workspaces) || [];
    if (ws.length === 1) return A.go("#/ws/" + ws[0].id);
    A.crumbs([]);
    const sys = A.me && A.me.is_superadmin ? `<p class="note" style="margin-top:14px">要新增或刪除工作區，到 <a href="#/system">系統管理</a>。</p>` : "";
    A.$("#view").innerHTML = `<h2>選擇工作區</h2>` + (ws.length ? `<div class="cards">${ws.map((w) => `<a class="card" href="#/ws/${w.id}"><b>${A.esc(w.name)}</b><span class="note">${A.esc(w.slug)}</span></a>`).join("")}</div>` : `<div class="empty">你還沒有加入任何工作區${A.me && A.me.is_superadmin ? "，可以到系統管理建立一個" : "，請找 owner 把你加進去"}。</div>`) + sys;
  }
  window.addEventListener("hashchange", route);
  A.$("#sideClose").onclick = A.side.close;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") A.side.close(); });
  route();
})();
