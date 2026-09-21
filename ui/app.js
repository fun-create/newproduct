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
  var ALIAS = { "#/gates": "#/projects", "#/review": "#/projects",
                "#/opportunities": "#/plan" };

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
        }
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
          text: "案件がまだありません。年間プラン（第1段・未実装）の枠から作る設計ですが、いまは下のフォームから起こせます。" }));
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
                el("td", { "class": "np-num", text: x.hours + "h" }),
                el("td", { "class": "np-num", text: String(x.n) }),
                el("td", { text: d.load.limit_label })]));
            });
            m.external.forEach(function (x) {
              lr.push(el("tr", null, [el("td", { text: m.month }),
                el("td", { text: x.role_label + "（他部署・試算対象外）" }),
                el("td", { "class": "np-num", text: x.hours + "h" }),
                el("td", { "class": "np-num", text: String(x.n) }),
                el("td", { text: "—" })]));
            });
          });
          b.appendChild(table(["月（タスク実施月）", "ロール", "工数", "件数", "上限"], lr));
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
          "AI削減可能h（6フロー合算）"],
          tt.by_role.map(function (r) {
            return el("tr", null, [
              el("td", { text: (r.role_label || "—") + (r.external ? "（他部署・試算対象外）" : "") }),
              el("td", { "class": "np-num", text: String(r.n) }),
              el("td", { "class": "np-num", text: r.hours + "h" }),
              el("td", { "class": "np-num", text: r.ai_hours + "h" })]);
          })));
        b.appendChild(table(["開発タイプ", "タスク数", "実作業h（1本あたり）"],
          tt.by_flow.map(function (r) {
            return el("tr", null, [el("td", { text: r.label }),
              el("td", { "class": "np-num", text: String(r.n) }),
              el("td", { "class": "np-num", text: r.hours + "h" })]);
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
  // まだ作っていない画面。**「未実装」と正直に書き、何段で入るかを言う。**
  // ══════════════════════════════════════════════════════
  var NOT_YET = {
    "#/ideas": ["アイデア台帳", "第1段", "アイデア・採点（rubric v2）・機会カレンダー・年間プランの枠。"],
    "#/plan": ["プラン", "第1段", "年間プランの枠とコンセプト在庫月数。販売計画シミュレーション（F-14）は keiei の plan が0行のため、入力の無い状態から立ち上がる設計にします。"],
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
    if (path === "#/") return viewHome();
    if (m) return viewProject(m[1]);
    if (path === "#/projects") return viewProjects();
    if (path === "#/tasks") return viewTasks();
    if (path === "#/gates") { return viewGates(); }
    return viewNotYet(path, entry);
  }

  window.addEventListener("hashchange", go);

  // 初回。**hash が無いときは書き換えない**（履歴に余計な1件を残さない）
  go();
})();
