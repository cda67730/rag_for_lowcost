/* 我的帳號：改密碼（右上角點自己的名字） */
(function () {
  A.views.account = async function () {
    A.crumbs([["我的帳號"]]);
    const me = A.me || {};
    A.$("#view").innerHTML = `<h2>我的帳號</h2>
      <p class="note">帳號：<b>${A.esc(me.login)}</b>${me.is_superadmin ? "（總管理員）" : ""}</p>
      <div class="box" style="max-width:460px"><h4>變更密碼</h4>
        <label for="po">目前的密碼</label><input type="password" id="po" autocomplete="current-password">
        <label for="pn" style="margin-top:8px">新密碼（至少 10 個字元）</label><input type="password" id="pn" autocomplete="new-password">
        <label for="pc" style="margin-top:8px">再輸入一次新密碼</label><input type="password" id="pc" autocomplete="new-password">
        <p class="note">改完後，其他電腦或瀏覽器上的登入都會被登出；這個視窗會保持登入。</p>
        <div class="row end"><button class="btn pri" id="ps" type="button">變更密碼</button></div><p id="pm" class="err"></p>
      </div>`;
    A.$("#ps").onclick = (e) => {
      const old = A.$("#po").value, nw = A.$("#pn").value, msg = A.$("#pm");
      msg.textContent = "";
      if (nw.length < 10) return (msg.textContent = "新密碼至少 10 個字元");
      if (nw !== A.$("#pc").value) return (msg.textContent = "兩次輸入的新密碼不一樣");
      A.act(e.target, async () => {
        try {
          const r = await A.api("POST", "/me/password", { old, new: nw });
          A.token.set(r.token);
          ["#po", "#pn", "#pc"].forEach((s) => (A.$(s).value = ""));
        } catch (err) { msg.textContent = String(err.message).split("：")[0]; throw err; }
      }, "密碼已變更");
    };
  };
})();
