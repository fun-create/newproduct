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
                "#/opportunities": "#/plan",
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
    return t;
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
        if (m.link) c.appendChild(el("p", null, [el("a", { href: m.link, text: "開く" })]));
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
        b.appendChild(table(["発売予定日", "商品（分類）", "ステージ", ""],
          d.upcoming.map(function (u) {
            return el("tr", null, [
              el("td", { text: dash(u.launch_date) }),
              el("td", { text: u.product }),
              el("td", { text: u.stage }),
              el("td", null, [el("a", { href: "#/projects/" + u.id, text: "カルテ" })])
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
          el("td", { text: "**標準タスク未定義。**この開発タイプはタスク一覧が空になります。" })])
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
        b.appendChild(el("p", { "class": "np-note" }, [
          "本来は ",
          el("a", { href: "#/plan", text: "年間プラン" }),
          " の枠から「案件にする」で起こします（発売の2か月前のタスク設定期限が出ます）。"
          + "枠に無いものは、下のフォームから直接起こせます。" ]));
      } else {
        b.appendChild(table(
          ["ステージ", "ゲート", "次のゲート", "発売予定日", "商品（分類・サイズ）",
           "開発タイプ", "売上計上", "工数ポイント", "担当", "欠けているもの",
           "正本", "試算原価"],
          d.rows.map(function (r) {
            return el("tr", null, [
              el("td", { text: r.stage }),
              el("td", null, [gateChips(r.gates)]),
              el("td", { text: r.next_gate }),
              el("td", { text: dash(r.launch_date) }),
              el("td", null, [el("a", { href: "#/projects/" + r.id, text: r.product })]),
              el("td", { text: r.flow_label }),
              el("td", { text: r.revenue }),
              el("td", { "class": "np-num",
                text: r.effort_point === null ? "未確定" : String(r.effort_point) }),
              el("td", { text: dash(r.owner) }),
              el("td", { "class": "np-num" }, [
                el("a", { href: "#/projects/" + r.id, text: String(r.missing_n) + " 件" })]),
              // R-2。**移行期間の正本を列で出す**
              el("td", { text: r.source_of_truth === "app" ? "アプリ" : "Drive（編集不可）" }),
              el("td", { text: "未実装（第3段）" })
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
          msg.textContent = "起こしました。**この開発タイプには標準タスクがありません**"
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
        setTitle("案件 " + d.id, "／ " + h.product);

        // A. ヘッダ（常時固定）。**商品名は出さない**（N-6-2）
        var head = el("div", { "class": "np-card" });
        head.appendChild(el("h2", { text: "A. " + h.product }));
        head.appendChild(table(["項目", "値"], [
          row("社内呼称", dash(h.internal_name)),
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
              el("a", { href: "#np-sec-" + m.goto, text: "→ " + m.goto + "節へ" })
            ]);
            if (m.stage) li.appendChild(el("span", { "class": "np-stage",
              text: "（" + m.stage + "で自動化。いまは人が確認した記録で判定します）" }));
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

        // バリエーション展開（F-4-5）。**40本の複製を作らない**
        var v = el("div", { "class": "np-card" });
        v.appendChild(el("h2", { text: "バリエーション展開" }));
        if (!d.variants.length) v.appendChild(el("p", { "class": "np-note", text: "ありません。" }));
        else v.appendChild(table(["本体モデル", "対応状況", "発売日", "Seisan商品コード"],
          d.variants.map(function (x) {
            return el("tr", null, [el("td", { text: x.label }), el("td", { text: x.state }),
              el("td", { text: dash(x.launch_date) }), el("td", { text: dash(x.product_code) })]);
          })));
        b.appendChild(v);

        // タスク
        var tv = el("div", { "class": "np-card" });
        tv.appendChild(el("h2", { text: "タスク（" + d.tasks.length + "件）" }));
        if (!d.template_defined) {
          tv.appendChild(el("p", { "class": "np-warn",
            text: "**標準タスク未定義。**この開発タイプ（ページリニューアル）には標準タスクが"
              + "存在しません。178行の種データに1行もなく、発明もしていません。"
              + "そのため、案件を作ってもタスク一覧は空になります。"
              + "定義は商品開発部の未着手事項です（全体設計書 第11章 ⑪）。" }));
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
            text: "標準h は**予備時間を含まない実作業h**です（テンプレート由来）。" }));
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
      }).catch(fail);

    function row(k, v) {
      return el("tr", null, [el("th", { text: k }), el("td", { text: v })]);
    }
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
        text: "あなたはこのゲートを判定できません。**承認資格はアプリ権限（admin/user）ではなく業務ロールで決まります。**" }));
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
    "week": "今週", "none": "期限なし", "all": "すべて"
  };
  var TAB_LABEL = { project: "案件タスク", work: "案件外の仕事", request: "他部署への依頼" };

  function viewTasks() {
    loading();
    var q = hashQuery();
    var when = q.get("when") || "overdue+today";
    var tab = q.get("tab") || "project";
    api("/api/tasks?when=" + encodeURIComponent(when) + "&tab=" + encodeURIComponent(tab))
      .then(function (d) {
        var b = clear();
        setTitle("タスク", "／ " + (WHEN_LABEL[when] || when));

        // タブ
        var tabs = el("div", { "class": "np-filters", role: "tablist" });
        Object.keys(TAB_LABEL).forEach(function (k) {
          tabs.appendChild(el("a", {
            href: "#/tasks?when=" + encodeURIComponent(when) + "&tab=" + k,
            text: TAB_LABEL[k], "aria-current": k === tab ? "true" : null
          }));
        });
        b.appendChild(tabs);

        // 期間フィルタ。**「期限なし」を常設ボタンに**
        var fl = el("div", { "class": "np-filters" });
        ["overdue+today", "overdue", "today", "+1", "+2", "+3", "+4", "+5", "+6",
         "week", "none", "all"].forEach(function (k) {
          var n = d.counts[k];
          fl.appendChild(el("a", {
            href: "#/tasks?when=" + encodeURIComponent(k) + "&tab=" + tab,
            text: WHEN_LABEL[k] + (n === undefined ? "" : " " + n),
            "aria-current": k === when ? "true" : null
          }));
        });
        b.appendChild(fl);
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
              el("td", null, [statusForm(t)]),
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
        d.notes.forEach(function (n) { b.appendChild(el("p", { "class": "np-note", text: n })); });

        // **帯に出していない画面への入口**（帯は7つまで）。
        // 自動化依頼は「この作業をやらなくて済ませたい」なので、タスクの下に置く
        b.appendChild(el("p", { "class": "np-note" }, [
          "手でやっている作業を自動化したいときは ",
          el("a", { href: "#/automation", text: "自動化依頼" }),
          " へ。作業名だけで出せます（そのあと8つの質問で要件にします）。" ]));

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
          text: "この表は **" + tt.caption + "** です。"
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
          text: "⑦ページリニューアルはこの表に出ません。**標準タスクが1行も定義されていない**ためです。" }));
      }).catch(fail);
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
        var head = ["商品（分類）", "開発タイプ", "次の判定", "誰の番か"]
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
        text: "`対象外` は放置ではなく設計です。④ニューモデル追加は G2 → G5 の簡易フローで、"
          + "G1・G3・G4 を通しません（F-6-4）。" }));
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
          text: "アイデアがありません。移行は tools/import_ideas.py で流します。" }));
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
    var box = el("details", { "class": "np-card", open: "open" });
    box.appendChild(el("summary", { text: "アイデアを起票する（4項目＋起票経路だけ）" }));
    box.appendChild(el("p", { "class": "np-note",
      text: "起票に要るのは 商品案名・概要・想定ターゲット・起票経路 の4つだけです。デザイン自由度・生産方法・参考URL・エリアは採点のときに足します（F-1-3）。" }));
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
        text: "版の違う点数を合算していません。v1 は移行したシートの点そのもの（再採点していません）、v2 は新しい軸です。並べて見比べるためのものです（F-1-10）。" }));

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
        ["ランクの決め方", s.rank_basis === "percentile" ? "テーマ内の百分位（F-1-9）" : "シートに書かれていた絶対点の閾値"],
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
      text: "総合点 =（①共通点 0〜70 ＋ ②テーマ適合点 0〜30）× 生産方法の減点係数。ランクはテーマ内の百分位で決まります（F-1-5・F-1-6・F-1-9）。" }));
    if (!d.v2_ready) {
      box.appendChild(el("p", { "class": "np-warn", text: "まだ採点できません。足りないものがあります:" }));
      var ul = el("ul", { "class": "np-miss" });
      d.v2_blockers.forEach(function (x) { ul.appendChild(el("li", { text: x })); });
      box.appendChild(ul);
      box.appendChild(el("p", { "class": "np-note",
        text: "足りない値を 1.0 や 0 で代用しません。作れない案が上位に来るのを防ぐのが v2 の目的です（N-10）。" }));
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
    c.appendChild(el("h3", { text: "AI採点（F-1-11）" }));
    c.appendChild(el("p", { "class": a.enabled ? "np-note" : "np-warn",
      text: a.enabled ? "有効です。" : a.reason }));
    c.appendChild(el("p", { "class": "np-note",
      text: "採点結果には rubric版・実行日時・モデル名を残します（" + a.records.kept.join("／") + "）。" }));
    c.appendChild(el("p", { "class": "np-note",
      text: "AI が付けた点は現在 " + a.ai_scored + " 件です。" }));
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
        text: "**承認済みは年度に1つだけ**です（F-3-4）。承認済みの版は編集できません。"
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
      if (cur.state === "策定中") {
        var ap = el("button", { type: "button", text: "この版を承認する" });
        ap.addEventListener("click", function () {
          post("/api/plan/versions/" + encodeURIComponent(cur.id) + "/approve", {})
            .then(function () { go(); })
            .catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
        });
        acts.appendChild(ap);
      } else {
        var rv = el("button", { type: "button", text: "改訂版を作る（枠ごと写す）" });
        rv.addEventListener("click", function () {
          post("/api/plan/versions/" + encodeURIComponent(cur.id) + "/revise", {})
            .then(function () { go(); })
            .catch(function (e) { msg.textContent = "できませんでした: " + e.message; });
        });
        acts.appendChild(rv);
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
      text: "**発売月だけで作れます。**日は決まってから入れます。"
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
        text: "手でやっている作業のうち、**自動化したいもの**を集める画面です。"
          + "出したあと、8つの質問に答えると要件になります。"
          + "**このアプリが実装するわけではありません。**要件を書き出して、作る人へ渡します。" }));

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
              el("td", { text: r.in_template ? "あり" : "**無し**" }),
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
      text: "**作業名だけで出して構いません。**細かいことは、このあと質問でうかがいます。" }));
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
        : "**標準タスク178行のどれにも当たりません。**「やっていない」のではなく、表のほうが現場に追いついていないという意味です。";
      b.appendChild(t);
      if (r.note) b.appendChild(el("p", { "class": "np-sub", text: r.note }));

      b.appendChild(autoEffort(d));
      b.appendChild(el("h2", { text: "質問（答えが揃うと要件になります）" }));
      d.questions.forEach(function (q) { b.appendChild(autoQ(id, q)); });
      b.appendChild(autoStage(d));
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
      text: "**答えが揃う前に「要件確定」から先へは進めません。**"
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
    box.appendChild(el("p", null, [
      el("a", { href: "/api/automation/" + encodeURIComponent(r.id) + "/requirement",
                text: "要件として書き出す（作る人へ渡す）" })]));
    return box;
  }

  // ══════════════════════════════════════════════════════
  // まだ作っていない画面。**「未実装」と正直に書き、何段で入るかを言う。**
  // ══════════════════════════════════════════════════════
  var NOT_YET = {
    "#/cost": ["原価・調達", "第3段", "調達先・資材・為替・試算原価と、seisan への商品マスタ登録ファイル。"],
    "#/settings": ["設定", "—", "マスタ・利用者・データの出どころ・監査ログ。第2段では監査の記録だけ取っています。"]
  };

  function viewNotYet(path, entry) {
    var b = clear();
    var x = NOT_YET[path] || [entry.label, "—", ""];
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
    return viewNotYet(path, entry);
  }

  window.addEventListener("hashchange", go);

  // 初回。**hash が無いときは書き換えない**（履歴に余計な1件を残さない）
  go();
})();
