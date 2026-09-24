#!/usr/bin/env python3
"""
自動化依頼（F-15 ／ FR-159〜FR-170）。2026-09-24 十文字さん指示。

**「この作業を自動化してほしい」を受け付け、質問で要件に変えて、実装へ渡す。**

## なぜ質問を挟むか

増地さんの15件を実際に読むと、**書き方の細かさが揃っていない。**

- 「スタンプ登録 → 元となる画像を作成したらあとは各サイズ作ってくれて登録もしてほしい」
  … 入力（元画像）と出力（各サイズ＋登録）が書かれている
- 「治具作成」「ワイヤーフレーム作成」
  … 作業名だけ。**何を渡すと始まり、何ができたら終わりかが分からない**

**足りないまま実装に渡すと、作った側が想像で埋める。**質問はそれを止めるためのもの。

## 質問は固定。AI に作らせない

AI予算枠（`newproduct-*`）は未取得（FR-146）。**いま AI を呼ぶ口は開けない。**
固定の8問と、自由記述の追加欄で足りる —— 8問はどれも
「答えが無いと実装できない」ものに絞ってある。

## 完了の判定

**必須の質問すべてに答えが入ったら `要件確定` にできる。**
「答えていない」と「聞いたが無いと答えた」を区別する（行が無い ＝ 未回答）。
**空欄を『無し』と読み替えない**（N-10）。
"""
from __future__ import annotations

from . import store

STAGES = ["起票", "質問中", "要件確定", "実装待ち", "実装中", "実装済", "見送り"]
OPEN_STAGES = ("起票", "質問中", "要件確定", "実装待ち", "実装中")

# ── 質問（F-15-2）──────────────────────────────────────
# key, 質問文, なぜ聞くか, 必須か, 例
QUESTIONS = [
    ("how_now", "いま、どうやっていますか（手順を1〜2行で）",
     "自動化は今の手順の写し。**手順が書けない作業は自動化できません。**",
     True, "WebDecoの管理画面を開く → 画像を1枚ずつアップ → サイズを選んで保存"),
    ("input", "何を渡すと、その作業が始まりますか（入力）",
     "**始まりが決まらないと、いつ動かすかが決まりません。**"
     "「電子タバコの写真」のように、物で答えてください。",
     True, "元となる画像1枚（PNG・透過）"),
    ("output", "何ができたら、終わりですか（出力）",
     "**終わりが決まらないと、できたかどうかを誰も判定できません。**",
     True, "各サイズ（S/M/L）の画像が作られ、管理画面に登録まで済んでいる"),
    ("judgement", "人が見ないと決められないところは、どこですか",
     "**ここが自動化の可否を分けます。**全部が機械で決まるなら丸ごと自動化でき、"
     "1か所だけ人の目が要るなら、そこで止めて人に見せる作りにします。"
     "**「無い」なら「無い」と答えてください。**",
     True, "色味の調整。元画像によっては人が見ないと決まらない"),
    ("tools", "いま使っている道具（画面・ソフト）は何ですか",
     "つなぎ方が変わります。管理画面しか無いものと、"
     "ファイルを置けば済むものとでは、作りが違います。",
     True, "WebDeco管理画面 ／ Photoshop"),
    ("freq", "どのくらいの頻度で、1回どのくらい時間がかかりますか",
     "**効果の見積りに使います。**「月に何回」「1回あたり何分」を、"
     "おおよそで構いませんので数で。",
     True, "月20回・1回30分"),
    ("risk", "間違えたら、どうなりますか（やり直せますか／お客様に出ますか）",
     "やり直せない作業や、お客様に出るものは、**自動化しても人の承認を挟みます。**",
     True, "登録し直せば直る。お客様には出ない"),
    ("done_check", "「できた」と分かるのは、何を見たときですか",
     "**自動化したあと、動いているかを見張るための目印です。**"
     "これが無いと、黙って止まっていても誰も気づきません。",
     True, "管理画面のスタンプ一覧に3サイズとも並んでいること"),
    ("extra", "ほかに、伝えておきたいことはありますか",
     "任意です。上の8問で拾えなかったことがあれば。",
     False, ""),
]
QMAP = {q[0]: q for q in QUESTIONS}
REQUIRED = [q[0] for q in QUESTIONS if q[3]]


def questions() -> list[dict]:
    return [{"key": k, "text": t, "why": w, "required": r, "example": e}
            for k, t, w, r, e in QUESTIONS]


# ══════════════════════════════════════════════════════════
def create(user_id: str, **f) -> dict:
    title = (f.get("title") or "").strip()
    if not title:
        raise ValueError("作業名を入れてください")
    if store.one("SELECT 1 FROM automation_request WHERE title=?", (title,)):
        raise ValueError(f"同じ作業名が既にあります: {title}")
    tid = (f.get("template_id") or "") or None
    if tid:
        tid = int(tid)
        if not store.one("SELECT 1 FROM task_template WHERE id=?", (tid,)):
            raise ValueError(f"知らない標準タスク {tid}")
    rid = store.new_id("automation_request")
    with store.tx() as c:
        c.execute(
            "INSERT INTO automation_request (id,title,raw_request,requester,dept,"
            "stage,template_id,in_template,category,note,"
            "created_at,created_by,updated_at,updated_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, title, (f.get("raw_request") or "").strip() or None,
             (f.get("requester") or "").strip() or None,
             (f.get("dept") or "").strip() or None,
             f.get("stage") or "起票", tid, 1 if tid else 0,
             (f.get("category") or "").strip() or None,
             (f.get("note") or "").strip() or None,
             store.now_s(), user_id, store.now_s(), user_id))
    return {"id": rid, "title": title}


def answer(request_id: str, q_key: str, text: str, user_id: str) -> dict:
    """1問に答える。**空文字も答えとして残す**（「無い」という答え）。"""
    if q_key not in QMAP:
        raise ValueError(f"知らない質問 {q_key!r}")
    r = store.one("SELECT * FROM automation_request WHERE id=?", (request_id,))
    if r is None:
        raise ValueError("その依頼がありません")
    if r["stage"] in ("実装済", "見送り"):
        raise ValueError(f"{r['stage']}の依頼には答えられません")
    with store.tx() as c:
        c.execute("INSERT INTO automation_answer (request_id,q_key,answer,"
                  "answered_by,answered_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(request_id,q_key) DO UPDATE SET answer=excluded.answer,"
                  "answered_by=excluded.answered_by,answered_at=excluded.answered_at",
                  (request_id, q_key, (text or "").strip(), user_id, store.now_s()))
        # 起票のままなら「質問中」へ。**勝手に要件確定へは上げない**
        if r["stage"] == "起票":
            c.execute("UPDATE automation_request SET stage='質問中',updated_at=?,"
                      "updated_by=? WHERE id=?", (store.now_s(), user_id, request_id))
    return progress(request_id)


def _answers(request_id: str) -> dict:
    return {r["q_key"]: dict(r) for r in store.q(
        "SELECT * FROM automation_answer WHERE request_id=?", (request_id,))}


def progress(request_id: str) -> dict:
    """答えの埋まり具合。**未回答と空の答えを区別する。**"""
    a = _answers(request_id)
    missing = [k for k in REQUIRED if k not in a]
    blank = [k for k in REQUIRED if k in a and not a[k]["answer"]]
    return {
        "answered": len(a), "required": len(REQUIRED),
        "missing": missing, "blank": blank,
        # **空の答えも「答えた」に数える。**聞いた結果「無い」なら、それは情報
        "ready": not missing,
        "note": ("あと %d問です。" % len(missing)) if missing else
                "必要な質問はすべて埋まりました。要件確定にできます。",
    }


def effort(request_id: str) -> dict:
    """効果の見積り。**答えから数が取れたときだけ数を出す**（N-10）。"""
    r = store.one("SELECT * FROM automation_request WHERE id=?", (request_id,))
    if r is None:
        raise ValueError("その依頼がありません")
    if r["hours_per_month"] is None:
        a = _answers(request_id)
        return {"hours_per_month": None, "state": "未計測",
                "why": "1回あたりの時間と月の回数が未入力です。"
                       + ("「どのくらいの頻度で」の答えはあります（"
                          + a["freq"]["answer"][:40] + "）が、**文章のままでは数えられません。**"
                          "数を入れてください" if a.get("freq") and a["freq"]["answer"]
                          else "「どのくらいの頻度で」の質問がまだです")}
    # 標準タスクに当たるなら、既にある AI削減の見込みを添える
    tpl = None
    if r["template_id"]:
        t = store.one("SELECT title, standard_hours, ai_reduction_rate, "
                      "ai_reduction_hours FROM task_template WHERE id=?",
                      (r["template_id"],))
        if t:
            tpl = {"title": t["title"], "standard_hours": t["standard_hours"],
                   "ai_reduction_rate": t["ai_reduction_rate"],
                   "ai_reduction_hours": t["ai_reduction_hours"]}
    return {"hours_per_month": r["hours_per_month"], "state": "計測済",
            "per_year": round(float(r["hours_per_month"]) * 12, 1),
            "template": tpl,
            "note": "この時間は依頼した人の申告です。"
                    + ("標準タスク「%s」のAI削減見込み（%s）とは別の数字で、**足しません。**"
                       % (tpl["title"],
                          "未算出" if tpl["ai_reduction_rate"] is None
                          else f"{tpl['ai_reduction_rate']:.0%}")
                       if tpl else "標準タスク178行のどれにも当たりません。")}


def set_effort(request_id: str, minutes_each, times_per_month, user_id: str) -> dict:
    def num(v):
        if v in (None, ""):
            return None
        return float(v)
    m, t = num(minutes_each), num(times_per_month)
    h = round(m * t / 60, 2) if (m is not None and t is not None) else None
    with store.tx() as c:
        c.execute("UPDATE automation_request SET minutes_each=?,times_per_month=?,"
                  "hours_per_month=?,updated_at=?,updated_by=? WHERE id=?",
                  (m, t, h, store.now_s(), user_id, request_id))
    return {"hours_per_month": h}


def set_stage(request_id: str, stage: str, user_id: str, handoff_to: str = "") -> dict:
    if stage not in STAGES:
        raise ValueError(f"知らない状態 {stage!r}")
    r = store.one("SELECT * FROM automation_request WHERE id=?", (request_id,))
    if r is None:
        raise ValueError("その依頼がありません")
    if stage in ("要件確定", "実装待ち", "実装中", "実装済"):
        p = progress(request_id)
        if not p["ready"]:
            raise ValueError(
                "必要な質問が %d問 残っています（%s）。"
                "**答えが揃う前に実装へ渡さない**のがこの画面の趣旨です"
                % (len(p["missing"]), "・".join(QMAP[k][1] for k in p["missing"])[:80]))
    with store.tx() as c:
        c.execute("UPDATE automation_request SET stage=?,decided_by=?,decided_at=?,"
                  "handoff_to=?,updated_at=?,updated_by=? WHERE id=?",
                  (stage, user_id, store.now_s(),
                   (handoff_to or "").strip() or r["handoff_to"],
                   store.now_s(), user_id, request_id))
    return {"id": request_id, "stage": stage}


def add_note(request_id: str, body: str, user_id: str, q_key: str = "") -> dict:
    body = (body or "").strip()
    if not body:
        raise ValueError("内容を入れてください")
    if q_key and q_key not in QMAP:
        raise ValueError(f"知らない質問 {q_key!r}")
    with store.tx() as c:
        c.execute("INSERT INTO automation_note (request_id,q_key,body,by_user,at) "
                  "VALUES (?,?,?,?,?)",
                  (request_id, q_key or None, body, user_id, store.now_s()))
    return {"ok": True}


# ══════════════════════════════════════════════════════════
def listing(args: dict | None = None) -> dict:
    args = args or {}
    sql = ("SELECT r.*, t.title AS template_title, t.flow_type, "
           "t.ai_reduction_rate, "
           "(SELECT COUNT(*) FROM automation_answer a WHERE a.request_id=r.id) AS answered "
           "FROM automation_request r "
           "LEFT JOIN task_template t ON t.id=r.template_id")
    where, p = [], []
    if args.get("stage"):
        where.append("r.stage=?")
        p.append(args["stage"])
    if args.get("only") == "not_in_template":
        where.append("r.in_template=0")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (r.stage='見送り'), (r.stage='実装済'), r.created_at"
    rows = store.rows(store.q(sql, p))
    for x in rows:
        x["required"] = len(REQUIRED)
        x["ready"] = x["answered"] >= len(REQUIRED)
    by_stage = {r["stage"]: r["n"] for r in store.q(
        "SELECT stage, COUNT(*) AS n FROM automation_request GROUP BY stage")}
    n_all = sum(by_stage.values())
    n_gap = store.val("SELECT COUNT(*) FROM automation_request WHERE in_template=0", (), 0)
    measured = store.q("SELECT SUM(hours_per_month) AS h, COUNT(*) AS n "
                       "FROM automation_request WHERE hours_per_month IS NOT NULL "
                       "AND stage NOT IN ('見送り','実装済')")
    h = measured[0]["h"] if measured else None
    return {
        "rows": rows, "by_stage": by_stage, "total": n_all,
        "not_in_template": n_gap,
        "hours_per_month": round(float(h), 1) if h else None,
        "hours_measured_n": measured[0]["n"] if measured else 0,
        # **測れていない件数を必ず出す**（N-10）。合計だけ見せると全部の合計に見える
        "hours_note": (
            f"月あたりの時間が入っているのは {measured[0]['n'] if measured else 0} / "
            f"{n_all} 件です。**残りは未計測**で、合計には入っていません。"),
        "template_note": (
            f"**{n_gap} 件は標準タスク178行のどれにも当たりません。**"
            "「やっていない」のではなく、**表のほうが現場に追いついていない**という意味です。"
            if n_gap else None),
        "stages": STAGES,
    }


def detail(request_id: str) -> dict | None:
    r = store.one("SELECT * FROM automation_request WHERE id=?", (request_id,))
    if r is None:
        return None
    a = _answers(request_id)
    qs = []
    for k, t, w, req, ex in QUESTIONS:
        got = a.get(k)
        qs.append({"key": k, "text": t, "why": w, "required": req, "example": ex,
                   "answer": got["answer"] if got else None,
                   # **未回答と「無い」を区別する**
                   "state": "未回答" if not got else ("無しと回答" if not got["answer"]
                                                   else "回答済"),
                   "answered_by": got["answered_by"] if got else None,
                   "answered_at": got["answered_at"] if got else None})
    tpl = None
    if r["template_id"]:
        t = store.one("SELECT tt.*, f.label AS flow_label FROM task_template tt "
                      "LEFT JOIN flow_type f ON f.code=tt.flow_type WHERE tt.id=?",
                      (r["template_id"],))
        if t:
            tpl = dict(t)
    return {
        "request": dict(r), "questions": qs, "progress": progress(request_id),
        "effort": effort(request_id), "template": tpl,
        "notes": store.rows(store.q(
            "SELECT * FROM automation_note WHERE request_id=? ORDER BY at", (request_id,))),
        "stages": STAGES,
        "handoff_note":
            "**このアプリは実装しません。**答えが揃ったら要件として書き出し、"
            "作る人へ渡すところまでが、この画面の役目です。",
    }


def requirement_text(request_id: str) -> str:
    """要件として書き出す（F-15-5）。**渡す相手が読む形にする。**"""
    d = detail(request_id)
    if d is None:
        raise ValueError("その依頼がありません")
    r, e = d["request"], d["effort"]
    out = [f"# 自動化の要件: {r['title']}", "",
           f"- 依頼: {r['requester'] or '—'}（{r['dept'] or '—'}）",
           f"- 状態: {r['stage']}",
           f"- 標準タスク: " + (f"{d['template']['title']}（{d['template']['flow_label']}）"
                              if d["template"] else
                              "**178行のどれにも当たりません**（表が現場に追いついていない）"),
           f"- 月あたりの時間: " + (f"{e['hours_per_month']}h（年 {e['per_year']}h）"
                                if e["hours_per_month"] is not None else "**未計測**"),
           "", "## 元の言葉（書き換えていません）", "",
           "> " + (r["raw_request"] or r["title"]).replace("\n", "\n> "), "",
           "## 質問と答え", ""]
    for q in d["questions"]:
        out.append(f"### {q['text']}")
        out.append("")
        if q["state"] == "未回答":
            out.append("**未回答。**" + q["why"])
        elif q["state"] == "無しと回答":
            out.append("（聞いたところ「無い」との回答）")
        else:
            out.append(q["answer"])
        out.append("")
    if d["notes"]:
        out += ["## やりとり", ""]
        for n in d["notes"]:
            out.append(f"- {n['at']} {n['by_user'] or '—'}"
                       + (f"（{QMAP[n['q_key']][1]}）" if n["q_key"] else "") + f": {n['body']}")
    return "\n".join(out) + "\n"
