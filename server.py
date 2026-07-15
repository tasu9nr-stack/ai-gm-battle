import asyncio
import os
import random
import string
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

import game_master
import storage

BASE_DIR = Path(__file__).parent
DEFAULT_MAX_HP = 100
MAX_HP_CAP = 999
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

app = FastAPI()
storage.init_db()


class Room:
    def __init__(self, code: str, max_hp: int, stage: str):
        self.code = code
        self.max_hp = max_hp
        self.stage = stage
        self.players: dict[str, str] = {}  # role -> player_id
        self.sockets: dict[str, WebSocket] = {}  # role -> websocket
        self.passives: dict[str, dict] = {}
        self.hp = {"a": max_hp, "b": max_hp}
        self.log: list[str] = []
        self.pending_actions: dict[str, dict] = {}
        self.game_over = False
        self.lock = asyncio.Lock()

    def role_for(self, player_id: str) -> str | None:
        for role, pid in self.players.items():
            if pid == player_id:
                return role
        return None


rooms: dict[str, Room] = {}


def _new_room_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(alphabet, k=5))
        if code not in rooms:
            return code


@app.get("/api/passive")
async def api_passive(player_id: str):
    return JSONResponse(storage.get_or_assign_daily_passive(player_id))


@app.post("/api/passive/custom")
async def api_passive_custom(payload: dict = Body(...)):
    player_id = payload["player_id"]
    text = str(payload.get("text", ""))
    existing = storage.get_daily_passive(player_id)
    if existing and not existing.get("is_custom"):
        return JSONResponse({"error": "already_assigned"}, status_code=409)
    if not text.strip():
        return JSONResponse({"error": "empty_text"}, status_code=400)
    return JSONResponse(storage.assign_custom_passive(player_id, text))


def _check_admin_key(key: str) -> bool:
    return bool(ADMIN_KEY) and key == ADMIN_KEY


@app.get("/admin/passives")
async def admin_passives_page(key: str = ""):
    if not _check_admin_key(key):
        return HTMLResponse("<h1>403 Forbidden</h1>", status_code=403)
    return FileResponse(BASE_DIR / "static" / "admin.html")


@app.get("/api/admin/submissions")
async def api_admin_submissions(key: str = ""):
    if not _check_admin_key(key):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse(storage.list_pending_submissions())


@app.post("/api/admin/submissions/{submission_id}/adopt")
async def api_admin_adopt(submission_id: int, key: str = ""):
    if not _check_admin_key(key):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    result = storage.adopt_submission(submission_id)
    if result is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.post("/api/admin/submissions/{submission_id}/reject")
async def api_admin_reject(submission_id: int, key: str = ""):
    if not _check_admin_key(key):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    ok = storage.reject_submission(submission_id)
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.post("/api/rooms")
async def create_room(payload: dict = Body(...)):
    player_id = payload["player_id"]
    try:
        max_hp = int(payload.get("max_hp", DEFAULT_MAX_HP))
    except (TypeError, ValueError):
        max_hp = DEFAULT_MAX_HP
    max_hp = max(1, min(MAX_HP_CAP, max_hp))
    stage = str(payload.get("stage", "")).strip()[:200]

    code = _new_room_code()
    room = Room(code, max_hp, stage)
    room.players["a"] = player_id
    rooms[code] = room
    return {"room_code": code}


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


def _winner(room: Room) -> str | None:
    if not room.game_over:
        return None
    a_dead = room.hp["a"] <= 0
    b_dead = room.hp["b"] <= 0
    if a_dead and b_dead:
        return "draw"
    if a_dead:
        return "b"
    if b_dead:
        return "a"
    return None


async def _send_state(room: Room, role: str):
    ws = room.sockets.get(role)
    if not ws:
        return
    opponent_role = "b" if role == "a" else "a"
    await ws.send_json(
        {
            "type": "state",
            "your_role": role,
            "max_hp": room.max_hp,
            "stage": room.stage,
            "hp_you": room.hp[role],
            "hp_opponent": room.hp[opponent_role],
            "passive_you": room.passives.get(role),
            "passive_opponent": room.passives.get(opponent_role) if room.game_over else None,
            "log": room.log,
            "waiting_for_opponent": len(room.players) < 2,
            "game_over": room.game_over,
            "winner": _winner(room),
        }
    )


@app.websocket("/ws/{room_code}")
async def ws_room(websocket: WebSocket, room_code: str, player_id: str):
    await websocket.accept()
    room = rooms.get(room_code)
    if room is None:
        await websocket.send_json({"type": "error", "message": "部屋が見つかりません"})
        await websocket.close()
        return

    role = room.role_for(player_id)
    if role is None:
        if len(room.players) >= 2:
            await websocket.send_json({"type": "error", "message": "部屋が満員です"})
            await websocket.close()
            return
        role = "b" if "a" in room.players else "a"
        room.players[role] = player_id

    if role not in room.passives:
        passive = storage.get_daily_passive(player_id)
        if passive is None:
            passive = storage.get_or_assign_daily_passive(player_id)
            if passive.get("pool_exhausted"):
                await websocket.send_json(
                    {"type": "error", "message": "先にホーム画面でパッシブを確定してください"}
                )
                await websocket.close()
                return
        room.passives[role] = passive

    room.sockets[role] = websocket

    just_started = len(room.players) == 2 and not room.log
    if just_started:
        room.log.append("対戦開始！")

    if just_started:
        for r in list(room.sockets):
            await _send_state(room, r)
    else:
        await _send_state(room, role)

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "action" or room.game_over:
                continue
            category = data.get("category")
            if category not in game_master.CATEGORIES:
                continue
            text = str(data.get("text", "")).strip()[:200] or "(何もしなかった)"
            async with room.lock:
                room.pending_actions[role] = {"category": category, "text": text}
                if len(room.pending_actions) == 2 and len(room.players) == 2:
                    action_a = room.pending_actions.get("a")
                    action_b = room.pending_actions.get("b")
                    room.pending_actions.clear()
                    state = {
                        "passive_a": room.passives.get("a"),
                        "passive_b": room.passives.get("b"),
                        "hp_a": room.hp["a"],
                        "hp_b": room.hp["b"],
                        "stage": room.stage,
                        "log": room.log,
                    }
                    result = await run_in_threadpool(
                        game_master.resolve_turn, state, action_a, action_b
                    )
                    room.hp["a"] = max(0, min(room.max_hp, room.hp["a"] + result["hp_delta_a"]))
                    room.hp["b"] = max(0, min(room.max_hp, room.hp["b"] + result["hp_delta_b"]))
                    room.log.append(result["narration"])
                    room.game_over = room.hp["a"] <= 0 or room.hp["b"] <= 0
                    for r in list(room.sockets):
                        await _send_state(room, r)
    except WebSocketDisconnect:
        room.sockets.pop(role, None)


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
