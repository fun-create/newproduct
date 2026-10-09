#!/usr/bin/env python3
"""
ログインが要る画面のAPIを、**実際に HTTP で**通す検査。

    python3 tests/e2e_http.py

**外部通信はしない。**127.0.0.1 だけ。
本番の `config/` にも `data/` にも触らない。アプリ一式を一時ディレクトリへ
写し、**そこの DB と config** を使う。本番のセッション台帳を書き換えない。

`selfcheck.py` は未ログインの既定拒否しか見られない（利用者の資格を持たないため）。
ここでは `H.user()` だけを差し替えて、**その先の振り分けと JSON を実際に動かす**。
差し替えるのは「誰としてログインしているか」だけで、権限の判定
（`gate_def.approver_role` × `role_member`）は本物のまま通す。
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent

OK, NG = "OK", "NG"
_bad = []


def note(ok: bool, what: str, detail: str = ""):
    s = OK if ok else NG
    if not ok:
        _bad.append(what)
    print(f"[{s}] {what}" + (f" — {detail}" if detail else ""), flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Cl:
    def __init__(self, port):
        self.port = port

    def req(self, method, path, form=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            body, hdr = None, {}
            if form is not None:
                body = urllib.parse.urlencode(form)
                hdr["Content-Type"] = "application/x-www-form-urlencoded"
            c.request(method, path, body, hdr)
            r = c.getresponse()
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
        finally:
            c.close()

    def get(self, p):
        return self.req("GET", p)

    def post(self, p, form):
        return self.req("POST", p, form)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="np-e2e-"))
    app = tmp / "app_copy"
    shutil.copytree(SRC, app, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "data", "config", "logs"))
    (app / "config").mkdir()
    (app / "data").mkdir()
    port = free_port()
    os.environ["NEWPRODUCT_DB"] = str(app / "data" / "e2e.db")
    os.environ["NEWPRODUCT_TODAY"] = "2026-09-21"
    os.environ["NEWPRODUCT_PORT"] = str(port)

    sys.path.insert(0, str(app))
    for m in list(sys.modules):
        if m == "server" or m == "auth" or m.startswith("app."):
            del sys.modules[m]
    spec = importlib.util.spec_from_file_location("server", app / "server.py")
    server = importlib.util.module_from_spec(spec)
    sys.modules["server"] = server
    spec.loader.exec_module(server)

    from app import seed, store
    counts = seed.run()
    note(counts["task_template"] == 178, "種データ 178行", str(counts))

    # 業務ロールを割り当てる。**アプリ権限ではない**（§10-2 ②）
    store.ex("INSERT OR REPLACE INTO role_member (role_code,user_id,granted_at) "
             "VALUES ('president','shacho',?)", (store.now_s(),))
    store.ex("INSERT OR REPLACE INTO role_member (role_code,user_id,granted_at) "
             "VALUES ('admin','kanri',?)", (store.now_s(),))
    store.conn().commit()

    who = {"user_id": "kanri", "name": "管理者テスト", "role": "admin"}
    server.H.user = lambda self: who          # 差し替えるのはここだけ

    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", port), server.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cl = Cl(port)

    try:
        st, d = cl.get("/api/me")
        note(st == 200 and d["business_roles"] == ["admin"],
             "/api/me が業務ロールを返す", f"{st} {d}")

        st, d = cl.get("/api/meta")
        note(st == 200 and len(d["gates"]) == 7 and len(d["flow_types"]) == 7
             and len(d["reasons"]["hold"]) == 5,
             "/api/meta が7ゲート・7フロー・5保留理由", str(st))

        st, d = cl.get("/api/dashboard")
        note(st == 200 and set(d["stuck"]) == {"overdue", "gate_waiting", "today"},
             "/api/dashboard の1段目が「いま詰まっているもの」", str(st))
        note(all(m["value"] is None and m["state"] in ("未計測", "対象なし") for m in d["monthly"]),
             "/api/dashboard の2段目が「未計測」（0 ではない）",
             str([m["state"] for m in d["monthly"]]))

        st, d = cl.post("/api/projects", {
            "internal_name": "E2E検査", "flow_type": "meire",
            "launch_date": "2026-11-27", "occasion": "七五三(11/15)",
            "area": "推し活", "summary": "検査", "owner": "kanri",
            "cat1": "アクリル製品", "cat2": "アクスタ", "size": "90×120mm"})
        # 実作業32 ＋ 予備2（管理者・メンバー各 4.0h）＝34
        note(st == 200 and d["tasks_created"] == 34,
             "POST /api/projects がテンプレート34件を展開（実作業32＋予備2）",
             f"{st} {d}")
        pid = d["id"]

        st, d = cl.get("/api/projects")
        note(st == 200 and any(r["id"] == pid for r in d["rows"]),
             "/api/projects に出る", str(st))
        row = next(r for r in d["rows"] if r["id"] == pid)
        note("E2E検査" not in row["product"] and "アクスタ" in row["product"],
             "一覧が商品名を出さず分類で表す（N-6-2）", row["product"])
        note(row["source_of_truth"] == "app",
             "一覧に source_of_truth の列がある（R-2）", row["source_of_truth"])

        st, d = cl.get("/api/projects/" + pid)
        note(st == 200 and d["next_gate"]["gate"] == "G0",
             "カルテの次のゲートが G0", str(st))
        note(len(d["missing"]) > 0, "カルテ最上段に「欠けているもの」が出る",
             str([m["key"] for m in d["missing"]]))
        keys = [s["key"] for s in d["sections"]]
        note(keys == ["C", "D", "E", "F"], "17枚が判断の順の節に畳まれている", str(keys))
        note(d.get("ai_names") == [], "カルテに商品名の AI の案（まだ無い）が付く（ADR-082）", str(d.get("ai_names")))
        st, d = cl.post(f"/api/projects/{pid}/ai-names", {})
        note((st == 403) or (st == 200 and d.get("started") is False and d.get("reason")),
             "商品名の案は、ロールか設定・ログイン・予算がそろうまで走らない", str(d.get("reason") or d.get("error"))[:60])

        st, d = cl.post(f"/api/projects/{pid}/section",
                        {"key": "C.target", "body": "七五三の記念撮影"})
        note(st == 200, "節の保存", str(st))
        st, d = cl.post(f"/api/projects/{pid}/check",
                        {"item_key": "origin", "done": "1", "note": "社内"})
        note(st == 200, "手動確認の記録", str(st))
        st, d = cl.get("/api/projects/" + pid)
        note(not d["missing"], "G0 の欠落が解消した", str([m["key"] for m in d["missing"]]))

        # **管理者（アプリ権限 admin）は G0 の承認者に含まれる**
        st, d = cl.post(f"/api/gates/{pid}/G0", {"result": "通過", "comment": "起票"})
        note(st == 200, "G0 を管理者が通せる", f"{st} {d}")
        # **揃わないと通さない。**G1 は第1段のデータ4件がまだ確認されていない
        st, d = cl.post(f"/api/gates/{pid}/G1", {"result": "通過", "comment": ""})
        note(st == 400 and "欠けているもの" in d.get("error", ""),
             "欠けているものがあると通過させない（F-6）", f"{st} {d}")
        st, d = cl.post(f"/api/gates/{pid}/G1",
                        {"result": "差戻し", "comment": "採点がまだ"})
        note(st == 200, "欠けていても差戻しは記録できる", f"{st} {d}")
        for k in ("scored", "feasibility", "research", "dedup"):
            cl.post(f"/api/projects/{pid}/check",
                    {"item_key": k, "done": "1", "note": "確認"})
        st, d = cl.post(f"/api/gates/{pid}/G1", {"result": "通過", "comment": ""})
        note(st == 200 and d["missing_at_review"] == [],
             "埋めれば G1 を管理者が通せる", f"{st} {d}")
        # **G3 は社長だけ。**アプリ権限 admin では通せない
        st, d = cl.post(f"/api/gates/{pid}/G3", {"result": "通過"})
        note(st == 403 and "承認資格" in d.get("error", ""),
             "G3 を admin の管理者が通せない（§10-2 ②）", f"{st} {d}")
        # **保留は理由コードが要る**
        st, d = cl.post(f"/api/gates/{pid}/G2", {"result": "保留"})
        note(st == 400 and "理由" in d.get("error", ""),
             "保留に理由コードが要る（F-6-5）", f"{st} {d}")
        st, d = cl.post(f"/api/gates/{pid}/G2",
                        {"result": "保留", "reason_code": "effort_over"})
        note(st == 200, "保留を理由つきで記録できる", f"{st} {d}")

        # ── ステージ・バリエーション・案件外の仕事（FR-33/37/47/48）──
        st, d = cl.post(f"/api/projects/{pid}/stage", {"action": "next"})
        note(st == 200 and d.get("stage") == "評価済", "G1 を通した案件は「評価済」へ進める", f"{st} {d}")
        st, d = cl.post(f"/api/projects/{pid}/stage", {"action": "next"})
        st, d = cl.post(f"/api/projects/{pid}/stage", {"action": "next"})
        note(st == 400 and "G2" in d.get("error", ""), "関門を通していないステージへは進めない", f"{st} {d}")
        # バリエーションは**別の案件で**（バリエーションのある案件は seisan 登録が機種ごとになるため）
        # 標準タスクの無い開発タイプで作る（後ろの「期限なし34件」の検査を崩さない）
        st, d = cl.post("/api/projects", {"internal_name": "E2E機種別", "flow_type": "pagerenew",
                                          "launch_date": "2026-12-01"})
        st, d = cl.post(f"/api/projects/{d['id']}/variant", {"label": "E2E機種"})
        note(st == 200, "バリエーションを足せる", f"{st} {d}")
        st, d = cl.post("/api/work-items", {"kind": "他部署依頼", "title": "撮影", "dept": "Webマーケ"})
        wid = d.get("id")
        st, d = cl.post(f"/api/tasks/work_item/{wid}/status", {"status": "完了"})
        note(st == 400 and "受け側" in d.get("error", ""), "他部署依頼は送った側だけでは閉じない", f"{st} {d}")
        st, d = cl.post(f"/api/work-items/{wid}/accept", {})
        note(st == 200, "受け側の完了で閉じる", f"{st} {d}")

        # ── 原価・調達（第3段・ADR-047）──
        st, d = cl.post(f"/api/projects/{pid}/cost-candidate",
                        {"kind": "資材", "part": "本体", "supplier": "E2E社", "lead_days": "10"})
        note(st == 200, "相見積の候補を足せる", f"{st} {d}")
        st, d = cl.post(f"/api/projects/{pid}/cost-adopt", {"id": str(d.get("id")), "adopted": "0"})
        note(st == 400 and "理由" in d.get("error", ""), "不採用は理由が要る", f"{st} {d}")
        st, d = cl.post(f"/api/projects/{pid}/cost-version", {"price_ex_tax": "1000"})
        note(st == 200 and d.get("version") == 1, "試算原価 v1 を作れる", f"{st} {d}")
        st, d = cl.post(f"/api/projects/{pid}/cost-line",
                        {"version_id": str(d.get("id")), "part": "本体", "name": "本体", "qty": "1"})
        st, d = cl.get(f"/api/projects/{pid}/cost")
        note(st == 200 and d["versions"][0]["totals"]["material"]["yen"] is None,
             "単価の無い行は 0 円ではなく未確定", f"{st}")

        # ── 売上実績（2026-10-01）。出どころが無い環境では「無い」と言う（0 にしない）──
        st, d = cl.get("/api/sales?site=goods")
        note(st == 200 and len(d["sites"]) == 6 and (d["month"] is None and "ありません" in d.get("why", "")
             or d["month"]), "/api/sales が6サイトを返し、集計が無ければ理由を言う", f"{st}")
        st, d = cl.get("/api/abc?site=goods")
        note(st in (200, 409), "/api/abc が応える（売上フィードが無い環境では 409 と理由）", f"{st} {d.get('error','')[:40]}")
        st, d = cl.get("/api/abc?site=goods&from=2026-13")
        note(st in (400, 409), "/api/abc は月の形が違えば断る", f"{st}")
        st, d = cl.get("/api/sales?site=yahoo")
        note(st == 400, "/api/sales は知らないサイトを断る", f"{st}")

        # ── seisan への登録（第3段・ADR-043）。**本番と同じく登録口が無い状態** ──
        st, d = cl.get(f"/api/projects/{pid}/seisan")
        note(st == 200 and d["configured"] is False and d["targets"][0]["state"] == "未着手"
             and len(d["vocab"]["sales_types"]) == 10,
             "seisan 欄: 登録口が無いと言い、販売タイプ10種を出す", f"{st}")
        st, d = cl.post(f"/api/projects/{pid}/seisan-draft",
                        {"code": "E2E-1", "name": "E2E名前ABC", "sales_type": "定型",
                         "cat1": "うちわ"})
        note(st == 200, "seisan 下書きの保存", f"{st} {d}")
        st, d = cl.post(f"/api/projects/{pid}/seisan-register", {})
        note(st == 409 and "接続設定" in d.get("error", ""),
             "登録口が無いと登録を断り、理由を言う", f"{st} {d}")
        st, d = cl.post(f"/api/projects/{pid}/seisan-code", {"code": "E2E-1"})
        note(st == 200 and d.get("verified") is False,
             "seisan で登録したコードを記録できる（未確認と返す）", f"{st} {d}")
        leaked = store.val("SELECT COUNT(*) FROM audit WHERE detail LIKE ?",
                           ("%E2E名前ABC%",), 0)
        kept = store.val("SELECT COUNT(*) FROM seisan_registration WHERE draft_json LIKE ?",
                         ("%E2E名前ABC%",), 0)
        note(leaked == 0 and kept == 0,
             "商品名が監査にも残らず、記録後は下書きも消える（正は seisan）",
             f"audit={leaked} draft={kept}")

        st, d = cl.get("/api/tasks")
        note(st == 200 and d["when"] == "overdue+today",
             "/api/tasks の既定が「期限切れ＋今日」", str(st))
        note("none" in d["counts"] and "overdue" in d["counts"],
             "期間フィルタの件数が出る", str(d["counts"]))
        note("6フロー合算" in d["template_totals"]["caption"],
             "標準工数に「6フロー合算」の注記", d["template_totals"]["caption"])
        st, d = cl.get("/api/tasks?when=none")
        note(st == 200 and len(d["rows"]) == 34,
             "「期限なし」で34件（展開直後は期限を入れない）", str(len(d["rows"])))
        st, d = cl.get("/api/projects/" + pid)
        note(st == 200 and "schedule" in d and all("plan_due" in t for t in d["tasks"]),
             "カルテに目安の数と、タスクごとの目安の列がある（ADR-084）", str(d.get("schedule")))
        st, d = cl.post(f"/api/projects/{pid}/schedule-adopt", {})
        st2, d2 = cl.get("/api/tasks?when=none")
        note(st == 200 and len(d2["rows"]) == 34 - d["adopted"],
             "目安を期限にすると、その件数だけ「期限なし」が減る（目安が無ければ0件）", f"{d} / {len(d2['rows'])}")
        st, d = cl.get("/api/tasks?when=%2B9")
        note(st == 400, "知らない期間フィルタは 400", f"{st} {d}")

        st, d = cl.get("/api/gates")
        note(st == 200 and len(d["gates"]) == 7, "/api/gates が7ゲート", str(st))

        st, d = cl.get("/api/tasks?tab=work")
        note(st == 200, "案件外の仕事のタブ", str(st))
        st, d = cl.get("/api/tasks?tab=request")
        note(st == 200, "他部署への依頼のタブ", str(st))

        # ── 第1段: アイデア台帳と採点v2（F-1）──────────────
        st, d = cl.get("/api/meta")
        note(st == 200 and len(d["idea"]["origins"]) == 7
             and len(d["idea"]["themes"]) == 4,
             "/api/meta に起票経路7つと評価テーマ4つ", str(st))
        note(d["ai_scoring"]["enabled"] is False,
             "AI採点は既定 off（F-1-11・第11章 ⑩）",
             d["ai_scoring"]["reason"] or "")

        st, d = cl.post("/api/ideas", {
            "title": "推し色アクリルスタンド", "summary": "検査用の企画名",
            "target_scene": "推し活", "origin": "internal",
            "theme_id": "oshikatsu"})
        note(st == 200, "POST /api/ideas が4項目＋起票経路で通る", f"{st} {d}")
        iid = d["id"]
        st, d = cl.post("/api/ideas", {"title": "似ていない案", "origin": ""})
        note(st == 400, "起票経路が無いと断る（F-1-4）", f"{st} {d}")

        st, d = cl.post("/api/ideas/similar", {"title": "推し色アクリルスタンド"})
        note(st == 200 and any(r["id"] == iid for r in d["rows"]),
             "起票時に似た案が出る（F-1-12）", str(d["rows"])[:120])

        st, d = cl.post(f"/api/ideas/{iid}/score",
                        {"demand": "8", "market_size": "8", "advantage": "8",
                         "theme_fit": "8"})
        note(st == 400 and "未入力" in d.get("error", ""),
             "足りない入力があると v2 で採点させない（F-1-6・F-1-7・N-10）",
             f"{st} {d}")
        cl.post(f"/api/ideas/{iid}/fields",
                {"production_feasibility": "3", "expected_margin_yen": "1200",
                 "demand_cycle": "通年"})
        st, d = cl.post(f"/api/ideas/{iid}/score",
                        {"demand": "8", "market_size": "8", "advantage": "8",
                         "theme_fit": "8"})
        note(st == 200 and d["feasibility_factor"] == 0.85
             and d["common_score"] == 53.0 and d["theme_fit"] == 24.0
             and d["raw_total"] == 77.0 and d["total"] == 65.45,
             "v2 が2層＋減点係数で出る（F-1-5・F-1-6）", f"{st} {d}")
        note(d["rank_basis"] == "percentile" and d["percentile"] is not None,
             "ランクが百分位で決まる（F-1-9）", str(d["rank"]))

        st, d = cl.get("/api/ideas/" + iid)
        note(st == 200 and len(d["scores"]) == 1
             and d["scores"][0]["rubric_version"] == "v2",
             "アイデア1件に版ごとの点が並ぶ（合算しない・F-1-10）", str(st))
        st, d = cl.get("/api/ideas?rubric=v2")
        note(st == 200 and d["rubric"] == "v2"
             and any(r["id"] == iid for r in d["rows"]),
             "一覧が版を1つだけ出す", str(st))
        st, d = cl.get("/api/rubrics")
        note(st == 200 and len(d["rows"]) == 5,
             "/api/rubrics が5版（v1×4＋v2）", str(st))

        st, d = cl.post(f"/api/ideas/{iid}/ai-score", {})
        note((st == 403 and "業務ロール" in d.get("error", ""))
             or (st == 200 and d.get("started") is False and d.get("reason")),
             "AI採点の案は、ロールか設定・ログイン・予算がそろうまで走らない（ADR-081）",
             str(d.get("reason") or d.get("error"))[:60])
        st, d = cl.get("/api/ai/score")
        note(st == 200 and d["scorer_implemented"] is True and d["enabled"] is False
             and isinstance(d["runs"], list) and isinstance(d["pending_rows"], list),
             "/api/ai/score が状態・回・出ている案を返す", str(d.get("reason"))[:60])
        st, d = cl.get(f"/api/ideas/{iid}")
        note(st == 200 and d.get("ai_proposals") == [], "アイデアの詳細に AI の案（まだ無い）が付く", str(st))

        st, d = cl.get("/api/settings")
        note(st == 200 and d["concept_stock"]["value"] is None
             and d["concept_stock"]["state"] == "未計測",
             "コンセプト在庫月数は月間目標が未設定なら「未計測」（F-1-14）",
             d["concept_stock"]["why"][:60])

        # ── 年間プランの枠（F-3 ／ FR-82〜FR-86）──────────
        # **HTTP を通して「警告が保存を止めない」ことを見る。**
        # 単体テストだけだと、画面から来た経路で弾いていても気づけない。
        st, d = cl.post("/api/plan/versions", {"fiscal_year": "2026"})
        vid = d.get("id")
        note(st == 200 and bool(vid), "年間プランの版を作れる（FR-85）", str(st))

        made = []
        for i in range(4):
            st, r = cl.post("/api/plan/slots", {
                "version_id": vid, "launch_month": "2026-05",
                "product_kind": "original", "flow_type": "meire"})
            made.append(st)
        note(all(x == 200 for x in made),
             "ルールを外れる枠でも保存できる（F-3-3・FR-84）", str(made))

        st, d = cl.get("/api/plan/versions/" + vid)
        rules = {(r["rule"], r["scope"]): r for r in d["rules"]["results"]}
        note(rules[("count", "2026-05")]["level"] == "warn",
             "月あたりの本数が警告になる（FR-83）",
             rules[("count", "2026-05")]["message"])
        note(rules[("effort", "2026-05")]["level"] == "warn",
             "月間工数ポイントが警告になる（FR-83）",
             rules[("effort", "2026-05")]["message"])
        note(rules[("holiday", "FY")]["level"] == "unavailable",
             "数えられない連休ルールは「未計測」（N-10）",
             rules[("holiday", "FY")]["message"][:40])

        st, d = cl.post("/api/plan/versions/" + vid + "/ack",
                         {"rule": "count", "scope": "2026-05", "reason": ""})
        note(st == 400, "理由が空の例外は断る（F-3-3）", str(st))
        cl.post("/api/plan/versions/" + vid + "/ack",
                 {"rule": "count", "scope": "2026-05", "reason": "式典向けの集中投入"})
        st, d = cl.get("/api/plan/versions/" + vid)
        r = {(x["rule"], x["scope"]): x for x in d["rules"]["results"]}[("count", "2026-05")]
        note(r["level"] == "warn" and r["acked"]["reason"] == "式典向けの集中投入",
             "承知しても警告は消えない（消せると理由が書かれなくなる）", r["level"])

        sid = d["months"][0]["slots"][0]["id"]
        st, r = cl.post("/api/plan/slots/" + sid + "/convert", {})
        note(st == 200 and r["task_setup_due"]["due"] == "2026-03-01",
             "枠→案件が1操作で、タスク設定期限を割り戻す（FR-86）",
             str(r.get("task_setup_due", {}).get("due")))
        st, r2 = cl.post("/api/plan/slots/" + sid + "/convert", {})
        note(st == 400, "同じ枠を二度変換できない", str(st))

        # 承認は社長の業務ロールだけ（ADR-058）。管理者では断られ、社長なら通る
        st, r = cl.post("/api/plan/versions/" + vid + "/approve", {})
        note(st in (400, 403), "年間プランの承認は管理者では断られる（社長の業務ロールだけ）", str(st))
        who.update(user_id="shacho", name="社長テスト")
        st, r = cl.post("/api/plan/versions/" + vid + "/approve", {})
        note(st == 200, "社長は年間プランを承認できる", str(st))
        who.update(user_id="kanri", name="管理者テスト")
        st, r = cl.post("/api/plan/slots", {
            "version_id": vid, "launch_month": "2026-06", "product_kind": "uchiwa"})
        note(st == 400, "承認済みの版は編集できない（F-3-4）", str(st))

        st, d = cl.get("/api/meta")
        kinds = {k["code"]: k for k in d["plan"]["kinds"]}
        note(kinds["pagerenew"]["counts_as_launch"] == 0,
             "ページリニューアルは発売本数に数えない（F-10-11）",
             kinds["pagerenew"]["label"])

        # ── 自動化依頼（F-15 ／ FR-159〜）────────────────
        # **HTTP を通して「揃う前に渡せない」ことを見る。**
        st, d = cl.post("/api/automation", {
            "title": "E2E: スタンプ登録",
            "raw_request": "元画像を作ったら各サイズ作って登録まで",
            "requester": "増地さん"})
        aid = d.get("id")
        note(st == 200 and bool(aid), "自動化依頼を出せる（作業名だけで可）", str(st))

        st, d = cl.post("/api/automation/" + aid + "/stage", {"stage": "実装待ち"})
        note(st == 400 and "残っています" in (d.get("error") or ""),
             "答えが揃う前は実装へ渡せない（F-15 の芯）", str(d.get("error"))[:60])

        st, d = cl.get("/api/meta")
        qs = [q["key"] for q in d["automation"]["questions"] if q["required"]]
        note(len(qs) == 8, "必須の質問が8問", str(len(qs)))
        for k in qs:
            cl.post("/api/automation/" + aid + "/answer",
                    {"q_key": k, "answer": "" if k == "judgement" else "こう答えました"})

        st, d = cl.get("/api/automation/" + aid)
        byk = {q["key"]: q for q in d["questions"]}
        note(byk["judgement"]["state"] == "無しと回答"
             and byk["extra"]["state"] == "未回答",
             "未回答と「無いという答え」を分けて出す（N-10）",
             f"judgement={byk['judgement']['state']} / extra={byk['extra']['state']}")

        st, d = cl.post("/api/automation/" + aid + "/stage",
                        {"stage": "実装待ち", "handoff_to": "NEW PRODUCT"})
        note(st == 200 and d.get("stage") == "実装待ち",
             "答えが揃えば実装へ渡せる", str(d))

        cl.post("/api/automation/" + aid + "/effort",
                {"minutes_each": "30", "times_per_month": "20"})
        st, d = cl.get("/api/automation/" + aid)
        note(d["effort"]["hours_per_month"] == 10.0,
             "効果が 分×回数 から出る", str(d["effort"]["hours_per_month"]))

        st, body = cl.get("/api/automation/" + aid + "/requirement")
        txt = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        note(st == 200 and "# 自動化の要件" in txt and "**未回答。**" in txt,
             "要件の書き出しに、未回答が未回答として出る", str(st))

        n = store.val("SELECT COUNT(*) FROM audit")
        note(n >= 34, "全操作が audit に残る", f"{n} 件")
        # **断られた操作は残らない。**残るのは成功した操作だけ、が現在の設計
        acts = {r[0] for r in store.q(
            "SELECT DISTINCT action FROM audit WHERE action LIKE 'plan.%'")}
        want = {"plan.version.create", "plan.slot.create", "plan.ack",
                "plan.slot.convert", "plan.version.approve"}
        note(want <= acts, "年間プランの操作が audit に残る",
             ",".join(sorted(acts)))

        st, body = cl.get("/")
        note(st == 200 and b"fca-nav-data" in body, "ログイン済みで SPA の外枠が出る",
             str(st))
    finally:
        srv.shutdown()
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("─" * 60)
    print(f"失敗 {len(_bad)} 件")
    for x in _bad:
        print("  NG " + x)
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
