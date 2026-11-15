/* 系統管理（只有總管理員看得到）：所有工作區、建立、刪除，重設任何帳號的密碼，跨工作區操作紀錄 */
(function () {
  const ACT = { "workspace.create": "建立工作區", "workspace.delete": "刪除工作區", "user.reset_password": "重設密碼", "user.superadmin": "總管理員設定" };
  const actName = (x) => (x.action === "user.superadmin" && x.detail ? (x.detail.on ? "設為總管理員" : "取消總管理員") : ACT[x.action] || x.action);

  A.views.system = async function () {
    A.crumbs([["系統管理"]]);
    const v = A.$("#view");
    if (!A.me.is_superadmin) { v.innerHTML = `<div class="empty">只有總管理員可以使用系統管理。</div>`; return; }
    const [d, log, sa] = await Promise.all([A.api("GET", "/system/workspaces"), A.api("GET", "/system/audit?limit=50"), A.api("GET", "/system/superadmins")]);
    const mine = new Set((A.me.workspaces || []).map((w) => w.id));
    v.innerHTML = `<h2>系統管理</h2>
      <p class="note">總管理員可以建立、刪除工作區，重設任何帳號的密碼；但不會因此看到工作區的內容，要管理某個工作區，請在建立時把自己設為 owner，或請它的 owner 把你加為成員。</p>
      <div class="box"><h4>新增工作區</h4>
        <div class="grid3">
          <div><label for="ns">slug（英數字與連字號）</label><input type="text" id="ns" autocomplete="off" placeholder="例如 team-b"></div>
          <div><label for="nn">名稱</label><input type="text" id="nn" autocomplete="off"></div>
          <div><label for="no">owner 帳號</label><input type="text" id="no" autocomplete="off" placeholder="${A.esc(A.me.login)}（留空＝自己）"></div>
        </div>
        <div style="margin-top:8px"><label for="np">新帳號的密碼（選填）</label><input type="password" id="np" autocomplete="new-password" placeholder="owner 是新帳號時才用；留空會產生一次性密碼" style="max-width:420px"></div>
        <div class="row end" style="margin-top:10px"><button class="btn pri" id="nc" type="button">建立</button></div><p id="nt" class="ok"></p>
      </div>
      <h3>所有工作區（${d.items.length}）</h3>
      <div class="tablewrap"><table><thead><tr><th>名稱</th><th>slug</th><th>owner</th><th>機器人</th><th>知識庫</th><th>文件</th><th>對話</th><th>成員</th><th>建立</th><th></th></tr></thead>
        <tbody>${d.items.map((w) => `<tr><td>${mine.has(w.id) ? `<a href="#/ws/${w.id}">${A.esc(w.name)}</a>` : A.esc(w.name)}</td><td>${A.esc(w.slug)}</td><td>${A.esc(w.owners.join("、"))}</td>
          <td>${w.bots}</td><td>${w.kbs}</td><td>${w.documents}</td><td>${w.conversations}</td><td>${w.members}</td><td>${A.esc(new Date(w.created_at).toLocaleDateString("zh-TW"))}</td>
          <td><button class="btn sm danger" data-del="${w.id}" type="button">刪除</button></td></tr>`).join("")}</tbody></table></div>
      <div class="box" style="margin-top:14px"><h4>重設帳號密碼</h4>
        <p class="note">任何帳號都可以，包括屬於多個工作區的帳號。重設後對方目前的登入會失效。</p>
        <div class="row"><input type="text" id="rl" placeholder="帳號" autocomplete="off" style="max-width:240px"><button class="btn" id="rb" type="button">重設</button></div><p id="rt" class="ok"></p>
      </div>
      <div class="box"><h4>總管理員（${sa.items.length}）</h4>
        <p class="note">總管理員可以建立、刪除工作區與重設任何帳號的密碼。帳號要先存在（先加進某個工作區）。至少要保留一位。</p>
        <div class="tablewrap"><table><tbody>${sa.items.map((x) => `<tr><td>${A.esc(x.login)}${x.login === A.me.login ? "（你）" : ""}${x.disabled ? "（已停用）" : ""}</td><td>${A.esc(x.display_name || "")}</td>
          <td style="text-align:right"><button class="btn sm danger" data-revoke="${A.esc(x.login)}" type="button">取消</button></td></tr>`).join("")}</tbody></table></div>
        <div class="row" style="margin-top:10px"><input type="text" id="sl" placeholder="帳號" autocomplete="off" style="max-width:240px"><button class="btn" id="sb" type="button">設為總管理員</button></div>
      </div>
      <h3>操作紀錄</h3>
      ${log.items.length ? `<div class="tablewrap"><table><thead><tr><th>時間</th><th>誰</th><th>動作</th><th>對象</th></tr></thead><tbody>${log.items.map((x) => `<tr><td>${A.esc(A.fmtTime(x.at))}</td>
        <td>${A.esc(x.login || "（指令）")}</td><td>${A.esc(actName(x))}</td><td>${A.esc(x.target)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">還沒有紀錄</div>`}`;

    A.$("#nc").onclick = (e) => A.act(e.target, async () => {
      const r = await A.api("POST", "/system/workspaces", { slug: A.$("#ns").value, name: A.$("#nn").value, owner: A.$("#no").value.trim() || A.me.login, password: A.$("#np").value });
      A.me = await A.api("GET", "/me");          // 自己是 owner 時，新工作區要出現在清單
      await A.views.system();
      A.$("#nt").textContent = `已建立「${r.name}」，owner：${r.owners.join("、")}` + (r.temp_password ? `。新帳號的一次性密碼：${r.temp_password}（只顯示這一次，請轉交本人並請他登入後更改）` : "");
    }, "已建立");

    A.$("#rb").onclick = (e) => {
      const login = A.$("#rl").value.trim();
      if (!login || !confirm(`重設 ${login} 的密碼？對方目前的登入會失效。`)) return;
      A.act(e.target, async () => {
        const r = await A.api("POST", "/system/users/reset-password", { login });
        A.$("#rt").textContent = `${r.login} 的一次性密碼：${r.temp_password}（只顯示這一次）`;
      });
    };

    A.$("#sb").onclick = (e) => {
      const login = A.$("#sl").value.trim();
      if (!login || !confirm(`把 ${login} 設為總管理員？他將可以建立、刪除工作區與重設任何帳號的密碼。`)) return;
      A.act(e.target, async () => { await A.api("POST", "/system/superadmins", { login }); await A.views.system(); }, "已設定");
    };
    A.$$("[data-revoke]", v).forEach((b) => (b.onclick = () => {
      const login = b.dataset.revoke, self = login === A.me.login;
      if (!confirm(self ? "取消你自己的總管理員？取消後就不能再進系統管理。" : `取消 ${login} 的總管理員？`)) return;
      A.act(b, async () => {
        await A.api("POST", "/system/superadmins/revoke", { login });
        if (self) { A.me = null; return A.go("#/"); }      // 重新讀自己的資料，右上角的「系統管理」也會消失
        await A.views.system();
      }, "已取消");
    }));

    A.$$("[data-del]", v).forEach((b) => (b.onclick = () => {
      const w = d.items.find((x) => x.id === b.dataset.del);
      const body = A.side.open("刪除工作區", `<p>即將刪除「<b>${A.esc(w.name)}</b>」，連同底下的資料一起刪除，<b>不能復原</b>：</p>
        <ul><li>機器人 ${w.bots} 個、知識庫 ${w.kbs} 個、文件 ${w.documents} 份</li><li>對話紀錄 ${w.conversations} 筆，以及設定、邀請碼、評測、稽核紀錄</li><li>${w.members} 位成員的角色（帳號本身會保留）</li></ul>
        <p class="note">建議先到工作區匯出需要的紀錄，並備份資料庫。</p>
        <label for="dc">請輸入 slug「${A.esc(w.slug)}」確認</label><input type="text" id="dc" autocomplete="off">
        <div class="row end" style="margin-top:10px"><button class="btn danger" id="dd" type="button" disabled>永久刪除</button></div>`);
      const inp = A.$("#dc", body), btn = A.$("#dd", body);
      inp.oninput = () => (btn.disabled = inp.value.trim() !== w.slug);
      btn.onclick = () => A.act(btn, async () => {
        await A.api("DELETE", "/system/workspaces/" + w.id, { confirm: inp.value.trim() });
        A.side.close();
        A.me = await A.api("GET", "/me");
        await A.views.system();
      }, "已刪除");
    }));
  };
})();
