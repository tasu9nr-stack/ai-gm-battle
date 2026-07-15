import random
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


def assign_random_passive(player_id: str) -> dict | None:
    """今日まだ誰にも配られていないカタログ内パッシブを1つランダムに割り当てる。
    プール枯渇時はNoneを返す（呼び出し側で自己申告フローに切り替える）。"""
    for _ in range(5):  # 同時アクセスによる重複競合時の簡易リトライ
        with _conn() as conn:
            used = _used_catalog_indices_today(conn)
            available = [i for i in range(len(PASSIVE_CATALOG)) if i not in used]
            if not available:
                return None
            idx = random.choice(available)
            entry = PASSIVE_CATALOG[idx]
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
    return {"name": text, "desc": text, "is_custom": True}


def get_or_assign_daily_passive(player_id: str) -> dict:
    cached = get_daily_passive(player_id)
    if cached:
        return cached
    assigned = assign_random_passive(player_id)
    if assigned:
        return assigned
    return {"pool_exhausted": True}
