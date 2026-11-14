/* 工作區：機器人、知識庫、成員、稽核 */
A.views.workspace = async function (wsId, tab) {
  const ws = A.me.workspaces.find((w) => w.id === wsId);
  const isOwner = A.can(wsId, []);
  const wsAdmin = A.roleIn(wsId).length && ws && (ws.roles.includes("owner") || ws.roles.includes("admin"));
  A.crumbs([[ws ? ws.name : "工作區", "#/ws/" + wsId]]);
  const tabs = [["bots", "機器人"], ["kbs", "知識庫"]].concat(isOwner ? [["members", "成員"], ["audit", "稽核紀錄"]] : []);
  const v = A.$("#view");
  v.innerHTML = `<h2>${A.esc(ws ? ws.name : "")}</h2>` + A.tabs("#/ws/" + wsId, tabs, tab) + `<div id="pane"></div>`;
  const p = A.$("#pane");
  await ({ bots, kbs, members, audit }[tab] || bots)();

  async function bots() {
    const d = await A.api("GET", `/workspaces/${wsId}/bots`);
    p.innerHTML = (wsAdmin ? `<details class="box"><summary><b>新增機器人</b></summary>
        <div class="grid2"><div><label for="bs">slug（網址用，小寫英數字與連字號）</label><input type="text" id="bs" placeholder="例如 helpdesk"></div>
        <div><label for="bn">名稱</label><input type="text" id="bn"></div></div>
        <div class="row end" style="margin-top:10px"><button class="btn pri" id="bc" type="button">建立</button></div></details>` : "") +
      (d.items.length ? `<div class="cards">${d.items.map((b) => `<a class="card" href="#/bot/${b.id}"><b>${A.esc(b.name)}</b><span class="note">${A.esc(b.slug)}</span> ${b.disabled ? A.pill("disabled", "停用") : ""}</a>`).join("")}</div>`
        : `<div class="empty">還沒有機器人。${wsAdmin ? "用上面的「新增機器人」建一個。" : ""}</div>`);
    const bc = A.$("#bc");
    if (bc) bc.onclick = () => A.act(bc, async () => {
      const b = await A.api("POST", `/workspaces/${wsId}/bots`, { slug: A.$("#bs").value, name: A.$("#bn").value });
      A.go("#/bot/" + b.id + "/settings");
    }, "已建立");
  }

  async function kbs() {
    const [d, m] = await Promise.all([A.api("GET", `/workspaces/${wsId}/kbs`), wsAdmin ? A.api("GET", "/models") : Promise.resolve(null)]);
    const opts = m ? m.embed.map((e) => e.dims.map((dim) => `<option value="${A.esc(e.id)}|${dim}" ${e.available ? "" : "disabled"}>${A.esc(e.id)}・${dim} 維${e.available ? "" : "（未設定金鑰）"}</option>`).join("")).join("") : "";
    p.innerHTML = (wsAdmin ? `<details class="box"><summary><b>新增知識庫</b></summary>
        <div class="grid2"><div><label for="ks">slug</label><input type="text" id="ks"></div><div><label for="kn">名稱</label><input type="text" id="kn"></div></div>
        <label for="ke">向量模型與維度 <span class="note">建立後不能改；同一個機器人掛的知識庫要用同一個</span></label><select id="ke">${opts}</select>
        <div class="grid3"><div><label for="kz">段落長度（字）</label><input type="number" id="kz" value="500"></div><div><label for="ko">重疊字數</label><input type="number" id="ko" value="80"></div>
        <div><label for="kv">可見範圍</label><select id="kv"><option value="workspace">整個工作區</option><option value="restricted">只給指定的機器人</option></select></div></div>
        <div class="row end" style="margin-top:10px"><button class="btn pri" id="kc" type="button">建立</button></div></details>` : "") +
      (d.items.length ? `<div class="tablewrap"><table><thead><tr><th>名稱</th><th>向量模型</th><th>文件</th><th>段落</th><th>掛載的機器人</th><th>範圍</th></tr></thead><tbody>${d.items.map((k) =>
        `<tr class="click" data-id="${k.id}"><td><b>${A.esc(k.name)}</b> <span class="note">${A.esc(k.slug)}</span></td><td class="mono">${A.esc(k.embed_model)}・${k.embed_dim}</td><td>${k.documents}</td><td>${k.chunks}</td>
        <td>${k.bots.map((b) => `<span class="chip">${A.esc(b.name)}</span>`).join("") || '<span class="note">—</span>'}</td><td>${k.visibility === "restricted" ? "指定機器人" : "整個工作區"}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">還沒有知識庫。</div>`);
    A.$$("tr.click", p).forEach((tr) => (tr.onclick = () => A.go("#/kb/" + tr.dataset.id)));
    const kc = A.$("#kc");
    if (kc) kc.onclick = () => A.act(kc, async () => {
      const [model, dim] = A.$("#ke").value.split("|");
      const k = await A.api("POST", `/workspaces/${wsId}/kbs`, { slug: A.$("#ks").value, name: A.$("#kn").value, embed_model: model, embed_dim: +dim,
        chunk_size: +A.$("#kz").value, chunk_overlap: +A.$("#ko").value, visibility: A.$("#kv").value });
      A.go("#/kb/" + k.id + "/docs");
    }, "已建立");
  }

  async function members() {
    const [d, b] = await Promise.all([A.api("GET", `/workspaces/${wsId}/members`), A.api("GET", `/workspaces/${wsId}/bots`)]);
    const botName = (id) => (b.items.find((x) => x.id === id) || {}).name || "";
    const roleSel = (cur, id) => `<select data-role="${id}">${["owner", "admin", "reviewer", "analyst"].map((r) => `<option ${r === cur ? "selected" : ""}>${r}</option>`).join("")}</select>`;
    const botSel = (cur, id) => `<select ${id ? `data-bot="${id}"` : 'id="mb"'}><option value="">整個工作區</option>${b.items.map((x) => `<option value="${x.id}" ${x.id === cur ? "selected" : ""}>${A.esc(x.name)}</option>`).join("")}</select>`;
    p.innerHTML = `<div class="box"><h4>新增成員 <span class="note">帳號不存在會自動建立並給一次性密碼</span></h4>
      <div class="grid3"><div><label for="ml">帳號</label><input type="text" id="ml"></div><div><label for="mr">角色</label>${roleSel("admin", "").replace('data-role=""', 'id="mr"')}</div><div><label for="mb">範圍</label>${botSel("", "")}</div></div>
      <div class="row end" style="margin-top:10px"><button class="btn pri" id="ma" type="button">新增</button></div><p id="mt" class="ok"></p></div>
      <p class="note">owner：全部；admin：設定、Skill、邀請碼、紀錄、評測（工作區層的 admin 也能管知識庫）；reviewer：審核文件、圖譜候選、評測抽查；analyst：唯讀紀錄與統計</p>
      <div class="tablewrap"><table><thead><tr><th>帳號</th><th>角色</th><th>範圍</th><th></th></tr></thead><tbody>${d.items.map((x) => `<tr><td>${A.esc(x.login)}${x.disabled ? " " + A.pill("disabled", "停用") : ""}</td>
        <td>${roleSel(x.role, x.id)}</td><td>${botSel(x.bot_id, x.id)}</td>
        <td class="row"><button class="btn sm" data-reset="${x.user_id}" type="button">重設密碼</button><button class="btn sm danger" data-del="${x.id}" type="button">移除</button></td></tr>`).join("")}</tbody></table></div>`;
    A.$("#ma").onclick = (e) => A.act(e.target, async () => {
      const r = await A.api("POST", `/workspaces/${wsId}/members`, { login: A.$("#ml").value, role: A.$("#mr").value, bot_id: A.$("#mb").value || null });
      await members();
      if (r.temp_password) A.$("#mt").textContent = `已建立帳號 ${r.login}，一次性密碼：${r.temp_password}（只顯示這一次，請轉交本人並請他登入後更改）`;
    }, "已新增");
    const patch = (id, body) => A.act(null, async () => { await A.api("PATCH", `/workspaces/${wsId}/members/${id}`, body); }, "已更新").then(members);
    A.$$("[data-role]", p).forEach((s) => s.dataset.role && (s.onchange = () => patch(s.dataset.role, { role: s.value })));
    A.$$("[data-bot]", p).forEach((s) => (s.onchange = () => patch(s.dataset.bot, { bot_id: s.value || null })));
    A.$$("[data-del]", p).forEach((x) => (x.onclick = () => confirm("確定移除這個角色？") && A.act(x, () => A.api("DELETE", `/workspaces/${wsId}/members/${x.dataset.del}`), "已移除").then(members)));
    A.$$("[data-reset]", p).forEach((x) => (x.onclick = () => confirm("重設後對方目前的登入會失效，確定？") && A.act(x, async () => {
      const r = await A.api("POST", `/workspaces/${wsId}/users/${x.dataset.reset}/reset-password`);
      A.$("#mt").textContent = "新的一次性密碼：" + r.temp_password + "（只顯示這一次）";
    })));
  }

  async function audit() {
    let next = null;
    p.innerHTML = `<div class="row" style="margin-bottom:10px"><input type="search" id="af" placeholder="動作開頭，例如 bot、document、member" style="max-width:320px"><button class="btn" id="ag" type="button">篩選</button></div>
      <div class="tablewrap"><table><thead><tr><th>時間</th><th>誰</th><th>動作</th><th>對象</th><th>內容</th></tr></thead><tbody id="ab"></tbody></table></div>
      <div class="row end" style="margin-top:8px"><button class="btn" id="am" type="button" hidden>載入更多</button></div>`;
    async function load(reset) {
      const q = new URLSearchParams({ limit: 50 });
      if (A.$("#af").value) q.set("action", A.$("#af").value);
      if (!reset && next) q.set("cursor", next);
      const d = await A.api("GET", `/workspaces/${wsId}/audit?` + q);
      next = d.next;
      const html = d.items.map((a) => `<tr><td>${A.fmtTime(a.at)}</td><td>${A.esc(a.user || "")}</td><td class="mono">${A.esc(a.action)}</td><td>${A.esc(a.target)}</td><td class="mono" style="max-width:420px;overflow:hidden;text-overflow:ellipsis">${A.esc(JSON.stringify(a.detail))}</td></tr>`).join("");
      A.$("#ab").innerHTML = reset ? html : A.$("#ab").innerHTML + html;
      A.$("#am").hidden = !next;
    }
    A.$("#ag").onclick = () => load(true);
    A.$("#am").onclick = () => load(false);
    await load(true);
  }
};
