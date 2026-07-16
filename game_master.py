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

# パッシブの説明文からキーワードで効果タイプを推定する。
# 静的カタログだけでなく、管理者が採用した自己申告パッシブにもそのまま効く。
EFFECT_KEYWORDS = {
    "low_hp_power": ["3割以下", "HPが低い", "低いほど", "劣勢", "凶暴化"],
    "guard": ["防御的な行動", "受け止め", "耐える", "耐え", "被ダメージが"],
    "self_regen": ["毎ターン", "わずかにHPが回復", "大きくHPが戻る"],
    "lifesteal": ["自分も回復", "奪取", "吸収"],
    "dot": ["刻印", "焼く", "後を引く", "毒", "縛る"],
    "variance_high": ["ランダム", "予想外", "振れやすい", "完全にランダム"],
    "variance_low": ["安定", "失敗をしにくい"],
    "crit": ["一撃必殺", "命中すれば"],
    "reflect": ["跳ね返す"],
    "read": ["見切り", "見抜き", "読み", "読める"],
}

RANDOM_EVENTS = [
    "突然の強風が吹き荒れ、双方の体勢が崩れた。",
    "観衆がどよめき、一瞬だけ互いの集中が乱れた。",
    "足元の地面が崩れ、二人とも体勢を立て直すのに苦労した。",
    "遠くで雷鳴が轟き、緊張が走った。",
    "冷たい風が舞い込み、痛みが増したように感じられた。",
]

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


def category_advantage(cat_a: str | None, cat_b: str | None) -> str | None:
    if not cat_a or not cat_b or cat_a == cat_b:
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
      "max_hp": int, "stage": str, "log": [str, ...]
    }
    action_a/action_b: {"category": one of CATEGORIES or None(未入力), "text": str}
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
            "max_hp": state.get("max_hp", 100),
            "action_a": action_a,
            "action_b": action_b,
            "recent_log": state.get("log", [])[-8:],
        }
        message = client.messages.create(
            model=_MODEL,
            max_tokens=600,
            system=(
                "あなたは1対1バトルTRPGのゲームマスターです。"
                "プレイヤーA・Bはそれぞれ「宣言カテゴリ（攻撃/防御/特殊/カウンターのいずれか、"
                "または60秒以内に未入力だった場合はnull）」と「自由記述の行動テキスト」を提出します。"
                f"{RELATION_TEXT}"
                "判定手順: 最優先事項として、両者のパッシブの説明文の内容を必ず判定に反映してください"
                "（パッシブの内容がこのゲームの面白さの核です）。"
                "その上で、宣言テキストの内容が宣言カテゴリと矛盾していないかを見て、"
                "矛盾がなければ相性表の有利不利を軽く反映してください（あくまで弱めの補正であり、絶対的な優劣ではありません）。"
                "矛盾していれば相性表は適用せず、行動内容の説得力・状況にふさわしさだけで自由に判定してください。"
                "宣言カテゴリがnull（未入力）だった側は、ほぼ攻撃できず、隙が生まれてやや被ダメージが増える扱いにしてください。"
                "低確率（15%程度）でどちらのHPもほとんど変化しない「拮抗したターン」があってもよく、"
                "低確率（10〜15%程度）で舞台や状況を揺さぶる小さなイベントの描写を挟んでも構いません。"
                "舞台設定と直近の戦闘ログを踏まえ、物語として自然につながるナレーションにしてください。"
                "ナレーションには、両者のHP増減が具体的にどう変化したかを明示する一文を必ず含めてください"
                "（例:「Aは手痛い一撃を受けた」「Bはほぼ無傷だった」「Aは体力を取り戻した」等）。"
                "HP増減は概ね-25〜+12の範囲を目安にしてください。"
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


def _derive_passive_effects(desc: str) -> list[str]:
    """パッシブの説明文からキーワードで効果タイプを最大2つまで推定する。"""
    effects = []
    for effect, keywords in EFFECT_KEYWORDS.items():
        if any(kw in desc for kw in keywords):
            effects.append(effect)
        if len(effects) >= 2:
            break
    return effects


def _apply_offense(effects: list[str], category: str | None, hp: int, hp_max: int, base: int) -> int:
    dealt = base
    hp_ratio = (hp / hp_max) if hp_max else 1.0
    for effect in effects:
        if effect == "low_hp_power" and hp_ratio <= 0.3:
            dealt = round(dealt * 1.4)
        elif effect == "dot" and category == "攻撃":
            dealt += random.randint(2, 6)
        elif effect == "variance_high":
            dealt = round(dealt * random.uniform(0.5, 1.6))
        elif effect == "variance_low":
            dealt = round(dealt * random.uniform(0.85, 1.1))
        elif effect == "crit" and random.random() < 0.25:
            dealt = round(dealt * 1.8)
    return dealt


def _apply_defense(effects: list[str], category: str | None, taken: int) -> int:
    for effect in effects:
        if effect == "guard" and category in ("防御", "カウンター"):
            taken = round(taken * 0.7)
        elif effect == "read":
            taken = round(taken * 0.85)
    if category is None:
        taken = round(taken * 1.15)  # 何もしなかった側は隙が生まれる
    return taken


def _delta_phrase(delta: int) -> str:
    if delta >= 5:
        return f"は大きく回復した（+{delta}）"
    if delta >= 1:
        return f"は少し回復した（+{delta}）"
    if delta == 0:
        return "はダメージを受けなかった"
    if delta >= -4:
        return f"はかすり傷を負った（{delta}）"
    if delta >= -14:
        return f"は手痛い一撃を受けた（{delta}）"
    return f"は大きなダメージを受けた（{delta}）"


def _mock_resolve_turn(state: dict, action_a: dict, action_b: dict) -> dict:
    cat_a, text_a = action_a["category"], action_a["text"]
    cat_b, text_b = action_b["category"], action_b["text"]
    hp_a, hp_b = state["hp_a"], state["hp_b"]
    max_hp = state.get("max_hp", 100)

    passive_a = state.get("passive_a") or {}
    passive_b = state.get("passive_b") or {}
    effects_a = _derive_passive_effects(passive_a.get("desc", ""))
    effects_b = _derive_passive_effects(passive_b.get("desc", ""))

    idle_a = cat_a is None
    idle_b = cat_b is None

    base_a = 0 if idle_a else random.randint(5, 18)
    base_b = 0 if idle_b else random.randint(5, 18)

    match_a = (not idle_a) and _keyword_match(cat_a, text_a)
    match_b = (not idle_b) and _keyword_match(cat_b, text_b)

    note = ""
    if not (idle_a or idle_b):
        if match_a and match_b:
            adv = category_advantage(cat_a, cat_b)
            if adv == "A":
                base_a = round(base_a * 1.25)
                base_b = round(base_b * 0.8)
                note = f"（{cat_a}が{cat_b}に対して有利に働いた）"
            elif adv == "B":
                base_b = round(base_b * 1.25)
                base_a = round(base_a * 0.8)
                note = f"（{cat_b}が{cat_a}に対して有利に働いた）"
        else:
            note = "（宣言内容とカテゴリが一致しなかったため相性は無視）"

    no_change = random.random() < 0.15
    if no_change:
        base_a = 0
        base_b = 0

    outgoing_a = base_a if idle_a else _apply_offense(effects_a, cat_a, hp_a, max_hp, base_a)
    outgoing_b = base_b if idle_b else _apply_offense(effects_b, cat_b, hp_b, max_hp, base_b)

    taken_by_b = _apply_defense(effects_b, cat_b, outgoing_a)
    taken_by_a = _apply_defense(effects_a, cat_a, outgoing_b)

    if "reflect" in effects_b and random.random() < 0.3:
        taken_by_a += round(taken_by_b * 0.3)
    if "reflect" in effects_a and random.random() < 0.3:
        taken_by_b += round(taken_by_a * 0.3)

    heal_a = 0
    heal_b = 0
    for effect in effects_a:
        if effect == "self_regen":
            heal_a += random.randint(2, 6)
        elif effect == "lifesteal" and cat_a == "攻撃":
            heal_a += round(outgoing_a * 0.3)
    for effect in effects_b:
        if effect == "self_regen":
            heal_b += random.randint(2, 6)
        elif effect == "lifesteal" and cat_b == "攻撃":
            heal_b += round(outgoing_b * 0.3)

    event_text = ""
    event_dmg = 0
    if random.random() < 0.12:
        event_text = random.choice(RANDOM_EVENTS) + " "
        event_dmg = random.randint(2, 5)

    hp_delta_a = heal_a - taken_by_a - event_dmg
    hp_delta_b = heal_b - taken_by_b - event_dmg

    label_a = f"「{text_a}」（{cat_a}）を繰り出した" if not idle_a else "何も行動しなかった"
    label_b = f"「{text_b}」（{cat_b}）を繰り出した" if not idle_b else "何も行動しなかった"
    narration = f"{event_text}Aは{label_a}。Bは{label_b}。{note}"
    if no_change:
        narration += " しかし互いの一撃はかすっただけで、目立った変化はなかった。"
    narration += f" A{_delta_phrase(hp_delta_a)}。B{_delta_phrase(hp_delta_b)}。"
    narration += "（AI未接続のため簡易判定）"

    new_hp_a = hp_a + hp_delta_a
    new_hp_b = hp_b + hp_delta_b
    return {
        "narration": narration,
        "hp_delta_a": hp_delta_a,
        "hp_delta_b": hp_delta_b,
        "game_over": new_hp_a <= 0 or new_hp_b <= 0,
    }
