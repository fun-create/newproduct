# ══════════════════════════════════════════════════════════
# 出どころ: keiei /opt/keiei/app/auth.py（2026-09-21 取得）
#           さらにその上流は lpscope /opt/lpscope/reports/auth.py
# **上流をコピーしたもの**。直接編集すると、次に上流を取り込むときに
# 食い違いが生じる。直したいときは上流を直してから持ってくる。
# 複製元の指紋は _upstream.json にある。
# 変更点: 置き場（CONFIG）・SESSION_COOKIE・CSRF_HEADER の3つだけ（下記）
# ══════════════════════════════════════════════════════════
#!/usr/bin/env python3
"""
ユーザー登録・ログイン・権限（管理者／ユーザー）。

**なぜ要るか**
これまでは 127.0.0.1 に閉じた1人用アプリだったので認証が不要だった。
サーバー上で共有するなら、**クライアントの機密（広告実績・契約金額・請求）** が
URLを知っているだけで全部読める状態になる。認証は後付けの飾りではなく前提。

**方式の選択（stdlibのみ・追加依存なしが設計原則）**
- 採用: **ローカルパスワード ＋ hashlib.scrypt**。ユーザー情報は `config/users.json`。
- 却下: HTTP Basic … 毎回パスワードを送る／ログアウトできない／総当たりを止められない。
- 却下: Google OAuth … 追加依存とネット必須。社内数名の共有には過大。
  （将来入れるなら `verify_password` の差し替え点だけを増やす）

**保存するもの / しないもの**
パスワードは**平文を一切保存しない**。scrypt のダイジェストと salt だけを持つ。
パラメータもレコードに書いておく（後で強度を上げても、古いレコードを検証できる）。

**セッション**
メモリに持ちつつ、`config/.sessions.json` にも落とす（0600）。
**保存するのは sid の SHA-256 だけ**なので、このファイルが漏れてもログインはできない。
落とす理由は、診断が最大15分かかることと、サーバー更新のたびに全員が
作業を中断させられるのを避けるため（再起動＝全員ログアウトは運用に噛み合わない）。
Cookie は `HttpOnly` + `SameSite=Strict` + `Path=/`。`Secure` は HTTPS のときだけ付ける
（httpでSecureを付けるとCookieが一切保存されず、ログインできなくなる）。

**CSRF**
`SameSite=Strict` で他サイトからの遷移にCookieが付かないため、主要な経路は塞がる。
これに加えて **状態を変えるAPI（POST）には X-CSRF ヘッダを要求**する。
fetch は同一オリジンからしか任意ヘッダを付けられないので、フォーム送信型のCSRFは通らない。

**検証用アカウント（`test: true`）**
画面の確認をエージェント（Claude Code）に任せるために、管理者権限のアカウントが要る。
ただし**常時有効な管理者パスワードを1つ増やす**ことになるので、次の2つで縛る:
  1. `loopback_only` … 127.0.0.1 / ::1 からのログインだけを受け付ける。
     サーバーに載せても**外からは一切ログインできない**。
  2. `expires_on` … 期限切れは自動的にログインできない（消し忘れても腐る）。
画面の利用者一覧に「検証用」と出るので、放置されていることに気づける。
`tools/test_user.py` でいつでも作り直し・削除ができる。

**権限**
- 管理者(admin): すべて。ユーザー管理・接続情報（APIキー）・クライアント削除・請求。
- ユーザー(user): 日々の作業（レポート作成・閲覧・書き出し・案件の記録）。
  **壊すと戻せないもの／お金に触るもの／全体設定は渡さない**。
判定は必ずサーバー側で行う（画面で隠すだけでは直叩きで回避できる）。
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import unicodedata
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "config"
USERS_PATH = CONFIG / "users.json"

ROLES = [("admin", "管理者"), ("user", "ユーザー")]

# 表示（配色）。**この3つ以外は受けない** — 任意の文字列をそのまま
# `data-theme` 属性へ入れると、画面へ差し込む口になる。
# 既定は `auto`（端末の設定に従う）で、これまでの見え方を変えない。
THEMES = ("auto", "light", "dark")
THEME_DEFAULT = "auto"
ROLE_LABEL = dict(ROLES)

SESSION_COOKIE = "newproduct_sid"
SESSION_TTL = 12 * 3600        # 12時間（業務日1日ぶん）。無操作で切れる
CSRF_HEADER = "X-NEWPRODUCT-CSRF"

# scrypt のパラメータ。**レコードごとに保存する**ので、後で上げても古い記録を検証できる。
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "dklen": 32}    # 実測 約80ms/回

MIN_PASSWORD = 10              # 総当たりより「使い回し」が現実の脅威。長さだけ担保する
# 連続失敗の扱い。**2026-09-15 十文字さん指示で 8回/5分 → 20回/30秒へ緩めた。**
#
# **なぜ緩めるか。**共通ログインへ移った直後、合言葉が変わったことを知らない人が
# 何度も試して凍結した（渡邊さん・杉浦さん）。**5分は、人が待つには長い。**
# 画面には「IDまたはパスワードが違います」としか出ないので、
# 本人は原因が分からないまま待たされる。
#
# **なぜ 0 にしないか。**5アプリとも公開URLでログイン画面に到達できる。
# 無制限にすると総当たりの費用がゼロになる。
#
# **凍結の有無で振る舞いを変えない**こと（停止中だけ数えない等）。
# 9回叩いて凍結しなければ「停止されている」と分かってしまう。
LOCK_FAILS = 20
LOCK_SECONDS = 30              # **止める長さ**

# **失敗を数え合わせる窓。止める長さとは別に持つ。**
#
# 以前は `LOCK_SECONDS` が両方を兼ねていた。5分のときは問題にならなかったが、
# 止める長さを30秒に縮めると、**数え合わせる窓まで30秒になる**。
# すると 31秒おきに叩くだけで回数が積み上がらず、**永久に凍結しない**
# （毎時116回。5分のときの約10倍）。**止める長さを短くしたいだけ**なので、
# 窓は元の5分のまま据え置く（2026-09-15 LP SCOPE 指摘）。
FAILS_WINDOW = 300

_LOCK = threading.RLock()      # users.json の読み書きを直列化（共有サーバーでは同時保存が起きる）
SESSIONS_PATH = CONFIG / ".sessions.json"
_SESSIONS = {}                 # sid -> {user_id, expires, ip, created}
_FAILS = {}                    # user_id -> [失敗回数, 直近時刻]


# ─────────────────────────── ユーザー台帳 ───────────────────────────
class LedgerCorrupt(Exception):
    """台帳が読めない。**空として扱わない。**

    2026-09-14 まで `_read()` は失敗すると `{"users": []}` を返していた。
    そのあと `_write(doc)` する経路が2つあり（`create()` と `login()`）、
    **一度でも読めなくなると次の書き込みで全員が消えた**。
    しかも `create()` は空の台帳だと `first = not doc["users"]` が真になるので、
    **読めなくなった瞬間に初回登録画面が出て、誰でも最初の管理者になれた**。

    引き金はパース壊れではなく **`except Exception` が読み取り失敗も飲むこと**。
    本番でテストを root で流して `users.json` の所有者が変わり、
    lpscope が読めなくなった事故が実際にある（CLAUDE.md 記録）。
    """


_LAST_GOOD = {"doc": None}         # 最後に読めた台帳。読み取り専用の退避先


def _read(*, for_write=False):
    """台帳を読む。**読めなければ例外。空を返さない。**

    ただし**読むだけのときは、最後に読めた内容へ退避する**（`for_write=False`）。
    ここで例外を上げると `session_of()` 経由で**全リクエストが 500 になる**
    （`session_of` → `get` → `users` → `_read`。各アプリの入口が毎回通る）。
    止める向きは正しくても、**読める形で止まらなければ運用にならない**。

    書くときは退避しない（`for_write=True`）。古い内容に書き戻すと、
    その間に入った変更が消える。**書き込みは必ず止める。**
    """
    try:
        raw = USERS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"users": []}           # 未設置は「0人」で正しい（初回登録へ進む）
    except OSError as e:
        return _fallback(LedgerCorrupt(f"{USERS_PATH} を読めません: {e}"), for_write)
    try:
        d = json.loads(raw)
    except ValueError as e:
        return _fallback(LedgerCorrupt(f"{USERS_PATH} が壊れています: {e}"), for_write)
    if not (isinstance(d, dict) and isinstance(d.get("users"), list)):
        return _fallback(LedgerCorrupt(f"{USERS_PATH} の形が違います"), for_write)
    _LAST_GOOD["doc"] = d
    return d


_DEGRADED_AT = {"t": 0.0}
DEGRADED_LOG_EVERY = 300        # 5分に1回。ログを埋めずに、続いていることは分かる


def _fallback(exc, for_write):
    if for_write or _LAST_GOOD["doc"] is None:
        raise exc
    # **黙って退避しない。**退避中も画面は普通に動くので、誰かが「追加」「停止」を
    # 押すまで誰も気づかない。cip のバックアップが10日気づかれなかったのと同じ形
    now = time.time()
    if now - _DEGRADED_AT["t"] > DEGRADED_LOG_EVERY:
        _DEGRADED_AT["t"] = now
        print(f"[auth] 利用者台帳が読めないため、最後に読めた内容で動いています: {exc}",
              file=sys.stderr, flush=True)
    return json.loads(json.dumps(_LAST_GOOD["doc"]))    # 退避（呼び出し側の変更を波及させない）


_SHARED_DOWN = {"at": 0.0, "why": ""}


def _note_shared_down(exc):
    _SHARED_DOWN["why"] = str(exc)
    now = time.time()
    if now - _SHARED_DOWN["at"] > DEGRADED_LOG_EVERY:
        _SHARED_DOWN["at"] = now
        print(f"[auth] 共通台帳が読めないため、停止とパスワード変更の判定を"
              f"見送っています: {exc}", file=sys.stderr, flush=True)


def degraded():
    """いま台帳が読めているか。画面の帯に出す用（空文字＝正常）。

    **`_ledger_readable()` を使い回す。**ここだけ `read_text()` の成否で
    判定していると、**壊れたJSONのときに「正常」と返す**。
    帯は人に見える唯一の合図なので、ここが黙ると
    「stderr には出ているが画面には何も出ない」状態になる。
    """
    if not _ledger_readable():
        return "利用者台帳が読めません。追加・変更はできません（記録は最後に読めた内容です）"
    if shared_on():
        why = binding_warning()
        if why:
            _note_bind(why)
            return f"共通ログインの設定を確認してください（{why}）"
        try:
            _shared_load()
        except SharedUnavailable as e:
            _note_shared_down(e)
            # **「もう誰も止められない」と読ませない。**
            # 効かなくなるのは共通台帳での停止（`_shared_allows` は fail-open）で、
            # **各アプリの `active=false` は毎リクエスト効く**（`session_of`）。
            # 読み手が「打つ手なし」と受け取ると、本当は効く手段を使わなくなる
            return ("共通のアカウント台帳が読めません。"
                    "**共通台帳での停止が効きません**（各アプリ側での停止は効きます）。"
                    "新しいログインは止まります")
    return ""


BACKUP_KEEP = 10


def _write(doc):
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # **上書きの前に世代を残す。**日次バックアップは最大24時間ぶん失う。
    # clients.json は保存のたびに20世代を持つのに、users.json は持たなかった
    try:
        if USERS_PATH.exists():
            keep = USERS_PATH.with_name(
                USERS_PATH.name + "." + time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(USERS_PATH, keep)
            os.chmod(keep, 0o600)
            olds = sorted(USERS_PATH.parent.glob(USERS_PATH.name + ".2*"))
            for f in olds[:-BACKUP_KEEP]:
                f.unlink()
    except OSError:
        pass                           # 世代が残せなくても本体の保存は止めない
    tmp = USERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, USERS_PATH)        # 書き換え中に読まれても壊れた台帳を見せない
    try:
        os.chmod(USERS_PATH, 0o600)    # パスワードダイジェストを他ユーザーに読ませない
    except OSError:
        pass


def norm_user_id(v):
    """ログインID。URL・ファイル名には使わないが、揺れると別人扱いになるので正規化する。"""
    s = unicodedata.normalize("NFKC", str(v or "")).strip().lower()
    return re.sub(r"[^a-z0-9._@-]+", "", s)[:64]


def _ledger_readable():
    """台帳がいま読めるか。**「空の一覧」を 0人 と読んでよいかの判定に使う。**

    **開けるかどうかだけを見てはいけない。**壊れたJSONはファイルとしては
    読めるので「読める」と判定され、`users()` の空リストが 0人 と解釈されて
    **初回登録画面が出る**（誰でも最初の管理者になれる）。中身まで通す。
    `for_write=True` は退避を使わない読み方という意味で、ここでは書かない。
    """
    try:
        _read(for_write=True)
        return True
    except LedgerCorrupt:
        return False


def users():
    """読めないときは退避へ。**退避先も無ければ空を返す**（例外を上げない）。

    起動直後は退避先が空なので、ここで例外を上げると
    `count()` / `public_users()` を呼ぶ**起動路がそのまま落ちる**
    （`is_bootstrap()` だけ直しても、False を返すぶん必ず次の行へ進む）。

    **「空＝0人」と読んでよいのは `is_bootstrap()` だけ**で、そこは
    `_ledger_readable()` で別に確かめる。起動時のバナーや利用者一覧が
    空になるのは見た目が変なだけで、危険ではない。
    """
    with _LOCK:
        try:
            return _read()["users"]
        except LedgerCorrupt:
            return []


def get(user_id):
    uid = norm_user_id(user_id)
    return next((u for u in users() if u.get("user_id") == uid), None)


def count():
    return len(users())


def is_bootstrap():
    """1人も居ない状態。**最初の1人だけ登録画面から管理者を作れる**。

    **読めないときは False。**起動直後は退避先（`_LAST_GOOD`）が空なので、
    ここで例外を上げると**生のトレースバックでサービスが上がらない**
    （更新のたびの再起動が全断になる）。かといって「0人」と読むと
    **初回登録画面が出て、誰でも最初の管理者になれる**。両方避ける。
    """
    if not _ledger_readable():
        return False                   # 読めないのに「0人」と読まない
    return count() == 0


# ─────────────────────────── パスワード ───────────────────────────
def hash_password(password, params=None):
    p = dict(SCRYPT, **(params or {}))
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=p["n"], r=p["r"], p=p["p"], dklen=p["dklen"],
                        maxmem=p["n"] * p["r"] * 256)
    return {"algo": "scrypt", **p, "salt": salt.hex(), "hash": dk.hex()}


def verify_password(rec, password):
    # **形を確かめてから読む。** 以前この欄を書くのは `hash_password` だけで、
    # 必ず dict だった。**共通台帳を使うようになって、別のアプリや手作業が
    # 書く**ようになったので、その前提はもう成り立たない。
    # 文字列や null が入っていると `pw.get` が例外になり、ログインが
    # **500 で落ちる**（認証の失敗として返らない）。壊れた記録は「合わない」と扱う。
    pw = (rec or {}).get("password")
    if not isinstance(pw, dict) or pw.get("algo") != "scrypt":
        return False
    try:
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(pw["salt"]),
                            n=pw["n"], r=pw["r"], p=pw["p"], dklen=pw["dklen"],
                            maxmem=pw["n"] * pw["r"] * 256)
    except Exception:
        return False
    return hmac.compare_digest(dk.hex(), pw.get("hash", ""))   # 比較時間を揃える


def check_password_policy(password):
    """長さだけ見る。記号必須などの複雑さ要求は、使い回しと付箋を増やすだけで効果が薄い。"""
    if len(password or "") < MIN_PASSWORD:
        return f"パスワードは{MIN_PASSWORD}文字以上にしてください"
    if password.strip() != password:
        return "先頭・末尾の空白は使えません（打ち間違いの原因になります）"
    return ""


# ─────────────────────────── 登録・更新 ───────────────────────────
LOOPBACK = ("127.0.0.1", "::1", "localhost", "")


def create(user_id, name, password, role="user", created_by="",
           test=False, loopback_only=False, expires_on=""):
    """(user, error)。**最初の1人は必ず管理者**（誰も管理できないアプリを作らない）。"""
    uid = norm_user_id(user_id)
    if not uid:
        return None, "ログインIDは英数字・記号(. _ - @)で入力してください"
    if len(str(name or "").strip()) == 0:
        return None, "表示名を入力してください"
    err = check_password_policy(password)
    if err:
        return None, err
    with _LOCK:
        try:
            doc = _read(for_write=True)
        except LedgerCorrupt as e:
            # **空の台帳として続けない。**続けると `first` が真になり、
            # 追加される1人が自動で管理者になったうえで台帳を1人で上書きする
            return None, f"利用者台帳が読めないため追加できません（{e}）"
        if any(u.get("user_id") == uid for u in doc["users"]):
            return None, "このログインIDは既に使われています"
        first = not doc["users"]
        rec = {
            "user_id": uid,
            "name": str(name).strip()[:60],
            "role": "admin" if first else (role if role in ROLE_LABEL else "user"),
            "active": True,
            "password": hash_password(password),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_by": created_by or ("(初期設定)" if first else ""),
            "last_login": "",
            "must_change": False,
        }
        if test:
            # **検証用と分かる印を必ず残す**。ただの管理者として紛れ込ませない
            rec["test"] = True
            rec["loopback_only"] = True if loopback_only or test else False
            rec["expires_on"] = str(expires_on or "")
        doc["users"].append(rec)
        _write(doc)
        return _public(rec), ""


def update(user_id, *, name=None, role=None, active=None, actor=""):
    """**`test` と `loopback_only` は受けない。作成時にしか立てられない。**

    `_test_local()` はこの2つが揃った記録だけを「共通台帳を通さず
    ローカルの合言葉で照合してよい」と判断する。ここに引数を足すと、
    **既存の利用者を検証用に変えて共通台帳を迂回する口**になる。
    足さないことが不変条件。`scripts/t_shared.py` が固定している。
    """
    """(user, error)。**最後の管理者は降格も無効化もできない**（締め出しの防止）。"""
    uid = norm_user_id(user_id)
    with _LOCK:
        try:
            doc = _read(for_write=True)
        except LedgerCorrupt as e:
            return None, f"利用者台帳が読めません（{e}）"
        rec = next((u for u in doc["users"] if u.get("user_id") == uid), None)
        if not rec:
            return None, "ユーザーが見つかりません"
        admins = [u for u in doc["users"] if u.get("role") == "admin" and u.get("active")]
        losing_admin = (rec.get("role") == "admin" and rec.get("active")
                        and ((role is not None and role != "admin") or active is False))
        if losing_admin and len(admins) <= 1:
            return None, ("最後の管理者です。先にもう1人を管理者にしてください"
                          "（誰も管理できない状態になります）")
        if name is not None:
            rec["name"] = str(name).strip()[:60] or rec["name"]
        if role in ROLE_LABEL:
            rec["role"] = role
        if active is not None:
            rec["active"] = bool(active)
            if not rec["active"]:
                revoke_user_sessions(uid)      # 無効化したら**その場で追い出す**
        rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        rec["updated_by"] = actor
        _write(doc)
        return _public(rec), ""


def password_change_blocked(user_id):
    """このアプリでは合言葉を変えられない理由。**空文字＝変えられる。**

    **文言も判定も1か所だけ。** 画面・入口・`set_password` の3か所で
    書くと、片方だけ条件が古くなる（`READY_WORDS` と同じ轍）。
    """
    if shared_on() and not _test_local(norm_user_id(user_id)):
        return ("パスワードは共通のアカウント管理で変更してください"
                "（5アプリ共通のため、ここでは変えられません）")
    return ""


def set_password(user_id, new_password, *, actor="", must_change=False):
    """合言葉を変える。**共通台帳が有効なら、ここでは変えない。**

    `login()` は共通台帳で照合するのに、この関数はローカルの `rec["password"]`
    に書く。**そのままだと「変更しました」と出て、次のログインは古い合言葉で通る。**
    管理者のリセットも同じで、**リセットしたつもりで何も変わらない。**エラーも出ない。

    付録Dで「パスワード変更で5アプリから追い出す」と決めた当の機能が、
    その変更自体を届かせないことになる。**断って、どこで変えるかを案内する。**
    """
    blocked = password_change_blocked(user_id)
    if blocked:
        return None, blocked
    err = check_password_policy(new_password)
    if err:
        return None, err
    uid = norm_user_id(user_id)
    with _LOCK:
        try:
            doc = _read(for_write=True)
        except LedgerCorrupt as e:
            return None, f"利用者台帳が読めません（{e}）"
        rec = next((u for u in doc["users"] if u.get("user_id") == uid), None)
        if not rec:
            return None, "ユーザーが見つかりません"
        rec["password"] = hash_password(new_password)
        rec["must_change"] = bool(must_change)
        rec["password_changed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        rec["updated_by"] = actor
        _write(doc)
    # 本人以外（管理者によるリセット）でも、**古いセッションは全部切る**
    revoke_user_sessions(uid)
    return _public(rec), ""


def delete(user_id, *, actor=""):
    uid = norm_user_id(user_id)
    with _LOCK:
        try:
            doc = _read(for_write=True)
        except LedgerCorrupt as e:
            return None, f"利用者台帳が読めません（{e}）"
        rec = next((u for u in doc["users"] if u.get("user_id") == uid), None)
        if not rec:
            return None, "ユーザーが見つかりません"
        admins = [u for u in doc["users"] if u.get("role") == "admin" and u.get("active")]
        if rec.get("role") == "admin" and rec.get("active") and len(admins) <= 1:
            return None, "最後の管理者は削除できません"
        doc["users"] = [u for u in doc["users"] if u.get("user_id") != uid]
        _write(doc)
    revoke_user_sessions(uid)
    return {"user_id": uid}, ""


def theme_of(rec):
    """その利用者の表示設定。**知らない値は既定へ倒す**（古いデータ・手書き対策）。"""
    t = (rec or {}).get("theme") or THEME_DEFAULT
    return t if t in THEMES else THEME_DEFAULT


def set_theme(user_id, theme):
    """表示（ライト／ダーク／端末に合わせる）。

    **本人の見た目だけ**を変える設定で、権限にも他人にも影響しない。
    だから管理者専用にしない（そうすると全員が頼みに来る）。

    サーバーにも持つのは、**別の端末でも同じ見え方**にするため。
    localStorage だけだと「スマホでは選べたのにPCでは戻る」になる。
    """
    if theme not in THEMES:
        return None, "表示の指定が不正です"
    uid = norm_user_id(user_id)
    with _LOCK:
        try:
            doc = _read(for_write=True)
        except LedgerCorrupt as e:
            return None, f"利用者台帳が読めません（{e}）"
        rec = next((u for u in doc["users"] if u.get("user_id") == uid), None)
        if not rec:
            return None, "ユーザーが見つかりません"
        rec["theme"] = theme
        _write(doc)
    return _public(rec), ""


def _public(rec):
    """画面へ返す形。**password は絶対に含めない**。

    **共通台帳が有効なら `must_change` は共通台帳の値を載せる。**

    ローカルの `must_change` は「ローカルの合言葉が仮のまま」という印だが、
    その合言葉はもう使われないうえ、**更新もされなくなって古い値のまま固まる**。
    そのままだと2通りに壊れる。どちらもエラーにならない:
      ・仮パスワードを変えたのに**止まり続ける**（`set_password` は断るので永久に）
      ・共通台帳で仮パスワードを配ったのに**どのアプリも止めない**

    伏せるだけでは後者が残る。**共通台帳の印を載せて、関門を生かす。**
    共通台帳が読めないときは False（＝止めない）。止める力は `active` が持つので、
    ここで True に倒して全員を変更画面へ閉じ込めない。
    検証用（`test` かつ `loopback_only`）はローカルのまま。
    """
    if rec and shared_on() and not (rec.get("test") and rec.get("loopback_only")):
        rec = dict(rec, must_change=_shared_must_change(rec.get("user_id")))
    out = {k: rec.get(k) for k in
           ("user_id", "name", "role", "active", "created_at", "created_by",
            "last_login", "must_change", "updated_at", "updated_by",
            "test", "loopback_only", "expires_on")}
    out["theme"] = theme_of(rec)
    return out


def public_users():
    return [_public(u) for u in users()]


# ─────────────────────── 共通台帳（/opt/accounts）───────────────────
# **5アプリで同じIDとパスワードを使うための層。**
#
# 持ち分を分ける:
#   /opt/accounts/users.json … **その人が誰か**（ID・氏名・合言葉）だけ
#   各アプリの config/users.json … **そのアプリを使えるか／どの役割か**
# 共通台帳に載っていても、アプリの台帳に居なければ入れない。**権限は増えない。**
#
# `ACCOUNTS_DIR` が無ければ**今までどおり**動く（既定は使わない側に倒す）。
ACCOUNTS_DIR = os.environ.get("ACCOUNTS_DIR", "")
AUTH_APP = os.environ.get("AUTH_APP", "")      # map.json を引く鍵（lpscope / calfc …）
_SHARED_CACHE = {"at": 0.0, "users": None, "map": None}
SHARED_TTL = 1.0                               # 1秒。毎リクエストのディスク読みを避ける


class SharedUnavailable(Exception):
    """共通台帳が読めない。**「全員停止」と読み替えない。**

    読めないことを理由に `active=false` 相当にすると、ファイルが一瞬読めない
    だけで**5アプリ同時に全員がログイン不能**になる。しかも何も表示されない。
    止める力は各アプリの `users.json` の `active` が持っているので、
    共通側が落ちても停止機能は死なない。**倒す向きは場所ごとに変える**:
      ログインの照合        … 読めなければ入れない（照合できない以上、通せない）
      作業中のセッション確認 … 判定なし（黙って切らない）
      ログイン失敗の回数    … ロックしていない扱い（全員締め出しより害が小さい）
    """


def shared_on():
    """**設定されているか。**「いま読めるか」とは別にする。

    ここを `os.path.isdir()` で判定すると、マウントが落ちる・権限が変わる・
    消えるだけで False になり、**黙ってローカル照合へ戻る**。
    ローカルの `password` は移行前の古いダイジェストなので、
    **旧パスワードが再び通る**。設定済みで読めないなら「入れない」へ倒す。
    """
    return bool(ACCOUNTS_DIR)


def _shared_load():
    """共通台帳と対応表を読む。**読めなければ例外。空を返さない。**"""
    now = time.time()
    c = _SHARED_CACHE
    if c["users"] is not None and (now - c["at"]) < SHARED_TTL:
        return c["users"], c["map"]
    try:
        with open(os.path.join(ACCOUNTS_DIR, "users.json"), encoding="utf-8") as f:
            doc = json.load(f)
        with open(os.path.join(ACCOUNTS_DIR, "map.json"), encoding="utf-8") as f:
            amap = json.load(f)
    except (OSError, ValueError) as e:
        raise SharedUnavailable(f"{ACCOUNTS_DIR} を読めません: {e}") from e
    if not isinstance(doc.get("users"), list) or not isinstance(amap, dict):
        raise SharedUnavailable(f"{ACCOUNTS_DIR} の形が違います")
    users_by_id = {u.get("user_id"): u for u in doc["users"] if u.get("user_id")}
    c.update(at=now, users=users_by_id, map=amap)
    return users_by_id, amap


def shared_get(common_id):
    return _shared_load()[0].get(common_id)


def _test_local(uid):
    """共通台帳の管理外として、**ローカルだけで完結させる記録か。**

    検証用アカウント（`test` かつ `loopback_only`）は共通台帳へ出さない
    （出すと5アプリのどこからでも使える普通の管理者になる）。
    ただし合言葉の照合まで共通台帳へ移すと、**ローカルにしか無いこの記録では
    ログインできなくなる。**そこでこの2つが揃った記録に限り、
    合言葉もローカルで見る。**条件は狭く、127.0.0.1 からしか使えない。**
    """
    try:
        rec = get(uid)
    except LedgerCorrupt:
        return False
    return bool(rec and rec.get("test") and rec.get("loopback_only"))


def local_id(common_id):
    """共通ID → このアプリのローカルID。**対応表に無ければ、同じIDを使う。**

    2026-09-15 に**5アプリのIDを共通IDへ一本化した**ので、対応表は
    「揃っていないものを書く場所」に変わった。以前は「載っていない＝使えない」
    だったが、それだと**綴りが変わらない人を載せ忘れただけで、
    その人だけが静かに入れなくなる**（実際に作り込んで、切替前に見つけた）。

    **そのアプリを使えるかどうかは、アプリ自身の `users.json` が決める。**
    `login()` は `rec = get(uid)` が無ければ通さないので、既定拒否は保たれる。
    対応表は認可ではなく、**綴りの差を吸収するためだけのもの**。
    """
    ent = _shared_load()[1].get(common_id)
    if not isinstance(ent, dict):
        return common_id
    return ent.get(AUTH_APP, common_id)


_BIND_AT = {"t": 0.0}


def binding_warning():
    """共通ログインの設定で、気づいておきたいこと。**空文字＝言うことは無い。**

    **ログインは止めない。** 2026-09-15 に5アプリのIDを共通IDへ一本化し、
    `local_id()` は対応表に無ければ同じIDを使うようになった。その結果
    **対応表が空なのが通常の状態**になる。以前ここで止める作りにしていたが、
    それだと**一本化が正しく済んだ状態で全員が入れなくなる**
    （こちらで作り込み、切り替え前に見つけた）。

    残すのは**気づくための合図**だけ。止め過ぎは止めなさ過ぎより害が大きい:
      ・対応表が空          … 一本化後の通常。**何も言わない**
      ・`AUTH_APP` が未設定  … 例外を1件も引けない
      ・項目はあるのに `AUTH_APP` の鍵がゼロ … 例外表を書いたのに効いていない。
        綴り違いの可能性が高い（`AUTH_APP=calendar` / 正しくは `calfc` で実際に起きた。
        13名全員が「割り当て無し」と出て、そのまま報告されかけた）

    **`shared_on()` を False へ倒して逃げない。** 倒すとローカル照合へ戻り、
    移行前の古い合言葉が復活する。
    """
    if not shared_on():
        return ""
    try:
        amap = _shared_load()[1]
    except SharedUnavailable:
        return ""                      # 読めないことは degraded() が別に言う
    if not amap:
        return ""                      # 例外が1件も無い＝一本化後の通常
    if not AUTH_APP:
        return "AUTH_APP が未設定です。対応表の例外が1件も効きません"
    if any(isinstance(v, dict) and v.get(AUTH_APP) for v in amap.values()):
        return ""
    return (f"AUTH_APP='{AUTH_APP}' の割り当てが対応表にありません。"
            "綴り違いの可能性があります（例外を書いたのに効いていません）")


_DENIED = {}                     # (uid, 理由) -> 直近に出した時刻
DENY_LOG_EVERY = 60             # 同じ組み合わせは1分に1回（総当たりでログを埋めない）


def _deny_reason(rec, pwrec, alive):
    """ログインを断った理由。**画面には出さない。journal 用。**"""
    if rec is None:
        return "このアプリの台帳に居ません"
    if not rec.get("active"):
        return "このアプリの台帳で停止されています"
    if shared_on() and pwrec is None:
        return "共通台帳に居ないか、このアプリの割り当てがありません"
    if shared_on() and pwrec is not rec and not pwrec.get("active", True):
        return "共通台帳で停止されています（5アプリ全部で入れません）"
    return "合言葉が違います"


def _note_denied(uid, why):
    """**なぜ入れないかを journal に出す。**画面の文言は変えない。

    止められた人と、ただの入力ミスが、画面では見分けられない
    （見分けられるようにすると、そのIDが在ることを漏らす）。
    運用側が `journalctl -u <app> | grep '[auth] 拒否'` で即答できるようにする。

    **凍結の有無で振る舞いを変えない。**停止中だけ数えないようにすると、
    9回叩いて凍結しないことで「このIDは停止されている」と分かる
    ——文言より観測しやすい印を足すことになる（2026-09-15 カレンダー指摘）。
    """
    now = time.time()
    k = (uid, why)
    if now - _DENIED.get(k, 0) > DENY_LOG_EVERY:
        _DENIED[k] = now
        print(f"[auth] 拒否: {uid} — {why}", file=sys.stderr, flush=True)


def _note_bind(why):
    """**黙らせない。** 帯と journal の両方に出す（止めはしない）。"""
    if not why:
        return
    now = time.time()
    if now - _BIND_AT["t"] > DEGRADED_LOG_EVERY:
        _BIND_AT["t"] = now
        print(f"[auth] 共通ログインの設定を確認してください: {why}",
              file=sys.stderr, flush=True)


# ── ログイン失敗の回数は5アプリで共有する ──────────────────────
# プロセス内の `_FAILS` だけだと、8回/5分 が5アプリぶん独立して **40回/5分** になる。
# 書ける場所は `state/` だけに絞る（`users.json` は読み取り専用のまま）。
def _fails_path():
    return os.path.join(ACCOUNTS_DIR, "state", "fails.json")


def _shared_fails():
    """{uid: [回数, 時刻]}。**壊れていたら「ロックしていない」と読む。**"""
    try:
        with open(_fails_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


FAILS_MAX = 500                # 安全弁（下の理由を参照）。**捨てるのは回数の少ないほう**


def _prune_fails(d):
    """古い項目を落とす。**時刻だけで決める。**

    ログイン画面は外から叩けるので、総当たりが無作為なIDを投げるほど項目が増える。
    失敗のたびに**全体を読んで書き直す**作りなので、放っておくと重くなる。
    掃除は**読むときではなく書くとき**にやる —— 読むときにやると、
    攻撃が続いている間は誰も掃除できない（実測 2026-09-15: 8件のうち6件が
    既に窓より古く、凍結には一切効いていなかった）。

    **件数の上限で「古い順に」捨てない。** それをやると、攻撃側が無作為なIDを
    大量に投げるだけで**本物の凍結を押し出せる**（2026-09-15 カレンダー指摘）。
    安全弁の `FAILS_MAX` に当たったときも捨てるのは**回数の少ないほう**にする ——
    1回しか失敗していない項目をいくら積んでも、8回まで来ている人は残る。
    """
    now = time.time()
    out = {}
    for k, v in d.items():
        if not k:                      # 空のIDは数えない（誰にも当たらない）
            continue
        if not isinstance(v, list) or len(v) < 2:
            continue                   # 壊れた項目はここで落とす（読む側は {} と読む）
        try:
            if (now - float(v[1])) < FAILS_WINDOW:
                out[k] = v
        except (TypeError, ValueError):
            continue
    if len(out) > FAILS_MAX:
        out = dict(sorted(out.items(), key=lambda kv: -kv[1][0])[:FAILS_MAX])
    return out


def _shared_fail_bump(uid, reset=False):
    if not shared_on():
        return
    if not reset and not uid:
        return                         # **数える前に弾く**
    try:
        d = _prune_fails(_shared_fails())
        if reset:
            d.pop(uid, None)
        else:
            n = (d.get(uid) or [0, 0])[0]
            d[uid] = [n + 1, time.time()]
        os.makedirs(os.path.dirname(_fails_path()), exist_ok=True)
        # **固定名にしない。**5アプリは別の unix ユーザーで、同時に書くと
        # 奪い合って壊れる。壊れは `{}` と読む設計なので**黙って回数が消える**
        tmp = f"{_fails_path()}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        # **umask 任せにしない。0640。**
        #   other … 読ませない（置き場所が 0770 でも、権限がいつ緩むか分からない）
        #   group … **読める必要がある。**5アプリで回数を足すのがこの機能なので、
        #           **0600 にしてはいけない**。読めなくなると `_shared_fails()` が
        #           OSError を飲んで {}（＝ロックしていない）を返し、
        #           **8回/5分 が黙って 40回/5分 へ戻る**（エラーは出ない）
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        # **一時ファイル → os.replace の形を崩さない。**
        # 所有者でないアプリが書けるのは `state/` が group 書き込み可（2770・setgid）
        # だからで、**ファイル自身への書き込み権ではない**。実測: keiei 所有の
        # fails.json へ autogrowth が直接 open すると Permission denied。
        # `open(path, "w")` に簡略化すると所有者以外すべてで失敗し、
        # やはり {} として飲まれる。setgid のおかげで新しいファイルの group が
        # funcreate-auth になる点も、崩すと同じ結果になる
        os.replace(tmp, _fails_path())
    except OSError:
        pass                            # 数えられなくてもログインは止めない


# ─────────────────────────── ログイン ───────────────────────────
def _locked_out(uid):
    n, last = _FAILS.get(uid, (0, 0.0))
    if shared_on():                    # 5アプリぶんを足す（40回/5分にしない）
        sn, slast = (_shared_fails().get(uid) or [0, 0.0])[:2]
        if sn > n:
            n, last = sn, slast
    if n >= LOCK_FAILS and (time.time() - last) < LOCK_SECONDS:
        return int(LOCK_SECONDS - (time.time() - last))
    return 0


def login(user_id, password, ip=""):
    """(session, error)。**失敗理由は「IDかパスワードが違います」に統一**する
    （IDの存在有無を教えると、総当たりの的を絞らせてしまう）。"""
    typed = norm_user_id(user_id)
    if not _ledger_readable():
        # **台帳が読めないまま通さない。**退避の内容で照合すると、
        # その間に停止した人が入れてしまう
        return None, "利用者台帳が読めません。管理者にご連絡ください"
    # **空のIDは数える前に弾く。** 誰にも当たらないので数えても意味が無く、
    # 5アプリ共通の置き場に `""` の箱ができる（実測 2026-09-15: 3件たまっていた）。
    # 返す文言は変えない（存在の有無を教えない）
    if not typed:
        return None, "ログインIDまたはパスワードが違います"
    wait = _locked_out(typed)
    if wait:
        # **秒のときは秒で言う。**待ちが30秒になったので、
        # 「あと約1分」と出すと実際より長く待たせることになる
        left = f"{wait}秒" if wait < 60 else f"{wait // 60 + 1}分"
        return None, f"ログインの失敗が続いたため一時的に停止しています（あと約{left}）"

    # 合言葉を確かめる先と、アプリの中で使うIDを決める。
    # 共通台帳が有効なら **合言葉は共通台帳、それ以外はローカルの記録**が決める。
    uid, pwrec = typed, None
    if shared_on() and not _test_local(typed):
        # **ここで止めない。** 対応表に無ければ同じIDを使う作りになったので、
        # 載せ忘れでは壊れない。設定の不備は `degraded()` と journal で知らせる
        # （止める作りにしていたため、一本化後の正常な状態で全員が入れなくなっていた）
        _note_bind(binding_warning())
        try:
            pwrec = shared_get(typed)
            lid = local_id(typed)
        except SharedUnavailable:
            # **照合できない以上、通してはいけない。**障害として表に出す。
            # ここでローカル照合へ戻すと、移行前の**古い合言葉が復活する**
            return None, "ログインの台帳が読めません。管理者にご連絡ください"
        # 共通台帳に居ない／このアプリに割り当てが無い＝**入れない**。
        # 理由は言わない（存在の有無を教えると総当たりの的を絞らせる）。
        # **決められないときは uid を決めない**（順序を変えると通る形を残さない）
        if pwrec is None or lid is None:
            pwrec = None
        else:
            uid = lid
    try:
        rec = get(uid)
    except LedgerCorrupt:
        return None, "利用者台帳が読めません。管理者にご連絡ください"
    if pwrec is None and not shared_on():
        pwrec = rec
    elif _test_local(typed):
        pwrec = rec              # 検証用（loopback専用）はローカルで完結

    # **検証用アカウントは手元(127.0.0.1)からしか使えない**。
    # サーバーに載せても外部からログインできないので、常時有効でも踏み台にならない。
    # **この2つはローカルの記録で見る。**共通台帳へ出すと、5アプリのどこからでも
    # 使える普通の管理者になってしまう
    if rec and rec.get("loopback_only") and str(ip).strip() not in LOOPBACK:
        return None, "このアカウントはこのサーバー上（127.0.0.1）からのみ利用できます"
    if rec and rec.get("expires_on") and time.strftime("%Y-%m-%d") > str(rec["expires_on"]):
        return None, f"このアカウントは {rec['expires_on']} で期限切れです"

    # 停止は**どちらかが false なら入れない**。
    # 共通台帳＝全アプリ停止（退職・端末紛失）／ローカル＝そのアプリだけ停止
    alive = bool(rec) and rec.get("active") and (
        pwrec is rec or bool(pwrec) and pwrec.get("active", True))
    ok = alive and bool(pwrec) and verify_password(pwrec, password or "")
    if not ok:
        # **利用者への文言は変えない。**理由を出すと「そのIDは在る」が漏れる。
        # ただし運用側は「なぜ入れないか」を即答できる必要がある。
        # 止められた人・割り当ての無い人と、ただの入力ミスが、画面では見分けられない
        _note_denied(typed, _deny_reason(rec, pwrec, alive))
        n, _ = _FAILS.get(typed, (0, 0.0))
        _FAILS[typed] = (n + 1, time.time())
        _shared_fail_bump(typed)
        # **止められた人には、止められたと伝える。** 文言を分けると
        # 「そのIDが在る」ことは漏れるが、漏れるのは**停止済みで使えないID**
        # だけで、生きているIDは総当たりから見分けられない（合言葉違いは
        # 下の一般的な文言に倒れる）。一方、伝えないと本人は入力ミスだと思って
        # 叩き続け、8回で凍結する。**割に合わない。**
        #
        # **どちらの台帳で止めても同じことを言う。** 以前はローカルだけを見ており、
        # 共通台帳で止められた人には「IDかパスワードが違います」と出ていた。
        # 同じ状況なのに案内が変わり、**5アプリ全部で止められた人ほど
        # 原因に辿り着けない**。`pwrec is None`（割り当てが無い・共通台帳に居ない）は
        # 停止とは別なので、ここには含めない。
        shared_off = (shared_on() and pwrec is not None and pwrec is not rec
                      and not pwrec.get("active", True))
        if (rec and not rec.get("active")) or shared_off:
            return None, "このアカウントは無効化されています。管理者にご連絡ください"
        return None, "ログインIDまたはパスワードが違います"
    _FAILS.pop(typed, None)
    _shared_fail_bump(typed, reset=True)
    try:
        with _LOCK:
            doc = _read(for_write=True)
            for u in doc["users"]:
                if u.get("user_id") == uid:
                    u["last_login"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _write(doc)
    except LedgerCorrupt:
        # **最終ログインが書けないだけでログインを止めない。**
        # ただし空の台帳で上書きはしない（以前はここで全員が消えた）
        pass
    return _new_session(uid, ip), ""


def _digest(sid):
    return hashlib.sha256((sid or "").encode("utf-8")).hexdigest()


def _load_sessions():
    """起動時に読み戻す。**保存されているのはハッシュだけ**なので中身は復元しない。"""
    try:
        rows = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    now = time.time()
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("h") and (r.get("expires") or 0) > now:
            _SESSIONS[r["h"]] = {"h": r["h"], "user_id": r.get("user_id", ""),
                                 "expires": r["expires"], "ip": r.get("ip", ""),
                                 "created": r.get("created", now)}


def _persist():
    try:
        SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SESSIONS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(list(_SESSIONS.values()), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, SESSIONS_PATH)
        os.chmod(SESSIONS_PATH, 0o600)
    except Exception:
        pass


def _new_session(uid, ip=""):
    sid = secrets.token_urlsafe(32)
    h = _digest(sid)
    _SESSIONS[h] = {"h": h, "user_id": uid, "expires": time.time() + SESSION_TTL,
                    "ip": ip, "created": time.time()}
    _sweep()
    _persist()
    return {"sid": sid, "h": h, "user_id": uid}


def _sweep():
    now = time.time()
    for h in [k for k, v in _SESSIONS.items() if v["expires"] < now]:
        _SESSIONS.pop(h, None)


def session_of(sid):
    """有効なセッションと、その利用者を返す。無効なら (None, None)。"""
    if not sid:
        return None, None
    h = _digest(sid)
    s = _SESSIONS.get(h)
    if not s or s["expires"] < time.time():
        if s:
            _SESSIONS.pop(h, None)
            _persist()
        return None, None
    u = get(s["user_id"])
    if not u:
        # **「読めない」を「その利用者は消された」と読み替えない。**
        # `users()` は台帳が読めないとき空を返すので、ここで捨てると
        # **台帳が読めないあいだ、全員のセッションが1リクエストごとに消え、
        # `.sessions.json` にも確定する**。直しても全員がログインし直しになる。
        # 台帳が読めているのに居ない＝本当に消された人だけを切る。
        # `_ledger_readable()` を呼ぶのは `u` が None のときだけなので負担にならない
        if not _ledger_readable():
            return None, None             # 読めないだけ。セッションは捨てない
        _SESSIONS.pop(h, None)
        _persist()
        return None, None
    if not u.get("active"):
        _SESSIONS.pop(h, None)            # 無効化されたユーザーのセッションは即座に切る
        _persist()
        return None, None
    if shared_on() and not _shared_allows(s):
        _SESSIONS.pop(h, None)
        _persist()
        return None, None
    s["expires"] = time.time() + SESSION_TTL      # 操作のたびに延長（無操作で切れる）
    return s, _public(u)


def _shared_allows(s):
    """共通台帳から見て、このセッションを続けてよいか。

    **合言葉が共通台帳へ移ると、`set_password()` が呼ぶ `revoke_user_sessions()` は
    そのプロセスのセッションしか切れない。**つまり「パスワードを変えて追い出す」が
    5アプリに効かなくなる。そこで2つを毎回見る:
      `active`              … false なら5アプリ即停止（1か所で済む）
      `password_changed_at` … これより古いセッションは無効。
                              変えれば5アプリから同時に追い出せる

    **読めないときは True（判定なし）。**ここで False に倒すと、台帳が一瞬
    読めないだけで作業中の全員が黙って切れる。止める力はローカルの `active` が持つ。
    """
    try:
        cid = _local_to_common(s["user_id"])
        rec = shared_get(cid) if cid else None
    except SharedUnavailable as e:
        # **黙って通さない。**このあいだ「停止したはずの人」が5アプリに入れたままになる。
        # 監査ログの EVENTS 登録漏れで19種類が黙って捨てられていたのと同じ壊れ方
        _note_shared_down(e)
        return True
    if rec is None:
        # 共通台帳の管理外（検証用など）は触らない。
        # **ここが「map.json から消しただけでは剥奪にならない」理由。**
        # `_local_to_common` も map に無ければ None を返すので、
        # 生きているセッションはそのまま残る。
        # **剥奪は必ず `active=false`。map の削除だけで済ませない。**
        return True
    if not rec.get("active", True):
        return False
    changed = rec.get("password_changed_at") or ""
    if not changed:
        return True
    # **書く側と同じ時計であることが前提**（同じホストのローカル時刻）。
    # 数値ならエポック秒として読む（時計の解釈を挟まずに済む形も許す）
    try:
        t = (float(changed) if str(changed).replace(".", "", 1).isdigit()
             else time.mktime(time.strptime(str(changed), "%Y-%m-%d %H:%M:%S")))
    except (ValueError, OverflowError):
        return True
    return float(s.get("created") or 0) >= t


def _shared_must_change(uid):
    """共通台帳の `must_change`。読めなければ False（閉じ込めない）。"""
    try:
        cid = _local_to_common(uid)
        rec = shared_get(cid) if cid else None
    except SharedUnavailable:
        return False
    return bool(rec and rec.get("must_change"))


def _local_to_common(uid):
    """ローカルID → 共通ID。map.json を逆から引く。"""
    try:
        amap = _shared_load()[1]
    except SharedUnavailable:
        return None
    for cid, apps in amap.items():
        if isinstance(apps, dict) and apps.get(AUTH_APP) == uid:
            return cid
    # **対応表に無ければ、同じIDとみなす**（`local_id` と対にする）。
    # ここで None を返すと `_shared_allows` が「管理外」として素通りさせ、
    # **共通台帳での停止とパスワード変更が効かなくなる**
    return uid


def logout(sid):
    if _SESSIONS.pop(_digest(sid), None):
        _persist()


def revoke_user_sessions(uid):
    hit = [k for k, v in _SESSIONS.items() if v["user_id"] == uid]
    for h in hit:
        _SESSIONS.pop(h, None)
    if hit:
        _persist()


def active_sessions():
    _sweep()
    out = {}
    for s in _SESSIONS.values():      # 1ユーザー複数端末をまとめて1行にする
        cur = out.setdefault(s["user_id"], {"user_id": s["user_id"], "count": 0, "since": s["created"]})
        cur["count"] += 1
        cur["since"] = min(cur["since"], s["created"])
    return [{**v, "since": time.strftime("%Y-%m-%d %H:%M", time.localtime(v["since"]))}
            for v in out.values()]


def cookie_header(sid, secure, max_age=SESSION_TTL):
    """Set-Cookie。**Secure は HTTPS のときだけ**（httpで付けると保存されずログインできない）。"""
    parts = [f"{SESSION_COOKIE}={sid}", "Path=/", "HttpOnly", "SameSite=Strict",
             f"Max-Age={int(max_age)}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header(secure):
    return cookie_header("", secure, max_age=0)


_load_sessions()


def parse_cookie(header):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == SESSION_COOKIE:
            return v
    return ""
