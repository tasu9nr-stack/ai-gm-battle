import hashlib
import random
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "game.db"
JST = timezone(timedelta(hours=9))

# 全ユーザー共通のパッシブプール。日ごとに重複なく配布される。
PASSIVE_CATALOG = [
    {"name": "不屈の闘志", "desc": "HPが3割以下になると行動の威力が増す。"},
    {"name": "疾風の反射", "desc": "相手の動きを読みやすく、被弾を軽減しやすい。"},
    {"name": "業火の刻印", "desc": "攻撃的な行動のたびに、じわじわ相手を焼く。"},
    {"name": "鉄壁の意志", "desc": "防御的な行動の効果が通常より高まる。"},
    {"name": "幸運の女神", "desc": "行動の結果に予想外の追い風が吹くことがある。"},
    {"name": "氷結の呪縛", "desc": "相手の動きを鈍らせる行動の効果が高まる。"},
    {"name": "再生の血脈", "desc": "毎ターン、わずかにHPが回復する。"},
    {"name": "一撃必殺の型", "desc": "命中すれば大きいが、外れれば隙が大きい。"},
    {"name": "見切りの極意", "desc": "相手の攻撃的な行動を見切りやすくなる。"},
    {"name": "毒牙の一撃", "desc": "攻撃が当たると、後を引くダメージを残す。"},
    {"name": "癒しの残光", "desc": "守りに徹するほど、回復の恩恵が大きくなる。"},
    {"name": "嵐を呼ぶ者", "desc": "行動の結果が大きく振れやすい、ハイリスクな加護。"},
    {"name": "石の心臓", "desc": "受けるダメージの上限がわずかに抑えられる。"},
    {"name": "影縫いの手", "desc": "搦め手・妨害系の行動が刺さりやすくなる。"},
    {"name": "闘気凝縮", "desc": "同じ系統の行動を続けるほど威力が乗ってくる。"},
    {"name": "百戦錬磨", "desc": "行動の結果が安定し、大きな失敗をしにくい。"},
    {"name": "月光の加護", "desc": "劣勢のときほど幸運が味方しやすくなる。"},
    {"name": "破壊衝動", "desc": "攻めれば攻めるほど威力が増すが、隙も生まれる。"},
    {"name": "静寂の呼吸", "desc": "様子見・防御的な行動の直後、次の一手が冴える。"},
    {"name": "生命奪取", "desc": "攻撃を当てた分だけ、わずかに自分も回復する。"},
    {"name": "雷光の加速", "desc": "素早い行動を取ると追加の一手が発生しやすい。"},
    {"name": "呪詛返し", "desc": "相手の妨害的な行動を受け流し、逆手に取りやすい。"},
    {"name": "不動の構え", "desc": "防御行動を続けるほど、被ダメージが減っていく。"},
    {"name": "混沌の采配", "desc": "行動の結果を完全にランダムに引き寄せる。"},
    {"name": "深淵の瞳", "desc": "相手の弱点を見抜き、狙った一撃が刺さりやすい。"},
    {"name": "自己再生", "desc": "何もせず様子を見ると、大きくHPが戻る。"},
    {"name": "怨嗟の鎖", "desc": "相手の動きを縛るような行動の持続効果が伸びる。"},
    {"name": "鏡面の守り", "desc": "受けた攻撃の一部を相手に跳ね返す。"},
    {"name": "狂乱の一撃", "desc": "HPが低いほど、放つ一撃が凶暴化する。"},
    {"name": "天秤の加護", "desc": "劣勢側に自動的にわずかな補正がかかる。"},
]


def _today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# 管理者が採用した自己申告パッシブは、静的カタログとは別テーブルに追加し、
# catalog_index = CATALOG_EXTRA_OFFSET + catalog_extra.id として合算プールで扱う。
# （静的リストの件数が変わってもインデックスがずれないようにオフセットを大きく取る）
CATALOG_EXTRA_OFFSET = 1000


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_assignment (
                player_id TEXT PRIMARY KEY,
                passive_name TEXT NOT NULL,
                passive_desc TEXT NOT NULL,
                is_custom INTEGER NOT NULL,
                catalog_index INTEGER,
                assigned_date TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_catalog
            ON daily_assignment(assigned_date, catalog_index)
            WHERE catalog_index IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_extra (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                desc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id TEXT NOT NULL,
                text TEXT NOT NULL,
                submitted_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                player_id TEXT PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                login_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_date TEXT NOT NULL,
                email TEXT,
                email_verified INTEGER NOT NULL DEFAULT 0,
                verify_token TEXT,
                verify_token_expires TEXT
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL"
        )


def reset_all_data() -> None:
    """全ゲームデータを削除して初期状態に戻す（アカウント・パッシブ・ポイント・申請すべて）。
    テーブル構造はinit_db()で直後に再作成される。"""
    with _conn() as conn:
        for table in ("users", "daily_assignment", "players", "custom_submissions", "catalog_extra"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def create_user(username: str, login_id: str, email: str, password: str) -> dict | None:
    """アカウントを新規作成する。ユーザー名（表示名）は重複可、ログインIDは重複不可。
    ログインIDまたはメールが既に使われていればNoneを返す。"""
    username = username.strip()[:30]
    login_id = login_id.strip()[:30]
    email = email.strip().lower()[:200]
    if not username or not login_id or not password or "@" not in email:
        return None
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)
    verify_token = secrets.token_urlsafe(32)
    expires = (datetime.now(JST) + timedelta(hours=24)).isoformat()
    with _conn() as conn:
        try:
            conn.execute(
                """
                INSERT INTO users
                    (login_id, username, password_hash, salt, created_date, email, email_verified, verify_token, verify_token_expires)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (login_id, username, password_hash, salt, _today(), email, verify_token, expires),
            )
        except sqlite3.IntegrityError:
            return None
    return {"player_id": login_id, "email": email, "verify_token": verify_token}


def force_verify(login_id: str) -> None:
    """メール送信が未設定な環境（ローカル開発など）で確認をスキップする。"""
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET email_verified = 1, verify_token = NULL WHERE login_id = ?",
            (login_id,),
        )


def verify_email(token: str) -> str | None:
    """確認トークンを検証し、成功したログインIDを返す。失敗/期限切れならNone。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT login_id, verify_token_expires FROM users WHERE verify_token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        if row["verify_token_expires"] and datetime.fromisoformat(row["verify_token_expires"]) < datetime.now(JST):
            return None
        conn.execute(
            "UPDATE users SET email_verified = 1, verify_token = NULL WHERE login_id = ?",
            (row["login_id"],),
        )
    return row["login_id"]


def verify_user(login_id: str, password: str) -> dict | None:
    """ログインID+パスワードを検証する。不一致ならNone、メール未確認ならemail_not_verifiedを立てて返す。"""
    login_id = login_id.strip()[:30]
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE login_id = ?", (login_id,)
        ).fetchone()
    if row is None:
        return None
    if not secrets.compare_digest(_hash_password(password, row["salt"]), row["password_hash"]):
        return None
    if row["email"] and not row["email_verified"]:
        return {"email_not_verified": True}
    return {"player_id": login_id, "username": row["username"]}


def update_username(login_id: str, new_username: str) -> str | None:
    """表示名を変更する。ログインIDは変わらない。成功したら新しい表示名を返す。"""
    new_username = new_username.strip()[:30]
    if not new_username:
        return None
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE users SET username = ? WHERE login_id = ?", (new_username, login_id)
        )
    return new_username if cur.rowcount > 0 else None


def _row_to_passive(row: sqlite3.Row) -> dict:
    return {
        "name": row["passive_name"],
        "desc": row["passive_desc"],
        "is_custom": bool(row["is_custom"]),
    }


def get_daily_passive(player_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM daily_assignment WHERE player_id = ?", (player_id,)
        ).fetchone()
    if row is None or row["assigned_date"] != _today():
        return None
    return _row_to_passive(row)


def _used_catalog_indices_today(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        "SELECT catalog_index FROM daily_assignment WHERE assigned_date = ? AND catalog_index IS NOT NULL",
        (_today(),),
    ).fetchall()
    return {r["catalog_index"] for r in rows}


def _all_catalog_entries(conn: sqlite3.Connection) -> dict[int, dict]:
    """静的カタログ + 管理者が採用したカタログを合算し、{index: {"name","desc"}} で返す。"""
    entries = {i: p for i, p in enumerate(PASSIVE_CATALOG)}
    rows = conn.execute("SELECT id, name, desc FROM catalog_extra").fetchall()
    for r in rows:
        entries[CATALOG_EXTRA_OFFSET + r["id"]] = {"name": r["name"], "desc": r["desc"]}
    return entries


def assign_random_passive(player_id: str) -> dict | None:
    """今日まだ誰にも配られていないカタログ内パッシブを1つランダムに割り当てる。
    プール枯渇時はNoneを返す（呼び出し側で自己申告フローに切り替える）。"""
    for _ in range(5):  # 同時アクセスによる重複競合時の簡易リトライ
        with _conn() as conn:
            catalog = _all_catalog_entries(conn)
            used = _used_catalog_indices_today(conn)
            available = [i for i in catalog if i not in used]
            if not available:
                return None
            idx = random.choice(available)
            entry = catalog[idx]
            try:
                conn.execute(
                    """
                    INSERT INTO daily_assignment
                        (player_id, passive_name, passive_desc, is_custom, catalog_index, assigned_date)
                    VALUES (?, ?, ?, 0, ?, ?)
                    ON CONFLICT(player_id) DO UPDATE SET
                        passive_name = excluded.passive_name,
                        passive_desc = excluded.passive_desc,
                        is_custom = excluded.is_custom,
                        catalog_index = excluded.catalog_index,
                        assigned_date = excluded.assigned_date
                    """,
                    (player_id, entry["name"], entry["desc"], idx, _today()),
                )
            except sqlite3.IntegrityError:
                continue  # 別プレイヤーに先を越された。空きを引き直す
            return {"name": entry["name"], "desc": entry["desc"], "is_custom": False}
    return None


def assign_custom_passive(player_id: str, text: str) -> dict:
    text = text.strip()[:15] or "無名の力"
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_assignment
                (player_id, passive_name, passive_desc, is_custom, catalog_index, assigned_date)
            VALUES (?, ?, ?, 1, NULL, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                passive_name = excluded.passive_name,
                passive_desc = excluded.passive_desc,
                is_custom = excluded.is_custom,
                catalog_index = excluded.catalog_index,
                assigned_date = excluded.assigned_date
            """,
            (player_id, text, text, _today()),
        )
        conn.execute(
            "INSERT INTO custom_submissions (player_id, text, submitted_date) VALUES (?, ?, ?)",
            (player_id, text, _today()),
        )
    return {"name": text, "desc": text, "is_custom": True}


def list_pending_submissions() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM custom_submissions WHERE status = 'pending' ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def adopt_submission(submission_id: int) -> dict | None:
    """自己申告パッシブを正式にカタログへ採用し、以後のガチャ抽選対象に加える。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM custom_submissions WHERE id = ? AND status = 'pending'",
            (submission_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "INSERT INTO catalog_extra (name, desc) VALUES (?, ?)", (row["text"], row["text"])
        )
        conn.execute(
            "UPDATE custom_submissions SET status = 'adopted' WHERE id = ?", (submission_id,)
        )
    return {"text": row["text"]}


def reject_submission(submission_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE custom_submissions SET status = 'rejected' WHERE id = ? AND status = 'pending'",
            (submission_id,),
        )
    return cur.rowcount > 0


def get_or_assign_daily_passive(player_id: str) -> dict:
    cached = get_daily_passive(player_id)
    if cached:
        return cached
    assigned = assign_random_passive(player_id)
    if assigned:
        return assigned
    return {"pool_exhausted": True}


SUBMIT_PASSIVE_COST = 10


def get_points(player_id: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT points FROM players WHERE player_id = ?", (player_id,)
        ).fetchone()
    return row["points"] if row else 0


def add_point(player_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO players (player_id, points) VALUES (?, 1)
            ON CONFLICT(player_id) DO UPDATE SET points = points + 1
            """,
            (player_id,),
        )


def spend_points(player_id: str, amount: int) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT points FROM players WHERE player_id = ?", (player_id,)
        ).fetchone()
        current = row["points"] if row else 0
        if current < amount:
            return False
        conn.execute(
            "UPDATE players SET points = points - ? WHERE player_id = ?", (amount, player_id)
        )
    return True


def submit_custom_passive_via_points(player_id: str, text: str) -> dict | None:
    """ポイントを消費して自作パッシブを管理者レビュー待ちキューに申請する。
    ポイント不足時はNoneを返す。"""
    text = text.strip()[:15]
    if not text:
        return None
    if not spend_points(player_id, SUBMIT_PASSIVE_COST):
        return None
    with _conn() as conn:
        conn.execute(
            "INSERT INTO custom_submissions (player_id, text, submitted_date) VALUES (?, ?, ?)",
            (player_id, text, _today()),
        )
    return {"text": text, "points": get_points(player_id)}
