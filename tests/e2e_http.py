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
        note(all(m["value"] is None and m["state"] == "未計測" for m in d["monthly"]),
             "/api/dashboard の2段目が「未計測」（0 ではない）",
             str([m["state"] for m in d["monthly"]]))

        st, d = cl.post("/api/projects", {
            "internal_name": "E2E検査", "flow_type": "meire",
            "launch_date": "2026-11-27", "occasion": "七五三(11/15)",
            "area": "推し活", "summary": "検査", "owner": "kanri",
            "cat1": "アクリル製品", "cat2": "アクスタ", "size": "90×120mm"})
        note(st == 200 and d["tasks_created"] == 32,
             "POST /api/projects がテンプレート32件を展開", f"{st} {d}")
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

        st, d = cl.get("/api/tasks")
        note(st == 200 and d["when"] == "overdue+today",
             "/api/tasks の既定が「期限切れ＋今日」", str(st))
        note("none" in d["counts"] and "overdue" in d["counts"],
             "期間フィルタの件数が出る", str(d["counts"]))
        note("6フロー合算" in d["template_totals"]["caption"],
             "標準工数に「6フロー合算」の注記", d["template_totals"]["caption"])
        st, d = cl.get("/api/tasks?when=none")
        note(st == 200 and len(d["rows"]) == 32,
             "「期限なし」で32件（展開直後は期限を入れない）", str(len(d["rows"])))
        st, d = cl.get("/api/tasks?when=%2B9")
        note(st == 400, "知らない期間フィルタは 400", f"{st} {d}")

        st, d = cl.get("/api/gates")
        note(st == 200 and len(d["gates"]) == 7, "/api/gates が7ゲート", str(st))

        st, d = cl.get("/api/tasks?tab=work")
        note(st == 200, "案件外の仕事のタブ", str(st))
        st, d = cl.get("/api/tasks?tab=request")
        note(st == 200, "他部署への依頼のタブ", str(st))

        n = store.val("SELECT COUNT(*) FROM audit")
        note(n >= 6, "全操作が audit に残る", f"{n} 件")

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
