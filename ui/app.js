/* NEW PRODUCT — 画面の切り替え（hash routing）と第2段の画面。
 *
 * **なぜ hash を持たせるか**（全体設計書 §5-1）
 * Drive で唯一うまく機能しているのは「その商品のシートURLを貼る」こと。
 * **案件カルテのURLが貼れないと、人は Drive を使い続ける。**
 *
 * **規律**（§5-9・N-11）
 *  - 色に意味を持たせない。状態は必ず**列（語）**として出す
 *  - 現在地は色だけで示さず `aria-current` を出す
 *  - 未計測と0を区別する。無いときは「無い」と書く
 *  - **商品名を表示しない**（N-6-2）。分類とサイズで表す
 *  - 文字列は textContent で入れる。innerHTML にデータを混ぜない
 *
 * ビルドも依存も足さない（vanilla JS）。
 */
(function () {
  "use strict";

  var APP = "newproduct";

  // NAV の正本は index.html の <script type="application/json" id="fca-nav-data">。
  // **ここに二つ目の表を作らない。**帯と画面がずれる原因になる。
  var NAV = JSON.parse(document.getElementById("fca-nav-data").textContent);

  // NAV に出さない画面を、どの NAV の下に置くか（画面設計 2-2）。
  // ゲート盤・発売後評価は「案件」の下。機会カレンダーは「プラン」の下。
  // **帯は7つまで**（keiei の layout.py の上限。selfcheck が見張っている）。
  // 帯に出さない画面は、どの帯の下に置くかをここで決める（画面設計 2-2）。
  var ALIAS = { "#/gates": "#/projects", "#/review": "#/projects",
                "#/cost": "#/sales", "#/abc": "#/sales", "#/trends": "#/ideas", "#/reports": "#/",
                "#/opportunities": "#/plan", "#/manual": "#/settings", "#/settings/events": "#/settings", "#/simulate": "#/plan",
                "#/automation": "#/tasks" };

  var links = Array.prototype.slice.call(
    document.querySelectorAll(".fca-nav a[data-view]"));

  // ── 下ごしらえ ───────────────────────────────────────
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") n.textContent = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function txt(s) { return document.createTextNode(s === null || s === undefined ? "" : String(s)); }
  /** 導線はボタンにする（2026-10-06 十文字さん「テキストリンクがわかりにくい」・ADR-057）。
   *  kind: "back"（戻る・控えめ）／ "sm"（節の中の小さな導線）。行き先は語で書く */
  function navBtn(href, label, kind) {
    // 進む導線には「→」を付け、選択肢（設定の押しボタン）と見分けられるようにする（2026-10-06 点検）
    var t = kind ? label : label + " →";
    return el("a", { href: href, "class": "np-btn" + (kind ? " np-btn-" + kind : ""), text: t });
  }
  function btnRow(kids) { return el("p", { "class": "np-btnrow" }, kids); }
  /** 同じ画面の中の節へ移るボタン。**hash を変えない**（変えると振り分けが別の画面を出す・2026-10-06 点検）。
   *  たたんだ節は開き、見出しへ移ってフォーカスを置く */
  function secBtn(id, label) {
    var bt = el("button", { type: "button", "class": "np-btn np-btn-sm", text: label });
    bt.addEventListener("click", function () { goSection(id); });
    return bt;
  }
  function goSection(id) {
    var t = document.getElementById(id);
    if (!t) {                                   // **黙らない**（2026-10-06 再点検: 飛び先が無く何も起きなかった）
      window.alert("その節がこの画面に見つかりません（" + id.replace("np-sec-", "") + "）");
      return;
    }
    var det = t.closest("details");
    if (det) det.open = true;
    var h = (det && det.querySelector("summary")) || t;
    h.scrollIntoView({ behavior: "smooth", block: "start" });      // 題が上に隠れないよう、見出しへ移る
    if (!h.hasAttribute("tabindex") && h.tagName !== "SUMMARY") h.setAttribute("tabindex", "-1");
    h.focus({ preventScroll: true });
  }
  function dash(v) { return (v === null || v === undefined || v === "") ? "—" : String(v); }

  function hashPath() { return (location.hash || "#/").split("?")[0]; }
  function hashQuery() {
    var i = (location.hash || "").indexOf("?");
    return new URLSearchParams(i < 0 ? "" : location.hash.slice(i + 1));
  }

  function match(path) {
    var p = ALIAS[path] || path;
    var best = NAV[0], bestLen = -1;
    for (var i = 0; i < NAV.length; i++) {
      var h = NAV[i].hash;
      var hit = (p === h) || (h !== "#/" && p.indexOf(h + "/") === 0);
      if (hit && h.length > bestLen) { best = NAV[i]; bestLen = h.length; }
    }
    return best;
  }

  /** 現在地。**色だけで示さない。**aria-current を出す（§5-7・N-11）。 */
  function markCurrent(view) {
    links.forEach(function (a) {
      if (a.getAttribute("data-view") === view) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  function api(path) {
    return fetch(path, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error(path + " が " + r.status + " を返しました");
      return r.json();
    });
  }
  function post(path, obj) {
    var b = new URLSearchParams();
    Object.keys(obj).forEach(function (k) { b.append(k, obj[k]); });
    return fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: b.toString()
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j && j.error ? j.error : ("HTTP " + r.status));
        return j;
      });
    });
  }

  var BOX = document.getElementById("view");
  var TITLE = document.getElementById("view-title");

  function setTitle(label, sub) {
    TITLE.textContent = label;
    // **タブを5枚開いて見比べるのが日常。**タイトルが全部同じだと選べない（§5-1）
    document.title = (sub ? label + " " + sub : label) + " ／ " + APP;
  }
  function clear() { BOX.innerHTML = ""; return BOX; }
  function put(node) { BOX.appendChild(node); }
  function loading() { clear().appendChild(el("p", { "class": "fca-note", text: "読み込んでいます。" })); }
  function fail(e) {
    clear().appendChild(el("div", { "class": "np-err" }, [
      el("p", { text: "表示できませんでした。" }),
      el("p", { "class": "np-note", text: String(e && e.message || e) })
    ]));
  }

  function table(headers, rows) {
    var t = el("table", { "class": "np" });
    var h = el("tr");
    headers.forEach(function (x) { h.appendChild(el("th", { text: x })); });
    t.appendChild(el("thead", null, [h]));
    var tb = el("tbody");
    rows.forEach(function (r) { tb.appendChild(r); });
    t.appendChild(tb);
    // **スマホ幅で画面ごと横にずれないように**、表だけを横に流せる箱で包む（2026-10-06 点検・375px で4画面がはみ出した）
    return el("div", { "class": "np-tablewrap" }, [t]);
  }

  function gateChips(gates) {
    var w = el("div", { "class": "np-gates" });
    gates.forEach(function (g) {
      w.appendChild(el("span", { "class": "np-gate", "data-state": g.state }, [
        el("span", { "class": "np-glyph", "aria-hidden": "true", text: g.glyph }),
        " " + g.gate + " " + g.word
      ]));
    });
    return w;
  }

  // ══════════════════════════════════════════════════════
  // ダッシュボード（§10-2 ①）
  // **1段目は「いま詰まっているもの」。**毎朝開く人が最初に要るのは
  // 「何をすればいいか」。コンセプト在庫月数は2段目へ置く。
  // ══════════════════════════════════════════════════════
  function viewHome() {
    loading();
    api("/api/dashboard").then(function (d) {
      var b = clear();
      b.appendChild(el("p", { "class": "np-note", text: "きょうは " + d.today + " です。" }));

      // ── 0段目: 自分のやること（2026-10-06 十文字さん選択）。開いた人が最初に要るのは「何をすればいいか」──
      var my = d.my, mc = el("div", { "class": "np-card" });
      mc.appendChild(el("h2", { text: "自分のやること" }));
      mc.appendChild(el("p", { "class": "np-sub", text: my.roles.length
        ? "あなたの業務ロール（" + my.roles.join("・") + "）のタスクと、あなたが担当のタスクです。期限切れと、7日先までを出しています。"
        : "あなたに業務ロールが割り当てられていないため、担当者があなたのタスクだけを出しています（いまのタスクは業務ロールに付いています）。" }));
      if (!my.tasks.length) mc.appendChild(el("p", { "class": "np-note", text: "7日先までにやるタスクはありません。" }));
      else {
        mc.appendChild(table(["期限", "状態", "タスク", "案件", "なぜ自分か"], my.tasks.map(function (t) {
          return el("tr", null, [el("td", { text: t.due_on + (t.overdue ? "（期限切れ）" : "") }), el("td", { text: t.status }),
            el("td", { text: t.title }),
            el("td", null, [t.project_id ? el("a", { href: "#/projects/" + t.project_id, text: t.product }) : txt("—")]),
            el("td", { text: t.by === "担当" ? "担当者" : t.role_label })]);
        })));
        if (my.tasks_n > my.tasks.length) mc.appendChild(btnRow([navBtn("#/tasks?when=next7&mine=1", "ほか " + (my.tasks_n - my.tasks.length) + " 件も含めて、自分のタスクをすべて見る")]));
      }
      if (my.gates.length) {
        mc.appendChild(el("h3", { text: "あなたが判定するゲート（" + my.gates_n + "件）" }));
        mc.appendChild(table(["ゲート", "状態", "案件", "足りないもの"], my.gates.map(function (g) {
          return el("tr", null, [el("td", { text: g.gate + " " + g.name }), el("td", { text: g.state }),
            el("td", null, [el("a", { href: "#/projects/" + g.project_id, text: g.product })]),
            el("td", { "class": "np-num", text: g.missing_n + " 件" })]);
        })));
      }
      mc.appendChild(btnRow([navBtn("#/tasks", "タスクの一覧を開く"), navBtn("#/gates", "ゲートの一覧を開く", "back")]));
      b.appendChild(mc);

      // ── 1段目 ──
      b.appendChild(el("h2", { text: "いま詰まっているもの" }));
      var g1 = el("div", { "class": "np-grid np-grid-3" });
      function card(label, n, mine, link, sub) {
        var c = el("div", { "class": "np-card" });
        c.appendChild(el("h3", { text: label }));
        c.appendChild(el("a", { "class": "np-big", href: link, text: String(n) }));
        if (mine !== null && mine !== undefined)
          c.appendChild(el("p", { "class": "np-sub", text: "うち自分の担当 " + mine + " 件" }));
        if (sub) c.appendChild(el("p", { "class": "np-sub", text: sub }));
        return c;
      }
      g1.appendChild(card("期限切れタスク", d.stuck.overdue.n, d.stuck.overdue.mine,
        d.stuck.overdue.link, "完了・対象外は除いています。"));

      var gw = el("div", { "class": "np-card" });
      gw.appendChild(el("h3", { text: "自分のゲート待ち" }));
      gw.appendChild(el("a", { "class": "np-big", href: d.stuck.gate_waiting.link,
        text: String(d.stuck.gate_waiting.mine) }));
      if (!d.my_roles.length) {
        gw.appendChild(el("p", { "class": "np-sub",
          text: "あなたに業務ロールが割り当てられていません。承認の可否は業務ロールで決まります（アプリ権限とは別です）。" }));
      }
      if (d.stuck.gate_waiting.by_role.length) {
        gw.appendChild(el("p", { "class": "np-sub", text: "誰の番か（全体）:" }));
        var ul = el("ul", { "class": "np-miss" });
        d.stuck.gate_waiting.by_role.forEach(function (r) {
          ul.appendChild(el("li", { text: r.label + " " + r.n + "件" }));
        });
        gw.appendChild(ul);
      } else {
        gw.appendChild(el("p", { "class": "np-sub", text: "判定待ちはありません。" }));
      }
      g1.appendChild(gw);

      g1.appendChild(card("今日のタスク", d.stuck.today.n, d.stuck.today.mine,
        d.stuck.today.link, null));
      b.appendChild(g1);

      // ── 2段目。**未計測と0を区別する**（§5-9 の7）──
      b.appendChild(el("h2", { text: "月次で見るもの" }));
      var g2 = el("div", { "class": "np-grid np-grid-3" });
      b.appendChild(btnRow([navBtn("#/reports", "月次レポートを見る")]));
      d.monthly.forEach(function (m) {
        var c = el("div", { "class": "np-card" });
        c.appendChild(el("h3", { text: m.label }));
        if (m.value === null) {
          c.appendChild(el("span", { "class": "np-big np-big-unmeasured", text: m.state }));
          c.appendChild(el("p", { "class": "np-sub", text: m.why }));
        } else {
          c.appendChild(el("span", { "class": "np-big", text: String(m.value) }));
          // **色に意味を持たせない**（N-11）。状態は語として添える
          if (m.state) c.appendChild(el("p", { "class": "np-sub", text: "状態: " + m.state }));
          if (m.why) c.appendChild(el("p", { "class": "np-sub", text: m.why }));
        }
        if (m.definition) c.appendChild(el("p", { "class": "np-sub", text: "定義: " + m.definition }));
        // **数えた対象を書く。**「どの版の何月を見たのか」が無いと確かめようがない
        if (m.version) c.appendChild(el("p", { "class": "np-sub",
          text: "対象: " + m.version + " の " + m.month }));
        if (m.link) c.appendChild(btnRow([navBtn(m.link, "開く", "sm")]));
        if (m.stock_n !== undefined && m.stock_n !== null)
          c.appendChild(el("p", { "class": "np-sub", text: "G3通過・未発売 " + m.stock_n + " 件" }));
        g2.appendChild(c);
      });
      b.appendChild(g2);

      // ── 直近の発売予定 ──
      b.appendChild(el("h2", { text: "直近の発売予定（4週）" }));
      if (!d.upcoming.length) {
        b.appendChild(el("p", { "class": "np-note", text: "4週以内の発売予定はありません。" }));
      } else {
        b.appendChild(table(["発売予定日", "案件（社内呼称）", "分類", "ステージ"],
          d.upcoming.map(function (u) {
            return el("tr", null, [
              el("td", { text: dash(u.launch_date) }),
              el("td", null, [el("a", { href: "#/projects/" + u.id, text: u.name })]),
              el("td", { text: u.product }),
              el("td", { text: u.stage })
            ]);
          })));
      }

      // ── 要注意。**空欄を画面に出す。出さないと空欄のまま増える** ──
      b.appendChild(el("h2", { text: "要注意（空欄・未確定）" }));
      var a = d.attention;
      b.appendChild(table(["項目", "件数", "意味"], [
        el("tr", null, [el("td", null, [el("a", { href: "#/tasks?when=none", text: "期限なしのタスク" })]),
          el("td", { "class": "np-num", text: String(a.no_due) }),
          el("td", { text: "隠すと、期限を入れない運用が固定します。" })]),
        el("tr", null, [el("td", { text: "所要時間なしのタスク" }),
          el("td", { "class": "np-num", text: String(a.no_hours) }),
          el("td", { text: "月次の負荷に積めません。" })]),
        el("tr", null, [el("td", { text: "工数ポイントが未確定の案件" }),
          el("td", { "class": "np-num", text: String(a.effort_unknown) }),
          el("td", { text: "⑤資材リニューアルは係数そのものが未確定です（実測できていません）。" })]),
        el("tr", null, [el("td", { text: "ページリニューアルの案件" }),
          el("td", { "class": "np-num", text: String(a.pagerenew_no_template) }),
          el("td", { text: "標準タスク未定義。この開発タイプはタスク一覧が空になります。" })])
      ]));

      b.appendChild(el("p", { "class": "np-note",
        text: "この画面は「新規に増える画面」です。月次会議で『コンセプトが積み上がっていない』と毎回言われながら、それを数える場所がありませんでした。" }));
    }).catch(fail);
  }

  // ══════════════════════════════════════════════════════
  // 案件一覧（画面設計 3-7）
  // ══════════════════════════════════════════════════════
  function viewProjects() {
    loading();
    var q = hashQuery();
    var qs = [];
    ["stage", "flow", "owner", "launch_month", "source_of_truth"].forEach(function (k) {
      if (q.get(k)) qs.push(k + "=" + encodeURIComponent(q.get(k)));
    });
    api("/api/projects" + (qs.length ? "?" + qs.join("&") : "")).then(function (d) {
      var b = clear();

      // 絞り込み
      var f = el("form", { "class": "np-filters", id: "np-pfilter" });
      function sel(name, label, opts, cur) {
        var s = el("select", { name: name, "aria-label": label });
        s.appendChild(el("option", { value: "", text: label + "（すべて）" }));
        opts.forEach(function (o) {
          var v = typeof o === "string" ? o : o.code;
          var t = typeof o === "string" ? o : o.label;
          var op = el("option", { value: v, text: t });
          if (cur === v) op.setAttribute("selected", "selected");
          s.appendChild(op);
        });
        return s;
      }
      f.appendChild(sel("stage", "ステージ", d.filters.stage, q.get("stage")));
      f.appendChild(sel("flow", "フロー", d.filters.flow, q.get("flow")));
      f.appendChild(sel("owner", "担当", d.filters.owner, q.get("owner")));
      f.appendChild(sel("launch_month", "発売月", d.filters.launch_month, q.get("launch_month")));
      f.appendChild(el("button", { type: "submit", text: "絞り込む" }));
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var p = [];
        Array.prototype.forEach.call(f.elements, function (x) {
          if (x.name && x.value) p.push(x.name + "=" + encodeURIComponent(x.value));
        });
        location.hash = "#/projects" + (p.length ? "?" + p.join("&") : "");
      });
      b.appendChild(f);

      if (!d.rows.length) {
        b.appendChild(el("p", { "class": "np-note",
          text: "案件がまだありません。" }));
        b.appendChild(el("p", { "class": "np-note", text: "本来は年間プランの枠から「案件にする」で起こします（発売の2か月前のタスク設定期限が出ます）。"
          + "枠に無いものは、下のフォームから直接起こせます。" }));
        b.appendChild(btnRow([navBtn("#/plan", "年間プランを開く")]));
      } else {
        b.appendChild(table(
          ["案件（社内呼称）", "ステージ", "ゲート", "次のゲート", "発売予定日", "分類・サイズ",
           "開発タイプ", "売上計上", "工数ポイント", "担当", "欠けているもの",
           "正本"],
          d.rows.map(function (r) {
            return el("tr", null, [
              el("td", { "class": "np-name" }, [el("a", { href: "#/projects/" + r.id, text: r.name })]),
              el("td", { text: r.stage }),
              el("td", null, [gateChips(r.gates)]),
              el("td", { text: r.next_gate }),
              el("td", { text: dash(r.launch_date) }),
              el("td", { text: r.product }),
              el("td", { text: r.flow_label }),
              el("td", { text: r.revenue }),
              el("td", { "class": "np-num",
                text: r.effort_point === null ? "未確定" : String(r.effort_point) }),
              el("td", { text: dash(r.owner) }),
              el("td", { "class": "np-num" }, [
                el("a", { href: "#/projects/" + r.id, text: String(r.missing_n) + " 件" })]),
              // R-2。**移行期間の正本を列で出す**
              el("td", { text: r.source_of_truth === "app" ? "アプリ" : "Drive（編集不可）" })
            ]);
          })));
      }
      d.notes.forEach(function (n) { b.appendChild(el("p", { "class": "np-note", text: n })); });
      b.appendChild(newProjectForm(d.filters.flow));
    }).catch(fail);
  }

  function newProjectForm(flows) {
    var box = el("details", { "class": "np-card" });
    box.appendChild(el("summary", { text: "案件を起こす" }));
    var f = el("form", { "class": "np-filters" });
    function inp(name, ph) { return el("input", { name: name, placeholder: ph, "aria-label": ph }); }
    f.appendChild(inp("internal_name", "社内呼称（商品名ではありません）"));
    var s = el("select", { name: "flow_type", "aria-label": "開発タイプ" });
    s.appendChild(el("option", { value: "", text: "開発タイプ" }));
    flows.forEach(function (o) {
      s.appendChild(el("option", { value: o.code,
        text: o.label + (o.effort_point === null ? "（工数ポイント未確定）" : "") }));
    });
    f.appendChild(s);
    f.appendChild(inp("launch_date", "発売予定日 YYYY-MM-DD"));
    f.appendChild(inp("occasion", "機会（なぜその日か）"));
    f.appendChild(inp("cat1", "分類1")); f.appendChild(inp("cat2", "分類2"));
    f.appendChild(inp("size", "サイズ"));
    f.appendChild(inp("owner", "担当"));
    f.appendChild(el("button", { type: "submit", text: "起こす" }));
    var msg = el("p", { "class": "np-note" });
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var o = {};
      Array.prototype.forEach.call(f.elements, function (x) { if (x.name) o[x.name] = x.value; });
      post("/api/projects", o).then(function (r) {
        if (!r.template_defined) {
          msg.textContent = "起こしました。この開発タイプには標準タスクがありません"
            + "（178行の種データに1行もありません）。タスク一覧は空のままです。";
          setTimeout(function () { location.hash = "#/projects/" + r.id; }, 1500);
        } else {
          location.hash = "#/projects/" + r.id;
        }
      }).catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    box.appendChild(f); box.appendChild(msg);
    return box;
  }

  // ══════════════════════════════════════════════════════
  // 案件カルテ（画面設計 3-8・重点1）
  // **17枚をタブにしない。**タブにすると、開いていないタブの不備が見えなくなる。
  // **B「いま欠けているもの」を最上段に置くのがこの画面の設計そのもの。**
  // ══════════════════════════════════════════════════════
  function viewProject(id) {
    loading();
    Promise.all([api("/api/projects/" + encodeURIComponent(id)), api("/api/meta")])
      .then(function (r) {
        var d = r[0], meta = r[1];
        var b = clear();
        var h = d.header;
        setTitle(h.name, "／ 案件 " + d.id);

        // A. ヘッダ（常時固定）。**商品名は出さない**（N-6-2）
        var head = el("div", { "class": "np-card", id: "np-sec-A" });     // 「A節へ」の飛び先
        head.appendChild(el("h2", { text: "A. " + h.name }));
        head.appendChild(table(["項目", "値"], [
          row("社内呼称", dash(h.internal_name)),
          row("分類・サイズ", h.product),
          row("開発タイプ", h.flow_label),
          row("発売予定日", dash(h.launch_date)),
          row("機会（なぜその日か）", dash(h.occasion)),
          row("作成エリア", dash(h.area)),
          row("担当", dash(h.owner)),
          row("ステージ", h.stage),
          row("売上計上", h.revenue),
          row("工数ポイント", h.effort_point === null ? "未確定" : String(h.effort_point)),
          row("正本（移行期間）", h.source_of_truth === "app" ? "アプリ" : "Drive（アプリでは編集不可）")
        ]));
        if (h.effort_point === null && h.effort_point_note)
          head.appendChild(el("p", { "class": "np-warn", text: "工数ポイント: " + h.effort_point_note }));
        if (h.flow_note) head.appendChild(el("p", { "class": "np-warn", text: h.flow_note }));
        head.appendChild(gateChips(d.gates));
        if (h.editable) head.appendChild(stageControl(d, meta));
        if (h.editable) head.appendChild(revenueControl(d));
        b.appendChild(head);

        // B. いま欠けているもの（最上段）
        var miss = el("div", { "class": "np-card" });
        var ng = d.next_gate;
        miss.appendChild(el("h2", {
          text: "B. いま欠けているもの" + (ng ? "（" + ng.gate + " " + ng.name
            + " を通すのに " + d.missing.length + "件）" : "")
        }));
        if (!ng) {
          miss.appendChild(el("p", { "class": "np-note", text: "判定待ちのゲートはありません。" }));
        } else if (!d.missing.length) {
          miss.appendChild(el("p", { "class": "np-note", text: "欠けているものはありません。" }));
        } else {
          var ul = el("ul", { "class": "np-miss" });
          d.missing.forEach(function (m) {
            var li = el("li", null, [
              m.label + " — " + m.why + " ",
              secBtn("np-sec-" + m.goto, m.goto + "節へ")
            ]);
            if (m.stage) li.appendChild(el("span", { "class": "np-stage",
              text: "（いまは人が確認した記録で判定します）" }));
            if (m.check === "manual" && h.editable) li.appendChild(checkForm(d.id, m));
            if (m.hint) li.appendChild(el("p", { "class": "np-sub", text: m.hint }));
            ul.appendChild(li);
          });
          miss.appendChild(ul);
          var last = ng.review;
          if (last) miss.appendChild(el("p", { "class": "np-note",
            text: last.result + " " + (last.approved_at || "") + " " +
                  (last.approved_by || "") + "「" + (last.comment || "") + "」" }));
        }
        b.appendChild(miss);

        // 止めずに知らせる（FR-101）。**通過は止めない**が、判定する人の目に入る場所に置く
        (d.warnings || []).forEach(function (w) {
          miss.appendChild(el("p", { "class": "np-warn" }, [txt(w.text + " "),
            secBtn("np-sec-" + w.goto, "原価・調達へ")]));
        });

        // C〜F の節
        d.sections.forEach(function (s) {
          var c = el("div", { "class": "np-card", id: "np-sec-" + s.key });
          c.appendChild(el("h2", {
            text: s.key + ". " + s.title + " — " + s.asks
          }));
          // **パーセントにしない。**「71%」では何が足りないか分からない
          c.appendChild(el("p", { "class": "np-sub",
            text: s.progress + (s.gate_pending ? "　｜　" + s.gate_pending : "") }));
          s.fields.forEach(function (fd) {
            c.appendChild(fieldForm(d.id, fd, h.editable));
          });
          b.appendChild(c);
        });

        // LP依頼書（FR-118）。カルテの項目から作る。**送らない**（人が見て貼る）
        var lp = el("div", { "class": "np-card", id: "np-sec-lpreq" });
        lp.appendChild(el("h2", { text: "LP依頼書" }));
        var lpb = el("button", { type: "button", text: "カルテから LP依頼書を作る" });
        lp.appendChild(el("p", null, [lpb, el("span", { "class": "np-sub", text: "　送りはしません。文面を確かめてから貼り付けて渡してください。" })]));
        lpb.addEventListener("click", function () {
          api("/api/projects/" + encodeURIComponent(d.id) + "/lp-request").then(function (r) {
            while (lp.childNodes.length > 2) lp.removeChild(lp.lastChild);
            if (r.missing.length) lp.appendChild(el("p", { "class": "np-warn", text: "埋まっていない項目が " + r.missing.length + " 件あります（依頼書には「未記入」と出ます）: " + r.missing.join("、") }));
            var ta = el("textarea", { rows: "20", readonly: "readonly", "aria-label": "LP依頼書" });
            ta.value = r.text;
            lp.appendChild(ta);
            var cp = el("button", { type: "button", text: "写す" });
            cp.addEventListener("click", function () {
              ta.select();
              if (navigator.clipboard) navigator.clipboard.writeText(r.text).then(function () { cp.textContent = "写しました"; });
            });
            lp.appendChild(el("p", null, [cp]));
          }).catch(function (e) { lp.appendChild(el("p", { "class": "np-err", text: e.message })); });
        });
        b.appendChild(lp);

        // 競合調査（FR-141〜143）。**出典と確認日が必須**。カルテ C の自由記述はメモとして残す
        var cm = el("div", { "class": "np-card", id: "np-sec-competitor" });
        cm.appendChild(el("h2", { text: "競合調査" }));
        cm.appendChild(el("p", { "class": "np-note", text: "読み込んでいます。" }));
        b.appendChild(cm);
        competitorPanel(d.id, cm);

        // バリエーション展開（F-4-5）。**40本の複製を作らない**
        var v = el("div", { "class": "np-card", id: "np-sec-variant" });
        v.appendChild(el("h2", { text: "バリエーション展開" }));
        v.appendChild(el("p", { "class": "np-sub", text: "本体モデル（機種など）ごとに1行。案件を複製しません。CIP への登録はバリエーションごとに行います。" }));
        if (!d.variants.length) v.appendChild(el("p", { "class": "np-note", text: "ありません。" }));
        else v.appendChild(table(["本体モデル", "仕様", "対応状況", "発売日", "CIP 商品コード", ""],
          d.variants.map(function (x) {
            var st = el("td", { text: x.state }), act = el("td");
            if (h.editable) {
              var sel = el("select", { "aria-label": "対応状況" });
              d.variant_states.forEach(function (k) { sel.appendChild(el("option", { value: k, text: k })); });
              sel.value = x.state;
              sel.addEventListener("change", function () {
                post("/api/projects/" + d.id + "/variant", { id: x.id, label: x.label, spec: x.spec || "", state: sel.value, launch_date: x.launch_date || "" })
                  .then(function () { go(); }).catch(function (e) { alert(e.message); });
              });
              st = el("td", null, [sel]);
              if (!x.product_code) {
                var del = el("button", { type: "button", text: "消す" });
                del.addEventListener("click", function () {
                  if (!window.confirm("「" + x.label + "」を消します。よろしいですか")) return;
                  post("/api/projects/" + d.id + "/variant-delete", { id: x.id }).then(function () { go(); })
                    .catch(function (e) { alert(e.message); });
                });
                act.appendChild(del);
              }
            }
            return el("tr", null, [el("td", { text: x.label }), el("td", { text: dash(x.spec) }), st,
              el("td", { text: dash(x.launch_date) }), el("td", { text: dash(x.product_code) }), act]);
          })));
        if (h.editable) {
          var vf = el("form", { "class": "np-inline" });
          var vl = el("input", { name: "label", size: "16", "aria-label": "本体モデル" });
          var vs = el("input", { name: "spec", size: "20", "aria-label": "仕様" });
          var vd = el("input", { name: "launch_date", type: "date", "aria-label": "発売日" });
          [txt("足す：本体モデル "), vl, txt(" 仕様 "), vs, txt(" 発売日 "), vd, txt(" "), el("button", { type: "submit", text: "足す" })]
            .forEach(function (x2) { vf.appendChild(x2); });
          vf.addEventListener("submit", function (ev) {
            ev.preventDefault();
            post("/api/projects/" + d.id + "/variant", { label: vl.value, spec: vs.value, launch_date: vd.value })
              .then(function () { go(); }).catch(function (e) { vf.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
          });
          v.appendChild(vf);
        }
        b.appendChild(v);

        // 対応確認（FR-102）。**ひな形は商品開発部が作る**（項目の中身は発明しない）
        var cp = el("div", { "class": "np-card", id: "np-sec-compat" });
        cp.appendChild(el("h2", { text: "対応確認" }));
        b.appendChild(cp);
        compatPanel(d.id, cp, h.editable);

        // 年間目標（FR-109/110・FR-106）。**自由入力にしない**（3方式と根拠が必須）
        var tg = el("div", { "class": "np-card", id: "np-sec-target" });
        tg.appendChild(el("h2", { text: "年間目標" }));
        b.appendChild(tg);
        targetPanel(d.id, tg, h.editable);

        // 発売後の売上（FR-183）。seisan に登録したコード → 店の商品番号 → 売上フィード
        var ps = el("div", { "class": "np-card", id: "np-sec-sales" });
        ps.appendChild(el("h2", { text: "発売後の売上" }));
        b.appendChild(ps);
        api("/api/projects/" + encodeURIComponent(d.id) + "/sales").then(function (r) {
          if (r.why) { ps.appendChild(el("p", { "class": "np-note", text: r.why })); return; }
          ps.appendChild(el("p", { "class": "np-sub", text: "共通商品コード " + r.codes.join("、") + "／発売日 " + r.launch_date
            + "／金額は" + r.tax + "の商品代（取消・返金を除く）。" + r.amazon }));
          ps.appendChild(table(["サイト", "店の商品番号", "発売から" + r.checkpoints[0] + "日", "発売から" + r.checkpoints[1] + "日", "発売からの合計", "数量"],
            r.sites.map(function (x) {
              function cp(c) { return c.revenue === null ? "—" : yen(c.revenue) + (c.reached ? "" : "（途中）"); }
              return el("tr", null, [el("td", { text: x.label }),
                el("td", { text: x.store_codes.length ? x.store_codes.join("、") : "紐付けなし" }),
                el("td", { "class": "np-num", text: cp(x.checkpoints[0]) }), el("td", { "class": "np-num", text: cp(x.checkpoints[1]) }),
                el("td", { "class": "np-num", text: x.total === null ? "—" : yen(x.total) }),
                el("td", { "class": "np-num", text: x.qty === null ? "—" : Math.round(x.qty).toLocaleString("ja-JP") })]);
            })));
          if (r.shared.length) ps.appendChild(el("p", { "class": "np-warn",
            text: "ほかの商品と共有している店の商品番号は数えていません（按分しないため）: " + r.shared.join("、") }));
        }).catch(function (e) { ps.appendChild(el("p", { "class": "np-err", text: e.message })); });

        // 原価・調達（第3段・ADR-047）。**マスタは持たない。**候補と試算だけ
        var cv = el("div", { "class": "np-card", id: "np-sec-cost" });
        cv.appendChild(el("h2", { text: "原価・調達" }));
        cv.appendChild(el("p", { "class": "np-note", text: "読み込んでいます。" }));
        b.appendChild(cv);
        costPanel(d.id, cv);

        // seisan への登録（第3段・FR-103）。**別に読む**（seisan が落ちていても案件は見える）
        var sv = el("div", { "class": "np-card", id: "np-sec-seisan" });
        sv.appendChild(el("h2", { text: "CIP への登録" }));
        sv.appendChild(el("p", { "class": "np-note", text: "読み込んでいます。" }));
        b.appendChild(sv);
        seisanPanel(d.id, sv);

        // タスク
        var tv = el("div", { "class": "np-card", id: "np-sec-tasks" });
        tv.appendChild(el("h2", { text: "タスク（" + d.tasks.length + "件）" }));
        if (!d.template_defined) {
          tv.appendChild(el("p", { "class": "np-warn",
            text: "標準タスク未定義。この開発タイプ（ページリニューアル）には標準タスクが"
              + "存在しません。178行の種データに1行もなく、発明もしていません。"
              + "そのため、案件を作ってもタスク一覧は空になります。"
              + "定義は商品開発部の未着手事項です。" }));
        }
        if (!d.tasks.length) {
          tv.appendChild(el("p", { "class": "np-note", text: "タスクはありません。" }));
        } else {
          tv.appendChild(table(["#", "進捗", "期限", "タスク", "ロール", "担当", "標準h"],
            d.tasks.map(function (t) {
              return el("tr", null, [
                el("td", { "class": "np-num", text: String(t.seq) }),
                el("td", { text: t.status }),
                el("td", { text: dash(t.due_on) }),
                el("td", { text: t.title }),
                el("td", { text: dash(t.role_label) }),
                el("td", { text: dash(t.assignee) }),
                el("td", { "class": "np-num", text: t.hours === null ? "—" : String(t.hours) })
              ]);
            })));
          tv.appendChild(el("p", { "class": "np-note",
            text: "標準h は予備時間を含まない実作業hです（テンプレート由来）。" }));
        }
        b.appendChild(tv);

        // G. 履歴 ＋ ゲートの判定
        var g = el("div", { "class": "np-card", id: "np-sec-G" });
        g.appendChild(el("h2", { text: "G. 履歴 — 誰がいつ何を決めたか" }));
        if (ng) g.appendChild(gateForm(d, ng, meta));
        g.appendChild(table(["日時", "誰が", "何を"], d.revisions.map(function (x) {
          return el("tr", null, [el("td", { text: x.changed_at }),
            el("td", { text: dash(x.changed_by) }), el("td", { text: x.what })]);
        })));
        b.appendChild(g);
        foldSections(b);
      }).catch(fail);

    function row(k, v) {
      return el("tr", null, [el("th", { text: k }), el("td", { text: v })]);
    }
  }

  /** 案件画面の節をたたむ（全体の見直し・2026-10-06 十文字さん選択）。縦に長すぎて目当ての節が探せないため。
   *  ・ヘッダ（A）と「いま欠けているもの」はたたまない（判断の起点なので常に見せる）
   *  ・上に目次のボタンを置く。押すとその節を開いて移動する
   *  ・既定は閉じる。開いた・閉じたは、この端末に節ごとに覚える（案件をまたいで同じ節が同じ状態になる） */
  var FOLD_KEY = "newproduct-fold-";
  function foldSections(b) {
    var cards = Array.prototype.filter.call(b.children, function (c) {
      // ヘッダ（A）はたたまない（判断の起点なので常に見せる。「A節へ」の飛び先でもある）
      return c.classList && c.classList.contains("np-card") && /^np-sec-/.test(c.id || "") && c.id !== "np-sec-A";
    });
    if (!cards.length) return;
    var toc = [];
    cards.forEach(function (c) {
      var h = c.querySelector("h2");
      var title = h ? h.textContent : c.id;
      var det = el("details", { "class": "np-fold" });
      var open = null;
      try { open = localStorage.getItem(FOLD_KEY + c.id); } catch (e) { /* 使えなくても開いたまま */ }
      if (open === "1") det.setAttribute("open", "open");          // 既定は閉じる（目次から開く）
      det.appendChild(el("summary", { text: title }));
      det.addEventListener("toggle", function () {
        try { localStorage.setItem(FOLD_KEY + c.id, det.open ? "1" : "0"); } catch (e) { /* 覚えないだけ */ }
      });
      b.insertBefore(det, c);
      det.appendChild(c);
      if (h) h.classList.add("np-fold-h");
      toc.push(secBtn(c.id, title.replace(/^[A-G]\. /, "").replace(/ — .*$/, "")));
    });
    var bar = btnRow(toc);
    bar.classList.add("np-toc");
    var allOpen = el("button", { type: "button", "class": "np-btn np-btn-sm", text: "すべて開く" });
    var allClose = el("button", { type: "button", "class": "np-btn np-btn-sm", text: "すべて閉じる" });
    allOpen.addEventListener("click", function () { b.querySelectorAll("details.np-fold").forEach(function (x) { x.open = true; }); });
    allClose.addEventListener("click", function () { b.querySelectorAll("details.np-fold").forEach(function (x) { x.open = false; }); });
    bar.appendChild(allOpen); bar.appendChild(allClose);
    b.insertBefore(el("p", { "class": "np-sub", text: "節へ移動（押すと開きます）" }), cards[0].parentNode);
    b.insertBefore(bar, cards[0].parentNode);
  }

  function fieldForm(pid, fd, editable) {
    var w = el("div", { "class": "np-field" });
    var id = "f-" + pid + "-" + fd.key.replace(".", "-");
    w.appendChild(el("label", { "for": id, text: fd.label }));
    if (!editable) {
      w.appendChild(el("p", { "class": "np-note", text: fd.body || "（まだ入力がありません）" }));
      return w;
    }
    var f = el("form");
    var ta = el("textarea", { id: id, name: "body" });
    ta.value = fd.body || "";
    f.appendChild(ta);
    var bar = el("p", { "class": "np-sub" });
    bar.appendChild(el("button", { type: "submit", text: "保存" }));
    bar.appendChild(txt(fd.updated_at ? "　最終更新 " + fd.updated_at + " " + (fd.updated_by || "") : "　まだ入力がありません"));
    f.appendChild(bar);
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/projects/" + pid + "/section", { key: fd.key, body: ta.value })
        .then(function () { go(); })
        .catch(function (e) { bar.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
    });
    w.appendChild(f);
    return w;
  }

  /** 売上計上（F-4-9）。**既定は含めない。**含めるなら全額か増分（同じ商品番号の前年との差） */
  function revenueControl(d) {
    var f = el("form", { "class": "np-inline" });
    var sel = el("select", { "aria-label": "売上計上" });
    [["0", "新商品売上に含めない"], ["全額", "含める（全額）"], ["増分", "含める（増分＝前年の同じ期間との差）"]].forEach(function (o) {
      sel.appendChild(el("option", { value: o[0], text: o[1] })); });
    var cur = d.header.revenue;
    sel.value = cur.indexOf("全額") >= 0 ? "全額" : cur.indexOf("増分") >= 0 ? "増分" : "0";
    [txt("売上計上: "), sel, txt(" "), el("button", { type: "submit", text: "変える" })].forEach(function (x) { f.appendChild(x); });
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/projects/" + encodeURIComponent(d.id) + "/revenue", { counted: sel.value === "0" ? "0" : "1", basis: sel.value === "0" ? "" : sel.value })
        .then(function () { go(); }).catch(function (e) { f.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
    });
    return f;
  }

  /** ステージの遷移（FR-33）。**1本の道。分岐は保留・中止だけ。**関門と対応するステージは関門の通過が要る */
  function stageControl(d, meta) {
    var o = d.stage_options, w = el("div", { "class": "np-inline" });
    var P = "/api/projects/" + encodeURIComponent(d.id) + "/stage";
    function go2(body) { post(P, body).then(function () { go(); }).catch(function (e) { w.appendChild(el("span", { "class": "np-err", text: " " + e.message })); }); }
    w.appendChild(txt("ステージ: " + o.stage + "　"));
    if (o.next) {
      var nb = el("button", { type: "button", text: "次へ: " + o.next });
      if (o.next_why) nb.setAttribute("disabled", "disabled");
      nb.addEventListener("click", function () { go2({ action: "next" }); });
      w.appendChild(nb);
      if (o.next_why) w.appendChild(el("span", { "class": "np-sub", text: " " + o.next_why }));
    }
    if (o.can_resume) {
      var rb = el("button", { type: "button", text: "再開（" + o.resume_to + " に戻す）" });
      rb.addEventListener("click", function () { go2({ action: "resume" }); });
      w.appendChild(rb);
    }
    function reasonPick(action, label, list) {
      var sel = el("select", { "aria-label": label + "の理由" });
      sel.appendChild(el("option", { value: "", text: label + "の理由を選ぶ" }));
      list.forEach(function (r) { sel.appendChild(el("option", { value: r.code, text: r.label })); });
      var bt = el("button", { type: "button", text: label });
      bt.addEventListener("click", function () {
        if (action === "abort" && !window.confirm("中止にします。中止の後はステージを動かせません。よろしいですか")) return;
        go2({ action: action, reason_code: sel.value });
      });
      w.appendChild(txt("　")); w.appendChild(sel); w.appendChild(bt);
    }
    if (o.can_hold) reasonPick("hold", "保留", meta.reasons.hold);
    if (o.can_abort) reasonPick("abort", "中止", meta.reasons.abort);
    return w;
  }

  /** 年間目標（F-10-8）。発売から1年・税込の商品代。**未設定は「目標未設定」**（0 と書かない） */
  function targetPanel(pid, box, editable) {
    var P = "/api/projects/" + encodeURIComponent(pid);
    api(P + "/target").then(function (t) {
      if (t.state === "目標未設定") {
        box.appendChild(el("p", { "class": "np-warn", text: "目標未設定（G3・G5 を通すには、方式と根拠つきの年間目標が要ります）" }));
      } else {
        box.appendChild(table(["項目", "値"], [
          el("tr", null, [el("th", { text: "年間目標（発売から1年・税込の商品代）" }), el("td", { text: yen(t.annual_yen) })]),
          el("tr", null, [el("th", { text: "方式" }), el("td", { text: t.method })]),
          el("tr", null, [el("th", { text: "根拠" }), el("td", { text: t.basis })]),
          el("tr", null, [el("th", { text: "発売からの実績" }), el("td", { text: t.actual === undefined || t.actual === null ? "未計測（CIP 登録と発売の後に出ます）" : yen(t.actual) + "（発売から " + t.days + " 日）" })]),
          el("tr", null, [el("th", { text: "達成率" }), el("td", { text: t.rate === null || t.rate === undefined ? "—" : t.rate + "%（年間目標に対して）"
            + (t.pace_rate !== null && t.pace_rate !== undefined ? "／経過日数で按分した目安に対して " + t.pace_rate + "%" : "") })])
        ]));
        box.appendChild(el("p", { "class": "np-sub", text: "最終更新 " + dash(t.updated_at) + " " + dash(t.updated_by) + "。Amazon の実績はまだ含みません（売上フィードに無いため）。" }));
      }
      if (!editable) return;
      var f = el("form", { "class": "np-field" });
      var ms = el("select", { name: "method", "aria-label": "方式" });
      ms.appendChild(el("option", { value: "", text: "（方式を選ぶ）" }));
      Object.keys(t.methods).forEach(function (m) { ms.appendChild(el("option", { value: m, text: m })); });
      if (t.method) ms.value = t.method;
      var hint = el("span", { "class": "np-sub" });
      ms.addEventListener("change", function () { hint.textContent = ms.value ? "　根拠に書くこと: " + t.methods[ms.value] : ""; });
      var yenIn = el("input", { name: "annual_yen", size: "10", "aria-label": "年間目標（円）" });
      if (t.annual_yen) yenIn.value = Math.round(t.annual_yen);
      var bs = el("textarea", { name: "basis", rows: "3", "aria-label": "根拠" });
      bs.value = t.basis || "";
      f.appendChild(el("p", null, [ms, txt(" 年間目標（円・税込の商品代） "), yenIn, hint]));
      f.appendChild(el("label", { text: "根拠（必須）" }));
      f.appendChild(bs);
      var dsx = t.dist || {};
      var bar = el("p", null, [el("button", { type: "submit", text: t.state === "目標未設定" ? "目標を決める" : "目標を直す" }),
        el("span", { "class": "np-sub", text: dsx.n ? "　平均で置くとほぼ全商品が未達になります（過去の新商品 " + dsx.n + " 件の発売から12か月の中央値は " + yen(dsx.median) + "・上位 " + dsx.top80 + " 商品で8割）。似ている商品の実績から置くのが目安です。" : "" })]);
      // FR-177。確定した販売計画があれば、その1本あたりを「逆算法」で入れられる
      var pr = t.plan_ref;
      if (pr && !pr.none && pr.each) {
        var fill = el("button", { type: "button", text: "販売計画（" + pr.fy + "年度・" + pr.n + "本）の1本あたり " + yen(pr.each) + " を入れる" });
        fill.addEventListener("click", function () {
          ms.value = "逆算法"; yenIn.value = pr.each; bs.value = pr.basis;
          hint.textContent = "　根拠に書くこと: " + t.methods["逆算法"];
        });
        f.appendChild(el("p", { "class": "np-btnrow" }, [fill]));
      } else if (pr && pr.none) {
        f.appendChild(el("p", { "class": "np-sub", text: pr.fy + "年度の販売計画はまだ確定していません（確定すると、ここから1本あたりを入れられます）。" }));
      }
      f.appendChild(bar);
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        post(P + "/target", { method: ms.value, annual_yen: yenIn.value, basis: bs.value }).then(function () { go(); })
          .catch(function (e) { bar.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
      });
      box.appendChild(f);
    }).catch(function (e) { box.appendChild(el("p", { "class": "np-err", text: e.message })); });
  }

  /** 競合調査（FR-141〜143）。価格・仕様・デザイン傾向・レビューを表で比べる。**出典と確認日が必須。**
   *  売上の推計（累計）＝ レビュー件数 ÷ レビュー率 × 価格。**レビュー率が決まるまで推計は出さない。** */
  function competitorPanel(pid, box) {
    var P = "/api/projects/" + encodeURIComponent(pid);
    function u(v, unit) { return (v === null || v === undefined) ? "未確認" : Number(v).toLocaleString("ja-JP") + (unit || ""); }
    api(P + "/competitor").then(function (o) {
      while (box.childNodes.length > 1) box.removeChild(box.lastChild);
      box.appendChild(el("p", { "class": "np-sub", text: "比べた競合商品を1行ずつ。値は確認日の時点のものです。調べ直したら確認日も直してください。分からない値は空欄のまま（未確認。0 にはしません）。" }));
      var rate = el("p", { "class": o.review_rate === null ? "np-warn" : "np-note",
        text: o.review_rate === null
          ? "レビュー率が未設定のため、売上の推計は出していません（決めるのは商品開発部）。"
          : "売上の推計（累計）＝ レビュー件数 ÷ レビュー率 " + o.review_rate + "% × 販売価格。仮定の率で出した目安で、実績ではありません。" });
      box.appendChild(rate);
      if (o.can_set_rate) box.appendChild(btnRow([navBtn("#/settings", "レビュー率を決める・変える（設定）", "back")]));
      if (o.price_range) box.appendChild(el("p", { "class": "np-note",
        text: "価格の幅: " + u(o.price_range[0], "円") + " 〜 " + u(o.price_range[1], "円") + "（価格が分かっている " + o.price_known + " 件・税込）" }));
      if (!o.rows.length) box.appendChild(el("p", { "class": "np-note", text: "まだ競合がありません。" }));
      else box.appendChild(table(["店・メーカー／商品", "販路", "価格（税込）", "仕様", "デザイン傾向", "レビュー", "目立つ声", "売上の推計（累計）", "確認日", ""],
        o.rows.map(function (r) {
          var est = r.estimate.revenue === null ? "—（" + r.estimate.missing.join("・") + "が無い）"
            : u(r.estimate.revenue, "円") + "（" + u(r.estimate.qty, "個") + "）";
          var act = el("td");
          if (o.editable) {
            var del = el("button", { type: "button", text: "消す" });
            del.addEventListener("click", function () {
              if (!window.confirm("「" + r.shop + "／" + r.item + "」を消します。よろしいですか")) return;
              post(P + "/competitor-delete", { id: r.id }).then(function () { competitorPanel(pid, box); })
                .catch(function (e) { act.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
            });
            act.appendChild(del);
          }
          return el("tr", null, [
            el("td", null, [txt(r.shop + "／"), /^https?:\/\//i.test(r.url) ? el("a", { href: r.url, rel: "noopener noreferrer", target: "_blank", text: r.item }) : txt(r.item)]),
            el("td", { text: dash(r.channel) }), el("td", { "class": "np-num", text: u(r.price_yen, "円") }),
            el("td", { text: dash(r.spec) }), el("td", { text: dash(r.design) }),
            el("td", { "class": "np-num", text: u(r.review_count, "件") + (r.review_avg === null ? "" : "・評価 " + r.review_avg) }),
            el("td", { text: dash(r.review_note) }), el("td", { "class": "np-num", text: est }),
            el("td", { text: r.checked_on + (r.age_days > 90 ? "（" + r.age_days + "日前・古い）" : "") }), act]);
        })));
      if (!o.editable) return;
      var f = el("form", { "class": "np-field" });
      f.appendChild(el("p", { "class": "np-sub", text: "競合を足す（店・商品・出典 URL・確認日は必須）" }));
      function inp(name, label, size, type) { return el("label", null, [label + " ", el("input", { name: name, size: size || "10", type: type || "text" })]); }
      var ch = el("select", { name: "channel", "aria-label": "販路" });
      [""].concat(o.channels).forEach(function (k) { ch.appendChild(el("option", { value: k, text: k || "—" })); });
      [el("p", null, [inp("shop", "店・メーカー", 14), txt(" "), inp("item", "商品", 20), txt(" "), el("label", null, ["販路 ", ch])]),
       el("p", null, [inp("url", "出典 URL", 28), txt(" "), inp("checked_on", "確認日", 10, "date")]),
       el("p", null, [inp("price_yen", "価格（円・税込）", 7), txt(" "), inp("review_count", "レビュー件数", 5), txt(" "), inp("review_avg", "平均評価（0〜5）", 3)]),
       el("p", null, [inp("spec", "仕様", 18), txt(" "), inp("design", "デザイン傾向", 18)]),
       el("p", null, [inp("review_note", "レビューで目立つ声", 28), txt(" "), inp("note", "備考", 18)])
      ].forEach(function (x) { f.appendChild(x); });
      var bar = el("p", null, [el("button", { type: "submit", text: "足す" })]);
      f.appendChild(bar);
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var obj = {};
        ["shop", "item", "channel", "url", "checked_on", "price_yen", "review_count", "review_avg", "spec", "design", "review_note", "note"]
          .forEach(function (k) { obj[k] = f.elements[k].value; });
        post(P + "/competitor", obj).then(function () { competitorPanel(pid, box); })
          .catch(function (e) { bar.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
      });
      box.appendChild(f);
    }).catch(function (e) { box.appendChild(el("p", { "class": "np-err", text: e.message })); });
  }

  /** 対応確認（FR-102・2026-10-01「商品開発部に決めてもらう」）。
   *  ひな形を選ぶと項目が写る。**写した後にひな形を直しても、この案件の記録は変わらない。** */
  function compatPanel(pid, box, editable) {
    var P = "/api/projects/" + encodeURIComponent(pid);
    api(P + "/compat").then(function (o) {
      var sm = o.summary;
      box.appendChild(el("p", { "class": "np-sub", text: "確認済 " + sm["確認済"] + "／不可 " + sm["不可"] + "／未確認 " + sm["未確認"] }));
      if (o.rows.length) {
        box.appendChild(table(["項目", "結果", "備考", ""], o.rows.map(function (r) {
          var act = el("td");
          if (editable) o.results.forEach(function (res) {
            if (res === r.result) return;
            var bt = el("button", { type: "button", text: res });
            bt.addEventListener("click", function () {
              var note = res === "不可" ? window.prompt("「不可」の理由（必須）", r.note || "") : (r.note || "");
              if (note === null) return;
              post(P + "/compat-result", { id: r.id, result: res, note: note }).then(function () { go(); })
                .catch(function (e) { act.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
            });
            act.appendChild(bt); act.appendChild(txt(" "));
          });
          return el("tr", null, [el("td", { text: r.item }), el("td", { text: r.result }), el("td", { text: dash(r.note) }), act]);
        })));
      } else {
        box.appendChild(el("p", { "class": "np-note", text: "まだ確認項目がありません。下で商品の種類（ひな形）を選ぶと項目が入ります。" }));
      }
      if (editable && o.templates.length) {
        var f = el("form", { "class": "np-inline" });
        var sel = el("select", { name: "template_id", "aria-label": "商品の種類" });
        o.templates.forEach(function (t) { sel.appendChild(el("option", { value: String(t.id), text: t.name + "（" + t.items.length + "項目）" })); });
        [txt("商品の種類を選んで項目を入れる "), sel, txt(" "), el("button", { type: "submit", text: "入れる" })].forEach(function (x) { f.appendChild(x); });
        f.addEventListener("submit", function (ev) {
          ev.preventDefault();
          post(P + "/compat-apply", { template_id: sel.value }).then(function () { go(); })
            .catch(function (e) { f.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
        });
        box.appendChild(f);
      } else if (!o.templates.length) {
        box.appendChild(el("p", { "class": "np-warn", text: "商品の種類ごとの確認項目（ひな形）がまだありません。商品開発部で作ってください（下の「ひな形を作る・直す」）。" }));
      }
      if (o.can_edit_templates) {
        var det = el("details");
        det.appendChild(el("summary", { text: "ひな形を作る・直す（商品開発部・管理者）" }));
        var tf = el("form", { "class": "np-field" });
        var nm = el("input", { name: "name", size: "20", list: "np-ctpl" });
        var dl = el("datalist", { id: "np-ctpl" });
        o.templates.forEach(function (t) { dl.appendChild(el("option", { value: t.name })); });
        var ta = el("textarea", { name: "items", rows: "5", "aria-label": "確認する項目（1行に1つ）" });
        nm.addEventListener("change", function () {
          var t = o.templates.filter(function (x) { return x.name === nm.value; })[0];
          if (t) ta.value = t.items.join("\n");
        });
        tf.appendChild(el("p", null, [el("label", null, ["商品の種類 ", nm]), dl,
          el("span", { "class": "np-sub", text: "　同じ名前なら上書きします（案件に写した項目は変わりません）" })]));
        tf.appendChild(el("label", { text: "確認する項目（1行に1つ）" }));
        tf.appendChild(ta);
        var bar = el("p", null, [el("button", { type: "submit", text: "保存" })]);
        tf.appendChild(bar);
        tf.addEventListener("submit", function (ev) {
          ev.preventDefault();
          post("/api/compat/templates", { name: nm.value, items: ta.value }).then(function () { go(); })
            .catch(function (e) { bar.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
        });
        det.appendChild(tf);
        box.appendChild(det);
      }
    }).catch(function (e) { box.appendChild(el("p", { "class": "np-err", text: e.message })); });
  }

  /** 原価・調達（第3段・ADR-047）。
   *  **マスタは seisan。**ここは案件ごとの相見積の候補と試算原価の版だけ。
   *  金額は円・税抜（為替は持たない）。**未確定は 0 と書かない。** */
  function cyen(v) { return (v === null || v === undefined) ? "未確定" : Math.round(v).toLocaleString("ja-JP") + "円"; }
  function cnum(v, u) { return (v === null || v === undefined) ? "未確定" : String(v) + (u || ""); }

  function costPanel(pid, box) {
    var P = "/api/projects/" + encodeURIComponent(pid);
    function err(node, e) { node.appendChild(el("p", { "class": "np-err", text: e.message })); }
    api(P + "/cost").then(function (o) {
      while (box.childNodes.length > 1) box.removeChild(box.lastChild);
      box.appendChild(el("p", { "class": "np-sub",
        text: "外注先・仕入先・原材料の一覧は CIP が持ちます。ここには、この案件で比べた候補と試算原価だけを置きます。金額は円・税抜で入れてください（外貨で見積もったときは、換算したレートを備考に）。" }));

      // 発注の締切（FR-101）
      var dl = o.deadline;
      box.appendChild(el("h3", { text: "本番発注の締切" }));
      box.appendChild(el("p", { "class": dl.state === "間に合う" ? "np-note" : "np-warn",
        text: dl.state + (dl.due ? "：" + dl.due + " まで（発売日 " + dl.launch_date + " − " + dl.by + " のリードタイム "
          + dl.lead_days + "日。" + (dl.days_left >= 0 ? "あと " + dl.days_left + " 日" : "") + "）" : "") + (dl.why ? "　" + dl.why : "") }));

      // 相見積（FR-98・FR-96）
      box.appendChild(el("h3", { text: "相見積の候補（" + o.candidates.length + "件）" }));
      if (o.candidates.length) {
        box.appendChild(table(["種類", "仕入先・外注先", "形状・サイズ", "単価", "最低ロット", "リードタイム", "安定供給", "判断", "seisan"],
          o.candidates.map(function (c) {
            var judge = el("td");
            judge.appendChild(txt(c.adopted ? "採用" : (c.not_adopted_reason ? "不採用：" + c.not_adopted_reason : "未判断")));
            if (o.editable) {
              var yes = el("button", { type: "button", text: "採用" });
              var no = el("button", { type: "button", text: "不採用" });
              yes.addEventListener("click", function () {
                post(P + "/cost-adopt", { id: c.id, adopted: "1" }).then(function () { go(); }).catch(function (e) { err(judge, e); });
              });
              no.addEventListener("click", function () {
                var r = window.prompt("採用しなかった理由（後で同じ候補を探し直さないため・必須）", c.not_adopted_reason || "");
                if (r === null) return;
                post(P + "/cost-adopt", { id: c.id, adopted: "0", reason: r }).then(function () { go(); }).catch(function (e) { err(judge, e); });
              });
              judge.appendChild(txt(" ")); judge.appendChild(yes); judge.appendChild(txt(" ")); judge.appendChild(no);
            }
            return el("tr", null, [el("td", { text: c.kind + (c.part ? "・" + c.part : "") }),
              el("td", null, [c.url && /^https?:\/\//i.test(c.url) ? el("a", { href: c.url, rel: "noopener noreferrer", target: "_blank", text: c.supplier }) : txt(c.supplier)]),
              el("td", { text: dash(c.shape_size) }), el("td", { "class": "np-num", text: cyen(c.unit_price) }),
              el("td", { "class": "np-num", text: cnum(c.min_lot, "個") }), el("td", { "class": "np-num", text: cnum(c.lead_days, "日") }),
              el("td", { text: dash(c.stability) }), judge, seisanCell(P, o, c)]);
          })));
      } else {
        box.appendChild(el("p", { "class": "np-note", text: "まだ候補がありません。" }));
      }
      if (o.editable) box.appendChild(candidateForm(P, o));

      // 試算原価（FR-88〜92）
      box.appendChild(el("h3", { text: "試算原価" }));
      if (!o.versions.length) {
        box.appendChild(el("p", { "class": "np-note", text: "まだ版がありません。「v1 を作る」から始めてください（ゼロから行を積みます）。" }));
      }
      o.versions.forEach(function (v) { box.appendChild(versionBlock(P, o, v)); });
      if (o.editable) {
        var nb = el("button", { type: "button", text: o.versions.length ? "新しい版を作る（v" + (o.versions[0].version + 1) + "。前の版は残ります）" : "v1 を作る" });
        nb.addEventListener("click", function () {
          post(P + "/cost-version", {}).then(function () { go(); }).catch(function (e) { err(box, e); });
        });
        box.appendChild(el("p", null, [nb]));
      }
    }).catch(function (e) { err(box, e); });
  }

  /** 採用した候補を seisan に登録する（FR-184）。**マスタは seisan。**ここには「どこに入れたか」だけ残る */
  function seisanCell(P, o, c) {
    var td = el("td");
    if (c.seisan_ref) {
      td.appendChild(txt("登録済 " + c.seisan_ref));
      if (c.seisan_note) td.appendChild(el("p", { "class": "np-warn", text: c.seisan_note }));
      return td;
    }
    if (!c.adopted) { td.appendChild(txt("—")); return td; }
    if (!(o.can_register && o.editable)) { td.appendChild(txt("未登録")); return td; }
    var btn = el("button", { type: "button", text: "CIP に登録" });
    td.appendChild(btn);
    btn.addEventListener("click", function () {
      btn.setAttribute("disabled", "disabled");
      api(P + "/cost-seisan-vocab").then(function (v) {
        if (!v.configured) { td.appendChild(el("p", { "class": "np-warn", text: v.why })); return; }
        td.appendChild(c.kind === "資材" ? materialForm(P, c, v) : outsourceForm(P, c, v));
      }).catch(function (e) { td.appendChild(el("p", { "class": "np-err", text: e.message })); });
    });
    return td;
  }

  function pickList(name, label, items, first) {
    var s = el("select", { name: name, "aria-label": label });
    if (first) s.appendChild(el("option", { value: "", text: first }));
    items.forEach(function (x) { s.appendChild(el("option", { value: x[0], text: x[1] })); });
    return el("label", null, [label + " ", s]);
  }

  function submitTo(P, f, c, fields) {
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var obj = { id: c.id };
      fields.forEach(function (k) {
        var e = f.elements[k];
        if (e) obj[k] = e.type === "checkbox" ? (e.checked ? "1" : "") : e.value;
      });
      if (!window.confirm("CIP のマスタに登録します。取り消しや修正は CIP の画面で行います。よろしいですか")) return;
      post(P + "/cost-seisan", obj).then(function (r) {
        if (r.note) window.alert(r.note);
        go();
      }).catch(function (e) { f.appendChild(el("p", { "class": "np-err", text: e.message })); });
    });
  }

  function materialForm(P, c, v) {
    var f = el("form", { "class": "np-field" });
    f.appendChild(el("p", { "class": "np-sub", text: "原材料として CIP に入れます。材料コードは登録する人が決めます（CIP は採番しません）。仕入先「" + c.supplier + "」・リードタイム・最低ロットは候補から写します。" }));
    f.appendChild(el("p", null, [el("label", null, [el("input", { type: "checkbox", name: "existing" }), " CIP に既にある材料に、仕入条件だけ付ける"])]));
    f.appendChild(el("p", null, [el("label", null, ["材料コード（必須） ", el("input", { name: "code", size: "12" })]), txt(" "),
      pickList("material_kind", "種別（必須）", v.kinds.map(function (k) { return [k, k]; }), "（選ぶ）")]));
    var cat = el("input", { name: "category", size: "14", list: "np-mcat-" + c.id });
    var dl = el("datalist", { id: "np-mcat-" + c.id });
    v.categories.forEach(function (x) { dl.appendChild(el("option", { value: x })); });
    f.appendChild(el("p", null, [el("label", null, ["分類（新規のとき必須・既存の分類から選べます） ", cat]), dl]));
    f.appendChild(el("p", null, [el("label", null, ["名称（新規のとき必須） ", el("input", { name: "name", size: "24" })]), txt(" "),
      el("label", null, ["単価（空欄なら候補の単価 " + cyen(c.unit_price) + "） ", el("input", { name: "unit_price", size: "7" })]), txt(" "),
      el("label", null, ["単位 ", el("input", { name: "unit", size: "4" })])]));
    f.appendChild(el("p", null, [el("button", { type: "submit", text: "CIP に登録する" })]));
    submitTo(P, f, c, ["existing", "code", "material_kind", "category", "name", "unit_price", "unit"]);
    return f;
  }

  function outsourceForm(P, c, v) {
    var f = el("form", { "class": "np-field" });
    f.appendChild(el("p", { "class": "np-sub", text: "外注先として CIP に入れます。CIP に既にある外注先なら選んでください（同じ相手を二重に作らないため）。単価・最低ロット・リードタイムは候補から写します。" }));
    f.appendChild(el("p", null, [pickList("outsourcer_id", "外注先", v.outsourcers.map(function (x) { return [String(x.id), x.name]; }),
      "新しく作る（名前: " + c.supplier + "）")]));
    f.appendChild(el("p", null, [el("label", null, ["対応できる加工（新規のとき） ", el("input", { name: "capabilities", size: "20" })]), txt(" "),
      el("label", null, ["発注方法（新規のとき） ", el("input", { name: "order_method", size: "10" })])]));
    f.appendChild(el("p", null, [pickList("target_kind", "単価の品目", v.target_kinds.map(function (k) { return [k, k]; }), "単価は登録しない"), txt(" "),
      el("label", null, ["品目（商品コード・分類名・工程） ", el("input", { name: "target_key", size: "14" })]), txt(" "),
      el("label", null, ["単位 ", el("input", { name: "unit", size: "4" })])]));
    f.appendChild(el("p", null, [el("button", { type: "submit", text: "CIP に登録する" })]));
    submitTo(P, f, c, ["outsourcer_id", "capabilities", "order_method", "target_kind", "target_key", "unit"]);
    return f;
  }

  function candidateForm(P, o) {
    var f = el("form", { "class": "np-field" });
    f.appendChild(el("p", { "class": "np-sub", text: "候補を足す" }));
    function sel(name, label, opts) {
      var s = el("select", { name: name, "aria-label": label });
      opts.forEach(function (x) { s.appendChild(el("option", { value: x[0], text: x[1] })); });
      return el("label", null, [label + " ", s]);
    }
    function inp(name, label, size) { return el("label", null, [label + " ", el("input", { name: name, size: size || "10" })]); }
    var row1 = el("p", null, [sel("kind", "種類", o.kinds.map(function (k) { return [k, k]; })), txt(" "),
      sel("part", "区分（資材）", [["", "—"]].concat(o.parts_material.map(function (k) { return [k, k]; }))), txt(" "),
      inp("supplier", "仕入先・外注先", 16), txt(" "), inp("shape_size", "形状・サイズ", 10)]);
    var row2 = el("p", null, [inp("unit_price", "単価（円・税抜）", 7), txt(" "), inp("min_lot", "最低ロット", 5), txt(" "),
      inp("lead_days", "リードタイム（日）", 4), txt(" "), inp("min_designs", "最低デザイン数（外注）", 4), txt(" "),
      sel("sample_ok", "印刷サンプル", [["", "未確認"], ["1", "可"], ["0", "不可"]])]);
    var row3 = el("p", null, [inp("url", "URL", 24), txt(" "), inp("stability", "安定供給の見込み", 14), txt(" "),
      inp("features", "特徴", 18), txt(" "), inp("note", "備考", 18)]);
    var bar = el("p", null, [el("button", { type: "submit", text: "候補を足す" }),
      el("span", { "class": "np-sub", text: "　分からない値は空欄のまま（未確定として扱い、0 にはしません）" })]);
    [row1, row2, row3, bar].forEach(function (x) { f.appendChild(x); });
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var obj = {};
      ["kind", "part", "supplier", "shape_size", "unit_price", "min_lot", "lead_days", "min_designs",
       "sample_ok", "url", "stability", "features", "note"].forEach(function (k) { obj[k] = f.elements[k].value; });
      post(P + "/cost-candidate", obj).then(function () { go(); })
        .catch(function (e) { bar.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
    });
    return f;
  }

  function versionBlock(P, o, v) {
    var w = el("div", { "class": "np-field" });
    var t = v.totals;
    w.appendChild(el("h4", { text: "v" + v.version + (v.latest ? "（最新）" : "（前の版・変更不可）")
      + "　確度 " + dash(v.confidence) + "　作成 " + dash(v.created_at) + " " + dash(v.created_by) }));
    var unknown = t.direct.unknown ? "（未確定 " + t.direct.unknown + " 行を含む。その分は入っていません）" : "";
    w.appendChild(table(["項目", "金額"], [
      el("tr", null, [el("td", { text: "材料費" }), el("td", { "class": "np-num", text: cyen(t.material.yen) + (t.material.unknown ? "＋未確定" + t.material.unknown + "行" : "") })]),
      el("tr", null, [el("td", { text: "外注費" }), el("td", { "class": "np-num", text: cyen(t.outsource.yen) + (t.outsource.unknown ? "＋未確定" + t.outsource.unknown + "行" : "") })]),
      el("tr", null, [el("td", { text: "直接費（材料＋外注）" }), el("td", { "class": "np-num", text: cyen(t.direct.yen) + unknown })]),
      el("tr", null, [el("td", { text: "工数費（別列・粗利から引かない）" }), el("td", { "class": "np-num", text: cyen(t.labor.yen) })]),
      el("tr", null, [el("td", { text: "販売価格（税抜）" }), el("td", { "class": "np-num",
        text: cyen(t.price_ex_tax) + (t.price_in_tax !== null ? "（税込 " + cyen(t.price_in_tax) + "・税率 " + t.tax_rate + "%）" : "") })]),
      el("tr", null, [el("td", { text: "想定粗利" }), el("td", { "class": "np-num",
        text: cyen(t.gross.yen) + (t.gross.rate !== null ? "（" + t.gross.rate + "%）" : "") + (t.gross.overstated ? "　※未確定の行があるため、実際より大きく出ています" : "") })])
    ]));
    if (v.lines.length) {
      w.appendChild(table(["区分", "何の費用か", "使用量", "単価", "小計", "備考", ""], v.lines.map(function (l) {
        var sub = (l.qty === null || l.unit_price === null) ? null : l.qty * l.unit_price;
        var act = el("td");
        if (v.latest && o.editable) {
          var del = el("button", { type: "button", text: "消す" });
          del.addEventListener("click", function () {
            post(P + "/cost-line-delete", { version_id: v.id, id: l.id }).then(function () { go(); })
              .catch(function (e) { act.appendChild(el("span", { "class": "np-err", text: e.message })); });
          });
          act.appendChild(del);
        }
        return el("tr", null, [el("td", { text: l.part }), el("td", { text: l.name }),
          el("td", { "class": "np-num", text: cnum(l.qty, l.unit ? " " + l.unit : "") }),
          el("td", { "class": "np-num", text: cyen(l.unit_price) }), el("td", { "class": "np-num", text: cyen(sub) }),
          el("td", { text: dash(l.note) }), act]);
      })));
    }
    if (!(v.latest && o.editable)) return w;

    // 価格・確度
    var vf = el("form", { "class": "np-inline" });
    var pr = el("input", { name: "price_ex_tax", size: "8", "aria-label": "販売価格（税抜）" }); pr.value = v.price_ex_tax === null ? "" : v.price_ex_tax;
    var cf = el("select", { name: "confidence", "aria-label": "確度" });
    cf.appendChild(el("option", { value: "", text: "確度—" }));
    o.confidence.forEach(function (c) { cf.appendChild(el("option", { value: c, text: "確度 " + c })); });
    cf.value = v.confidence || "";
    var nt = el("input", { name: "note", size: "20", "aria-label": "版の備考" }); nt.value = v.note || "";
    [txt("販売価格（税抜） "), pr, txt(" "), cf, txt(" 備考 "), nt, txt(" "), el("button", { type: "submit", text: "保存" })].forEach(function (x) { vf.appendChild(x); });
    vf.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post(P + "/cost-version-update", { version_id: v.id, price_ex_tax: pr.value, confidence: cf.value, note: nt.value })
        .then(function () { go(); }).catch(function (e) { vf.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
    });
    w.appendChild(vf);

    // 行を足す
    var lf = el("form", { "class": "np-inline" });
    var part = el("select", { name: "part", "aria-label": "区分" });
    o.parts.forEach(function (p2) { part.appendChild(el("option", { value: p2, text: p2 })); });
    var cand = el("select", { name: "candidate_id", "aria-label": "候補から単価を写す" });
    cand.appendChild(el("option", { value: "", text: "候補から写さない" }));
    o.candidates.forEach(function (c) {
      cand.appendChild(el("option", { value: String(c.id), text: c.supplier + "（" + cyen(c.unit_price) + (c.adopted ? "・採用" : "") + "）" }));
    });
    var nm = el("input", { name: "name", size: "14", "aria-label": "何の費用か" });
    var qy = el("input", { name: "qty", size: "4", "aria-label": "使用量" });
    var un = el("input", { name: "unit", size: "3", "aria-label": "単位" });
    var up = el("input", { name: "unit_price", size: "6", "aria-label": "単価（円・税抜）" });
    var no = el("input", { name: "note", size: "12", "aria-label": "備考" });
    [txt("行を足す："), part, txt(" 何の費用か "), nm, txt(" 使用量 "), qy, un, txt(" 単価 "), up, txt(" "), cand,
     txt(" 備考 "), no, txt(" "), el("button", { type: "submit", text: "足す" })].forEach(function (x) { lf.appendChild(x); });
    lf.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post(P + "/cost-line", { version_id: v.id, part: part.value, name: nm.value, qty: qy.value, unit: un.value,
        unit_price: up.value, candidate_id: cand.value, note: no.value })
        .then(function () { go(); }).catch(function (e) { lf.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
    });
    w.appendChild(lf);
    w.appendChild(el("p", { "class": "np-sub", text: "単価が分からない行は空欄で足してください（未確定として残ります）。候補を選ぶと、その時点の単価を写します（あとで候補を直しても、この版は変わりません）。" }));
    return w;
  }

  /** seisan への商品登録（第3段・FR-103／FR-60・ADR-043）。
   *  **マスタは持たない。正は seisan。**ここは「登録前の下書き」と「共通商品コード」だけ。
   *  経路は2つ: この画面から登録する／seisan の画面で登録してコードを記録する。
   *  **商品名の欄はこの画面だけの例外**（N-6-2）。seisan が必須とするため。
   *  開発部が付ける名前で、お客さまの入力値ではない。登録できたら消える。 */
  function seisanPanel(pid, box) {
    function fail(e) {
      box.appendChild(el("p", { "class": "np-err", text: e.message }));
    }
    api("/api/projects/" + encodeURIComponent(pid) + "/seisan").then(function (o) {
      while (box.childNodes.length > 1) box.removeChild(box.lastChild);
      box.appendChild(el("p", { "class": "np-sub",
        text: "商品マスタは CIP が持ちます。ここで入れるのは登録前の下書きで、"
          + "登録できたら消え、共通商品コードだけが残ります。" }));
      if (!o.configured) box.appendChild(el("p", { "class": "np-warn",
        text: "この画面からの登録はまだできません。" + o.why }));
      o.targets.forEach(function (t) { box.appendChild(seisanTarget(pid, o, t)); });
    }).catch(fail);
  }

  function seisanTarget(pid, o, t) {
    var w = el("div", { "class": "np-field" });
    var vid = t.variant_id === null ? "" : String(t.variant_id);
    w.appendChild(el("h3", { text: t.label + " — " + t.state }));
    function err(node, e) { node.appendChild(el("p", { "class": "np-err", text: e.message })); }

    if (t.state === "登録済") {
      w.appendChild(el("p", { text: "共通商品コード " + t.product_code + "（"
        + (t.via === "newproduct" ? "この画面から登録" : "CIP の画面で登録") + "・"
        + (t.registered_at || "") + " " + (t.registered_by || "") + "）" }));
      w.appendChild(el("p", { "class": t.verified ? "np-note" : "np-warn",
        text: t.verified ? "CIP に在ることを確かめました。"
          : "CIP に在るかは未確認です（CIP の登録口ができたら、開いたときに確かめます）。" }));
      w.appendChild(el("p", { "class": "np-sub",
        text: "内容を直すときは CIP の画面で直してください（正は CIP）。" }));
      if (o.can_register && o.editable) {
        var rb = el("button", { type: "button", text: "この記録を取り消す（CIP の商品は消えません）" });
        rb.addEventListener("click", function () {
          if (!window.confirm("このアプリの記録だけを下書きに戻します。CIP の商品は残ります。よろしいですか")) return;
          post("/api/projects/" + pid + "/seisan-reset", { variant_id: vid })
            .then(function () { go(); }).catch(function (e) { err(w, e); });
        });
        w.appendChild(rb);
      }
      return w;
    }

    if (t.last_error) w.appendChild(el("p", { "class": "np-err",
      text: "前回 CIP が断った理由: " + t.last_error }));

    // 下書き。**seisan の画面と同じ並び・同じ選択肢**
    var f = el("form");
    o.fields.forEach(function (fd) {
      var id = "sz-" + pid + "-" + (vid || "0") + "-" + fd.key;
      var lab = el("label", { "for": id, text: fd.label + (fd.required ? "（必須）" : "") });
      var cur = (t.draft && t.draft[fd.key]) || "";
      var input;
      var choices = fd.key === "sales_type" ? o.vocab.sales_types
        : (/^cat[123]$/.test(fd.key) ? o.vocab[fd.key] : null);
      if (choices) {
        input = el("select", { id: id, name: fd.key });
        input.appendChild(el("option", { value: "", text: "（選ぶ）" }));
        choices.forEach(function (c) { input.appendChild(el("option", { value: c, text: c })); });
        if (cur && choices.indexOf(cur) < 0)
          input.appendChild(el("option", { value: cur, text: cur + "（CIP に無い値）" }));
        input.value = cur;
      } else {
        input = el("input", { id: id, name: fd.key, maxlength: "200" });
        input.value = cur;
      }
      if (!o.editable) input.setAttribute("disabled", "disabled");
      var row = el("p", null, [lab, input]);
      if (fd.hint) row.appendChild(el("span", { "class": "np-sub", text: " " + fd.hint }));
      f.appendChild(row);
      if (fd.key === "copy_recipe_from" && o.configured && o.editable) f.appendChild(similarPicker(pid, f, input));
    });
    if (o.vocab.cat1 === null) f.appendChild(el("p", { "class": "np-sub",
      text: "分類は、CIP の登録口ができるまで既存の値と照らせません。CIP の画面の表記どおりに入れてください。" }));

    var list = t.errors.map(function (m) { return el("li", { text: "止まる: " + m }); })
      .concat(t.warnings.map(function (m) { return el("li", { text: "注意: " + m }); }));
    if (list.length) f.appendChild(el("ul", { "class": "np-miss" }, list));

    var bar = el("p", { "class": "np-sub" });
    if (o.editable) bar.appendChild(el("button", { type: "submit", text: "下書きを保存" }));
    var reg = el("button", { type: "button", text: "CIP に登録する" });
    var why = !o.configured ? "（CIP の登録口がまだありません）"
      : !o.can_register ? "（G5 を判定できる業務ロールの人だけが登録できます）"
      : t.state === "未着手" ? "（先に下書きを保存してください）"
      : t.errors.length ? "（止まる項目を直してください）" : "";
    if (why || !o.editable) reg.setAttribute("disabled", "disabled");
    bar.appendChild(txt(" "));
    bar.appendChild(reg);
    if (why) bar.appendChild(txt(" " + why));
    f.appendChild(bar);

    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var obj = { variant_id: vid };
      o.fields.forEach(function (fd) { obj[fd.key] = f.elements[fd.key].value; });
      post("/api/projects/" + pid + "/seisan-draft", obj)
        .then(function () { go(); }).catch(function (e) { err(bar, e); });
    });
    reg.addEventListener("click", function () {
      var code = (t.draft && t.draft.code) || "";
      if (!window.confirm("保存済みの下書きで、CIP に共通商品コード「" + code
        + "」を登録します。CIP の商品マスタに入り、取り消しは CIP の画面で行います。よろしいですか")) return;
      post("/api/projects/" + pid + "/seisan-register", { variant_id: vid })
        .then(function (r) {
          if (r.copy_error) window.alert("登録しました。ただしレシピの複製は失敗しました: " + r.copy_error);
          go();
        }).catch(function (e) { err(bar, e); });
    });
    w.appendChild(f);

    // もう1つの経路: seisan の画面で登録した
    if (o.can_register && o.editable) {
      var g = el("form", { "class": "np-inline" });
      var cid = "szc-" + pid + "-" + (vid || "0");
      g.appendChild(el("label", { "for": cid, text: "CIP の画面で登録した場合 — 共通商品コード " }));
      g.appendChild(el("input", { id: cid, name: "code", maxlength: "64" }));
      g.appendChild(el("button", { type: "submit", text: "記録する" }));
      g.addEventListener("submit", function (ev) {
        ev.preventDefault();
        post("/api/projects/" + pid + "/seisan-code", { variant_id: vid, code: g.elements.code.value })
          .then(function () { go(); }).catch(function (e) { err(g, e); });
      });
      w.appendChild(g);
    }
    return w;
  }

  /** レシピ複製元を、seisan の既存商品から分類で探して選ぶ。**商品名は出ない**（seisan が返さない）。 */
  function similarPicker(pid, f, input) {
    var w = el("div");
    var btn = el("button", { type: "button", text: "似ている商品を探す（上の分類で絞ります）" });
    w.appendChild(btn);
    btn.addEventListener("click", function () {
      var q = ["cat1", "cat2", "cat3"].filter(function (k) { return f.elements[k].value; })
        .map(function (k) { return k + "=" + encodeURIComponent(f.elements[k].value); });
      while (w.childNodes.length > 1) w.removeChild(w.lastChild);
      if (!q.length) { w.appendChild(el("p", { "class": "np-err", text: "先に大分類を選んでください" })); return; }
      api("/api/projects/" + encodeURIComponent(pid) + "/seisan-similar?" + q.join("&")).then(function (r) {
        if (!r.rows.length) { w.appendChild(el("p", { "class": "np-note", text: "この分類の商品は CIP にありません。" })); return; }
        w.appendChild(el("p", { "class": "np-sub", text: r.rows.length + " 件（レシピのある商品が先。最大50件）。行の「選ぶ」で複製元に入ります。" }));
        w.appendChild(table(["コード", "販売タイプ", "分類", "形状", "サイズ", "色", "レシピ", ""],
          r.rows.map(function (x) {
            var pick = el("button", { type: "button", text: "選ぶ" });
            pick.addEventListener("click", function () { input.value = x.code; input.focus(); });
            return el("tr", null, [el("td", { text: x.code }), el("td", { text: dash(x.sales_type) }),
              el("td", { text: [x.cat1, x.cat2, x.cat3].filter(Boolean).join(" / ") }),
              el("td", { text: dash(x.shape) }), el("td", { text: dash(x.size) }), el("td", { text: dash(x.color) }),
              el("td", { text: x.has_recipe ? "あり" : "なし" }), el("td", null, [pick])]);
          })));
      }).catch(function (e) { w.appendChild(el("p", { "class": "np-err", text: e.message })); });
    });
    return w;
  }

  /** 第3段/第4段でしか自動化できない項目を、人が確認したと記録する。
   *  **「無い」を「有る」に化けさせないため、誰がいつ確認したかを残す。** */
  function checkForm(pid, m) {
    var f = el("form", { "class": "np-inline" });
    f.appendChild(el("input", { name: "note", placeholder: "根拠（どこで確認したか）",
      "aria-label": "根拠" }));
    f.appendChild(el("button", { type: "submit", text: "確認した" }));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/projects/" + pid + "/check",
        { item_key: m.key, done: "1", note: f.elements.note.value })
        .then(function () { go(); })
        .catch(function (e) { f.appendChild(el("span", { "class": "np-err", text: e.message })); });
    });
    return f;
  }

  /** ゲートの判定。**理由は選択式**（F-6-5・B-14）。 */
  function gateForm(d, ng, meta) {
    var can = d.can_approve && d.can_approve[ng.gate];
    var w = el("div");
    var rl = (meta.roles || []).reduce(function (a, r) { a[r.code] = r.label; return a; }, {});
    w.appendChild(el("p", { "class": "np-sub",
      text: ng.gate + " " + ng.name + " の承認者（業務ロール）: "
        + ng.approver_role.map(function (c) { return rl[c] || c; }).join("／")
        + "　あなたの業務ロール: " + (d.my_roles.length
          ? d.my_roles.map(function (c) { return rl[c] || c; }).join("／") : "なし") }));
    if (!can) {
      w.appendChild(el("p", { "class": "np-warn",
        text: "あなたはこのゲートを判定できません。承認資格はアプリ権限（admin/user）ではなく業務ロールで決まります。" }));
      return w;
    }
    var f = el("form", { "class": "np-inline" });
    var res = el("select", { name: "result", "aria-label": "判定" });
    ["通過", "差戻し", "保留", "中止"].forEach(function (x) {
      res.appendChild(el("option", { value: x, text: x }));
    });
    f.appendChild(res);
    var reason = el("select", { name: "reason_code", "aria-label": "理由（選択式）" });
    function fillReasons() {
      reason.innerHTML = "";
      var list = res.value === "保留" ? meta.reasons.hold
        : res.value === "中止" ? meta.reasons.abort : [];
      reason.disabled = !list.length;
      if (!list.length) { reason.appendChild(el("option", { value: "", text: "理由は不要" })); return; }
      reason.appendChild(el("option", { value: "", text: "理由を選ぶ（必須）" }));
      list.forEach(function (x) { reason.appendChild(el("option", { value: x.code, text: x.label })); });
    }
    res.addEventListener("change", fillReasons); fillReasons();
    f.appendChild(reason);
    f.appendChild(el("input", { name: "comment", placeholder: "コメント", "aria-label": "コメント" }));
    f.appendChild(el("button", { type: "submit", text: "記録する" }));
    var msg = el("span", { "class": "np-sub" });
    f.appendChild(msg);
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/gates/" + d.id + "/" + ng.gate, {
        result: res.value, reason_code: reason.value || "",
        comment: f.elements.comment.value
      }).then(function () { go(); })
        .catch(function (e) { msg.textContent = " " + e.message; });
    });
    if (d.missing.length) w.appendChild(el("p", { "class": "np-sub",
      text: "欠けているものが " + d.missing.length + " 件あります。通過させると、その内容が判定の記録に残ります。" }));
    w.appendChild(f);
    return w;
  }

  // ══════════════════════════════════════════════════════
  // タスク（画面設計 3-10・重点2）
  // **1画面＋期間フィルタ。既定は「期限切れ＋今日」。**
  // ══════════════════════════════════════════════════════
  var WHEN_LABEL = {
    "overdue+today": "期限切れ＋今日（既定）", "overdue": "期限切れ", "today": "今日",
    "+1": "+1", "+2": "+2", "+3": "+3", "+4": "+4", "+5": "+5", "+6": "+6",
    "week": "今週", "next7": "7日先まで（未完）", "none": "期限なし", "all": "すべて"
  };
  var TAB_LABEL = { project: "案件タスク", work: "案件外の仕事", request: "他部署への依頼" };

  function viewTasks() {
    loading();
    var q = hashQuery();
    var when = q.get("when") || "overdue+today";
    var tab = q.get("tab") || "project";
    var mine = q.get("mine") === "1";
    var mq = mine ? "&mine=1" : "";
    api("/api/tasks?when=" + encodeURIComponent(when) + "&tab=" + encodeURIComponent(tab) + mq)
      .then(function (d) {
        var b = clear();
        setTitle("タスク", "／ " + (WHEN_LABEL[when] || when));

        // タブ
        var tabs = el("div", { "class": "np-filters", role: "tablist" });
        Object.keys(TAB_LABEL).forEach(function (k) {
          tabs.appendChild(el("a", {
            href: "#/tasks?when=" + encodeURIComponent(when) + "&tab=" + k + mq,
            text: TAB_LABEL[k], "aria-current": k === tab ? "true" : null
          }));
        });
        b.appendChild(tabs);

        // 期間フィルタ。**「期限なし」を常設ボタンに**
        var fl = el("div", { "class": "np-filters" });
        ["overdue+today", "overdue", "today", "+1", "+2", "+3", "+4", "+5", "+6",
         "week", "next7", "none", "all"].forEach(function (k) {
          var n = mine ? undefined : d.counts[k];        // 件数は全員分なので、自分の分だけのときは出さない
          fl.appendChild(el("a", {
            href: "#/tasks?when=" + encodeURIComponent(k) + "&tab=" + tab + mq,
            text: WHEN_LABEL[k] + (n === undefined ? "" : " " + n),
            "aria-current": k === when ? "true" : null
          }));
        });
        b.appendChild(fl);
        // 自分の分だけ（担当者が自分＋自分の業務ロール）。ダッシュボードの「自分のやること」の続き
        var mf = el("div", { "class": "np-filters" });
        mf.appendChild(el("a", { href: "#/tasks?when=" + encodeURIComponent(when) + "&tab=" + tab, text: "全員の分", "aria-current": mine ? null : "true" }));
        mf.appendChild(el("a", { href: "#/tasks?when=" + encodeURIComponent(when) + "&tab=" + tab + "&mine=1", text: "自分の分だけ", "aria-current": mine ? "true" : null }));
        b.appendChild(mf);
        b.appendChild(el("p", { "class": "np-note",
          text: "きょうは " + d.today + " です。期限切れ " + d.overdue_total
            + " 件／期限なし " + d.no_due_total + " 件。" }));

        if (!d.rows.length) {
          b.appendChild(el("p", { "class": "np-note",
            text: "この期間のタスクはありません。（期限切れ " + d.overdue_total
              + " 件・期限なし " + d.no_due_total + " 件は別にあります）" }));
        } else {
          var rows = [], sepDone = false;
          d.rows.forEach(function (t) {
            if (!t.due_on && !sepDone) {
              sepDone = true;
              rows.push(el("tr", { "class": "np-sep" }, [
                el("td", { colspan: "9", text: "ここから下は期限なし（" + d.no_due_total + "件）" })]));
            }
            rows.push(el("tr", null, [
              el("td", null, [t.dept !== undefined && tab === "request" ? requestCell(t) : statusForm(t)]),
              el("td", { text: dash(t.due_on) }),
              el("td", null, [t.project_id
                ? el("a", { href: "#/projects/" + t.project_id, text: t.project })
                : txt("—")]),
              el("td", { text: t.title }),
              el("td", { text: t.role_label + (t.role_external ? "（他部署）" : "") }),
              el("td", { text: dash(t.assignee) }),
              el("td", { "class": "np-num", text: t.hours === null ? "—" : String(t.hours) }),
              el("td", { text: t.ai_used ? "適用済" : "未" }),
              el("td", { "class": "np-num",
                text: t.ai_reduction_rate === null || t.ai_reduction_rate === undefined
                  ? "—" : Math.round(t.ai_reduction_rate * 100) + "%" })
            ]));
          });
          b.appendChild(table(["進捗", "期限", "案件（分類）", "タスク名", "ロール",
            "担当", "標準h", "AI適用", "削減見込"], rows));
        }
        if (tab === "work" || tab === "request") b.appendChild(workItemForm(tab));
        d.notes.forEach(function (n) { b.appendChild(el("p", { "class": "np-note", text: n })); });

        // **帯に出していない画面への入口**（帯は7つまで）。
        // 自動化依頼は「この作業をやらなくて済ませたい」なので、タスクの下に置く
        b.appendChild(el("p", { "class": "np-note", text: "手でやっている作業を自動化したいときは、自動化依頼へ。作業名だけで出せます（そのあと8つの質問で要件にします）。" }));
        b.appendChild(btnRow([navBtn("#/automation", "自動化依頼を開く")]));

        // ロール別の負荷（F-5-5）
        b.appendChild(el("h2", { text: "ロール別の負荷（月 × ロール）" }));
        b.appendChild(el("p", { "class": "np-note", text: d.load.caption }));
        if (!d.load.months.length) {
          b.appendChild(el("p", { "class": "np-note",
            text: "積める行がありません。" + d.load.no_month_caption }));
        } else {
          var lr = [];
          d.load.months.forEach(function (m) {
            m.own.forEach(function (x) {
              lr.push(el("tr", null, [el("td", { text: m.month }),
                el("td", { text: x.role_label }),
                // **実作業と予備を1つの数にしない**（2026-09-23 の決定）
                el("td", { "class": "np-num", text: x.work_hours + "h" }),
                el("td", { "class": "np-num", text: x.reserve_hours + "h" }),
                el("td", { "class": "np-num", text: x.hours + "h" }),
                el("td", { "class": "np-num", text: String(x.n) }),
                el("td", { text: d.load.limit_label })]));
            });
            m.external.forEach(function (x) {
              lr.push(el("tr", null, [el("td", { text: m.month }),
                el("td", { text: x.role_label + "（他部署・試算対象外）" }),
                el("td", { "class": "np-num", text: x.work_hours + "h" }),
                el("td", { "class": "np-num", text: x.reserve_hours + "h" }),
                el("td", { "class": "np-num", text: x.hours + "h" }),
                el("td", { "class": "np-num", text: String(x.n) }),
                el("td", { text: "—" })]));
            });
          });
          b.appendChild(table(["月（タスク実施月）", "ロール", "実作業h", "予備h",
                               "合計h", "件数", "上限"], lr));
          b.appendChild(el("p", { "class": "np-note", text: d.load.reserve_caption }));
          b.appendChild(el("p", { "class": "np-note", text: d.load.external_caption }));
          b.appendChild(el("p", { "class": "np-note", text: d.load.no_month_caption }));
        }

        // テンプレートの標準工数。**見出しに「6フロー合算」を必ず付ける**（§5-6）
        var tt = d.template_totals;
        b.appendChild(el("h2", { text: "テンプレートの標準工数 — " + tt.caption }));
        b.appendChild(el("p", { "class": "np-warn",
          text: "この表は " + tt.caption + " です。"
            + "つまり 1本あたりではない 数字です。" + tt.per_project_caption }));
        b.appendChild(table(["ロール", "タスク数", "実作業h（6フロー合算）",
          "予備h（6フロー合算）", "AI削減可能h（6フロー合算）"],
          tt.by_role.map(function (r) {
            return el("tr", null, [
              el("td", { text: (r.role_label || "—") + (r.external ? "（他部署・試算対象外）" : "") }),
              el("td", { "class": "np-num", text: String(r.n) }),
              el("td", { "class": "np-num", text: r.work_hours + "h" }),
              el("td", { "class": "np-num", text: r.reserve_hours + "h" }),
              el("td", { "class": "np-num", text: r.ai_hours + "h" })]);
          })));
        b.appendChild(el("p", { "class": "np-note", text: tt.reserve_caption }));
        b.appendChild(table(["開発タイプ", "タスク数", "実作業h（1本あたり）",
                             "予備h（1本あたり）"],
          tt.by_flow.map(function (r) {
            return el("tr", null, [el("td", { text: r.label }),
              el("td", { "class": "np-num", text: String(r.n) }),
              el("td", { "class": "np-num", text: r.work_hours + "h" }),
              el("td", { "class": "np-num", text: r.reserve_hours + "h" })]);
          })));
        b.appendChild(el("p", { "class": "np-note",
          text: "⑦ページリニューアルはこの表に出ません。標準タスクが1行も定義されていないためです。" }));
      }).catch(fail);
  }

  /** 他部署への依頼。**受け側の完了をもって完了**（FR-48）。送った側の進捗だけでは閉じない */
  function requestCell(t) {
    var w = el("span");
    if (t.accepted_at) { w.appendChild(txt("完了（受け側 " + t.accepted_at + "）")); return w; }
    w.appendChild(statusForm(t));
    var bt = el("button", { type: "button", text: "受け側が完了" });
    bt.addEventListener("click", function () {
      if (!window.confirm("依頼先（" + (t.dept || "—") + "）が完了したことを記録して閉じます。よろしいですか")) return;
      post("/api/work-items/" + t.id + "/accept", {}).then(function () { go(); }).catch(function (e) { alert(e.message); });
    });
    w.appendChild(txt(" ")); w.appendChild(bt);
    w.appendChild(el("span", { "class": "np-sub", text: " 依頼先: " + (t.dept || "—") }));
    return w;
  }

  /** 案件外の仕事・他部署への依頼を起票する（FR-47・FR-48）。同じ工数勘定に載る */
  function workItemForm(tab) {
    var f = el("form", { "class": "np-field" });
    var kind = tab === "request" ? "他部署依頼" : "案件外";
    f.appendChild(el("p", { "class": "np-sub", text: (kind === "他部署依頼" ? "他部署への依頼を起票（受け側の完了をもって完了）" : "案件に紐づかない仕事を起票（FBA納品・BtoB整備・旧商品修正・仕組み化など）") }));
    var title = el("input", { name: "title", size: "28", "aria-label": "何をするか" });
    var cat = el("input", { name: "category", size: "10", "aria-label": "区分" });
    var dept = el("input", { name: "dept", size: "10", "aria-label": "依頼先の部署" });
    var due = el("input", { name: "due_on", type: "date", "aria-label": "期限" });
    var hours = el("input", { name: "hours", size: "4", "aria-label": "時間" });
    var who = el("input", { name: "assignee", size: "8", "aria-label": "担当" });
    var p1 = el("p", null, [txt("何をするか "), title, txt(" 区分 "), cat]);
    if (kind === "他部署依頼") { p1.appendChild(txt(" 依頼先（必須） ")); p1.appendChild(dept); }
    var bar = el("p", null, [txt("期限 "), due, txt(" 時間 "), hours, txt(" 担当 "), who, txt(" "), el("button", { type: "submit", text: "起票" })]);
    f.appendChild(p1); f.appendChild(bar);
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/work-items", { kind: kind, title: title.value, category: cat.value, dept: dept.value,
        due_on: due.value, hours: hours.value, assignee: who.value })
        .then(function () { go(); }).catch(function (e) { bar.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
    });
    return f;
  }

  function statusForm(t) {
    var s = el("select", { "aria-label": "進捗" });
    ["未着手", "着手", "完了", "保留", "対象外"].forEach(function (x) {
      var o = el("option", { value: x, text: x });
      if (x === t.status) o.setAttribute("selected", "selected");
      s.appendChild(o);
    });
    s.addEventListener("change", function () {
      post("/api/tasks/" + t.kind + "/" + t.id + "/status", { status: s.value })
        .then(function () { go(); }).catch(function (e) { alert(e.message); });
    });
    return s;
  }

  // ══════════════════════════════════════════════════════
  // ゲート盤（画面設計 3-9）
  // **いま誰の番かを1枚に。**自分が判断者のものが最初に来る。
  // ══════════════════════════════════════════════════════
  function viewGates() {
    loading();
    api("/api/gates").then(function (d) {
      var b = clear();
      setTitle("ゲート盤");
      b.appendChild(el("p", { "class": "np-note",
        text: "あなたの業務ロール: " + (d.my_role_labels.length ? d.my_role_labels.join("／")
          : "なし（承認資格は業務ロールで決まります。アプリ権限 admin では通せません）") }));

      // 記号のルール。**色だけにしない**
      b.appendChild(el("p", { "class": "np-note",
        text: "記号: ● 通過 ／ ◐ 待ち ／ ◼ 差戻・保留・中止 ／ ○ 未 ／ / 対象外。"
          + "記号は列の表現にすぎません。状態は語で出しています。" }));

      if (!d.rows.length) {
        b.appendChild(el("p", { "class": "np-note",
          text: "案件がありません。" + (d.passed_30d !== null
            ? "直近30日で通過したゲートは " + d.passed_30d + " 件です。" : "") }));
      } else {
        var head = ["案件（社内呼称）", "開発タイプ", "次の判定", "誰の番か"]
          .concat(d.gates.map(function (g) { return g.gate + " " + g.name; }));
        b.appendChild(table(head, d.rows.map(function (r) {
          var tds = [
            el("td", null, [el("a", { href: "#/projects/" + r.id, text: r.product })]),
            el("td", { text: r.flow_label }),
            el("td", { text: r.next_gate }),
            el("td", { text: r.who })
          ];
          r.cells.forEach(function (c) {
            tds.push(el("td", null, [
              el("span", { "aria-hidden": "true", text: c.glyph }), " ",
              c.state === "判定待ち" && c.missing_n
                ? el("a", { href: "#/projects/" + r.id, text: c.word + "（欠 " + c.missing_n + "）" })
                : txt(c.word)
            ]));
          });
          return el("tr", null, tds);
        })));
      }
      b.appendChild(el("p", { "class": "np-note",
        text: "対象外 は放置ではなく設計です。④ニューモデル追加は G2 → G5 の簡易フローで、"
          + "G1・G3・G4 を通しません。" }));
      if (d.passed_30d !== null) b.appendChild(el("p", { "class": "np-note",
        text: "直近30日で通過したゲート: " + d.passed_30d + " 件。" }));
    }).catch(fail);
  }

  // ══════════════════════════════════════════════════════
  // アイデア台帳（第1段・F-1）
  //
  // **採点の版を混ぜない。**v1（シートのままの点）と v2（新しい軸）は
  // 同じ画面に並べるが、足し算はしない（F-1-10・第8章 ⑦）。
  // **ランクは絶対点ではなく百分位**（F-1-9）。
  // **色に意味を持たせない。**ランクも状態も語として列に出す（N-11）。
  // ══════════════════════════════════════════════════════
  var IDEA_FILTER_KEYS = ["stage", "rank", "rubric", "origin", "theme", "q"];

  function yen(v) { return (v === null || v === undefined || v === "") ? "未入力" : "¥" + Number(v).toLocaleString("ja-JP"); }
  function pctText(p) {
    if (p === null || p === undefined) return "—";
    return "上位 " + (Math.round(p * 1000) / 10) + "%";
  }

  function viewIdeas() {
    loading();
    var q = hashQuery();
    var qs = [];
    IDEA_FILTER_KEYS.forEach(function (k) {
      if (q.get(k)) qs.push(k + "=" + encodeURIComponent(q.get(k)));
    });
    Promise.all([api("/api/ideas" + (qs.length ? "?" + qs.join("&") : "")),
                 api("/api/meta")]).then(function (r) {
      var d = r[0], meta = r[1];
      var b = clear();
      setTitle("アイデア", d.rubric ? "（" + d.rubric + "）" : "");
      // 起票の入口を2つに（2026-10-06 点検「十文字さんが名指しした起票の流れが分かりにくい」）。
      // 自分で書く → 下の起票フォームへ移る／トレンドから選ぶ → トレンド画面で「アイデアにする」
      var toForm = el("button", { type: "button", "class": "np-btn", text: "アイデアを起票する ↓" });
      toForm.addEventListener("click", function () {
        var f = document.getElementById("np-newidea");
        if (!f) return;
        f.open = true;
        f.scrollIntoView({ behavior: "smooth", block: "start" });
        var t = f.querySelector("input[name=title]");
        if (t) t.focus({ preventScroll: true });
      });
      b.appendChild(btnRow([toForm, navBtn("#/trends", "トレンド（FCTR）から選んで起票する")]));

      // ── 絞り込み（ステージ・ランク・rubric版・起票経路・テーマ）──
      var f = el("form", { "class": "np-filters", id: "np-ifilter" });
      function sel(name, label, opts, cur) {
        var s = el("select", { name: name, "aria-label": label });
        s.appendChild(el("option", { value: "", text: label + "（すべて）" }));
        opts.forEach(function (o) {
          var v = typeof o === "string" ? o : (o.code || o.version);
          var t = typeof o === "string" ? o : o.label;
          var op = el("option", { value: v, text: t });
          if (cur === v) op.setAttribute("selected", "selected");
          s.appendChild(op);
        });
        return s;
      }
      f.appendChild(sel("stage", "ステージ", d.filters.stage, q.get("stage")));
      f.appendChild(sel("rubric", "採点の版", d.filters.rubric, q.get("rubric")));
      f.appendChild(sel("rank", "ランク", d.filters.rank, q.get("rank")));
      f.appendChild(sel("origin", "起票経路", d.filters.origin, q.get("origin")));
      f.appendChild(sel("theme", "テーマ", d.filters.theme, q.get("theme")));
      var qi = el("input", { name: "q", placeholder: "商品案名で探す", "aria-label": "商品案名で探す" });
      if (q.get("q")) qi.setAttribute("value", q.get("q"));
      f.appendChild(qi);
      f.appendChild(el("button", { type: "submit", text: "絞り込む" }));
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var p = [];
        Array.prototype.forEach.call(f.elements, function (x) {
          if (x.name && x.value) p.push(x.name + "=" + encodeURIComponent(x.value));
        });
        location.hash = "#/ideas" + (p.length ? "?" + p.join("&") : "");
      });
      b.appendChild(f);

      b.appendChild(el("p", { "class": "np-note",
        text: "該当 " + d.total + " 件。うち " + d.shown + " 件を表示しています（上限 " + d.limit + " 件）。" }));

      if (!d.rows.length) {
        b.appendChild(el("p", { "class": "np-note",
          text: "アイデアがありません。上の「アイデアを起票する」から足せます。" }));
      } else if (d.rubric) {
        // 版を選んだとき。**その版の点とランクだけ**を出す
        b.appendChild(table(
          ["商品案（社内の企画名）", "ステージ", "テーマ", "起票経路", "需要発生",
           "生産方法", "想定粗利額", "共通点", "テーマ適合", "減点係数", "総合点",
           "百分位", "ランク"],
          d.rows.map(function (x) {
            var s = x.score || {};
            return el("tr", null, [
              el("td", null, [el("a", { href: "#/ideas/" + x.id, text: x.title })]),
              el("td", { text: x.stage }),
              el("td", { text: x.theme_label }),
              el("td", { text: x.origin_label }),
              el("td", { text: dash(x.demand_cycle) }),
              el("td", { "class": "np-num", text: x.production_feasibility === null ? "未入力" : String(x.production_feasibility) }),
              el("td", { "class": "np-num", text: yen(x.expected_margin_yen) }),
              el("td", { "class": "np-num", text: s.common_score === null || s.common_score === undefined ? "—" : String(s.common_score) }),
              el("td", { "class": "np-num", text: s.theme_fit === null || s.theme_fit === undefined ? "—" : String(s.theme_fit) }),
              el("td", { "class": "np-num", text: s.feasibility_factor === null || s.feasibility_factor === undefined ? "—" : String(s.feasibility_factor) }),
              el("td", { "class": "np-num", text: s.total === null || s.total === undefined ? "—" : String(s.total) }),
              el("td", { "class": "np-num", text: pctText(s.percentile) }),
              el("td", { text: dash(s.rank) })
            ]);
          })));
      } else {
        b.appendChild(table(
          ["商品案（社内の企画名）", "ステージ", "テーマ", "起票経路", "需要発生",
           "生産方法", "想定粗利額", "採点の版"],
          d.rows.map(function (x) {
            return el("tr", null, [
              el("td", null, [el("a", { href: "#/ideas/" + x.id, text: x.title })]),
              el("td", { text: x.stage }),
              el("td", { text: x.theme_label }),
              el("td", { text: x.origin_label }),
              el("td", { text: dash(x.demand_cycle) }),
              el("td", { "class": "np-num", text: x.production_feasibility === null ? "未入力" : String(x.production_feasibility) }),
              el("td", { "class": "np-num", text: yen(x.expected_margin_yen) }),
              el("td", { text: x.score_versions.length ? x.score_versions.join("／") : "未採点" })
            ]);
          })));
      }
      d.notes.forEach(function (n) { b.appendChild(el("p", { "class": "np-note", text: n })); });
      b.appendChild(newIdeaForm(meta));
    }).catch(fail);
  }

  /** 起票フォーム。**4項目＋起票経路だけ**（F-1-3）。ここを重くしない。 */
  function newIdeaForm(meta) {
    var box = el("details", { "class": "np-card", open: "open", id: "np-newidea" });
    box.appendChild(el("summary", { text: "アイデアを起票する（4項目＋起票経路だけ）" }));
    box.appendChild(el("p", { "class": "np-note",
      text: "起票に要るのは 商品案名・概要・想定ターゲット・起票経路 の4つだけです。デザイン自由度・生産方法・参考URL・エリアは採点のときに足します。" }));
    var f = el("form", { "class": "np-form" });
    var title = el("input", { name: "title", placeholder: "商品案名（社内の企画名）", "aria-label": "商品案名" });
    var summary = el("textarea", { name: "summary", rows: "3", placeholder: "概要・仕様", "aria-label": "概要・仕様" });
    var target = el("textarea", { name: "target_scene", rows: "2", placeholder: "想定ターゲットと使用シーン", "aria-label": "想定ターゲットと使用シーン" });
    var origin = el("select", { name: "origin", "aria-label": "起票経路" });
    origin.appendChild(el("option", { value: "", text: "起票経路を選ぶ（必須）" }));
    meta.idea.origins.forEach(function (o) {
      origin.appendChild(el("option", { value: o.code, text: o.label }));
    });
    var theme = el("select", { name: "theme_id", "aria-label": "テーマ" });
    theme.appendChild(el("option", { value: "", text: "テーマ（あとで選べます）" }));
    meta.idea.themes.forEach(function (t) {
      theme.appendChild(el("option", { value: t.id, text: t.label }));
    });
    f.appendChild(title); f.appendChild(summary); f.appendChild(target);
    f.appendChild(origin); f.appendChild(theme);
    f.appendChild(el("button", { type: "submit", text: "起票する" }));

    // F-1-12。**起票時に似た案を出す。**完全な名寄せはしない
    var simBox = el("div", { "class": "np-sim" });
    var msg = el("p", { "class": "np-note" });
    var timer = null;
    function lookSimilar() {
      var t = title.value.trim();
      simBox.innerHTML = "";
      if (t.length < 2) return;
      post("/api/ideas/similar", { title: t }).then(function (r) {
        simBox.innerHTML = "";
        if (!r.rows.length) {
          simBox.appendChild(el("p", { "class": "np-note", text: "似た案は見つかりませんでした。" }));
          return;
        }
        simBox.appendChild(el("p", { "class": "np-warn",
          text: "似た案が " + r.rows.length + " 件あります。重複かどうかは人が見てください（自動では名寄せしません）。" }));
        var ul = el("ul", { "class": "np-miss" });
        r.rows.forEach(function (x) {
          ul.appendChild(el("li", null, [
            el("a", { href: "#/ideas/" + x.id, text: x.title }),
            " — 近さ " + x.score + "／" + x.stage
          ]));
        });
        simBox.appendChild(ul);
      }).catch(function () { /* 似た案が出せなくても起票は止めない */ });
    }
    title.addEventListener("input", function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(lookSimilar, 350);
    });

    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var o = {};
      Array.prototype.forEach.call(f.elements, function (x) { if (x.name) o[x.name] = x.value; });
      post("/api/ideas", o).then(function (r) {
        location.hash = "#/ideas/" + r.id;
      }).catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    box.appendChild(f); box.appendChild(simBox); box.appendChild(msg);
    return box;
  }

  // ── アイデア1件（採点画面を含む）────────────────────────
  function viewIdea(id) {
    loading();
    Promise.all([api("/api/ideas/" + encodeURIComponent(id)), api("/api/meta"),
                 api("/api/rubrics")]).then(function (r) {
      var d = r[0], meta = r[1], rubrics = r[2].rows;
      var b = clear();
      setTitle("アイデア", d.title);

      b.appendChild(el("h2", { text: d.title }));
      b.appendChild(table(["項目", "値"], [
        ["ステージ", d.stage],
        ["テーマ", dash(d.theme_label)],
        ["起票経路", d.origin_label],
        ["需要発生（通年／季節／単発）", d.demand_cycle === null ? "未入力" : d.demand_cycle],
        ["デザイン自由度(1-5)", d.design_freedom === null ? "未入力" : String(d.design_freedom)],
        ["生産方法(1-5)", d.production_feasibility === null ? "未入力" : String(d.production_feasibility)],
        ["想定粗利額（1個あたり）", yen(d.expected_margin_yen)],
        ["エリア候補", dash(d.area1) + "／" + dash(d.area2)],
        ["参考商品1", dash(d.ref_url1)],
        ["参考商品2", dash(d.ref_url2)],
        ["移行元", d.source_sheet ? (d.source_sheet + " の " + d.source_row + " 行目") : "このアプリで起票"]
      ].map(function (x) {
        return el("tr", null, [el("th", { scope: "row", text: x[0] }), el("td", { text: x[1] })]);
      })));
      if (d.summary) {
        b.appendChild(el("h3", { text: "概要・仕様" }));
        b.appendChild(el("p", { "class": "np-body", text: d.summary }));
      }
      if (d.target_scene) {
        b.appendChild(el("h3", { text: "想定ターゲットと使用シーン" }));
        b.appendChild(el("p", { "class": "np-body", text: d.target_scene }));
      }
      if (!d.origin) {
        b.appendChild(el("p", { "class": "np-warn",
          text: "起票経路が不明です（移行分）。元のシートに起票経路の列がありません。推測では埋めていません。分かる人が下のフォームで入れてください。" }));
      }

      // ── 似た案（F-1-12）──
      b.appendChild(el("h2", { text: "似た案" }));
      if (!d.similar.length) {
        b.appendChild(el("p", { "class": "np-note", text: "似た案は見つかりませんでした（文字の近さで機械的に出しています。名寄せ済みという意味ではありません）。" }));
      } else {
        var ul = el("ul", { "class": "np-miss" });
        d.similar.forEach(function (x) {
          ul.appendChild(el("li", null, [
            el("a", { href: "#/ideas/" + x.id, text: x.title }),
            " — 近さ " + x.score + "／" + x.stage
          ]));
        });
        b.appendChild(ul);
      }

      // ══ 採点。**v1 と v2 を並べる。合算しない** ══
      b.appendChild(el("h2", { text: "採点" }));
      b.appendChild(el("p", { "class": "np-warn",
        text: "版の違う点数を合算していません。v1 は移行したシートの点そのもの（再採点していません）、v2 は新しい軸です。並べて見比べるためのものです。" }));

      var gen1 = d.scores.filter(function (s) { return s.generation === 1; });
      var gen2 = d.scores.filter(function (s) { return s.generation === 2; });

      var grid = el("div", { "class": "np-grid np-grid-2" });
      grid.appendChild(scorePanel("v1（移行したそのまま・再採点しない）", gen1, rubrics, d, false));
      grid.appendChild(scorePanel("v2（2層採点・百分位）", gen2, rubrics, d, true));
      b.appendChild(grid);

      // v2 の採点フォーム
      b.appendChild(scoreV2Form(d, meta));

      // AI採点（F-1-11）。**既定 off**
      b.appendChild(aiPanel(d, meta));

      // 項目の追記
      b.appendChild(ideaFieldsForm(d, meta));

      if (d.projects.length) {
        b.appendChild(el("h2", { text: "この案から起こした案件" }));
        b.appendChild(table(["案件", "ステージ", "発売予定日"], d.projects.map(function (p) {
          return el("tr", null, [
            el("td", null, [el("a", { href: "#/projects/" + p.id, text: p.id })]),
            el("td", { text: p.stage }), el("td", { text: dash(p.launch_date) })]);
        })));
      }
    }).catch(fail);
  }

  function scorePanel(title, scores, rubrics, d, isV2) {
    var c = el("div", { "class": "np-card" });
    c.appendChild(el("h3", { text: title }));
    if (!scores.length) {
      c.appendChild(el("p", { "class": "np-note",
        text: isV2 ? "v2 ではまだ採点していません。" : "この案に v1 の点はありません（このアプリで起票した案です）。" }));
      return c;
    }
    scores.forEach(function (s) {
      var rb = null;
      rubrics.forEach(function (x) { if (x.version === s.rubric_version) rb = x; });
      c.appendChild(el("h4", { text: s.rubric_label + "（" + s.rubric_version + "）" }));
      var rows = [];
      if (rb) {
        rb.axes.forEach(function (ax) {
          if (ax.layer === "attribute") return;
          var raw = s.axes[ax.code];
          rows.push(el("tr", null, [
            el("th", { scope: "row", text: ax.label }),
            el("td", { "class": "np-num", text: raw === undefined || raw === null ? "—" : String(raw) }),
            el("td", { "class": "np-num", text: ax.weight === null ? "未実測" : "×" + ax.weight })
          ]));
        });
      }
      c.appendChild(table(["評価項目", "素点", "ウェイト"], rows));
      var sum = [
        ["総合点", s.total === null ? "—" : String(s.total) + (s.total_max ? "／" + s.total_max : "")],
        ["ランク", dash(s.rank)],
        ["ランクの決め方", s.rank_basis === "percentile" ? "テーマ内の百分位" : "シートに書かれていた絶対点の閾値"],
        ["採点者", s.scored_by === "import" ? "移行（シートの値）" : s.scored_by],
        ["モデル名", dash(s.model)],
        ["採点日時", dash(s.scored_at)]
      ];
      if (isV2) {
        sum.splice(0, 0,
          ["①共通点（0〜70）", s.common_score === null ? "—" : String(s.common_score)],
          ["②テーマ適合点（0〜30）", s.theme_fit === null ? "—" : String(s.theme_fit)],
          ["減点係数（生産方法）", s.feasibility_factor === null ? "—" : String(s.feasibility_factor)],
          ["係数を掛ける前", s.raw_total === null ? "—" : String(s.raw_total)]);
        sum.push(["テーマ内の百分位", pctText(s.percentile) + "（母数 " + d.v2_population + " 件）"]);
        sum.push(["共通点だけの横並び（テーマをまたぐ）", pctText(s.common_percentile)]);
      }
      c.appendChild(table(["", ""], sum.map(function (x) {
        return el("tr", null, [el("th", { scope: "row", text: x[0] }), el("td", { text: x[1] })]);
      })));
      if (s.source_note) c.appendChild(el("p", { "class": "np-note", text: s.source_note }));
    });
    return c;
  }

  function scoreV2Form(d, meta) {
    var box = el("details", { "class": "np-card" });
    box.appendChild(el("summary", { text: "v2 で採点する" }));
    box.appendChild(el("p", { "class": "np-note",
      text: "総合点 =（①共通点 0〜70 ＋ ②テーマ適合点 0〜30）× 生産方法の減点係数。ランクはテーマ内の百分位で決まります。" }));
    if (!d.v2_ready) {
      box.appendChild(el("p", { "class": "np-warn", text: "まだ採点できません。足りないものがあります:" }));
      var ul = el("ul", { "class": "np-miss" });
      d.v2_blockers.forEach(function (x) { ul.appendChild(el("li", { text: x })); });
      box.appendChild(ul);
      box.appendChild(el("p", { "class": "np-note",
        text: "足りない値を 1.0 や 0 で代用しません。作れない案が上位に来るのを防ぐのが v2 の目的です。" }));
      return box;
    }
    var f = el("form", { "class": "np-form" });
    [["demand", "購買意欲・ニーズ（1〜10）"], ["market_size", "ターゲット規模（1〜10）"],
     ["advantage", "競合優位性（1〜10）"], ["theme_fit", "テーマ適合（1〜10）"]].forEach(function (x) {
      f.appendChild(el("label", { "class": "np-label" }, [
        el("span", { text: x[1] }),
        el("input", { type: "number", name: x[0], min: "1", max: "10", step: "1", required: "required" })
      ]));
    });
    f.appendChild(el("p", { "class": "np-note",
      text: "想定粗利額は " + yen(d.expected_margin_yen) + " → " + d.margin_points + " 点として入ります。生産方法 " + d.production_feasibility + " → 減点係数 " + d.feasibility_factor + " を総合点に掛けます。" }));
    f.appendChild(el("p", { "class": "np-note", text: meta.idea.margin_bands_note }));
    f.appendChild(el("button", { type: "submit", text: "v2 で採点する" }));
    var msg = el("p", { "class": "np-note" });
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var o = {};
      Array.prototype.forEach.call(f.elements, function (x) { if (x.name) o[x.name] = x.value; });
      post("/api/ideas/" + encodeURIComponent(d.id) + "/score", o).then(function () {
        go();
      }).catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    box.appendChild(f); box.appendChild(msg);
    return box;
  }

  function aiPanel(d, meta) {
    var a = meta.ai_scoring;
    var c = el("div", { "class": "np-card" });
    c.appendChild(el("h3", { text: "AI採点" }));
    c.appendChild(el("p", { "class": a.enabled ? "np-note" : "np-warn",
      text: a.enabled ? "有効です。" : a.reason }));
    c.appendChild(el("p", { "class": "np-note",
      text: "採点結果には rubric版・実行日時・モデル名を残します（" + a.records.kept.join("／") + "）。" }));
    c.appendChild(el("p", { "class": "np-note",
      text: "AI が付けた点は現在 " + a.ai_scored + " 件です。" }));
    // **予算の状態をそのまま出す。**使えない理由が「設定」なのか「予算」なのかを分ける
    if (a.budget) {
      var b = a.budget;
      var bc = el("div");
      bc.appendChild(el("h3", { text: "AIの予算（Auto GROWTH が正本）" }));
      if (b.state === "ok") {
        bc.appendChild(el("p", { "class": "np-sub",
          // **「image の使用額」と書かない。**枠の合算なので（Auto GROWTH の申し送り）
          text: b.spent_label + ": " + b.spent + " / 上限 " + b.cap
                + "（残り " + b.remaining + "）" }));
        bc.appendChild(el("p", { "class": "np-sub", text: b.note }));
      } else {
        // **未計測と 0 を混ぜない。**状態を語で出す（N-11）
        bc.appendChild(el("span", { "class": "np-big np-big-unmeasured",
          text: { unavailable: "確かめられません", over_cap: "使い切りました",
                  no_cap: "枠がありません", bad_job: "設定の誤り",
                  refused: "断られました" }[b.state] || b.state }));
        bc.appendChild(el("p", { "class": "np-sub", text: b.why || "" }));
      }
      c.appendChild(bc);
    }
    if (a.scorer_note) c.appendChild(el("p", { "class": "np-warn", text: a.scorer_note }));
    var btn = el("button", { type: "button", text: "AI採点を実行する" });
    if (!a.enabled) btn.setAttribute("disabled", "disabled");
    var msg = el("p", { "class": "np-note" });
    btn.addEventListener("click", function () {
      post("/api/ideas/" + encodeURIComponent(d.id) + "/ai-score", {}).then(function (r) {
        msg.textContent = r.enabled ? ("採点 " + r.scored + " 件／見送り " + r.skipped + " 件") : r.reason;
        if (r.enabled) go();
      }).catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    c.appendChild(btn); c.appendChild(msg);
    return c;
  }

  function ideaFieldsForm(d, meta) {
    var box = el("details", { "class": "np-card" });
    box.appendChild(el("summary", { text: "項目を足す・直す" }));
    var f = el("form", { "class": "np-form" });
    function sel(name, label, opts, cur, blank) {
      var s = el("select", { name: name, "aria-label": label });
      s.appendChild(el("option", { value: "", text: blank }));
      opts.forEach(function (o) {
        var v = typeof o === "string" ? o : (o.code || o.id);
        var t = typeof o === "string" ? o : o.label;
        var op = el("option", { value: v, text: t });
        if (cur === v) op.setAttribute("selected", "selected");
        s.appendChild(op);
      });
      return s;
    }
    function num(name, label, cur, min, max) {
      var i = el("input", { type: "number", name: name, min: String(min), max: String(max), "aria-label": label, placeholder: label });
      if (cur !== null && cur !== undefined) i.setAttribute("value", String(cur));
      return el("label", { "class": "np-label" }, [el("span", { text: label }), i]);
    }
    f.appendChild(el("label", { "class": "np-label" }, [el("span", { text: "テーマ" }),
      sel("theme_id", "テーマ", meta.idea.themes, d.theme_id, "未選択")]));
    f.appendChild(el("label", { "class": "np-label" }, [el("span", { text: "起票経路" }),
      sel("origin", "起票経路", meta.idea.origins, d.origin, "不明のまま")]));
    f.appendChild(el("label", { "class": "np-label" }, [el("span", { text: "ステージ" }),
      sel("stage", "ステージ", meta.idea.stages, d.stage, "変えない")]));
    f.appendChild(el("label", { "class": "np-label" }, [el("span", { text: "需要発生（通年／季節／単発）" }),
      sel("demand_cycle", "需要発生", meta.idea.demand_cycles, d.demand_cycle, "未入力のまま")]));
    f.appendChild(num("design_freedom", "デザイン自由度(1-5)", d.design_freedom, 1, 5));
    f.appendChild(num("production_feasibility", "生産方法(1-5)", d.production_feasibility, 1, 5));
    f.appendChild(num("expected_margin_yen", "想定粗利額（円・1個あたり）", d.expected_margin_yen, 0, 10000000));
    f.appendChild(el("button", { type: "submit", text: "保存する" }));
    var msg = el("p", { "class": "np-note" });
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var o = {};
      Array.prototype.forEach.call(f.elements, function (x) {
        if (!x.name) return;
        if (x.name === "stage" && !x.value) return;      // 「変えない」
        o[x.name] = x.value;
      });
      post("/api/ideas/" + encodeURIComponent(d.id) + "/fields", o).then(function () {
        go();
      }).catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    box.appendChild(f); box.appendChild(msg);
    return box;
  }


  // ChatWork へ渡す（F-15-6）。
  // **自動では送らない。**送る文面をそのまま見せて、押されたときだけ送る。
  // ChatWork は取り消せない。lpscope も同じ部屋に対して同じ規約にしている。
  function autoChatwork(id) {
    var box = el("div", { "class": "np-card" });
    var msg = el("p", { "class": "np-note" });
    box.appendChild(el("h3", { text: "ChatWork へ渡す" }));
    api("/api/automation/" + encodeURIComponent(id) + "/chatwork").then(function (s) {
      box.appendChild(el("p", { "class": "np-note", text: s.note }));
      if (s.sent_count) {
        box.appendChild(el("p", { "class": "np-warn",
          text: "この依頼は " + s.sent_at + " に既に送っています（"
            + s.sent_count + " 回・部屋 " + dash(s.sent_room) + "）。" }));
      }
      if (!s.ready) {
        // **送れない理由を言葉で出す。**ボタンだけ出して押させない
        box.appendChild(el("p", { "class": "np-warn", text: "いまは送れません。" }));
        (s.blockers || []).forEach(function (x) {
          if (x) box.appendChild(el("p", { "class": "np-sub", text: "・" + x }));
        });
      }
      box.appendChild(el("p", { "class": "np-sub", text: "送る文面（このまま出ます）" }));
      box.appendChild(el("div", { "class": "np-raw", text: s.preview }));
      if (s.ready) {
        var btn = el("button", { type: "button",
          text: s.sent_count ? "同じ内容をもう一度送る" : "この内容で送る" });
        btn.addEventListener("click", function () {
          // **取り消せないので、押す前にもう一度聞く**
          if (!window.confirm("ChatWork の部屋 " + s.config.room_id
              + " へ送ります。送ったあと取り消せません。よろしいですか？")) return;
          btn.disabled = true;
          post("/api/automation/" + encodeURIComponent(id) + "/chatwork",
               s.sent_count ? { resend: "1" } : {})
            .then(function (r) {
              msg.textContent = "送りました（部屋 " + r.room + " ／ "
                + r.sent_count + " 回目）。";
              setTimeout(go, 2500);
            })
            .catch(function (e) {
              btn.disabled = false;
              msg.textContent = "送れませんでした: " + e.message;
            });
        });
        box.appendChild(el("p", { "class": "np-actions" }, [btn]));
      }
      box.appendChild(msg);
    }).catch(function (e) {
      box.appendChild(el("p", { "class": "np-sub", text: "状態を取れません: " + e.message }));
    });
    return box;
  }

  // ══════════════════════════════════════════════════════
  // 年間プラン（F-3 ／ FR-82〜FR-86）
  //
  // **警告は出すが、保存は止めない**（F-3-3）。止めると表計算に戻る。
  // **判定は色ではなく語で出す**（N-11）。「適合 / 警告 / 未計測」。
  // **未計測を「適合」に混ぜない。**数えられないものを OK と書くと、
  // 検査したことになってしまう。
  // ══════════════════════════════════════════════════════
  var RULE_LABEL = { ratio: "比率 3:1", effort: "月間工数ポイント",
                     count: "月あたりの本数", holiday: "長期連休の月" };
  var LEVEL_WORD = { ok: "適合", warn: "警告", unavailable: "未計測" };

  function viewPlan() {
    loading();
    var q = hashQuery();
    var fy = q.get("fy") || "";
    Promise.all([api("/api/plan" + (fy ? "?fy=" + encodeURIComponent(fy) : "")),
                 api("/api/meta")]).then(function (r) {
      var d = r[0], meta = r[1];
      var b = clear();
      b.appendChild(planVersionBar(d));
      if (!d.current) {
        b.appendChild(el("p", { "class": "np-note", text: d.empty_note ||
          "この年度の版がまだありません。" }));
        return;
      }
      setTitle("プラン", d.current.version.label);
      b.appendChild(btnRow([navBtn("#/opportunities", "機会カレンダーを開く"), navBtn("#/simulate", "販売計画シミュレーション")]));
      b.appendChild(el("p", { "class": "np-sub", text: "機会カレンダーでは、イベントの2か月前を発売の目安にして枠を足せます。" }));
      b.appendChild(planRules(d.current));
      b.appendChild(planMonths(d.current, meta));
      if (d.current.editable) b.appendChild(planAddSlot(d.current, meta));
    }).catch(fail);
  }

  function planVersionBar(d) {
    var box = el("div", { "class": "np-card" });
    var cur = d.current && d.current.version;
    box.appendChild(el("h2", { text: "年間プランの版" }));
    if (d.versions.length) {
      var rows = d.versions.map(function (v) {
        return el("tr", null, [
          el("td", null, [el("a", { href: "#/plan?v=" + v.id, text: v.label })]),
          el("td", { text: String(v.fiscal_year) }),
          // **状態は語で出す。**色だけにしない（N-11）
          el("td", { text: v.state }),
          el("td", { text: String(v.slot_n) + " 枠" }),
          el("td", { text: dash(v.approved_at) })
        ]);
      });
      box.appendChild(table(["版", "年度", "状態", "枠", "承認"], rows));
      box.appendChild(el("p", { "class": "np-note",
        text: "承認済みは年度に1つだけです。承認済みの版は編集できません。"
              + "期中に直すときは「改訂版を作る」で写してから直します。" }));
    }
    var msg = el("p", { "class": "np-note" });
    var f = el("form", { "class": "np-form" });
    f.appendChild(el("label", { text: "年度" }));
    f.appendChild(el("input", { name: "fiscal_year", inputmode: "numeric",
                                placeholder: "2026", required: "required" }));
    f.appendChild(el("label", { text: "呼び名（任意）" }));
    f.appendChild(el("input", { name: "label", placeholder: "2026年度 年間プラン" }));
    f.appendChild(el("button", { type: "submit", text: "版を作る" }));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/plan/versions", { fiscal_year: f.elements.fiscal_year.value,
                                   label: f.elements.label.value })
        .then(function () { go(); })
        .catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    box.appendChild(f);
    if (cur) {
      var acts = el("p", { "class": "np-actions" });
      if (cur.state === "策定中" && !d.can_approve) {
        acts.appendChild(el("span", { "class": "np-sub", text: "承認は社長の業務ロールの人が行います。" }));
      } else if (cur.state === "策定中") {
        var ap = el("button", { type: "button", text: "この版を承認する" });
        ap.addEventListener("click", function () {
          post("/api/plan/versions/" + encodeURIComponent(cur.id) + "/approve", {})
            .then(function () { go(); })
            .catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
        });
        acts.appendChild(ap);
      } else if (cur.state !== "策定中") {
        var rv = el("button", { type: "button", text: "改訂版を作る（枠ごと写す）" });
        rv.addEventListener("click", function () {
          post("/api/plan/versions/" + encodeURIComponent(cur.id) + "/revise", {})
            .then(function () { go(); })
            .catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
        });
        acts.appendChild(rv);
      }
      // 策定中の版を消す（ADR-064）。承認済み・失効は記録なので消さない。案件になった枠がある版はサーバーが断る
      if (cur.state === "策定中" && d.can_delete) {
        var n = (d.versions.filter(function (v) { return v.id === cur.id; })[0] || {}).slot_n || 0;
        var del = el("button", { type: "button", text: "この版を消す" });
        del.addEventListener("click", function () {
          if (!window.confirm("「" + cur.label + "」を消します（枠 " + n + " 本も一緒に消えます）。元に戻せません。よろしいですか")) return;
          post("/api/plan/versions/" + encodeURIComponent(cur.id) + "/delete", {})
            .then(function () { location.hash = "#/plan"; go(); })
            .catch(function (e) { msg.textContent = "消せませんでした: " + e.message; });
        });
        acts.appendChild(txt(" "));
        acts.appendChild(del);
      }
      box.appendChild(acts);
    }
    box.appendChild(msg);
    return box;
  }

  function planRules(cur) {
    var box = el("div", { "class": "np-card" });
    var c = cur.rules.counts;
    box.appendChild(el("h2", { text: "挿入ルールの検査" }));
    box.appendChild(el("p", { "class": "np-sub",
      text: "適合 " + (c.ok || 0) + " ／ 警告 " + (c.warn || 0)
            + "（うち理由未記入 " + cur.rules.warn_unacked + "） ／ 未計測 "
            + (c.unavailable || 0) }));
    // 判定の基準（ADR-069）。承認済みの版は承認した時点の値で判定する。いまの設定と違えば並べて出す
    var bs = cur.rules.basis;
    if (bs && bs.mode === "承認時の値") {
      box.appendChild(el("p", { "class": bs.diff.length ? "np-warn" : "np-note",
        text: "この版は承認した時点（" + bs.approved_at + "）の値で判定しています。"
          + (bs.diff.length ? "いまの設定と違うもの: " + bs.diff.map(function (x) {
              return x.label + " 承認時 " + dash(x.approved) + " → いま " + dash(x.now); }).join("、")
              + "。いまの値で直したいときは「改訂版を作る」で写してから見直します。" : "いまの設定とも同じです。") }));
    } else if (bs) {
      box.appendChild(el("p", { "class": "np-note", text: "策定中なので、いまの設定（設定ページ）の値で判定しています。承認すると、その時点の値がこの版に残ります。" }));
    }
    box.appendChild(el("p", { "class": "np-note", text: cur.rules.note }));
    box.appendChild(el("p", { "class": "np-note", text: cur.rules.effort_unit_note }));
    var rows = cur.rules.results.map(function (r) {
      var last = el("td");
      if (r.acked) {
        last.appendChild(el("span", { text: "承知: " + r.acked.reason }));
        last.appendChild(el("span", { "class": "np-sub",
          text: "（" + dash(r.acked.by) + " " + dash(r.acked.at) + "）" }));
      } else if (r.level === "warn" && cur.editable) {
        last.appendChild(ackForm(cur.version.id, r));
      } else {
        last.appendChild(txt("—"));
      }
      return el("tr", { "data-level": r.level }, [
        el("td", { text: RULE_LABEL[r.rule] || r.rule }),
        el("td", { text: r.scope === "FY" ? "年度" : r.scope }),
        // **語で出す**（N-11）
        el("td", { text: LEVEL_WORD[r.level] || r.level }),
        el("td", { text: r.message }),
        last
      ]);
    });
    box.appendChild(table(["ルール", "対象", "判定", "内容", "例外の理由"], rows));
    return box;
  }

  function ackForm(vid, r) {
    var f = el("form", { "class": "np-form np-form-inline" });
    var msg = el("span", { "class": "np-sub" });
    f.appendChild(el("input", { name: "reason", required: "required",
                                placeholder: "なぜこの月はこうするのか" }));
    f.appendChild(el("button", { type: "submit", text: "理由をつけて承知" }));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/plan/versions/" + encodeURIComponent(vid) + "/ack",
           { rule: r.rule, scope: r.scope, reason: f.elements.reason.value })
        .then(function () { go(); })
        .catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    f.appendChild(msg);
    return f;
  }

  function planMonths(cur, meta) {
    var box = el("div");
    box.appendChild(el("h2", { text: "月ごとの枠（" + cur.slot_n + " 枠）" }));
    if (!cur.months.length) {
      box.appendChild(el("p", { "class": "np-note",
        text: "枠がまだ1つもありません。下の「枠を足す」から始めます。" }));
      return box;
    }
    cur.months.forEach(function (m) {
      var s = el("section", { "class": "np-card" });
      s.appendChild(el("h3", { text: m.month }));
      s.appendChild(el("p", { "class": "np-sub",
        text: "発売に数える " + m.launch_n + " 本 ／ 工数ポイント " + m.effort
              + " 点" + (m.effort_unknown
                ? "（未確定 " + m.effort_unknown + " 本を含みません）" : "")
              + " ／ 案件化済 " + m.converted_n + " 件" }));
      var rows = m.slots.map(function (x) { return planSlotRow(cur, x); });
      s.appendChild(table(["発売日", "商品タイプ", "開発タイプ", "作成エリア",
                           "工数", "担当", "アイデア", "機会", "案件"], rows));
      box.appendChild(s);
    });
    return box;
  }

  function planSlotRow(cur, x) {
    var last = el("td");
    if (x.project_id) {
      last.appendChild(el("a", { href: "#/projects/" + x.project_id,
                                 text: "案件 " + x.project_id }));
      last.appendChild(el("span", { "class": "np-sub",
        text: "（" + dash(x.project_stage) + "）" }));
    } else if (cur.editable) {
      last.appendChild(convertButton(x));
    } else {
      last.appendChild(txt("—"));
    }
    return el("tr", null, [
      // 日が未定なら「月まで」と書く。**仮の日付を置かない**
      el("td", { text: x.launch_date || (x.launch_month + "（日は未定）") }),
      el("td", { text: dash(x.kind_label) }),
      el("td", { text: dash(x.flow_label) }),
      el("td", { text: dash(x.area) }),
      // **未確定と 0 を区別する**（N-10）
      el("td", { text: x.effort_point === null || x.effort_point === undefined
                       ? "未確定" : String(x.effort_point) }),
      el("td", { text: dash(x.owner) }),
      el("td", null, [x.idea_id
        ? el("a", { href: "#/ideas/" + x.idea_id, text: dash(x.idea_title) })
        : txt("—")]),
      el("td", { text: dash(x.occasion) }),
      last
    ]);
  }

  function convertButton(x) {
    var w = el("span");
    var msg = el("span", { "class": "np-sub" });
    var btn = el("button", { type: "button", text: "案件にする" });
    btn.addEventListener("click", function () {
      btn.disabled = true;
      post("/api/plan/slots/" + encodeURIComponent(x.id) + "/convert", {})
        .then(function (r) {
          // **期限をその場で出す**（FR-86）。一覧を描き直すと見落とす
          msg.textContent = r.message;
          setTimeout(go, 4000);
        })
        .catch(function (e) {
          btn.disabled = false;
          msg.textContent = "できませんでした: " + e.message;
        });
    });
    w.appendChild(btn); w.appendChild(msg);
    return w;
  }

  function planAddSlot(cur, meta) {
    var box = el("div", { "class": "np-card" });
    var msg = el("p", { "class": "np-note" });
    box.appendChild(el("h2", { text: "枠を足す" }));
    box.appendChild(el("p", { "class": "np-note",
      text: "発売月だけで作れます。日は決まってから入れます。"
            + "仮の日付を置くと、そこから逆算した期限が動き出します。" }));
    var f = el("form", { "class": "np-form" });
    f.appendChild(el("label", { text: "発売月（YYYY-MM）" }));
    f.appendChild(el("input", { name: "launch_month", required: "required",
                                placeholder: "2026-05" }));
    f.appendChild(el("label", { text: "発売日（決まっていれば）" }));
    f.appendChild(el("input", { name: "launch_date", type: "date" }));
    f.appendChild(el("label", { text: "商品タイプ" }));
    var k = el("select", { name: "product_kind" });
    k.appendChild(el("option", { value: "", text: "（未設定）" }));
    (meta.plan.kinds || []).forEach(function (x) {
      k.appendChild(el("option", { value: x.code, text: x.label }));
    });
    f.appendChild(k);
    f.appendChild(el("label", { text: "開発タイプ（工数ポイントの出どころ）" }));
    var ft = el("select", { name: "flow_type" });
    ft.appendChild(el("option", { value: "", text: "（未設定）" }));
    (meta.flow_types || []).forEach(function (x) {
      ft.appendChild(el("option", { value: x.code,
        text: x.label + (x.effort_point === null ? "（係数 未実測）"
                                                 : "（" + x.effort_point + " 点）") }));
    });
    f.appendChild(ft);
    f.appendChild(el("label", { text: "作成エリア" }));
    f.appendChild(el("input", { name: "area" }));
    f.appendChild(el("label", { text: "担当" }));
    f.appendChild(el("input", { name: "owner" }));
    f.appendChild(el("label", { text: "機会（なぜその月か）" }));
    f.appendChild(el("input", { name: "occasion", placeholder: "母の日 / 卒団 など" }));
    f.appendChild(el("button", { type: "submit", text: "枠を足す" }));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var o = { version_id: cur.version.id };
      Array.prototype.forEach.call(f.elements, function (x) {
        if (x.name && x.value) o[x.name] = x.value;
      });
      post("/api/plan/slots", o).then(function () { go(); })
        .catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    box.appendChild(f); box.appendChild(msg);
    return box;
  }

  // ══════════════════════════════════════════════════════
  // 自動化依頼（F-15 ／ FR-159〜）
  //
  // **このアプリは実装しない。**答えが揃ったら要件として書き出し、
  // 作る人へ渡すところまで。画面にもそう書く。
  // **未回答と「無いという答え」を区別する**（N-10）。語で出す。
  // ══════════════════════════════════════════════════════
  function viewAutomation() {
    loading();
    var q = hashQuery();
    var p = [];
    if (q.get("stage")) p.push("stage=" + encodeURIComponent(q.get("stage")));
    if (q.get("only")) p.push("only=" + encodeURIComponent(q.get("only")));
    api("/api/automation" + (p.length ? "?" + p.join("&") : "")).then(function (d) {
      var b = clear();
      b.appendChild(el("p", { "class": "np-note",
        text: "手でやっている作業のうち、自動化したいものを集める画面です。"
          + "出したあと、8つの質問に答えると要件になります。"
          + "このアプリが実装するわけではありません。要件を書き出して、作る人へ渡します。" }));

      // 後追い（FR-169）。実装済から30日たっても、実装後の手間が記録されていないもの
      if (d.followups && d.followups.length) {
        var fu = el("div", { "class": "np-card" });
        fu.appendChild(el("h2", { text: "後追い待ち（実装済から30日・" + d.followups.length + "件）" }));
        fu.appendChild(el("p", { "class": "np-sub", text: "自動化したのに作業が残っていたら、実装が足りていません。依頼を開いて、実装後の手間を記録してください。" }));
        fu.appendChild(table(["依頼", "実装済にした日", "実装前の手間"], d.followups.map(function (x) {
          return el("tr", null, [el("td", null, [el("a", { href: "#/automation/" + encodeURIComponent(x.id), text: x.title })]),
            el("td", { text: (x.done_at || "").slice(0, 10) }),
            el("td", { "class": "np-num", text: x.hours_per_month === null ? "未計測" : "月 " + x.hours_per_month + "h" })]);
        })));
        b.appendChild(fu);
      }

      // 数の段。**測れていない件数を必ず出す**
      var g = el("div", { "class": "np-grid np-grid-3" });
      function card(label, n, sub, link) {
        var c = el("div", { "class": "np-card" });
        c.appendChild(el("h3", { text: label }));
        c.appendChild(link ? el("a", { "class": "np-big", href: link, text: String(n) })
                           : el("span", { "class": "np-big", text: String(n) }));
        if (sub) c.appendChild(el("p", { "class": "np-sub", text: sub }));
        return c;
      }
      g.appendChild(card("依頼", d.total, "状態別は下の表", "#/automation"));
      g.appendChild(card("標準タスクに無い", d.not_in_template,
        "「やっていない」ではなく、表が現場に追いついていないという意味です",
        "#/automation?only=not_in_template"));
      g.appendChild(d.hours_per_month === null
        ? card("月あたりの時間", "未計測", d.hours_note)
        : card("月あたりの時間", d.hours_per_month + "h", d.hours_note));
      b.appendChild(g);
      if (d.template_note)
        b.appendChild(el("p", { "class": "np-warn", text: d.template_note }));

      // 状態の絞り込み。**語で出す**（色にしない）
      var bar = el("p", { "class": "np-sub" });
      bar.appendChild(el("a", { href: "#/automation", text: "すべて" }));
      d.stages.forEach(function (s) {
        bar.appendChild(txt("　"));
        bar.appendChild(el("a", { href: "#/automation?stage=" + encodeURIComponent(s),
          text: s + "（" + (d.by_stage[s] || 0) + "）" }));
      });
      b.appendChild(bar);

      if (!d.rows.length) {
        b.appendChild(el("p", { "class": "np-note", text: "該当する依頼はありません。" }));
      } else {
        b.appendChild(table(["作業", "出した人", "状態", "答え", "標準タスク", "月あたり"],
          d.rows.map(function (r) {
            return el("tr", null, [
              el("td", null, [el("a", { href: "#/automation/" + r.id, text: r.title })]),
              el("td", { text: dash(r.requester) }),
              el("td", { text: r.stage }),
              el("td", { text: r.answered + " / " + r.required }),
              el("td", { text: r.in_template ? "あり" : "無し" }),
              el("td", { "class": "np-num",
                text: r.hours_per_month === null ? "未計測" : r.hours_per_month + "h" })]);
          })));
      }
      b.appendChild(autoAddForm());
    }).catch(fail);
  }

  function autoAddForm() {
    var box = el("div", { "class": "np-card" });
    var msg = el("p", { "class": "np-note" });
    box.appendChild(el("h2", { text: "自動化してほしい作業を出す" }));
    box.appendChild(el("p", { "class": "np-note",
      text: "作業名だけで出して構いません。細かいことは、このあと質問でうかがいます。" }));
    var f = el("form", { "class": "np-form" });
    f.appendChild(el("label", { text: "作業名" }));
    f.appendChild(el("input", { name: "title", required: "required",
      placeholder: "例: スタンプ登録" }));
    f.appendChild(el("label", { text: "いまの困りごと・どうなってほしいか（任意）" }));
    f.appendChild(el("textarea", { name: "raw_request", rows: "3",
      placeholder: "元となる画像を作成したら、あとは各サイズ作って登録もしてほしい" }));
    f.appendChild(el("label", { text: "出した人" }));
    f.appendChild(el("input", { name: "requester" }));
    f.appendChild(el("button", { type: "submit", text: "出す" }));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var o = {};
      Array.prototype.forEach.call(f.elements, function (x) {
        if (x.name && x.value) o[x.name] = x.value;
      });
      post("/api/automation", o).then(function (r) {
        location.hash = "#/automation/" + r.id;
      }).catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    box.appendChild(f); box.appendChild(msg);
    return box;
  }

  /** 実装後の手間（FR-169）。前と並べ、手作業が残っていれば「実装が足りていない可能性」と出す */
  function autoAfter(id, d) {
    var a = d.after, c = el("div", { "class": "np-card" });
    c.appendChild(el("h3", { text: "実装後の手間（作業が実際に減ったか）" }));
    c.appendChild(table(["", "月あたり"], [
      el("tr", null, [el("td", { text: "実装前" }), el("td", { "class": "np-num", text: a.before === null ? "未計測" : a.before + " h" })]),
      el("tr", null, [el("td", { text: "実装後" }), el("td", { "class": "np-num", text: a.after === null ? "未計測" : a.after + " h" })]),
      el("tr", null, [el("td", { text: "減った量" }), el("td", { "class": "np-num", text: a.saved === null ? "—" : a.saved + " h" + (a.rate === null ? "" : "（" + a.rate + "%）") })])
    ]));
    if (a.verdict) c.appendChild(el("p", { "class": a.after !== null && a.after > 0 ? "np-warn" : "np-note", text: a.verdict }));
    if (a.checked_at) c.appendChild(el("p", { "class": "np-sub", text: "記録: " + a.checked_at + " " + dash(a.checked_by) + (a.note ? "　" + a.note : "") }));
    var f = el("form", { "class": "np-inline" });
    var mi = el("input", { size: "5", "aria-label": "実装後の1回あたりの分", value: a.after_minutes_each === null ? "" : String(a.after_minutes_each) });
    var ti = el("input", { size: "5", "aria-label": "実装後の月の回数", value: a.after_times_per_month === null ? "" : String(a.after_times_per_month) });
    var ni = el("input", { size: "24", "aria-label": "メモ", placeholder: "残っている手作業など" });
    [txt("1回 "), mi, txt(" 分 × 月 "), ti, txt(" 回 "), ni, el("button", { type: "submit", text: "記録する" })].forEach(function (x) { f.appendChild(x); });
    var st = el("p", { "class": "np-status", role: "status", "aria-live": "polite", text: "手作業が無くなったら 1回 0 分で記録します。" });
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/automation/" + encodeURIComponent(id) + "/after", { minutes_each: mi.value, times_per_month: ti.value, note: ni.value })
        .then(function () { go(); }).catch(function (e) { st.textContent = "記録できませんでした: " + e.message; });
    });
    c.appendChild(f); c.appendChild(st);
    return c;
  }

  function viewAutomationOne(id) {
    loading();
    api("/api/automation/" + encodeURIComponent(id)).then(function (d) {
      var b = clear();
      var r = d.request;
      setTitle("自動化依頼", r.title);
      b.appendChild(el("h2", { text: r.title }));
      b.appendChild(el("p", { "class": "np-sub",
        text: "出した人: " + dash(r.requester) + "（" + dash(r.dept) + "） ／ 状態: "
          + r.stage + " ／ " + d.progress.note }));
      b.appendChild(el("p", { "class": "np-note", text: d.handoff_note }));

      if (r.raw_request) {
        var o = el("div", { "class": "np-card" });
        o.appendChild(el("h3", { text: "元の言葉（書き換えていません）" }));
        o.appendChild(el("div", { "class": "np-raw", text: r.raw_request }));
        b.appendChild(o);
      }
      // 標準タスクに当たるか。**無いことにも意味がある**
      var t = el("p", { "class": d.template ? "np-note" : "np-warn" });
      t.textContent = d.template
        ? "標準タスク「" + d.template.title + "」（" + dash(d.template.flow_label) + "）に当たります。"
        : "標準タスク178行のどれにも当たりません。「やっていない」のではなく、表のほうが現場に追いついていないという意味です。";
      b.appendChild(t);
      // FR-168。標準タスクに無い作業を、ひな形の下書きか案件外の仕事へ。**どちらかは商品開発部の判断**
      if (!d.template && d.promote && d.promote.can) {
        var pc = el("div", { "class": "np-card" });
        pc.appendChild(el("h3", { text: "この作業をどこに入れるか" }));
        pc.appendChild(el("p", { "class": "np-sub", text: "案件ごとに毎回やる作業なら標準タスクのひな形へ（下書きに入り、ひな形の画面で差を見てから使い始めます）。案件にぶら下がらない定常の作業なら案件外の仕事へ。" }));
        var pf = el("form", { "class": "np-inline" });
        var fsel = el("select", { "aria-label": "開発タイプ" });
        d.promote.flows.forEach(function (f) { fsel.appendChild(el("option", { value: f.code, text: f.label })); });
        var rsel = el("select", { "aria-label": "担当の業務ロール" });
        d.promote.roles.forEach(function (x) { rsel.appendChild(el("option", { value: x.code, text: x.label })); });
        var hin = el("input", { size: "5", "aria-label": "標準時間（h）", placeholder: "h（後でも可）" });
        [txt("開発タイプ "), fsel, txt(" 担当 "), rsel, txt(" 標準時間 "), hin, el("button", { type: "submit", text: "ひな形の下書きに入れる" })]
          .forEach(function (x) { pf.appendChild(x); });
        var pmsg = el("p", { "class": "np-status", role: "status", "aria-live": "polite" });
        pf.addEventListener("submit", function (ev) {
          ev.preventDefault();
          post("/api/automation/" + encodeURIComponent(id) + "/promote", { flow: fsel.value, role: rsel.value, hours: hin.value })
            .then(function (res) { pmsg.textContent = "ひな形の下書き（版" + res.version + "）に入れました。"; })
            .catch(function (e) { pmsg.textContent = "入れられませんでした: " + e.message; });
        });
        var wb = el("button", { type: "button", text: "案件外の仕事として記録する" });
        wb.addEventListener("click", function () {
          if (!window.confirm("「" + r.title + "」を案件外の仕事として記録します。よろしいですか")) return;
          post("/api/automation/" + encodeURIComponent(id) + "/promote", { to: "work", role: rsel.value })
            .then(function () { pmsg.textContent = "案件外の仕事として記録しました（タスク → 案件外の仕事）。"; })
            .catch(function (e) { pmsg.textContent = "記録できませんでした: " + e.message; });
        });
        pc.appendChild(pf);
        pc.appendChild(el("p", { "class": "np-btnrow" }, [wb, navBtn("#/settings/templates", "ひな形の画面を開く", "back")]));
        pc.appendChild(pmsg);
        b.appendChild(pc);
      }
      if (r.note) b.appendChild(el("p", { "class": "np-sub", text: r.note }));

      b.appendChild(autoEffort(d));
      b.appendChild(el("h2", { text: "質問（答えが揃うと要件になります）" }));
      d.questions.forEach(function (q) { b.appendChild(autoQ(id, q)); });
      b.appendChild(autoStage(d));
      if (d.request.stage === "実装済") b.appendChild(autoAfter(id, d));
      b.appendChild(autoChatwork(d.request.id));
    }).catch(fail);
  }

  function autoQ(id, q) {
    var box = el("div", { "class": "np-card" });
    var msg = el("p", { "class": "np-sub" });
    box.appendChild(el("h3", { text: q.text + (q.required ? "" : "（任意）") }));
    // **なぜ聞くかを必ず出す。**理由が無いと、答える側が埋めるだけになる
    box.appendChild(el("p", { "class": "np-sub", text: q.why }));
    if (q.example) box.appendChild(el("p", { "class": "np-sub", text: "例: " + q.example }));
    // **未回答と「無いという答え」を語で区別する**（N-10）
    box.appendChild(el("p", { "class": "np-sub", text: "状態: " + q.state
      + (q.answered_by ? "（" + q.answered_by + " " + q.answered_at + "）" : "") }));
    var f = el("form", { "class": "np-form" });
    var ta = el("textarea", { name: "answer", rows: "2" });
    ta.value = q.answer || "";
    f.appendChild(ta);
    f.appendChild(el("button", { type: "submit", text: "答えを保存" }));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/automation/" + encodeURIComponent(id) + "/answer",
           { q_key: q.key, answer: ta.value })
        .then(function () { go(); })
        .catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
    });
    box.appendChild(f); box.appendChild(msg);
    return box;
  }

  function autoEffort(d) {
    var box = el("div", { "class": "np-card" });
    var msg = el("p", { "class": "np-note" });
    var r = d.request, e = d.effort;
    box.appendChild(el("h3", { text: "効果の見積り" }));
    if (e.hours_per_month === null) {
      // **未計測と 0 を区別する**（N-10）
      box.appendChild(el("span", { "class": "np-big np-big-unmeasured", text: "未計測" }));
      box.appendChild(el("p", { "class": "np-sub", text: e.why }));
    } else {
      box.appendChild(el("span", { "class": "np-big", text: e.hours_per_month + "h / 月" }));
      box.appendChild(el("p", { "class": "np-sub", text: "年 " + e.per_year + "h" }));
      box.appendChild(el("p", { "class": "np-sub", text: e.note }));
    }
    var f = el("form", { "class": "np-form" });
    f.appendChild(el("label", { text: "1回あたり（分）" }));
    var a = el("input", { name: "minutes_each", inputmode: "decimal" });
    a.value = r.minutes_each === null ? "" : r.minutes_each;
    f.appendChild(a);
    f.appendChild(el("label", { text: "月あたりの回数" }));
    var c = el("input", { name: "times_per_month", inputmode: "decimal" });
    c.value = r.times_per_month === null ? "" : r.times_per_month;
    f.appendChild(c);
    f.appendChild(el("button", { type: "submit", text: "保存" }));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/automation/" + encodeURIComponent(r.id) + "/effort",
           { minutes_each: a.value, times_per_month: c.value })
        .then(function () { go(); })
        .catch(function (x) { msg.textContent = "できませんでした: " + x.message; });
    });
    box.appendChild(f); box.appendChild(msg);
    return box;
  }

  function autoStage(d) {
    var box = el("div", { "class": "np-card" });
    var msg = el("p", { "class": "np-note" });
    var r = d.request;
    box.appendChild(el("h3", { text: "状態を変える" }));
    box.appendChild(el("p", { "class": "np-note",
      text: "答えが揃う前に「要件確定」から先へは進めません。"
        + "揃わないまま渡すと、作る側が想像で埋めることになります。" }));
    var f = el("form", { "class": "np-form" });
    var sel = el("select", { name: "stage" });
    d.stages.forEach(function (s) {
      var o = el("option", { value: s, text: s });
      if (s === r.stage) o.setAttribute("selected", "selected");
      sel.appendChild(o);
    });
    f.appendChild(sel);
    f.appendChild(el("label", { text: "渡す相手（任意）" }));
    var h = el("input", { name: "handoff_to" });
    h.value = r.handoff_to || "";
    f.appendChild(h);
    f.appendChild(el("button", { type: "submit", text: "変える" }));
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      post("/api/automation/" + encodeURIComponent(r.id) + "/stage",
           { stage: sel.value, handoff_to: h.value })
        .then(function () { go(); })
        .catch(function (x) { msg.textContent = "できませんでした: " + x.message; });
    });
    box.appendChild(f); box.appendChild(msg);
    // **アプリの外（生の Markdown）へ出さない。**画面の中に出し、そのまま写せるようにする（2026-10-06 点検）
    var rq = el("button", { type: "button", "class": "np-btn np-btn-sm", text: "要件として書き出す（作る人へ渡す）" });
    var rqBox = el("div", { "class": "np-field" });
    rq.addEventListener("click", function () {
      fetch("/api/automation/" + encodeURIComponent(r.id) + "/requirement", { credentials: "same-origin" })
        .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.text(); })
        .then(function (md) {
          rqBox.textContent = "";
          var ta = el("textarea", { rows: "14", readonly: "readonly", "aria-label": "要件（作る人へ渡す文）" });
          ta.value = md;
          var cp = el("button", { type: "button", text: "写す" });
          cp.addEventListener("click", function () {
            ta.select();
            (navigator.clipboard ? navigator.clipboard.writeText(md) : Promise.reject()).then(function () { cp.textContent = "写しました"; })
              .catch(function () { cp.textContent = "選んであります。⌘C / Ctrl+C で写してください"; });
          });
          rqBox.appendChild(ta); rqBox.appendChild(el("p", null, [cp]));
        }).catch(function (e) { rqBox.appendChild(el("p", { "class": "np-err", text: e.message })); });
    });
    box.appendChild(el("p", { "class": "np-btnrow" }, [rq]));
    box.appendChild(rqBox);
    return box;
  }

  // ══════════════════════════════════════════════════════
  // 売上実績（2026-10-01 十文字さん決定）
  // **何が売れているか（構成）を見る画面。**正式な売上は経営管理。
  // 金額は税込（決定）。前年比は丸1か月どうしのときだけ。未計測は「未計測」と書く（0 にしない）
  // ══════════════════════════════════════════════════════
  function yen(v) { return (v === null || v === undefined) ? "未計測" : Math.round(v).toLocaleString("ja-JP") + "円"; }
  function pct(v) { return (v === null || v === undefined) ? "—" : v.toFixed(1) + "%"; }
  function yoy(v) { return (v === null || v === undefined) ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "±") + Math.abs(v).toFixed(1) + "%"; }
  /** 構成比の棒。**1色。**長さだけで見せ、数字を必ず横に添える（色に意味を持たせない） */
  function shareBar(v) {
    var w = el("span", { "class": "np-share" });
    if (v !== null && v !== undefined)
      w.appendChild(el("span", { "class": "np-share-fill", style: "width:" + Math.max(0, Math.min(100, v)) + "%" }));
    return el("span", null, [w, " " + pct(v)]);
  }

  /** 自社側70点（FR-138）。**基準は商品開発部が決める。**4軸がそろうまで合計は出さない */
  function selfCell(t, axes, canScore) {
    var td = el("td"), ss = t.self || {};
    td.appendChild(txt(axes.map(function (a) { return a.label + " " + (ss[a.key] === null || ss[a.key] === undefined ? "未" : ss[a.key]); }).join("・")));
    if (!canScore) return td;
    var bt = el("button", { type: "button", text: "点を付ける" });
    bt.addEventListener("click", function () {
      var f = el("form", { "class": "np-inline" });
      axes.forEach(function (a) {
        var i = el("input", { name: a.key, size: "3", "aria-label": a.label + "（0〜" + a.max + "）" });
        if (ss[a.key] !== null && ss[a.key] !== undefined) i.value = ss[a.key];
        f.appendChild(txt(" " + a.label + "（〜" + a.max + "） ")); f.appendChild(i);
      });
      f.appendChild(txt(" ")); f.appendChild(el("button", { type: "submit", text: "保存" }));
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var o = { theme_id: t.theme_id };
        axes.forEach(function (a) { o[a.key] = f.elements[a.key].value; });
        post("/api/fctr/self", o).then(function () { go(); }).catch(function (e) { f.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
      });
      td.appendChild(f); bt.setAttribute("disabled", "disabled");
    });
    td.appendChild(txt(" ")); td.appendChild(bt);
    return td;
  }

  /** 月次レポート（FR-116）。**Markdown を安全に描く**（textContent だけ。HTML を混ぜない） */
  function mdRender(md) {
    var box = el("div", { "class": "np-report" }), tbl = null, ul = null;
    function flush() { tbl = null; ul = null; }
    md.split("\n").forEach(function (ln) {
      if (/^\|/.test(ln)) {
        if (/^\|[-| ]+\|$/.test(ln)) return;                 // 区切り行
        var cells = ln.replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); });
        if (!tbl) { tbl = el("table", { "class": "np" }); box.appendChild(tbl);
          var tr0 = el("tr"); cells.forEach(function (c) { tr0.appendChild(el("th", { text: c })); }); tbl.appendChild(tr0); return; }
        var tr = el("tr"); cells.forEach(function (c) { tr.appendChild(el("td", { text: c })); }); tbl.appendChild(tr); return;
      }
      if (/^- /.test(ln)) { if (!ul) { ul = el("ul"); box.appendChild(ul); } ul.appendChild(el("li", { text: ln.slice(2) })); return; }
      flush();
      if (/^### /.test(ln)) box.appendChild(el("h4", { text: ln.slice(4) }));
      else if (/^## /.test(ln)) box.appendChild(el("h3", { text: ln.slice(3) }));
      else if (/^# /.test(ln)) box.appendChild(el("h2", { text: ln.slice(2) }));
      else if (ln.trim()) box.appendChild(el("p", { text: ln }));
    });
    return box;
  }

  function viewReports() {
    loading();
    var q = hashQuery();
    api("/api/reports").then(function (d) {
      var b = clear();
      setTitle("月次レポート");
      b.appendChild(btnRow([navBtn("#/", "← ダッシュボードへ戻る", "back")]));
      b.appendChild(el("p", { "class": "np-note", text: "毎月2日 10:00 に前の月の分を作ります（売上・商品ABC・新商品・開発の進み・年間プラン・トレンド）。事実の数字だけで、AI の文章は入れていません。送りはしません。" }));
      var nav = el("div", { "class": "np-filters" });
      var month = q.get("month") || (d.rows[0] && d.rows[0].month) || d.default;
      d.rows.forEach(function (r) {
        nav.appendChild(el("a", { href: "#/reports?month=" + r.month, text: r.month + (r.problems ? "（一部作れず）" : ""), "aria-current": r.month === month ? "true" : null }));
      });
      var bt = el("button", { type: "button", text: month + " を作り直す" });
      bt.addEventListener("click", function () {
        bt.setAttribute("disabled", "disabled");
        post("/api/reports/" + month + "/build", {}).then(function () { go(); }).catch(function (e) { alert(e.message); });
      });
      nav.appendChild(bt);
      b.appendChild(nav);
      var card = el("div", { "class": "np-card" });
      b.appendChild(card);
      api("/api/reports/" + encodeURIComponent(month)).then(function (r) {
        card.appendChild(el("p", { "class": "np-sub", text: "作成 " + r.generated_at + "（" + r.generated_by + "）" }));
        card.appendChild(mdRender(r.body_md));
      }).catch(function (e) { card.appendChild(el("p", { "class": "np-note", text: e.message + "。上の「作り直す」で作れます。" })); });
    }).catch(fail);
  }

  /** 機会カレンダー（F-2・FR-78〜81）。**発売の目安はイベントの2か月前。**枠は人が押して作る */
  function viewOpportunities() {
    loading();
    api("/api/opportunities").then(function (d) {
      var b = clear();
      setTitle("機会カレンダー");
      b.appendChild(btnRow([navBtn("#/plan", "← プランへ戻る", "back")]));
      b.appendChild(el("p", { "class": "np-note", text: "発売の目安は、イベントの " + d.lead_months + " か月前です。「枠にする」で、策定中の年間プラン"
        + (d.version ? "（" + d.version.fiscal_year + "年度）" : "") + "にその月の枠を足します（自動では足しません）。日付は元表の書き方のままです。" }));
      if (!d.version) b.appendChild(el("p", { "class": "np-warn", text: "策定中の年間プランがありません。枠を足すには、プランの画面で版を作ってください。" }));
      function slotBtn(x, cell) {
        if (x.in_plan) { cell.appendChild(txt("枠あり")); return; }
        if (!d.version) { cell.appendChild(txt("—")); return; }
        var bt = el("button", { type: "button", text: "枠にする" });
        bt.addEventListener("click", function () {
          post("/api/opportunities/slot", { theme_id: x.id }).then(function () { go(); })
            .catch(function (e) { cell.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
        });
        cell.appendChild(bt);
      }

      // 今週のトレンド（FR-81）
      var tc = el("div", { "class": "np-card" });
      tc.appendChild(el("h2", { text: "今週のトレンド上位（FCTR・" + dash(d.trends.week) + "）" }));
      if (d.trends.why) tc.appendChild(el("p", { "class": "np-warn", text: d.trends.why }));
      d.trends.segments.forEach(function (sg) {
        tc.appendChild(el("p", null, [el("strong", { text: sg.name + "：" }),
          txt(sg.top.map(function (t) { return t.label + "（" + t.decayed + "）"; }).join("、") || "なし")]));
      });
      tc.appendChild(btnRow([navBtn("#/trends", "トレンドの一覧を開く・アイデアにする")]));
      b.appendChild(tc);

      // 年間イベント（FR-78・FR-80）
      var ac = el("div", { "class": "np-card" });
      ac.appendChild(el("h2", { text: "年間イベント（" + d.annual.length + "件）" }));
      ac.appendChild(table(["月", "日", "イベント", "販売可能性が高い", "次の回", "発売の目安", "年間プラン"], d.annual.map(function (x) {
        var c = el("td"); slotBtn(x, c);
        return el("tr", null, [el("td", { "class": "np-num", text: x.month ? x.month + "月" : "—" }), el("td", { text: dash(x.day) }),
          el("td", { text: x.label }), el("td", { text: x.sellable ? "印あり" : "" }),
          el("td", { text: dash(x.event_month) }), el("td", { text: dash(x.launch_month) }), c]);
      })));
      b.appendChild(ac);

      // ライフイベント（FR-79）
      var lc = el("div", { "class": "np-card" });
      lc.appendChild(el("h2", { text: "ライフイベント（" + d.life.length + "件）" }));
      lc.appendChild(el("p", { "class": "np-sub", text: "総合＝購買意欲×2＋写真親和性＋発生頻度×2（満点40）。月が決まっていないので、枠はプランの画面で発売月を決めて作ってください。" }));
      lc.appendChild(table(["ライフイベント", "総合", "購買意欲", "写真親和性", "発生頻度", "優先", "商品が作れていない", "商品例", "年間プラン"], d.life.map(function (x) {
        return el("tr", null, [el("td", { text: x.label }), el("td", { "class": "np-num", text: dash(x.total) }),
          el("td", { "class": "np-num", text: dash(x.gift_intent) }), el("td", { "class": "np-num", text: dash(x.photo_fit) }),
          el("td", { "class": "np-num", text: dash(x.frequency) }), el("td", { "class": "np-num", text: dash(x.priority) }),
          el("td", { text: x.product_gap ? "印あり" : "" }), el("td", { text: dash(x.product_ideas) }),
          el("td", { text: x.in_plan ? "枠あり" : "—" })]);
      })));
      b.appendChild(lc);
    }).catch(fail);
  }

  /** FCTR の週次トレンド（FR-135〜137）。**市場性（30点）だけ**を Auto GROWTH から受け取る。
   *  時限スコアなので恒久の採点とは足さない。**減衰は上流がかけ済み**（こちらでは下げない・ADR-055）。 */
  function viewTrends() {
    loading();
    api("/api/fctr").then(function (d) {
      var b = clear();
      setTitle("トレンド（FCTR）", d.latest_week ? "／ " + d.latest_week : "");
      b.appendChild(btnRow([navBtn("#/ideas", "← アイデアへ戻る", "back"), navBtn("#/settings", "見せ方を変える（設定）", "back")]));
      if (d.why) b.appendChild(el("p", { "class": "np-warn", text: d.why }));
      var m = d.meta || {};
      // 説明は1行だけ。詳しい中身はたたむ（2026-10-06 点検「表の前が長い・内部の言葉が出ている」）
      b.appendChild(el("p", { "class": "np-note", text: d.latest_week + " の需要テーマです（市場性・30点満点）。客層ごとに上位 " + d.top_n + " つに印。気になるテーマは「アイデアにする」で起票できます。" }));
      if (d.stale) b.appendChild(el("p", { "class": "np-warn", text: d.stale }));
      var more = el("details", { "class": "np-more" });
      more.appendChild(el("summary", { text: "点の出し方と出どころ" }));
      more.appendChild(el("p", { "class": "np-sub", text: "Auto GROWTH が毎週月曜に出します。今週観測されなかったテーマは、最後に観測した週から半減期8週で下げた点で届きます（こちらでは重ねて下げていません）。一過性の高得点を恒久の採点と混ぜないため、アイデアの点とは足しません。" }));
      if (m.sources_ok) more.appendChild(el("p", { "class": "np-sub", text: "今週使えた出どころ: " + (m.sources_ok.join("、") || "なし")
        + (m.sources_disabled && m.sources_disabled.length ? "／止めている出どころ: " + m.sources_disabled.length + " 種" : "") }));
      b.appendChild(more);
      if (!d.segments.length) { b.appendChild(el("p", { "class": "np-note", text: "表示できるテーマがありません。" })); return; }
      d.segments.forEach(function (sg) {
        var c = el("div", { "class": "np-card" });
        c.appendChild(el("h2", { text: sg.name }));
        c.appendChild(table(["", "テーマ", "", "市場性（点／上限）", "連続", "根拠", "自社側70点", "合計100"], sg.themes.map(function (t) {
          var act = el("td");
          if (t.idea_id) act.appendChild(el("a", { href: "#/ideas/" + encodeURIComponent(t.idea_id), text: "アイデア " + t.idea_id }));
          else {
            var bt = el("button", { type: "button", "class": "np-btn np-btn-sm", text: "アイデアにする" });
            bt.addEventListener("click", function () {
              post("/api/fctr/idea", { theme_id: t.theme_id, segment: sg.segment }).then(function (r) {
                location.hash = "#/ideas/" + encodeURIComponent(r.id);
              }).catch(function (e) { act.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
            });
            act.appendChild(bt);
          }
          return el("tr", null, [el("td", { text: t.top ? "上位" + d.top_n : "" }), el("td", { text: t.label }), act,
            el("td", { "class": "np-num", text: t.decayed + "／" + dash(t.score_max) + (t.age_weeks ? "（最後の観測 " + t.age_weeks + "週前）" : "") }),
            el("td", { "class": "np-num", text: dash(t.consecutive) + "週" }),
            el("td", { text: dash(t.evidence) }), selfCell(t, d.self_axes, d.can_score), el("td", { "class": "np-num", text: t.total100 === null ? "—" : String(t.total100) })]);
        })));
        b.appendChild(c);
      });
    }).catch(fail);
  }

  /** 商品ABC分析と比較ABC分析（ADR-050）。A 70%・B 90%・C 残り。サイトごとに店の商品番号で数える。
   *  比較は 増加・減少・同額・消滅・新規。**比べる期間にデータが無ければ比較しない。** */
  function viewAbc() {
    loading();
    var q = hashQuery();
    var params = ["site", "from", "to", "compare", "cfrom", "cto"].map(function (k) {
      return q.get(k) ? k + "=" + encodeURIComponent(q.get(k)) : null; }).filter(Boolean);
    api("/api/abc" + (params.length ? "?" + params.join("&") : "")).then(function (d) {
      var b = clear();
      setTitle("商品ABC", "／ " + d.site_label + " " + d.period.from + "〜" + d.period.to);
      b.appendChild(salesSubnav("#/abc"));

      // 条件
      var f = el("form", { "class": "np-filters" });
      function sel(name, label, opts, cur) {
        var s2 = el("select", { name: name, "aria-label": label });
        opts.forEach(function (o) { s2.appendChild(el("option", { value: o[0], text: o[1] })); });
        s2.value = cur || "";
        return el("label", null, [label + " ", s2]);
      }
      var mo = d.months.map(function (m) { return [m, m]; });
      f.appendChild(sel("site", "サイト", d.sites.map(function (x) { return [x.key, x.label]; }), d.site));
      f.appendChild(sel("from", "はじめ", mo, d.period.from.slice(0, 7)));
      f.appendChild(sel("to", "おわり", mo, d.period.to.slice(0, 7)));
      f.appendChild(sel("compare", "比べる", [["yoy", "前年の同じ期間"], ["custom", "期間を選ぶ"], ["none", "比べない"]], d.compare_mode));
      f.appendChild(sel("cfrom", "比べる期間のはじめ", [["", "—"]].concat(mo), q.get("cfrom") || ""));
      f.appendChild(sel("cto", "比べる期間のおわり", [["", "—"]].concat(mo), q.get("cto") || ""));
      f.appendChild(el("button", { type: "submit", text: "表示" }));
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var o = ["site", "from", "to", "compare", "cfrom", "cto"].map(function (k) {
          var v = f.elements[k].value; return v ? k + "=" + encodeURIComponent(v) : null; }).filter(Boolean);
        location.hash = "#/abc?" + o.join("&");
      });
      b.appendChild(f);
      d.notes.forEach(function (n) { b.appendChild(el("p", { "class": "np-warn", text: n })); });
      b.appendChild(el("p", { "class": "np-note", text: "金額は" + d.tax + "の商品代（取消・返金を除く・売上フィード）。区切りは A " + d.limits.A + "%・B " + d.limits.B
        + "%・C 残り（その商品の手前までの累計で決めます）。商品はサイトごとの店の商品番号です。" }));

      // ABC の集計
      function abcSummary(title, x) {
        var c = el("div", { "class": "np-card" });
        c.appendChild(el("h2", { text: title }));
        c.appendChild(table(["区分", "商品数", "売上", "構成比"], ["A", "B", "C"].map(function (k) {
          var v = x.classes[k];
          return el("tr", null, [el("td", { text: k }), el("td", { "class": "np-num", text: String(v.count) }),
            el("td", { "class": "np-num", text: yen(v.revenue) }), el("td", null, [shareBar(v.share)])]);
        }).concat([el("tr", null, [el("th", { text: "合計" }), el("td", { "class": "np-num", text: String(x.count) }),
          el("td", { "class": "np-num", text: yen(x.total) }), el("td", { text: "" })])])));
        return c;
      }
      var g = el("div", { "class": "np-grid np-grid-2" });
      g.appendChild(abcSummary("選んだ期間 " + d.period.from + "〜" + d.period.to, d.current));
      if (d.compare) g.appendChild(abcSummary("比べる期間 " + d.compare.period.from + "〜" + d.compare.period.to, d.compare));
      b.appendChild(g);
      if (d.why_compare) b.appendChild(el("p", { "class": "np-warn", text: d.why_compare }));

      function nameCell(x) { return el("td", { text: x.name || x.name_label || "—" }); }

      // 比較（増加・減少・同額・消滅・新規）
      if (d.changes) {
        var cc = el("div", { "class": "np-card" });
        cc.appendChild(el("h2", { text: "比較（増加・減少・消滅・新規）" }));
        var kinds = ["増加", "減少", "消滅", "新規", "同額"];
        cc.appendChild(table(["区分", "商品数", "選んだ期間の売上", "比べる期間の売上", "差額"], kinds.map(function (k) {
          var v = d.changes[k];
          return el("tr", null, [el("td", { text: k }), el("td", { "class": "np-num", text: String(v.count) }),
            el("td", { "class": "np-num", text: yen(v.current) }), el("td", { "class": "np-num", text: yen(v.previous) }),
            el("td", { "class": "np-num", text: (v.diff > 0 ? "+" : v.diff < 0 ? "−" : "±") + yen(Math.abs(v.diff)) })]);
        })));
        kinds.forEach(function (k) {
          var v = d.changes[k];
          if (!v.count) return;
          var det = el("details");
          det.appendChild(el("summary", { text: k + "の商品（" + v.count + "）— 差額の大きい順" }));
          det.appendChild(table(["店の商品番号", "表示名", "分類", "選んだ期間", "比べる期間", "差額", "ABC（前→今）"], v.rows.map(function (x) {
            return el("tr", null, [el("td", { text: x.key }), nameCell(x), el("td", { text: x.path }),
              el("td", { "class": "np-num", text: yen(x.current) }), el("td", { "class": "np-num", text: yen(x.previous) }),
              el("td", { "class": "np-num", text: (x.diff > 0 ? "+" : x.diff < 0 ? "−" : "±") + yen(Math.abs(x.diff)) }),
              el("td", { text: (x.class_before || "—") + " → " + (x.class_now || "—") })]);
          })));
          cc.appendChild(det);
        });
        b.appendChild(cc);
      }

      // ABC の一覧
      var lc = el("div", { "class": "np-card" });
      lc.appendChild(el("h2", { text: "ABC の一覧（" + d.rows.length + " 商品番号）" }));
      ["A", "B", "C"].forEach(function (k) {
        var rs = d.rows.filter(function (x) { return x.class === k; });
        if (!rs.length) return;
        var det = el("details", k === "A" ? { open: "open" } : null);
        det.appendChild(el("summary", { text: k + "（" + rs.length + " 商品番号・" + yen(d.current.classes[k].revenue) + "）" }));
        det.appendChild(table(["順位", "店の商品番号", "表示名", "分類", "売上", "数量", "構成比", "累計", "比べる期間のABC"], rs.map(function (x) {
          return el("tr", null, [el("td", { "class": "np-num", text: String(x.rank) }), el("td", { text: x.key }), nameCell(x),
            el("td", { text: x.path }), el("td", { "class": "np-num", text: yen(x.revenue) }),
            el("td", { "class": "np-num", text: Math.round(x.qty).toLocaleString("ja-JP") }),
            el("td", { "class": "np-num", text: pct(x.share) }), el("td", { "class": "np-num", text: pct(x.cum) }),
            el("td", { text: d.compare ? (x.prev_class || "なし") : "—" })]);
        })));
        lc.appendChild(det);
      });
      b.appendChild(lc);
    }).catch(fail);
  }

  /** 原価・調達の一覧（案件をまたぐ）。**締切に間に合わない案件を一番上に。**中身は各案件の画面で入れる */
  function viewCost() {
    loading();
    api("/api/cost").then(function (d) {
      var b = clear();
      setTitle("原価・調達");
      b.appendChild(salesSubnav("#/cost"));
      b.appendChild(el("p", { "class": "np-note", text: "発売前の案件だけを出します。相見積の候補と試算原価は、各案件の画面の「原価・調達」で入れます。外注先・仕入先・原材料の一覧は CIP が持ちます。" }));
      if (!d.rows.length) { b.appendChild(el("p", { "class": "np-note", text: "発売前の案件はありません。" })); return; }
      b.appendChild(table(["案件", "発売予定日", "本番発注の締切", "候補（採用／未判断）", "CIP 未登録", "試算", "直接費", "想定粗利"],
        d.rows.map(function (r) {
          var dl = r.deadline;
          return el("tr", null, [
            el("td", null, [el("a", { href: "#/projects/" + encodeURIComponent(r.id), text: r.id }),
              txt(" " + (r.internal_name || r.product))]),
            el("td", { text: dash(r.launch_date) }),
            el("td", { text: dl.state + (dl.due ? "（" + dl.due + (dl.days_left >= 0 ? "・あと" + dl.days_left + "日" : "・" + (-dl.days_left) + "日超過") + "）" : "") }),
            el("td", { "class": "np-num", text: r.candidates + "（" + r.adopted + "／" + r.undecided + "）" }),
            el("td", { "class": "np-num", text: String(r.unregistered) }),
            el("td", { text: r.version ? "v" + r.version : "なし" }),
            el("td", { "class": "np-num", text: r.direct ? cyen(r.direct.yen) + (r.direct.unknown ? "＋未確定" + r.direct.unknown + "行" : "") : "—" }),
            el("td", { "class": "np-num", text: r.gross && r.gross.yen !== null ? cyen(r.gross.yen) + (r.gross.rate !== null ? "（" + r.gross.rate + "%）" : "") + (r.gross.overstated ? "※" : "") : "—" })
          ]);
        })));
      b.appendChild(el("p", { "class": "np-sub", text: "※ 直接費に未確定の行があり、粗利が実際より大きく出ています。" }));
    }).catch(fail);
  }

  function salesSubnav(cur) {
    var n = el("div", { "class": "np-filters", role: "tablist", "aria-label": "売上・原価" });
    [["#/sales", "売上実績"], ["#/abc", "商品ABC"], ["#/cost", "原価・調達"]].forEach(function (x) {
      n.appendChild(el("a", { href: x[0], text: x[1], "aria-current": x[0] === cur ? "true" : null }));
    });
    return n;
  }

  function viewSales() {
    loading();
    var q = hashQuery();
    var site = q.get("site") || "all", month = q.get("month") || "";
    api("/api/sales?site=" + encodeURIComponent(site) + (month ? "&month=" + encodeURIComponent(month) : ""))
      .then(function (d) {
        var b = clear();
        setTitle("売上実績", "／ " + d.site_label + (d.month ? " " + d.month : ""));
        b.appendChild(salesSubnav("#/sales"));
        function href(st, m) { return "#/sales?site=" + st + (m ? "&month=" + m : ""); }

        // サイトのタブ
        var tabs = el("div", { "class": "np-filters", role: "tablist", "aria-label": "サイト" });
        d.sites.forEach(function (s) {
          tabs.appendChild(el("a", { href: href(s.key, d.month), text: s.label,
            "aria-current": s.key === d.site ? "true" : null }));
        });
        b.appendChild(tabs);
        if (!d.month || d.why) {
          b.appendChild(el("p", { "class": "np-warn", text: d.why || "集計がありません。" }));
          return;
        }
        // 月
        var mf = el("div", { "class": "np-filters", "aria-label": "月" });
        d.months.forEach(function (m) {
          mf.appendChild(el("a", { href: href(d.site, m), text: m, "aria-current": m === d.month ? "true" : null }));
        });
        b.appendChild(mf);
        if (d.feed_why) b.appendChild(el("p", { "class": "np-warn",
          text: "売上フィードが読めないため、Auto GROWTH の月次集計で表示しています（" + d.feed_why + "）。" }));
        if (d.source_until) b.appendChild(el("p", { "class": "np-note",
          text: "売上フィードは " + d.source_until + " までの注文を取り込み済みです。" }));
        if (!d.complete) b.appendChild(el("p", { "class": "np-warn",
          text: d.month + " はまだ終わっていない月です。前年比は出しません（丸1か月の前年と比べると必ず低く出るため）。" }));

        // 1段目: 合計
        var t = d.total, top = el("div", { "class": "np-card" });
        top.appendChild(el("h2", { text: d.site_label + " " + d.month + "（" + d.tax + "・" + d.basis + "）" }));
        if (t.revenue === null) {
          top.appendChild(el("span", { "class": "np-big np-big-unmeasured", text: "未計測" }));
          if (t.why) top.appendChild(el("p", { "class": "np-note", text: t.why }));
        } else {
          top.appendChild(el("span", { "class": "np-big", text: yen(t.revenue) }));
          top.appendChild(el("p", { "class": "np-sub",
            text: "前年同月（" + (d.prev_year_month || "—") + "） " + yen(t.prev_revenue) + "　前年比 " + yoy(t.yoy) }));
        }
        b.appendChild(top);

        // 2段目: 月ごとの推移
        var tr = el("div", { "class": "np-card" });
        tr.appendChild(el("h2", { text: "月ごとの推移" }));
        var maxv = d.trend.reduce(function (a, x) { return Math.max(a, x.revenue || 0, x.prev_revenue || 0); }, 0);
        // **最新の月を上に**（2026-10-02 十文字さん）
        tr.appendChild(table(["月", "売上", "", "前年同月", "前年比"], d.trend.slice().reverse().map(function (x) {
          return el("tr", null, [el("td", { text: x.month + (x.complete ? "" : "（途中）") }),
            el("td", { "class": "np-num", text: yen(x.revenue) }),
            el("td", null, [shareBar(maxv && x.revenue !== null ? x.revenue / maxv * 100 : null)]),
            el("td", { "class": "np-num", text: yen(x.prev_revenue) }),
            el("td", { "class": "np-num", text: x.complete && x.revenue !== null && x.prev_revenue
              ? yoy((x.revenue - x.prev_revenue) / x.prev_revenue * 100) : "—" })]);
        })));
        tr.appendChild(el("p", { "class": "np-note", text: "棒は表の中の最大の月を100としています。いま見られるのは " + d.months.length + " か月分です（Auto GROWTH の月次集計がある月）。" }));
        b.appendChild(tr);

        // 全体のときだけ: 店別
        if (d.stores) {
          var sc = el("div", { "class": "np-card" });
          sc.appendChild(el("h2", { text: "店別" }));
          sc.appendChild(table(["店", "売上", "構成比", "前年同月", "前年比"], d.stores.map(function (x) {
            return el("tr", null, [el("td", { text: x.store }), el("td", { "class": "np-num", text: yen(x.revenue) }),
              el("td", null, [shareBar(x.share)]), el("td", { "class": "np-num", text: yen(x.prev_revenue) }),
              el("td", { "class": "np-num", text: yoy(x.yoy) })]);
          })));
          b.appendChild(sc);
        }

        // 3段目: 構成（分類）
        var cc = el("div", { "class": "np-card" });
        cc.appendChild(el("h2", { text: "構成（分類）" }));
        var c = d.composition;
        if (!c.rows) {
          cc.appendChild(el("p", { "class": "np-big-unmeasured", text: "未計測" }));
          cc.appendChild(el("p", { "class": "np-note", text: c.why }));
        } else {
          cc.appendChild(el("p", { "class": "np-sub", text: "大分類の行を押すと中分類が開きます。分類済み " + pct(c.classified_share)
            + "（分類が付かない明細は「分類なし」に入れています）" }));
          var rows = [];
          c.rows.forEach(function (r, i) {
            var btn = el("button", { type: "button", "aria-expanded": "false", text: r.name });
            var head = el("tr", null, [el("td", null, [btn]), el("td", { "class": "np-num", text: yen(r.revenue) }),
              el("td", null, [shareBar(r.share)]), el("td", { "class": "np-num", text: yoy(r.yoy) })]);
            rows.push(head);
            var kids = r.children.map(function (k) {
              var tr2 = el("tr", { hidden: "hidden" }, [el("td", { text: "　└ " + k.name }),
                el("td", { "class": "np-num", text: yen(k.revenue) }), el("td", null, [shareBar(k.share)]),
                el("td", { "class": "np-num", text: yoy(k.yoy) })]);
              rows.push(tr2);
              return tr2;
            });
            btn.addEventListener("click", function () {
              var open = btn.getAttribute("aria-expanded") === "true";
              btn.setAttribute("aria-expanded", open ? "false" : "true");
              kids.forEach(function (k) { if (open) k.setAttribute("hidden", "hidden"); else k.removeAttribute("hidden"); });
            });
          });
          rows.push(el("tr", null, [el("td", { text: "分類なし（要確認・新商品など）" }),
            el("td", { "class": "np-num", text: yen(c.unclassified) }), el("td", null, [shareBar(c.unclassified_share)]),
            el("td", { "class": "np-num", text: "—" })]));
          cc.appendChild(table(["分類", "売上", "構成比", "前年比"], rows));
          if (d.site === "all") cc.appendChild(el("p", { "class": "np-note",
            text: "サイトごとの構成は、売上フィードと CIP の対応表が届いてから、各サイトのタブに出ます。" }));
        }
        b.appendChild(cc);

        // 新商品の売上（FR-112・115）。全体タブだけ
        if (d.site === "all") {
          var nc2 = el("div", { "class": "np-card" });
          nc2.appendChild(el("h2", { text: "新商品（NEW PRODUCT から出した商品）" }));
          b.appendChild(nc2);
          api("/api/sales/new-products").then(function (n) {
            nc2.appendChild(el("p", { "class": "np-sub", text: n.since + "〜" + n.until + "。" + n.note }));
            var flows = Object.keys(n.by_flow).map(function (k) { return k + " " + n.by_flow[k]; }).join("・");
            nc2.appendChild(table(["項目", "値"], [
              el("tr", null, [el("th", { text: "新商品の本数（発売から1年以内）" }), el("td", { text: n.count + " 本" + (flows ? "（" + flows + "）" : "") })]),
              el("tr", null, [el("th", { text: "売上として数える案件" }), el("td", { text: n.counted + " 件" })]),
              el("tr", null, [el("th", { text: "全額で数える分" }), el("td", { "class": "np-num", text: yen(n.totals["全額"]) })]),
              el("tr", null, [el("th", { text: "増分で数える分" }), el("td", { "class": "np-num", text: yen(n.totals["増分"]) })]),
              el("tr", null, [el("th", { text: "未計測・方式未選択" }), el("td", { text: n.totals["未計測"] + " 件・" + n.totals["方式未選択"] + " 件" })])
            ]));
            if (n.rows.length) nc2.appendChild(table(["案件", "発売日", "方式", "金額", "備考"], n.rows.map(function (r) {
              return el("tr", null, [el("td", null, [el("a", { href: "#/projects/" + encodeURIComponent(r.id), text: r.id }), txt(" " + (r.internal_name || r.product))]),
                el("td", { text: r.launch_date }), el("td", { text: dash(r.basis) }),
                el("td", { "class": "np-num", text: r.amount === null ? "未計測" : yen(r.amount) }), el("td", { text: dash(r.why) })]);
            })));
          }).catch(function (e) { nc2.appendChild(el("p", { "class": "np-err", text: e.message })); });
        }

        // 4段目: 商品別
        var pc = el("div", { "class": "np-card" });
        var pr = d.products;
        pc.appendChild(el("h2", { text: "商品別" + (pr.rows ? "（上位 " + pr.rows.length + "／" + pr.count + " 商品番号）" : "") }));
        if (!pr.rows) {
          pc.appendChild(el("p", { "class": "np-big-unmeasured", text: "未計測" }));
          pc.appendChild(el("p", { "class": "np-note", text: pr.why }));
        } else {
          if (pr.name_note) pc.appendChild(el("p", { "class": "np-note", text: pr.name_note }));
          pc.appendChild(table(["店の商品番号", "表示名", "分類", "共通商品コード", "売上", "数量", "構成比", "前年比", "案件"],
            pr.rows.map(function (x) {
              return el("tr", null, [el("td", { text: x.store_code }), el("td", { text: x.name || x.name_label || "—" }),
                el("td", { text: x.path }),
                el("td", { text: x.product_codes.length ? x.product_codes.join("、") + (x.more_codes ? " ほか" + x.more_codes : "") : "—" }),
                el("td", { "class": "np-num", text: yen(x.revenue) }),
                el("td", { "class": "np-num", text: Math.round(x.qty).toLocaleString("ja-JP") }),
                el("td", null, [shareBar(x.share)]), el("td", { "class": "np-num", text: yoy(x.yoy) }),
                el("td", null, [x.project_id ? el("a", { href: "#/projects/" + encodeURIComponent(x.project_id), text: "新商品 " + x.project_id }) : txt("—")])]);
            })));
        }
        b.appendChild(pc);

        // 数え方
        var nc = el("div", { "class": "np-card" });
        nc.appendChild(el("h2", { text: "数え方と出どころ" }));
        var ul = el("ul", { "class": "np-miss" });
        [ "金額は" + d.tax + "の商品代です（送料・決済手数料・クーポンは含みません）。会社の正式な売上（経営管理）は税抜・注文ごとの請求金額なので、ここの数字とは一致しません。",
          "出どころ: " + (d.source || "—") + "（" + (d.generated_at || "—") + " 作成）" ]
          .concat(d.caveats).forEach(function (x) { ul.appendChild(el("li", { text: x })); });
        nc.appendChild(ul);
        b.appendChild(nc);
      }).catch(fail);
  }

  // ══════════════════════════════════════════════════════
  // 設定（2026-10-06 十文字さん指示・ADR-057）。**自分の見え方**はここで誰でも変えられる。
  // 変えたら画面にすぐ当てる（読み込み直さなくてよい）。サーバーにも保存し、別の端末でも同じにする
  // ══════════════════════════════════════════════════════
  /** after = { id: 戻る節の id, msg: 知らせる文 }。全体に効く保存の後、**画面の先頭へ戻さない**（2026-10-06 再点検） */
  function afterSave(after) {
    if (!after) return;
    var t = document.getElementById(after.id);
    if (!t) return;
    t.scrollIntoView({ block: "start" });
    var st = el("p", { "class": "np-status", role: "status", "aria-live": "polite", text: after.msg });
    var h = t.querySelector("h2");
    if (h && h.nextSibling) t.insertBefore(st, h.nextSibling); else t.appendChild(st);
  }

  function viewSettings(after) {
    loading();
    Promise.all([api("/api/me"), api("/api/settings")]).then(function (r) {
      var me = r[0], st = r[1], b = clear();
      setTitle("設定");
      var root = document.documentElement;

      // 保存の状態は節ごとの欄（aria-live）で知らせる。送信中は押せなくする（2026-10-06 点検「保存が伝わらない」）
      var seq = 0;
      function choice(cur, opts, onPick) {
        var hid = "np-set-h-" + (++seq);
        var row = el("div", { "class": "np-choice", role: "group", "aria-labelledby": hid });
        var st = el("span", { "class": "np-status", role: "status", "aria-live": "polite" });
        opts.forEach(function (o) {
          var bt = el("button", { type: "button", text: o.label, "aria-pressed": String(o.value === cur) });
          bt.addEventListener("click", function () {
            var all = row.querySelectorAll("button");
            Array.prototype.forEach.call(all, function (x) { x.setAttribute("disabled", "disabled"); });
            st.textContent = "保存しています…";
            onPick(o.value).then(function () {
              Array.prototype.forEach.call(all, function (x) { x.setAttribute("aria-pressed", String(x === bt)); });
              st.textContent = "保存しました";
            }).catch(function (e) { st.textContent = "保存できませんでした: " + e.message; })
              .then(function () { Array.prototype.forEach.call(all, function (x) { x.removeAttribute("disabled"); }); });
          });
          row.appendChild(bt);
        });
        return { row: row, status: st, id: hid };
      }
      function section(card, title, note, ch) {
        var w = el("div", { "class": "np-setting" });
        w.appendChild(el("h3", { id: ch.id, text: title }));
        if (note) w.appendChild(el("p", { "class": "np-sub", text: note }));
        w.appendChild(ch.row);
        w.appendChild(ch.status);
        card.appendChild(w);
      }
      function savePref(key) {
        return function (v) {
          var o = {}; o[key] = v;
          return post("/api/me/display", o).then(function () {
            if (key === "font") root.setAttribute("data-np-font", v);
            if (key === "density") root.setAttribute("data-np-density", v);
            if (key === "start") root.setAttribute("data-np-start", v);
          });
        };
      }
      var opt = {};
      me.pref_options.forEach(function (o) { opt[o.key] = o; });

      // 表示
      var disp = el("div", { "class": "np-card" });
      disp.appendChild(el("h2", { text: "表示（" + (me.name || me.user_id) + " さんの画面だけ）" }));
      disp.appendChild(el("p", { "class": "np-sub", text: "ほかの人の画面は変わりません。別の端末で開いても同じ見え方になります。" }));
      section(disp, "配色", "「端末に合わせる」は、パソコンやスマホの明るさの設定に従います。",
        choice(me.theme, me.themes.map(function (t) { return { value: t[0], label: t[1] }; }), function (v) {
          return post("/api/me/display", { theme: v }).then(function () {
            if (v === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", v);
          });
        }));
      ["font", "density"].forEach(function (k) {
        section(disp, opt[k].label, null, choice(me.prefs[k], opt[k].options, savePref(k)));
      });
      section(disp, opt.start.label, "アプリを開いたとき（アドレスに画面の指定が無いとき）に出す画面です。",
        choice(me.prefs.start, opt.start.options, savePref("start")));
      b.appendChild(disp);

      // トレンドの見せ方
      var tr = el("div", { "class": "np-card" });
      tr.appendChild(el("h2", { text: "トレンド（FCTR）の見せ方" }));
      tr.appendChild(el("p", { "class": "np-sub", text: "トレンド画面の表示だけが変わります。月次レポートと機会カレンダーは、いつもの見せ方（上位3）のままです。" }));
      ["trend_top", "trend_order", "trend_faded"].forEach(function (k) {
        section(tr, opt[k].label, null, choice(me.prefs[k], opt[k].options, savePref(k)));
      });
      tr.appendChild(el("p", { "class": "np-sub", text: "変えた見せ方は、トレンド画面を開き直すと効きます。" }));
      tr.appendChild(btnRow([navBtn("#/trends", "トレンド画面を開く")]));
      b.appendChild(tr);

      // 操作マニュアル
      var mc = el("div", { "class": "np-card" });
      mc.appendChild(el("h2", { text: "操作マニュアル" }));
      mc.appendChild(el("p", { "class": "np-sub", text: "画面ごとに、何をするところか・よく使う操作を書いています。" }));
      mc.appendChild(btnRow([navBtn("#/manual", "操作マニュアルを開く")]));
      b.appendChild(mc);

      // データの登録（ADR-060〜）。重い登録は別の画面に分ける
      var dc = el("div", { "class": "np-card" });
      dc.appendChild(el("h2", { text: "データの登録" }));
      dc.appendChild(el("p", { "class": "np-sub", text: "このアプリが正本として持つデータを、画面から足したり直したりします。外注先・原材料・商品は CIP、利用者はカレンダーで登録します。" }));
      dc.appendChild(btnRow([navBtn("#/settings/events", "年間イベント・ライフイベント"),
        navBtn("#/settings/templates", "標準タスクのひな形・工数ポイント係数"),
        navBtn("#/settings/ideas-import", "アイデアの一括登録")]));
      b.appendChild(dc);

      // アプリ全体の設定（ADR-059）。変えられる人にだけ「変える」を出す。**根拠が必須**、空欄は未設定に戻す
      var ac = el("div", { "class": "np-card", id: "np-set-app" });
      ac.appendChild(el("h2", { text: "アプリ全体の設定（全員に効くもの）" }));
      ac.appendChild(el("p", { "class": "np-sub", text: "数え方の基準になる値です。全員が見られます。「未設定」は 0 ではなく、まだ決まっていないという意味です。変えた記録は下の「変更の記録」に残ります。" }));
      var groups = {};
      st.rows.forEach(function (x) { (groups[x.group] = groups[x.group] || []).push(x); });
      var setRows = [];
      Object.keys(groups).forEach(function (gname) {
        setRows.push(el("tr", { "class": "np-sep" }, [el("td", { colspan: "5", text: gname })]));
        setRows = setRows.concat(groups[gname].map(function (x) {
          var v = (x.value === null || x.value === "") ? "未設定" : x.value + (x.unit ? " " + x.unit : "");
          if (x.kind === "bool" && x.value !== null) v = x.value === "1" ? "使う" : "使わない";
          var act = el("td");
          if (x.editable) {
            var bt = el("button", { type: "button", text: "変える" });
            bt.addEventListener("click", function () {
              if (act.querySelector("form")) return;
              var f = el("form", { "class": "np-inline" });
              var vi = el("input", { name: "value", size: "8", "aria-label": x.label, value: x.value === null ? "" : String(x.value) });
              var ri = el("input", { name: "reason", size: "24", "aria-label": "根拠", placeholder: "根拠（必須）" });
              [vi, ri, el("button", { type: "submit", text: "保存" })].forEach(function (n) { f.appendChild(n); });
              f.appendChild(el("span", { "class": "np-sub", text: " 空欄で保存すると未設定に戻ります" }));
              f.addEventListener("submit", function (ev) {
                ev.preventDefault();
                post("/api/settings/value", { key: x.key, value: vi.value, reason: ri.value })
                  .then(function () { viewSettings({ id: "np-set-app", msg: "「" + x.label + "」を保存しました（変更の記録に残りました）" }); })
                  .catch(function (e) { f.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
              });
              act.appendChild(f);
            });
            act.appendChild(bt);
          } else {
            act.appendChild(el("span", { "class": "np-sub", text: x.locked_why || x.who }));
          }
          return el("tr", null, [el("td", { text: x.label }), el("td", { text: v }),
            el("td", { text: x.updated_at ? x.updated_at + " " + dash(x.updated_by) : "—" }),
            el("td", null, [txt(x.desc), x.note ? el("div", { "class": "np-sub", text: x.note }) : null]), act]);
        }));
      });
      ac.appendChild(table(["項目", "いまの値", "最終更新", "説明", ""], setRows));
      b.appendChild(ac);

      // 業務ロール（ADR-059）。全員に見せる。付け外しはアプリ権限 admin（十文字さんの選択）
      var rc = el("div", { "class": "np-card", id: "np-set-roles" });
      var R = st.roles;
      rc.appendChild(el("h2", { text: "業務ロール（誰が何を判定するか）" }));
      rc.appendChild(el("p", { "class": "np-sub", text: "ゲートや年間プランを承認できる資格は、ここの業務ロールで決まります（アプリの管理者かどうかとは別です）。"
        + (R.can_edit ? "印を押すと付け外しできます。" : "付け外しできるのは、アプリ権限が管理者（admin）の人です。")
        + "利用者そのものの追加は、カレンダーの ☰ →「人とアプリ」で行います。" }));
      var roleCols = R.roles;                       // 他部署のロール（生産部など）も G4 の承認に使うので全部出す
      rc.appendChild(table(["利用者"].concat(roleCols.map(function (r) { return r.label; })), R.people.map(function (p) {
        var mine = R.members[p.user_id] || [];
        return el("tr", null, [el("td", { text: p.name + "（" + p.user_id + "）" })].concat(roleCols.map(function (r) {
          var has = mine.indexOf(r.code) >= 0, td = el("td");
          if (!R.can_edit) { td.appendChild(txt(has ? "あり" : "—")); return td; }
          var bt = el("button", { type: "button", text: has ? "あり" : "—", "aria-pressed": String(has),
            "aria-label": p.name + " の " + r.label + (has ? "を外す" : "を付ける") });
          bt.addEventListener("click", function () {
            if (!window.confirm(p.name + " さんの「" + r.label + "」を" + (has ? "外します" : "付けます") + "。よろしいですか")) return;
            post("/api/settings/role", { user_id: p.user_id, role: r.code, on: has ? "0" : "1" })
              .then(function () { viewSettings({ id: "np-set-roles", msg: p.name + " さんの「" + r.label + "」を" + (has ? "外しました" : "付けました") }); })
              .catch(function (e) { td.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
          });
          td.appendChild(bt);
          return td;
        })));
      })));
      b.appendChild(rc);

      // 変更の記録（全員に見せる）
      var lc = el("div", { "class": "np-card", id: "np-set-log" });
      lc.appendChild(el("h2", { text: "変更の記録（全員に効くもの・新しい順）" }));
      if (!st.log.length) lc.appendChild(el("p", { "class": "np-note", text: "まだありません。" }));
      else lc.appendChild(table(["いつ", "誰が", "何を", "前 → 後", "根拠"], st.log.map(function (x) {
        var what = x.label || x.action_label || x.action;
        var ba = x.before !== undefined && x.before !== null || x.after !== undefined && x.after !== null
          ? (x.before === null || x.before === undefined ? "未設定" : x.before) + " → " + (x.after === null || x.after === undefined ? "未設定" : x.after)
          : (x.detail && x.detail.role ? x.target + " / " + x.detail.role : dash(x.target));
        return el("tr", null, [el("td", { text: x.at }), el("td", { text: dash(x.user_id) }), el("td", { text: what }),
          el("td", { text: ba }), el("td", { text: dash(x.reason) })]);
      })));
      b.appendChild(lc);
      afterSave(after);
    }).catch(fail);
  }

  /** 年間イベント・ライフイベントの登録（ADR-060）。**消さない**（使わない にする）。日付は原文のまま */
  function viewEvents(after) {
    loading();
    api("/api/settings/events").then(function (d) {
      var b = clear();
      setTitle("年間イベント・ライフイベント");
      b.appendChild(btnRow([navBtn("#/settings", "← 設定へ戻る", "back"), navBtn("#/opportunities", "機会カレンダーを開く", "back")]));
      b.appendChild(el("p", { "class": "np-sub", text: "機会カレンダーの元になる暦です。いま使っているのは 年間イベント " + d.count["年間イベント"]
        + "・ライフイベント " + d.count["ライフイベント"] + "（最終更新 " + (d.last ? d.last.edited_at + " " + d.last.edited_by : "—") + "）。"
        + "消さずに「使わない」にすると、機会カレンダーから外れます。名前を直すと、策定中の年間プランの枠の名前も付け替えます。" }));
      if (!d.can_edit) b.appendChild(el("p", { "class": "np-note", text: "登録・修正できるのは、商品開発部・管理者・社長の業務ロールの人です。" }));
      var editing = { row: null };

      function form(kind, r) {
        var f = el("form", { "class": "np-field" });
        r = r || {};
        f.appendChild(el("h3", { text: r.id ? "直す: " + r.label : kind + "を足す" }));
        function inp(name, label, size, val, extra) {
          var a = { name: name, size: size || "10", value: val === null || val === undefined ? "" : String(val) };
          if (extra) Object.keys(extra).forEach(function (k) { a[k] = extra[k]; });
          return el("label", null, [label + " ", el("input", a)]);
        }
        function chk(name, label, on) {
          var c = el("input", { type: "checkbox", name: name });
          if (on) c.setAttribute("checked", "checked");
          return el("label", null, [c, " " + label]);
        }
        var p1 = el("p", null, [inp("label", "名前（必須）", 18, r.label)]);
        if (kind === "年間イベント") {
          p1.appendChild(txt(" ")); p1.appendChild(inp("month", "月（必須）", 3, r.month, { inputmode: "numeric" }));
          p1.appendChild(txt(" ")); p1.appendChild(inp("day", "日付（書いたまま残します）", 16, r.day, { placeholder: "例: 11月15日前後の土日" }));
          f.appendChild(p1);
          f.appendChild(el("p", null, [chk("sellable", "販売可能性が高い", r.sellable), txt(" "), inp("note", "メモ", 24, r.note)]));
        } else {
          f.appendChild(p1);
          var p2 = el("p");
          d.axes.forEach(function (a) { p2.appendChild(inp(a.key, a.label + "（0〜" + a.max + "）", 3, r[a.key], { inputmode: "numeric" })); p2.appendChild(txt(" ")); });
          p2.appendChild(inp("priority", "優先順位", 3, r.priority, { inputmode: "numeric" }));
          f.appendChild(p2);
          f.appendChild(el("p", { "class": "np-sub", text: "総合点は自動で計算します（購買意欲×2 ＋ 写真親和性 ＋ 発生頻度×2、満点40）。3つそろわないと未設定のままです。" }));
          f.appendChild(el("p", null, [chk("sellable", "販売可能性が高い", r.sellable), txt(" "), chk("product_gap", "商品が作れていない", r.product_gap), txt(" "),
            inp("product_ideas", "商品例", 24, r.product_ideas), txt(" "), inp("note", "メモ", 18, r.note)]));
        }
        var bar = el("p", null, [el("button", { type: "submit", text: r.id ? "直す" : "足す" })]);
        f.appendChild(bar);
        function send(confirm) {
          var o = { kind: kind, id: r.id || "", confirm: confirm ? "1" : "" };
          ["label", "month", "day", "note", "priority", "product_ideas"].concat(d.axes.map(function (a) { return a.key; })).forEach(function (k) {
            if (f.elements[k]) o[k] = f.elements[k].value; });
          ["sellable", "product_gap"].forEach(function (k) { if (f.elements[k]) o[k] = f.elements[k].checked ? "1" : ""; });
          post("/api/settings/events", o).then(function (res) {
            if (res.similar) {
              if (window.confirm("似た名前があります: " + res.similar.map(function (x) { return x.label; }).join("、") + "\n別のものとして保存しますか？")) send(true);
              return;
            }
            viewEvents({ id: kind === "年間イベント" ? "np-ev-annual" : "np-ev-life", msg: "「" + f.elements.label.value + "」を保存しました" });
          }).catch(function (e) { bar.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
        }
        f.addEventListener("submit", function (ev) { ev.preventDefault(); send(false); });
        return f;
      }

      ["年間イベント", "ライフイベント"].forEach(function (kind) {
        var c = el("div", { "class": "np-card", id: kind === "年間イベント" ? "np-ev-annual" : "np-ev-life" });
        c.appendChild(el("h2", { text: kind }));
        var rows = d.rows.filter(function (r) { return r.kind === kind; });
        var head = kind === "年間イベント" ? ["名前", "月", "日付（原文）", "販売可能性", "枠", "状態", "出どころ", ""]
          : ["名前", "総合", "購買意欲", "写真", "頻度", "優先", "商品が作れていない", "枠", "状態", "出どころ", ""];
        var slot = el("div");
        c.appendChild(table(head, rows.map(function (r) {
          var act = el("td");
          if (d.can_edit) {
            var e1 = el("button", { type: "button", text: "直す" });
            e1.addEventListener("click", function () { while (slot.firstChild) slot.removeChild(slot.firstChild); slot.appendChild(form(kind, r)); slot.scrollIntoView({ block: "nearest" }); });
            var e2 = el("button", { type: "button", text: r.active ? "使わない" : "使う" });
            e2.addEventListener("click", function () {
              if (r.active && !window.confirm("「" + r.label + "」を使わないにします。機会カレンダーから外れます（既存の枠の記録は残ります）。よろしいですか")) return;
              post("/api/settings/events/active", { id: r.id, on: r.active ? "0" : "1" }).then(function () {
                viewEvents({ id: kind === "年間イベント" ? "np-ev-annual" : "np-ev-life", msg: "「" + r.label + "」を" + (r.active ? "使わないにしました" : "使うに戻しました") }); })
                .catch(function (e) { act.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
            });
            act.appendChild(e1); act.appendChild(txt(" ")); act.appendChild(e2);
          }
          var common = [el("td", { text: r.slots ? r.slots + " 件" : "—" }), el("td", { text: r.active ? "使う" : "使わない" }), el("td", { text: r.origin }), act];
          if (kind === "年間イベント") return el("tr", null, [el("td", { text: r.label }), el("td", { "class": "np-num", text: dash(r.month) }),
            el("td", { text: dash(r.day) }), el("td", { text: r.sellable ? "高い" : "—" })].concat(common));
          return el("tr", null, [el("td", { text: r.label }), el("td", { "class": "np-num", text: r.total === null ? "未設定" : String(r.total) }),
            el("td", { "class": "np-num", text: dash(r.gift_intent) }), el("td", { "class": "np-num", text: dash(r.photo_fit) }),
            el("td", { "class": "np-num", text: dash(r.frequency) }), el("td", { "class": "np-num", text: dash(r.priority) }),
            el("td", { text: r.product_gap ? "印あり" : "—" })].concat(common));
        })));
        c.appendChild(slot);
        if (d.can_edit) {
          var add = el("button", { type: "button", "class": "np-btn", text: kind + "を足す" });
          add.addEventListener("click", function () { while (slot.firstChild) slot.removeChild(slot.firstChild); slot.appendChild(form(kind, null)); });
          c.appendChild(el("p", { "class": "np-btnrow" }, [add]));
        }
        b.appendChild(c);
      });
      afterSave(after);
    }).catch(fail);
  }

  /** 標準タスクのひな形と係数（ADR-061）。版1は種データ。改訂は下書き → 使い始める。起こし済みの案件は変えない */
  function viewTemplates() {
    loading();
    api("/api/settings/templates").then(function (d) {
      var b = clear();
      setTitle("標準タスクのひな形・工数ポイント係数");
      b.appendChild(btnRow([navBtn("#/settings", "← 設定へ戻る", "back")]));
      b.appendChild(el("p", { "class": "np-sub", text: "案件を起こしたときに並ぶタスクの元です。改訂は「下書き」で作り、差を見てから使い始めます。使い始めた後に起こす案件から効き、起こし済みの案件のタスクは変わりません。" }));
      if (!d.can_edit) b.appendChild(el("p", { "class": "np-note", text: "変えられるのは、商品開発部・管理者・社長の業務ロールの人です。" }));
      b.appendChild(table(["開発タイプ", "使用中の版", "実作業", "実作業 h", "予備 h", "時間未定", "工数ポイント係数", "下書き", ""], d.flows.map(function (f) {
        var sm = f.summary;
        return el("tr", null, [el("td", { text: f.label }),
          el("td", { text: "版" + f.version + (f.version === 1 ? "（種データ）" : "") }),
          el("td", { "class": "np-num", text: f.has_template ? sm.n + " 行" : "標準タスク未定義" }),
          el("td", { "class": "np-num", text: f.has_template ? String(sm.work_hours) : "—" }),
          el("td", { "class": "np-num", text: String(sm.reserve_hours) }),
          el("td", { "class": "np-num", text: sm.unknown_rows + " 行" }),
          el("td", { text: f.effort_point === null ? "未確定" : String(f.effort_point) }),
          el("td", { text: f.draft ? "版" + f.draft + " を作成中" : "—" }),
          el("td", null, [navBtn("#/settings/templates/" + f.code, d.can_edit ? "開く・直す" : "開く", "sm")])]);
      })));
      b.appendChild(el("p", { "class": "np-sub", text: "合計は開発タイプごとの1本分です。予備時間（旧テンプレートの半分・1人分）はここでは変えません。" }));
    }).catch(fail);
  }

  function viewTemplate(code) {
    loading();
    var P = "/api/settings/templates/" + encodeURIComponent(code);
    api(P).then(function (d) {
      var b = clear(), f = d.flow;
      setTitle("ひな形: " + f.label);
      b.appendChild(btnRow([navBtn("#/settings/templates", "← ひな形の一覧へ戻る", "back")]));
      function err(node, e) { node.appendChild(el("span", { "class": "np-err", text: " " + e.message })); }
      function act(name, body, after) { return post(P + "/" + name, body || {}).then(after || function () { viewTemplate(code); }); }
      var rl = {}; d.roles.forEach(function (r) { rl[r.code] = r.label; });

      // 工数ポイント係数
      var ec = el("div", { "class": "np-card" });
      ec.appendChild(el("h2", { text: "工数ポイント係数" }));
      ec.appendChild(el("p", { text: "いまの値: " + (f.effort_point === null ? "未確定" : f.effort_point)
        + (f.effort_point_note ? "（" + f.effort_point_note + "）" : "") + (f.decided_at ? "　決めた人: " + f.decided_by + "・" + f.decided_at : "") }));
      if (d.can_edit) {
        var ef = el("form", { "class": "np-inline" });
        var ev = el("input", { name: "value", size: "5", "aria-label": "係数", value: f.effort_point === null ? "" : String(f.effort_point) });
        var er = el("input", { name: "reason", size: "28", "aria-label": "根拠", placeholder: "根拠（必須）" });
        [txt("係数 "), ev, er, el("button", { type: "submit", text: "決める" }), el("span", { "class": "np-sub", text: " 空欄で未確定に戻します" })].forEach(function (x) { ef.appendChild(x); });
        ef.addEventListener("submit", function (e) { e.preventDefault(); act("effort", { value: ev.value, reason: er.value }).catch(function (x) { err(ef, x); }); });
        ec.appendChild(ef);
      }
      b.appendChild(ec);

      function rowsTable(rows, editable) {
        return table(["#", "タスク", "担当の業務ロール", "標準 h", "種類", ""], rows.map(function (r) {
          var a = el("td");
          if (editable && r.kind !== "予備") {
            [["↑", "1"], ["↓", "0"]].forEach(function (m) {
              var bt = el("button", { type: "button", text: m[0], "aria-label": (m[1] === "1" ? "上へ" : "下へ") + "：" + r.title });
              bt.addEventListener("click", function () { act("row-move", { id: r.id, up: m[1] }).catch(function (x) { err(a, x); }); });
              a.appendChild(bt); a.appendChild(txt(" "));
            });
            var ed = el("button", { type: "button", text: "直す" });
            ed.addEventListener("click", function () { slot.textContent = ""; slot.appendChild(rowForm(r)); slot.scrollIntoView({ block: "nearest" }); });
            var dl = el("button", { type: "button", text: "消す" });
            dl.addEventListener("click", function () {
              if (!window.confirm("「" + r.title + "」を下書きから消します。よろしいですか")) return;
              act("row-delete", { id: r.id }).catch(function (x) { err(a, x); });
            });
            a.appendChild(ed); a.appendChild(txt(" ")); a.appendChild(dl);
          }
          return el("tr", null, [el("td", { "class": "np-num", text: String(r.seq) }), el("td", { text: r.title }),
            el("td", { text: rl[r.role] || r.role }), el("td", { "class": "np-num", text: r.standard_hours === null ? "未定" : String(r.standard_hours) }),
            el("td", { text: r.kind }), a]);
        }));
      }
      var slot = el("div");
      function rowForm(r) {
        r = r || {};
        var fm = el("form", { "class": "np-inline" });
        var ti = el("input", { name: "title", size: "28", "aria-label": "タスク名", value: r.title || "", placeholder: "タスク名" });
        var ro = el("select", { name: "role", "aria-label": "担当の業務ロール" });
        d.roles.forEach(function (x) { var o = el("option", { value: x.code, text: x.label }); if (x.code === r.role) o.setAttribute("selected", "selected"); ro.appendChild(o); });
        var hi = el("input", { name: "standard_hours", size: "5", "aria-label": "標準時間（h）", value: r.standard_hours === null || r.standard_hours === undefined ? "" : String(r.standard_hours), placeholder: "h" });
        [ti, ro, hi, el("button", { type: "submit", text: r.id ? "直す" : "足す" })].forEach(function (x) { fm.appendChild(x); });
        fm.addEventListener("submit", function (e) {
          e.preventDefault();
          act("row", { id: r.id || "", title: ti.value, role: ro.value, standard_hours: hi.value }).catch(function (x) { err(fm, x); });
        });
        return fm;
      }

      // 下書き
      var dc = el("div", { "class": "np-card" });
      if (d.draft) {
        var dr = d.draft, sm = dr.summary;
        dc.appendChild(el("h2", { text: "下書き 版" + dr.version + "（" + dr.created_by + "・" + dr.created_at + "）" }));
        dc.appendChild(el("p", { "class": "np-sub", text: "実作業 " + sm.n + " 行・" + sm.work_hours + " h" + (sm.unknown_rows ? "（時間未定 " + sm.unknown_rows + " 行。全行に入れるまで使い始められません）" : "") + "・予備 " + sm.reserve_hours + " h" }));
        var df = dr.diff;
        dc.appendChild(el("h3", { text: "いまの版（版" + d.current.version + "）との差" }));
        var ul = el("ul", { "class": "np-miss" });
        ul.appendChild(el("li", { text: "足した行: " + (df.added.length ? df.added.join("、") : "なし") }));
        ul.appendChild(el("li", { text: "消した行: " + (df.removed.length ? df.removed.join("、") : "なし") }));
        ul.appendChild(el("li", { text: "時間を変えた行: " + (df.changed.length ? df.changed.map(function (c) { return c.title + "（" + (c.before === null ? "未定" : c.before) + " → " + (c.after === null ? "未定" : c.after) + " h）"; }).join("、") : "なし") }));
        dc.appendChild(ul);
        dc.appendChild(table(["担当の業務ロール", "いまの版 h", "下書き h"], df.by_role.map(function (x) {
          return el("tr", null, [el("td", { text: x.role }), el("td", { "class": "np-num", text: String(x.before) }), el("td", { "class": "np-num", text: String(x.after) })]); })));
        dc.appendChild(el("h3", { text: "下書きの行" }));
        dc.appendChild(rowsTable(dr.rows, d.can_edit));
        dc.appendChild(slot);
        if (d.can_edit) {
          var add = el("button", { type: "button", "class": "np-btn", text: "行を足す" });
          add.addEventListener("click", function () { slot.textContent = ""; slot.appendChild(rowForm(null)); });
          var go1 = el("form", { "class": "np-inline" });
          var why = el("input", { name: "reason", size: "30", "aria-label": "改訂の理由", placeholder: "改訂の理由（必須）" });
          [why, el("button", { type: "submit", text: "この下書きを使い始める" })].forEach(function (x) { go1.appendChild(x); });
          go1.addEventListener("submit", function (e) {
            e.preventDefault();
            if (!window.confirm("版" + dr.version + " を使い始めます。これから起こす案件のタスクが、この版になります（起こし済みの案件は変わりません）。よろしいですか")) return;
            act("activate", { reason: why.value }).catch(function (x) { err(go1, x); });
          });
          var ds = el("button", { type: "button", text: "下書きを捨てる" });
          ds.addEventListener("click", function () {
            if (!window.confirm("下書き 版" + dr.version + " を捨てます。よろしいですか")) return;
            act("discard").catch(function (x) { err(dc, x); });
          });
          dc.appendChild(el("p", { "class": "np-btnrow" }, [add]));
          dc.appendChild(go1);
          dc.appendChild(el("p", { "class": "np-btnrow" }, [ds]));
        }
      } else {
        dc.appendChild(el("h2", { text: "改訂" }));
        dc.appendChild(el("p", { "class": "np-sub", text: d.current.rows.length ? "いまの版を写して下書きを作り、行を足す・直す・消す・並べ替えます。" : "この開発タイプには標準タスクがありません。空の下書きから作ります。" }));
        if (d.can_edit) {
          var nd = el("button", { type: "button", "class": "np-btn", text: "改訂版を作る（下書き）" });
          nd.addEventListener("click", function () { act("draft").catch(function (x) { err(dc, x); }); });
          dc.appendChild(el("p", { "class": "np-btnrow" }, [nd]));
        }
      }
      b.appendChild(dc);

      // 使用中の版
      var cc = el("div", { "class": "np-card" });
      cc.appendChild(el("h2", { text: "使用中の版（版" + d.current.version + (d.current.version === 1 ? "・種データ" : "") + "）" }));
      if (!d.current.rows.length) cc.appendChild(el("p", { "class": "np-note", text: "標準タスク未定義です。" }));
      else cc.appendChild(rowsTable(d.current.rows, false));
      if (d.can_edit && d.current.version > 1) {
        var rv = el("form", { "class": "np-inline" });
        var rw = el("input", { name: "reason", size: "28", "aria-label": "戻す理由", placeholder: "戻す理由（必須）" });
        [rw, el("button", { type: "submit", text: "ひとつ前の版に戻す" })].forEach(function (x) { rv.appendChild(x); });
        rv.addEventListener("submit", function (e) { e.preventDefault(); act("revert", { reason: rw.value }).catch(function (x) { err(rv, x); }); });
        cc.appendChild(rv);
      }
      b.appendChild(cc);
    }).catch(fail);
  }

  /** アイデアの一括登録（ADR-062）。貼り付け → 確認（保存しない）→ 登録。取り消しは手が入っていない行だけ */
  function viewIdeasImport() {
    loading();
    api("/api/settings/ideas-import").then(function (d) {
      var b = clear();
      setTitle("アイデアの一括登録");
      b.appendChild(btnRow([navBtn("#/settings", "← 設定へ戻る", "back"), navBtn("#/ideas", "アイデアの一覧を開く", "back")]));
      if (!d.can_edit) { b.appendChild(el("p", { "class": "np-note", text: "一括登録できるのは、商品開発部・管理者・社長の業務ロールの人です。" })); }
      var c = el("div", { "class": "np-card" });
      c.appendChild(el("h2", { text: "貼り付ける" }));
      c.appendChild(el("p", { "class": "np-sub", text: "表計算ソフトの範囲をそのまま貼り付けるか、CSV を貼り付けます。1行目は見出しにしてください（商品案名・概要・想定ターゲット・起票経路・テーマ。商品案名は必須）。1回 " + d.max_rows + " 行まで。お客さまの情報（名前・メールアドレス・電話番号）は入れないでください。" }));
      var ta = el("textarea", { rows: "10", "aria-label": "貼り付ける内容", placeholder: "商品案名\t概要\t想定ターゲット\t起票経路\n…" });
      c.appendChild(el("div", { "class": "np-field" }, [ta]));     // 幅いっぱい（.np-field textarea）
      var og = el("select", { "aria-label": "起票経路が空の行に入れる経路" });
      og.appendChild(el("option", { value: "", text: "空欄の行は止める（既定）" }));
      d.origins.forEach(function (o) { og.appendChild(el("option", { value: o.code, text: "空欄の行はすべて「" + o.label + "」にする" })); });
      var pv = el("button", { type: "button", text: "確認する（まだ登録しません）" });
      c.appendChild(el("p", { "class": "np-btnrow" }, [og, pv]));
      var out = el("div");
      c.appendChild(out);
      b.appendChild(c);

      pv.addEventListener("click", function () {
        out.textContent = "";
        post("/api/settings/ideas-import/preview", { text: ta.value, default_origin: og.value }).then(function (r) {
          var cnt = Object.keys(r.count).map(function (k) { return k + " " + r.count[k]; }).join("・");
          out.appendChild(el("p", { "class": r.done ? "np-warn" : "np-note", text: (r.done ? "この内容はもう登録済みです。" : "") + "確認の結果: " + cnt }));
          var boxes = {};
          var order = { "止まる": 0, "貼り付けた中で重複": 1, "既にある": 2, "似た案あり": 3, "入る": 4 };
          var rows = r.rows.slice().sort(function (a, z) { return (order[a.state] - order[z.state]) || (a.line - z.line); });
          out.appendChild(el("p", { "class": "np-sub", text: "止まった行・入れない行を先に並べています。" }));
          out.appendChild(table(["入れる", "行", "商品案名", "起票経路", "テーマ", "判定", "理由・似た案"], rows.map(function (x) {
            var td = el("td");
            if (x.state === "入る" || x.state === "似た案あり") {
              var cb = el("input", { type: "checkbox", "aria-label": x.line + "行目を入れる" });
              if (x.include) cb.setAttribute("checked", "checked");
              boxes[x.line] = cb; td.appendChild(cb);
            } else td.appendChild(txt("—"));
            var why = x.why.join("／") + (x.similar ? "似た案: " + x.similar.map(function (s) { return s.title + "（" + s.id + "）"; }).join("、") : "")
              + (x.origin_note ? "　" + x.origin_note : "");
            return el("tr", null, [td, el("td", { "class": "np-num", text: String(x.line) }), el("td", { text: x.title || "（空）" }),
              el("td", { text: dash(x.origin || (x.origin_code ? "（" + x.origin_code + "）" : "")) }), el("td", { text: dash(x.theme) }),
              el("td", { text: x.state }), el("td", { text: why })]);
          })));
          if (!d.can_edit || r.done) return;
          var go1 = el("button", { type: "button", "class": "np-btn", text: "印を付けた行を登録する" });
          go1.addEventListener("click", function () {
            var ex = Object.keys(boxes).filter(function (k) { return !boxes[k].checked; });
            var n = Object.keys(boxes).length - ex.length;
            if (!n) { window.alert("登録する行がありません"); return; }
            if (!window.confirm(n + " 件を登録します。よろしいですか")) return;
            post("/api/settings/ideas-import/register", { text: ta.value, default_origin: og.value, token: r.token, exclude: ex.join(",") })
              .then(function (res) { window.alert(res.n + " 件を登録しました"); viewIdeasImport(); })
              .catch(function (e) { out.appendChild(el("p", { "class": "np-err", text: e.message })); });
          });
          out.appendChild(el("p", { "class": "np-btnrow" }, [go1]));
        }).catch(function (e) { out.appendChild(el("p", { "class": "np-err", text: e.message })); });
      });

      var hc = el("div", { "class": "np-card" });
      hc.appendChild(el("h2", { text: "これまでの一括登録" }));
      if (!d.batches.length) hc.appendChild(el("p", { "class": "np-note", text: "まだありません。" }));
      else hc.appendChild(table(["いつ", "誰が", "件数", ""], d.batches.map(function (x) {
        var td = el("td");
        if (d.can_edit) {
          var u = el("button", { type: "button", text: "取り消す" });
          u.addEventListener("click", function () {
            if (!window.confirm("この一括登録を取り消します。手が入っていない行だけを消し、採点・編集済みの行や案件につながった行は残します。よろしいですか")) return;
            post("/api/settings/ideas-import/undo", { token: x.token }).then(function (r) {
              window.alert("消した " + r.removed + " 件" + (r.kept.length ? "／残した " + r.kept.length + " 件: " + r.kept.map(function (k) { return k.title + "（" + k.why + "）"; }).join("、") : ""));
              viewIdeasImport();
            }).catch(function (e) { td.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
          });
          td.appendChild(u);
        }
        return el("tr", null, [el("td", { text: x.at }), el("td", { text: dash(x.by) }), el("td", { "class": "np-num", text: x.n + " 件" }), td]);
      })));
      b.appendChild(hc);
    }).catch(fail);
  }

  /** 販売計画シミュレーション（F-14・ADR-072）。過去の新商品の実績の分布から、本数ごとの年間売上の幅を出す。
   *  「目標 ÷ 本数」を単独で出さない（中央値の何倍が要るかを並べる）。工数の上限を超える案は、超える月を語で返す */
  function viewSimulate() {
    loading();
    var q = hashQuery();
    var qs = ["fy", "target", "cands", "ep"].filter(function (k) { return q.get(k); })
      .map(function (k) { return k + "=" + encodeURIComponent(q.get(k)); });
    api("/api/simulate" + (qs.length ? "?" + qs.join("&") : "")).then(function (d) {
      var b = clear();
      setTitle("販売計画シミュレーション", "／ " + d.fy + "年度");
      b.appendChild(btnRow([navBtn("#/plan", "← プランへ戻る", "back")]));
      b.appendChild(el("p", { "class": "np-sub", text: "新商品を年間に何本出せば、売上目標に届くかを試算します。案を並べて比べ、社長が確定します。目標は当面この画面で入れます（経営管理の計画を読む口ができたら切り替えます）。" }));

      // 入力
      var f = el("form", { "class": "np-inline" });
      function inp(name, label, size, val, ph) {
        return el("label", null, [label + " ", el("input", { name: name, size: size, value: val === null || val === undefined ? "" : String(val), placeholder: ph || "" })]);
      }
      [inp("fy", "年度", 5, d.fy), inp("target", "新商品の売上目標（円・税込）", 12, d.target_yen, "例 12000000"),
       inp("cands", "比べる本数", 14, d.scenarios.map(function (x) { return x.n; }).join(","), "24,36,48,52"),
       inp("ep", "1本あたりの工数ポイント", 5, d.effort_point, ""), el("button", { type: "submit", text: "試算する" })]
        .forEach(function (x) { f.appendChild(x); });
      f.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var p = ["fy", "target", "cands", "ep"].map(function (k) { var v = f.elements[k].value.trim(); return v ? k + "=" + encodeURIComponent(v) : null; }).filter(Boolean);
        location.hash = "#/simulate" + (p.length ? "?" + p.join("&") : "");
      });
      var ic = el("div", { "class": "np-card" });
      ic.appendChild(el("h2", { text: "前提" }));
      ic.appendChild(f);
      ic.appendChild(el("p", { "class": "np-sub", text: "1本あたりの工数ポイントの既定は、開発タイプの係数の平均（" + dash(d.effort_point_default) + "）です。月の枠と工数の上下限は設定ページの値、連休月の枠はカレンダーの営業日から決まります。" }));
      b.appendChild(ic);

      // 実績の分布
      var ds = d.distribution, dc = el("div", { "class": "np-card" });
      dc.appendChild(el("h2", { text: "過去の新商品の実績（発売から12か月）" }));
      if (!ds.n) dc.appendChild(el("p", { "class": "np-warn", text: ds.why }));
      else {
        dc.appendChild(table(["使った商品", "中央値", "真ん中の半分", "最大", "0円", "売上の8割を占める商品"], [el("tr", null, [
          el("td", { text: ds.range }), el("td", { "class": "np-num", text: yen(ds.median) }),
          el("td", { "class": "np-num", text: yen(ds.p25) + " 〜 " + yen(ds.p75) }), el("td", { "class": "np-num", text: yen(ds.max) }),
          el("td", { "class": "np-num", text: ds.zero + " 件（" + Math.round(ds.zero_rate * 1000) / 10 + "%）" }),
          el("td", { "class": "np-num", text: "上位 " + ds.top80 + " 商品" })])]));
        dc.appendChild(el("p", { "class": "np-sub", text: "売れ方は一部の商品に偏っています。平均で目標を置くと、ほとんどの商品が届かない分布です。最近発売して12か月そろわない " + ds.incomplete + " 商品は入れていません。" }));
      }
      b.appendChild(dc);

      // 案の比較
      var sc = el("div", { "class": "np-card" });
      sc.appendChild(el("h2", { text: "本数ごとの見込み" }));
      sc.appendChild(el("p", { "class": "np-sub", text: d.method + (d.scenarios[0] && d.scenarios[0].mix && d.scenarios[0].mix.note ? "　" + d.scenarios[0].mix.note + "。単価帯は、過去の表に単価が無いので出していません。" : "") }));
      var head = ["年間の本数", "構成（うちわ以外／うちわ）", "年間売上の見込み（下振れ／真ん中／上振れ）", "0円見込み"];
      if (d.target_yen) head = head.concat(["目標に届く見込み", "1本あたりに要る売上"]);
      head = head.concat(["工数と枠", ""]);
      sc.appendChild(table(head, d.scenarios.map(function (x) {
        var cells = [el("td", { "class": "np-num", text: x.n + " 本" }),
          el("td", { "class": "np-num", text: x.mix && x.mix.other !== null ? x.mix.other + "／" + x.mix.uchiwa + " 本" : "—" }),
          el("td", { "class": "np-num", text: x.p50 === undefined ? "—" : yen(x.p10) + "／" + yen(x.p50) + "／" + yen(x.p90) }),
          el("td", { "class": "np-num", text: x.zero_expected === undefined ? "—" : "約 " + x.zero_expected + " 本" })];
        if (d.target_yen) cells = cells.concat([el("td", { "class": "np-num", text: x.reach_rate === undefined ? "—" : x.reach_rate + "%" }),
          el("td", { "class": "np-num", text: x.need_each === undefined ? "—" : yen(x.need_each) + "（中央値の " + x.need_x_median + " 倍）" })]);
        var act = el("td");
        if (d.can_confirm && x.feasible) {
          var bt = el("button", { type: "button", text: "この案で確定する" });
          bt.addEventListener("click", function () {
            var why = window.prompt(d.fy + "年度を " + x.n + " 本で確定します。理由（なぜこの本数か・必須）", "");
            if (!why) return;
            post("/api/simulate/confirm", { fy: d.fy, n: x.n, target: d.target_yen || "", ep: d.effort_point === null ? "" : d.effort_point, note: why })
              .then(function () { viewSimulate(); }).catch(function (e) { act.appendChild(el("span", { "class": "np-err", text: " " + e.message })); });
          });
          act.appendChild(bt);
        }
        cells = cells.concat([el("td", { text: x.feasible ? "収まる" : (x.why_not || []).join("／") }), act]);
        return el("tr", null, cells);
      })));
      if (!d.can_confirm) sc.appendChild(el("p", { "class": "np-sub", text: "確定は社長の業務ロールの人が行います。" }));
      b.appendChild(sc);

      // 月ごとの配置（案ごとにたたむ）
      d.scenarios.forEach(function (x) {
        var det = el("details", { "class": "np-fold" });
        det.appendChild(el("summary", { text: x.n + " 本のときの月ごとの配置" }));
        var c = el("div", { "class": "np-card" }), pl = x.placement;
        if (pl.note) c.appendChild(el("p", { "class": "np-sub", text: pl.note }));
        c.appendChild(table(["月", "本数", "枠", "工数ポイント", "工数（下限 " + dash(pl.effort_min) + "〜上限 " + dash(pl.effort_max) + "）"], pl.months.map(function (m) {
          return el("tr", null, [el("td", { text: m.month + (m.holiday ? "（連休月）" : "") }), el("td", { "class": "np-num", text: m.n + " 本" }),
            el("td", { "class": "np-num", text: m.cap === null ? "未設定" : m.cap + " 本" }), el("td", { "class": "np-num", text: dash(m.effort) }),
            el("td", { text: m.state })]);
        })));
        if (pl.unplaced) c.appendChild(el("p", { "class": "np-warn", text: "枠に入りきらない本数: " + pl.unplaced + " 本（年間の枠は " + pl.capacity + " 本）" }));
        // 部門ごと（FR-133）。見込みの真ん中を、過去の新商品の売れ方の割合で配る
        if (x.by_dept && x.by_dept.length) {
          c.appendChild(el("h3", { text: "部門ごとの見込み（真ん中 " + yen(x.p50) + " を、過去の新商品の売れ方の割合で配る）" }));
          c.appendChild(table(["部門", "割合", "見込み"], x.by_dept.map(function (dd) {
            return el("tr", null, [el("td", { text: dd.label + (dd.note ? "（" + dd.note + "）" : "") }),
              el("td", { "class": "np-num", text: Math.round(dd.share * 1000) / 10 + "%" }), el("td", { "class": "np-num", text: yen(dd.yen) })]);
          })));
        }
        det.appendChild(c);
        b.appendChild(det);
      });

      // 確定した版
      var vc = el("div", { "class": "np-card" });
      vc.appendChild(el("h2", { text: "確定した販売計画（" + d.fy + "年度）" }));
      if (!d.versions.length) vc.appendChild(el("p", { "class": "np-note", text: "まだ確定していません。" }));
      else vc.appendChild(table(["確定日時", "本数", "目標", "見込み（真ん中）", "使った実績", "理由", "状態"], d.versions.map(function (v) {
        return el("tr", null, [el("td", { text: v.decided_at + " " + v.decided_by }), el("td", { "class": "np-num", text: v.params.n_releases + " 本" }),
          el("td", { "class": "np-num", text: v.params.target_yen ? yen(v.params.target_yen) : "未入力" }),
          el("td", { "class": "np-num", text: v.result.p50 === undefined ? "—" : yen(v.result.p50) }),
          el("td", { text: v.data_range }), el("td", { text: dash(v.note) }), el("td", { text: v.state })]);
      })));
      b.appendChild(vc);
    }).catch(fail);
  }

  function viewManual() {
    loading();
    api("/api/manual").then(function (d) {
      var b = clear();
      setTitle("操作マニュアル");
      b.appendChild(btnRow([navBtn("#/settings", "← 設定へ戻る", "back")]));
      b.appendChild(mdRender(d.body_md));
    }).catch(fail);
  }

  // ══════════════════════════════════════════════════════
  // まだ作っていない画面。**「未実装」と正直に書き、何段で入るかを言う。**
  // ══════════════════════════════════════════════════════
  var NOT_YET = {
    "#/cost": ["原価・調達（案件をまたいだ一覧）", "第3段の後半", "相見積の候補と試算原価は、各案件の画面の「原価・調達」で入れます。ここに案件をまたいだ一覧（締切が近い順など）を作る予定です。外注先・仕入先・原材料の一覧は CIP が持ちます。"]
  };

  function viewNotYet(path, entry) {
    var b = clear();
    var x = NOT_YET[path] || [entry.label, "—", ""];
    if (path === "#/cost") { setTitle("原価・調達"); b.appendChild(salesSubnav("#/cost")); }
    b.appendChild(el("p", { "class": "np-warn",
      text: "未実装です（" + x[1] + "で作ります）。" }));
    if (x[2]) b.appendChild(el("p", { "class": "np-note", text: x[2] }));
    b.appendChild(el("p", { "class": "np-note", text: "URL: " + path }));
  }

  // ── 振り分け ────────────────────────────────────────
  function go() {
    var path = hashPath();
    var entry = match(path);
    markCurrent(entry.view);
    setTitle(entry.label);

    var m = /^#\/projects\/([A-Za-z0-9_-]+)$/.exec(path);
    var mi = /^#\/ideas\/([A-Za-z0-9_-]+)$/.exec(path);
    var ma = /^#\/automation\/([A-Za-z0-9_-]+)$/.exec(path);
    if (path === "#/") return viewHome();
    if (m) return viewProject(m[1]);
    if (mi) return viewIdea(mi[1]);
    if (path === "#/automation") return viewAutomation();
    if (path === "#/plan") return viewPlan();
    if (ma) return viewAutomationOne(ma[1]);
    if (path === "#/ideas") return viewIdeas();
    if (path === "#/projects") return viewProjects();
    if (path === "#/tasks") return viewTasks();
    if (path === "#/gates") { return viewGates(); }
    if (path === "#/sales") return viewSales();
    if (path === "#/cost") return viewCost();
    if (path === "#/abc") return viewAbc();
    if (path === "#/trends") return viewTrends();
    if (path === "#/opportunities") return viewOpportunities();
    if (path === "#/reports") return viewReports();
    if (path === "#/settings") return viewSettings();
    if (path === "#/simulate") return viewSimulate();
    if (path === "#/manual") return viewManual();
    if (path === "#/settings/events") return viewEvents();
    if (path === "#/settings/templates") return viewTemplates();
    if (path === "#/settings/ideas-import") return viewIdeasImport();
    var mt = /^#\/settings\/templates\/([a-z]+)$/.exec(path);
    if (mt) return viewTemplate(mt[1]);
    return viewNotYet(path, entry);
  }

  window.addEventListener("hashchange", go);

  // 初回。**hash が無いときは**、その人の「最初に開く画面」へ（設定・ADR-057）。
  // location.replace なので履歴に余計な1件を残さない。値はサーバーが許可リストで倒したもの
  var START = document.documentElement.getAttribute("data-np-start") || "#/";
  if (!location.hash && /^#\/[a-z]*$/.test(START) && START !== "#/") location.replace(START);
  else go();
})();
