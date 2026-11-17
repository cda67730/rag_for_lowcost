/* 機器人 › API 與外部資料：金鑰、呼叫端可帶入的欄位、外部連線。只存設定；外部資料只在那一回合使用，不存資料庫 */
(function () {
  const kv = (o) => Object.entries(o || {}).map(([k, v]) => `${k}=${v}`).join("\n");
  const unkv = (t) => Object.fromEntries(String(t || "").split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const i = l.indexOf("="); return i < 0 ? [l, ""] : [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }).filter(([k]) => k));
  const picks = (a) => (a || []).map((x) => `${x.path}=${x.label || ""}`).join("\n");
  const unpicks = (t) => Object.entries(unkv(t)).map(([path, label]) => ({ path, label: label || path }));
  const when = (t) => (t ? new Date(t).toLocaleString("zh-TW", { hour12: false }) : "—");

  A.apiView = async function (p, b) {
    const botId = b.id;
    const [keys, ig] = await Promise.all([A.api("GET", `/bots/${botId}/api-keys`), A.api("GET", `/bots/${botId}/integration`)]);
    let fields = ig.context_fields.map((x) => ({ ...x }));
    let sources = ig.sources.map((x) => ({ ...x }));
    const srv = ig.server;
    const base = (window.RAG_CONFIG && window.RAG_CONFIG.API_URL ? String(window.RAG_CONFIG.API_URL).replace(/\/api\/?$/, "") : location.origin);

    const draw = () => {
      const ctxEx = Object.fromEntries(fields.map((f) => [f.key, "…"]));
      const curl = `curl -X POST ${base}/api/v1/ask \\\n  -H "Authorization: Bearer rk_你的金鑰" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ message: "孩子午睡一直哭怎麼辦", session: "對話代號-123", history: [], ...(fields.length ? { context: ctxEx } : {}) })}'`;
      p.innerHTML = `<div class="box"><b>讓其他系統呼叫這個機器人。</b>其他系統用 API 金鑰呼叫，可以順便帶入背景資料（例如會員等級、班級）；也可以設定機器人回答前，即時去其他系統查資料。
        <div class="note" style="margin-top:4px">外部資料<b>只在那一次回答使用</b>，不存進資料庫、不寫進日誌；對話紀錄只記「帶了哪些欄位、連線有沒有成功」。回答本身照常存進對話紀錄。</div></div>

      <div class="box"><h4>API 金鑰</h4>
        <div class="row"><input type="text" id="kn" placeholder="用途，例如：會員系統" style="max-width:300px"><button class="btn pri" id="ka" type="button">產生金鑰</button></div>
        <div id="knew"></div>
        <div class="tablewrap" style="margin-top:10px"><table><thead><tr><th>用途</th><th>金鑰開頭</th><th>建立</th><th>最後使用</th><th>啟用</th><th></th></tr></thead><tbody>${keys.items.map((k) =>
          `<tr><td>${A.esc(k.name)}</td><td class="mono">${A.esc(k.prefix)}…</td><td>${when(k.created_at)}</td><td>${when(k.last_used_at)}</td>
          <td><input type="checkbox" data-ke="${k.id}" ${k.enabled ? "checked" : ""}></td><td><button class="btn sm danger" data-kd="${k.id}" type="button">刪除</button></td></tr>`).join("") || '<tr><td colspan="6" class="note">還沒有金鑰</td></tr>'}</tbody></table></div>
        <p class="note">金鑰只放在呼叫端的伺服器，不要放進網頁或 App。紀錄的「單位」欄會顯示「API：用途」。次數上限沿用「設定」的每小時、每日上限。</p></div>

      <div class="box"><h4>呼叫端可以帶入的欄位</h4>
        <p class="note">只有列在這裡的欄位會被使用，其他欄位直接忽略、不會送給模型。不需要的個資（姓名、電話）就不要列。</p>
        <div class="tablewrap"><table><thead><tr><th>key（英文）</th><th>名稱（給模型看）</th><th>補充說明</th><th></th></tr></thead><tbody>${fields.map((f, i) =>
          `<tr><td><input type="text" data-ff="key" data-i="${i}" value="${A.esc(f.key)}" placeholder="member_level"></td><td><input type="text" data-ff="label" data-i="${i}" value="${A.esc(f.label)}"></td>
          <td><input type="text" data-ff="hint" data-i="${i}" value="${A.esc(f.hint || "")}" placeholder="例如：單位是歲"></td><td><button class="btn sm danger" data-fd="${i}" type="button">刪</button></td></tr>`).join("") || '<tr><td colspan="4" class="note">還沒有欄位</td></tr>'}</tbody></table></div>
        <button class="btn sm" id="fadd" type="button" style="margin-top:6px">新增欄位</button></div>

      <div class="box"><h4>外部連線 <span class="note">回答前即時查詢，結果不保存</span></h4>
        <div class="note">伺服器允許連線的主機：${srv.allowed_hosts.length ? srv.allowed_hosts.map((h) => `<span class="chip mono">${A.esc(h)}</span>`).join("") : '<span class="warn">尚未設定。要在伺服器的環境變數 <span class="mono">EXTERNAL_ALLOWED_HOSTS</span> 填主機名稱（逗號分隔），外部連線才會執行。</span>'}</div>
        ${sources.map((x, i) => `<div class="box" style="margin-top:10px">
          <div class="grid3"><div><label>名稱</label><input type="text" data-sf="name" data-i="${i}" value="${A.esc(x.name || "")}"></div><div><label>key（英文）</label><input type="text" data-sf="key" data-i="${i}" value="${A.esc(x.key || "")}"></div>
          <div><label>方法</label><select data-sf="method" data-i="${i}">${["GET", "POST"].map((m) => `<option ${m === (x.method || "GET") ? "selected" : ""}>${m}</option>`).join("")}</select></div></div>
          <label>網址 <span class="note">可用 {欄位key} 帶入呼叫端的值，例如 https://api.example.com/members/{member_id}</span></label><input type="text" class="mono" data-sf="url" data-i="${i}" value="${A.esc(x.url || "")}">
          <div class="grid2"><div><label>查詢參數 <span class="note">一行一個：名稱=值</span></label><textarea rows="2" class="mono" data-sf="query" data-i="${i}">${A.esc(kv(x.query))}</textarea></div>
          <div><label>POST 內容 <span class="note">一行一個：名稱=值（送 JSON）</span></label><textarea rows="2" class="mono" data-sf="body" data-i="${i}">${A.esc(kv(x.body))}</textarea></div></div>
          <div class="grid3"><div><label>密碼的環境變數名稱 <span class="note">EXT_SECRET_ 開頭</span></label><input type="text" class="mono" data-sf="secret_env" data-i="${i}" value="${A.esc(x.secret_env || "")}" placeholder="EXT_SECRET_CRM">
            ${x.secret_env ? (srv.secret_env_set[x.secret_env] ? '<div class="note ok">伺服器已設定</div>' : '<div class="note warn">伺服器還沒設定這個變數</div>') : ""}</div>
          <div><label>放在哪個 Header</label><input type="text" class="mono" data-sf="secret_header" data-i="${i}" value="${A.esc(x.secret_header || "Authorization")}"></div>
          <div><label>前綴</label><input type="text" class="mono" data-sf="secret_prefix" data-i="${i}" value="${A.esc(x.secret_prefix == null ? "Bearer " : x.secret_prefix)}"></div></div>
          <div class="grid2"><div><label>要取的欄位 <span class="note">一行一個：路徑=名稱，例如 data.level=會員等級；留空取第一層</span></label><textarea rows="3" class="mono" data-sf="pick" data-i="${i}">${A.esc(picks(x.pick))}</textarea></div>
          <div><label>逾時（秒）</label><input type="number" data-sf="timeout" data-i="${i}" value="${x.timeout || 5}" min="1" max="10" style="width:90px">
            <label class="inline" style="margin-top:12px"><input type="checkbox" data-sf="enabled" data-i="${i}" ${x.enabled === false ? "" : "checked"}> 啟用</label>
            <div style="margin-top:12px"><button class="btn sm danger" data-sd="${i}" type="button">刪除這個連線</button></div></div></div></div>`).join("")}
        <button class="btn sm" id="sadd" type="button" style="margin-top:8px">新增外部連線</button>
        <p class="note">密碼不填在這裡：在伺服器設定環境變數（例如 Railway 的 Variables），這裡只填變數名稱。只允許 https、不跟隨轉址；連到內網位址會被拒絕。</p></div>

      <div class="box"><h4>測試</h4><p class="note">填測試值、按「測試連線」看外部連線查到什麼。結果只顯示在這裡，不會保存。</p>
        <div class="grid3">${fields.map((f) => `<div><label>${A.esc(f.label || f.key)} <span class="note mono">${A.esc(f.key)}</span></label><input type="text" data-tv="${A.esc(f.key)}"></div>`).join("") || '<span class="note">（沒有可帶入的欄位）</span>'}</div>
        <div class="row" style="margin-top:8px"><button class="btn" id="itest" type="button">測試連線</button></div><div id="tout"></div></div>

      <div class="row end savebar"><span class="note">欄位和外部連線改完要按儲存</span><button class="btn pri" id="isave" type="button">儲存</button></div>

      <div class="box"><h4>呼叫方式</h4><pre class="mono" style="white-space:pre-wrap;word-break:break-all;margin:0">${A.esc(curl)}</pre>
        <p class="note">回傳 JSON：<span class="mono">type</span>（answer／followup／referral…）、<span class="mono">answer</span>（回答各欄位）、<span class="mono">sources</span>（出處）。同一段對話用同一個 session，並把之前的問答放進 history（格式同聊天頁）。</p></div>`;
      bind();
    };
    const collect = () => ({ context_fields: fields.filter((f) => f.key), sources });
    function bind() {
      A.$("#ka").onclick = (e) => A.act(e.target, async () => {
        const r = await A.api("POST", `/bots/${botId}/api-keys`, { name: A.$("#kn").value });
        keys.items.push(r); draw();
        A.$("#knew").innerHTML = `<div class="box warn" style="margin-top:10px"><b>這把金鑰只會顯示這一次</b>，請現在複製、存到呼叫端的伺服器：
          <div class="row" style="margin-top:6px"><span class="mono" style="word-break:break-all">${A.esc(r.token)}</span><button class="btn sm" id="kcp" type="button">複製</button></div></div>`;
        A.$("#kcp").onclick = () => A.copy(r.token);
      }, "已產生");
      A.$$("[data-ke]", p).forEach((x) => (x.onchange = () => A.act(null, () => A.api("PATCH", `/api-keys/${x.dataset.ke}`, { enabled: x.checked }), "已更新")));
      A.$$("[data-kd]", p).forEach((x) => (x.onclick = () => confirm("刪除後用這把金鑰的系統會立刻無法呼叫，確定？") && A.act(x, () => A.api("DELETE", `/api-keys/${x.dataset.kd}`), "已刪除").then(() => A.apiView(p, b))));
      A.$$("[data-ff]", p).forEach((x) => (x.oninput = () => { fields[+x.dataset.i][x.dataset.ff] = x.value.trim(); }));
      A.$$("[data-fd]", p).forEach((x) => (x.onclick = () => { fields.splice(+x.dataset.fd, 1); draw(); }));
      A.$("#fadd").onclick = () => { fields.push({ key: "", label: "", hint: "" }); draw(); };
      A.$$("[data-sf]", p).forEach((x) => (x.onchange = x.oninput = () => {
        const o = sources[+x.dataset.i], f = x.dataset.sf;
        o[f] = f === "enabled" ? x.checked : f === "query" || f === "body" ? unkv(x.value) : f === "pick" ? unpicks(x.value) : f === "timeout" ? +x.value : x.value;
      }));
      A.$$("[data-sd]", p).forEach((x) => (x.onclick = () => confirm("刪除這個外部連線？（按儲存後生效）") && (sources.splice(+x.dataset.sd, 1), draw())));
      A.$("#sadd").onclick = () => { sources.push({ key: "src" + (sources.length + 1), name: "", method: "GET", url: "https://", query: {}, body: {}, secret_env: "", secret_header: "Authorization", secret_prefix: "Bearer ", pick: [], timeout: 5, enabled: true }); draw(); };
      A.$("#isave").onclick = (e) => A.act(e.target, async () => {
        await A.api("PUT", `/bots/${botId}/integration`, collect());
        await A.apiView(p, b);
      }, "已儲存");
      A.$("#itest").onclick = (e) => A.act(e.target, async () => {
        const ctx = Object.fromEntries(A.$$("[data-tv]", p).map((x) => [x.dataset.tv, x.value]));
        const r = await A.api("POST", `/bots/${botId}/integration/test`, { ...collect(), context: ctx });
        A.$("#tout").innerHTML = r.results.length ? r.results.map((g) => `<div class="box" style="margin-top:8px"><b>${A.esc(g.name)}</b> <span class="${g.ok ? "ok" : "err"}">${A.esc(g.status)}</span> <span class="note">${g.ms}ms</span>
          ${g.items.length ? `<table style="margin-top:6px"><tbody>${g.items.map((it) => `<tr><td style="width:30%">${A.esc(it.label)}</td><td>${A.esc(it.value)}</td></tr>`).join("")}</tbody></table>` : ""}</div>`).join("")
          : '<p class="note">沒有啟用中的外部連線。</p>';
      });
    }
    draw();
  };
})();
