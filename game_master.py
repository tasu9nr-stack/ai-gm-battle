import json
import os
import random
import re

CATEGORIES = ["攻撃", "防御", "特殊", "カウンター"]

# 相性表: 攻撃はどれにも勝てない。防御/特殊/カウンターは三すくみ。
WIN_MAP = {
    "防御": ["攻撃", "カウンター"],
    "特殊": ["攻撃", "防御"],
    "カウンター": ["特殊", "攻撃"],
    "攻撃": [],
}

RELATION_TEXT = (
    "相性表: 攻撃は防御・特殊・カウンターいずれにも不利。"
    "防御はカウンターに強くカウンターは特殊に強く特殊は防御に強い、という三すくみ。"
    "同じカテゴリ同士は相性なし。"
)

_KEYWORDS = {
    "攻撃": ["殴", "斬", "撃", "突", "蹴", "叩", "打", "攻撃", "切りつけ", "斬撃", "殺"],
    "防御": ["守", "防", "構え", "耐え", "ガード", "受け止め", "盾", "かばう"],
    "特殊": ["魔法", "呪", "術", "特殊", "スキル", "能力", "奇襲", "幻惑", "変化", "パッシブ"],
    "カウンター": ["反撃", "カウンター", "受け流", "返す", "いなす", "捌く"],
}

_client = None
_MODEL = "claude-sonnet-5"
_TIMEOUT_SECONDS = 15.0


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(timeout=_TIMEOUT_SECONDS)
    return _client


def _has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no json object found in model output")
    return json.loads(match.group(0))


def category_advantage(cat_a: str, cat_b: str) -> str | None:
    if cat_a == cat_b:
        return None
    if cat_b in WIN_MAP.get(cat_a, []):
        return "A"
    if cat_a in WIN_MAP.get(cat_b, []):
        return "B"
    return None


def resolve_turn(state: dict, action_a: dict, action_b: dict) -> dict:
    """
    state: {
      "passive_a": {...}, "passive_b": {...}, "hp_a": int, "hp_b": int,
      "stage": str, "log": [str, ...]
    }
    action_a/action_b: {"category": one of CATEGORIES, "text": str}
    Returns: {"narration": str, "hp_delta_a": int, "hp_delta_b": int, "game_over": bool}
    """
    if not _has_api_key():
        return _mock_resolve_turn(state, action_a, action_b)
    try:
        client = _get_client()
        prompt = {
            "stage": state.get("stage", ""),
            "passive_a": state["passive_a"],
            "passive_b": state["passive_b"],
            "hp_a": state["hp_a"],
            "hp_b": state["hp_b"],
            "action_a": action_a,
            "action_b": action_b,
            "recent_log": state.get("log", [])[-8:],
        }
        message = client.messages.create(
            model=_MODEL,
            max_tokens=600,
            system=(
                "あなたは1対1バトルTRPGのゲームマスターです。"
                "プレイヤーA・Bはそれぞれ「宣言カテゴリ（攻撃/防御/特殊/カウンターのいずれか）」と"
                "「自由記述の行動テキスト」を提出します。"
                f"{RELATION_TEXT}"
                "判定手順: まずAとBそれぞれについて、宣言テキストの内容が宣言カテゴリと矛盾していないかを判定してください。"
                "両者とも矛盾がなければ、相性表の有利不利をHP増減に強く反映してください。"
                "どちらか一方でも矛盾していれば、その回は相性表を一切適用せず、行動内容の説得力・状況にふさわしさだけで自由に判定してください。"
                "舞台設定と直近の戦闘ログを踏まえ、物語として自然につながるナレーションにしてください。"
                "HP増減は概ね-30〜+15の範囲を目安にしてください。"
                "出力は次のJSON形式のみ: "
                '{"narration": "このターンの結果を描写する日本語の文章", '
                '"hp_delta_a": 整数, "hp_delta_b": 整数}'
            ),
            messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        data = _extract_json(text)
        narration = str(data["narration"])
        hp_delta_a = int(data["hp_delta_a"])
        hp_delta_b = int(data["hp_delta_b"])
        new_hp_a = state["hp_a"] + hp_delta_a
        new_hp_b = state["hp_b"] + hp_delta_b
        return {
            "narration": narration,
            "hp_delta_a": hp_delta_a,
            "hp_delta_b": hp_delta_b,
            "game_over": new_hp_a <= 0 or new_hp_b <= 0,
        }
    except Exception:
        pass
    return _mock_resolve_turn(state, action_a, action_b)


def _keyword_match(category: str, text: str) -> bool:
    return any(kw in text for kw in _KEYWORDS.get(category, []))


def _mock_resolve_turn(state: dict, action_a: dict, action_b: dict) -> dict:
    cat_a, text_a = action_a["category"], action_a["text"]
    cat_b, text_b = action_b["category"], action_b["text"]

    match_a = _keyword_match(cat_a, text_a)
    match_b = _keyword_match(cat_b, text_b)

    dmg_to_a = random.randint(5, 20)
    dmg_to_b = random.randint(5, 20)

    note = ""
    if match_a and match_b:
        adv = category_advantage(cat_a, cat_b)
        if adv == "A":
            dmg_to_b = round(dmg_to_b * 1.6)
            dmg_to_a = round(dmg_to_a * 0.5)
            note = f"（{cat_a}が{cat_b}に対して有利に働いた）"
        elif adv == "B":
            dmg_to_a = round(dmg_to_a * 1.6)
            dmg_to_b = round(dmg_to_b * 0.5)
            note = f"（{cat_b}が{cat_a}に対して有利に働いた）"
    else:
        note = "（宣言内容とカテゴリが一致しなかったため相性は無視）"

    narration = (
        f"Aは「{text_a}」（{cat_a}）、Bは「{text_b}」（{cat_b}）を繰り出した。{note}"
        "（AI未接続のため簡易判定）"
    )
    hp_delta_a = -dmg_to_a
    hp_delta_b = -dmg_to_b
    new_hp_a = state["hp_a"] + hp_delta_a
    new_hp_b = state["hp_b"] + hp_delta_b
    return {
        "narration": narration,
        "hp_delta_a": hp_delta_a,
        "hp_delta_b": hp_delta_b,
        "game_over": new_hp_a <= 0 or new_hp_b <= 0,
    }
