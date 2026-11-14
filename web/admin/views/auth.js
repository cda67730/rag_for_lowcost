A.views.login = function () {
  A.crumbs([]);
  A.$("#me").innerHTML = "";
  A.me = null;
  A.$("#view").innerHTML = `<form class="login" id="lf">
    <h2>登入後台</h2>
    <label for="lu">帳號</label><input type="text" id="lu" autocomplete="username" required>
    <label for="lp">密碼</label><input type="password" id="lp" autocomplete="current-password" required>
    <p class="err" id="le"></p>
    <button class="btn pri" type="submit" style="width:100%">登入</button>
    ${A.base() ? "" : `<p class="err">尚未設定 API 網址：請編輯 admin/config.js 的 API_URL</p>`}
  </form>`;
  A.$("#lu").focus();
  A.$("#lf").onsubmit = async (e) => {
    e.preventDefault();
    A.$("#le").textContent = "";
    try {
      const r = await A.api("POST", "/auth/login", { login: A.$("#lu").value, password: A.$("#lp").value });
      A.token.set(r.token);
      A.go("#/");
    } catch (err) { A.$("#le").textContent = err.message; }
  };
};
