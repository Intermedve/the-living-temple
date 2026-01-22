from __future__ import annotations

import asyncio
import json
import random
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

TICK_HZ = 15
TICK_DT = 1.0 / TICK_HZ

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def now_s() -> float:
    return time.monotonic()


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def roman(n: int) -> str:
    return {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}.get(n, str(n))


@dataclass
class InputState:
    up: bool = False
    down: bool = False
    left: bool = False
    right: bool = False
    interact: bool = False
    ping: bool = False
    quick_msg: str | None = None

    _prev_interact: bool = False
    _prev_ping: bool = False

    def consume_interact_pressed(self) -> bool:
        pressed = self.interact and not self._prev_interact
        self._prev_interact = self.interact
        return pressed

    def consume_ping_pressed(self) -> bool:
        pressed = self.ping and not self._prev_ping
        self._prev_ping = self.ping
        return pressed


@dataclass
class PlayerState:
    player_id: str
    role: str  # "guardian" | "scholar"
    x: float
    y: float
    hp: int = 3
    downed: bool = False
    revive_progress: float = 0.0
    last_damage_s: float = 0.0


@dataclass
class Ping:
    x: float
    y: float
    msg: str | None
    created_s: float
    by_player: str


@dataclass
class Block:
    x: int
    y: int


@dataclass
class RoomDef:
    name: str
    tiles: list[str]  # list of strings (rows)
    hidden_hint: Any | None = None


@dataclass
class RoomState:
    index: int = 0
    door_open: bool = False
    solved: bool = False
    plate_hold_s: float = 0.0
    levers: list[bool] = field(default_factory=list)
    valve_open: list[bool] = field(default_factory=list)
    valve_progress: dict[int, float] = field(default_factory=dict)  # valve idx -> 0..1
    valve_sequence: list[int] = field(default_factory=list)
    water_level: float = 0.0
    phase1_hold_s: float = 0.0  # room5 lever hold
    keypad_enabled: bool = False


@dataclass
class GameState:
    room: RoomState = field(default_factory=RoomState)
    blocks: list[Block] = field(default_factory=list)
    pings: list[Ping] = field(default_factory=list)
    fragments: dict[int, str] = field(default_factory=dict)  # order -> fragment
    fragment_clues: dict[int, str] = field(default_factory=dict)  # order -> roman clue
    earned_orders: set[int] = field(default_factory=set)
    final_code: str = ""
    game_won: bool = False
    win_s: float | None = None


@dataclass
class Room:
    code: str
    created_s: float
    room_defs: list[RoomDef]
    sockets: dict[str, WebSocket] = field(default_factory=dict)  # player_id -> ws
    inputs: dict[str, InputState] = field(default_factory=dict)
    players: dict[str, PlayerState] = field(default_factory=dict)
    game: GameState = field(default_factory=GameState)
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def has_capacity(self) -> bool:
        return len(self.players) < 2


rooms: dict[str, Room] = {}
rooms_lock = asyncio.Lock()


def generate_room_code(rng: random.Random) -> str:
    return "".join(rng.choice(ROOM_CODE_ALPHABET) for _ in range(5))


def generate_player_id(rng: random.Random) -> str:
    return "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(10))


def make_room_defs() -> list[RoomDef]:
    return [
        RoomDef(
            name="Elocsarnok — Kettos nyomolap",
            tiles=[
                "####################",
                "#A.....T.....P....D#",
                "#......T..........##",
                "#......T...........#",
                "#......T...........#",
                "#......T.....P.....#",
                "#......T...........#",
                "#......T...........#",
                "#......T...........#",
                "#......T...........#",
                "#......T...........#",
                "#..........B.......#",
                "#..................#",
                "####################",
            ],
            hidden_hint={"clue": roman(1)},
        ),
        RoomDef(
            name="Feliratok terme — Rejtett kod",
            tiles=[
                "####################",
                "#A......L..L..L..D.#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#........B.........#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#..................#",
                "####################",
            ],
            hidden_hint={"target": [1, 0, 1], "clue": roman(3)},
        ),
        RoomDef(
            name="Oszlopcsarnok — Nehez targy",
            tiles=[
                "####################",
                "#A.......#######..D#",
                "#........#.....#...#",
                "#..O.....#.....#...#",
                "#........#.....#...#",
                "#........#..P..#...#",
                "#........#.....#...#",
                "#........#.....#...#",
                "#........#######...#",
                "#..............B...#",
                "#..................#",
                "#..................#",
                "#..................#",
                "####################",
            ],
            hidden_hint={"clue": roman(2)},
        ),
        RoomDef(
            name="Vizkamra — Szelep es zsilip",
            tiles=[
                "####################",
                "#A....V.......V..D.#",
                "#..................#",
                "#..................#",
                "#.........V........#",
                "#..................#",
                "#........B.........#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#..................#",
                "####################",
            ],
            hidden_hint={"order": [2, 1, 3], "clue": roman(5)},
        ),
        RoomDef(
            name="Fokapu — Finale",
            tiles=[
                "####################",
                "#A..G...........G.D#",
                "#..................#",
                "#..................#",
                "#...........H......#",
                "#..................#",
                "#..........K.......#",
                "#..................#",
                "#........B.........#",
                "#..................#",
                "#..................#",
                "#..................#",
                "#..................#",
                "####################",
            ],
            hidden_hint={"clue": roman(4)},
        ),
    ]


def find_tiles(tiles: list[str], ch: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for y, row in enumerate(tiles):
        for x, c in enumerate(row):
            if c == ch:
                out.append((x, y))
    return out


def get_tile(tiles: list[str], x: int, y: int) -> str:
    if y < 0 or y >= len(tiles):
        return "#"
    row = tiles[y]
    if x < 0 or x >= len(row):
        return "#"
    return row[x]


def is_wall(tiles: list[str], x: int, y: int) -> bool:
    return get_tile(tiles, x, y) == "#"


def collides_with_wall(tiles: list[str], x: float, y: float, r: float = 0.32) -> bool:
    min_x = int(x - r)
    max_x = int(x + r)
    min_y = int(y - r)
    max_y = int(y + r)
    for ty in range(min_y, max_y + 1):
        for tx in range(min_x, max_x + 1):
            if is_wall(tiles, tx, ty):
                return True
    return False


def move_player(tiles: list[str], p: PlayerState, dx: float, dy: float, dt: float) -> None:
    if p.downed:
        return
    speed = 3.2 if p.role == "scholar" else 2.7
    if dx != 0.0 and dy != 0.0:
        inv = 1.0 / (dx * dx + dy * dy) ** 0.5
        dx *= inv
        dy *= inv
    nx = p.x + dx * speed * dt
    ny = p.y + dy * speed * dt

    if not collides_with_wall(tiles, nx, p.y):
        p.x = nx
    if not collides_with_wall(tiles, p.x, ny):
        p.y = ny


def damage_player(p: PlayerState, amount: int = 1) -> None:
    if p.downed:
        return
    p.hp -= amount
    if p.hp <= 0:
        p.hp = 0
        p.downed = True
        p.revive_progress = 0.0


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return (dx * dx + dy * dy) ** 0.5


def build_snapshot(room: Room, t_s: float) -> dict[str, Any]:
    room_def = room.room_defs[room.game.room.index]
    tiles = room_def.tiles

    room.game.pings = [p for p in room.game.pings if (t_s - p.created_s) <= 3.0]

    return {
        "type": "snapshot",
        "t": t_s,
        "roomIndex": room.game.room.index,
        "roomName": room_def.name,
        "doorOpen": room.game.room.door_open,
        "solved": room.game.room.solved,
        "players": [
            {
                "id": p.player_id,
                "role": p.role,
                "x": p.x,
                "y": p.y,
                "hp": p.hp,
                "downed": p.downed,
                "revive": p.revive_progress,
            }
            for p in room.players.values()
        ],
        "blocks": [{"x": b.x, "y": b.y} for b in room.game.blocks],
        "levers": room.game.room.levers,
        "valves": room.game.room.valve_open,
        "valveProgress": room.game.room.valve_progress,
        "water": room.game.room.water_level,
        "phase1": {"hold": room.game.room.phase1_hold_s, "keypadEnabled": room.game.room.keypad_enabled},
        "pings": [{"x": p.x, "y": p.y, "msg": p.msg, "by": p.by_player} for p in room.game.pings],
        "fragments": [{"order": o, "clue": room.game.fragment_clues[o], "value": room.game.fragments[o]} for o in sorted(room.game.earned_orders)],
        "gameWon": room.game.game_won,
        "winS": room.game.win_s,
        "tilesLegend": {
            "plates": find_tiles(tiles, "P"),
            "traps": find_tiles(tiles, "T"),
            "exit": find_tiles(tiles, "D"),
            "levers": find_tiles(tiles, "L"),
            "valves": find_tiles(tiles, "V"),
            "gateLevers": find_tiles(tiles, "G"),
            "shield": find_tiles(tiles, "H"),
            "keypad": find_tiles(tiles, "K"),
        },
    }


async def ws_send(ws: WebSocket, obj: dict[str, Any]) -> None:
    await ws.send_text(json.dumps(obj))


async def broadcast(room: Room, obj: dict[str, Any]) -> None:
    text = json.dumps(obj)
    dead: list[str] = []
    for pid, ws in room.sockets.items():
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(pid)
    for pid in dead:
        await disconnect_player(room, pid, reason="send failed")


async def disconnect_player(room: Room, player_id: str, reason: str) -> None:
    async with room.lock:
        room.inputs.pop(player_id, None)
        room.players.pop(player_id, None)
        ws = room.sockets.pop(player_id, None)
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    await broadcast(room, {"type": "system", "text": f"Player left ({reason})."})

    if not room.players:
        async with rooms_lock:
            rooms.pop(room.code, None)
        if room.task is not None:
            room.task.cancel()


def reset_room_state(room: Room) -> None:
    idx = room.game.room.index
    room.game.room = RoomState(index=idx)
    room.game.blocks = []
    room.game.pings = []

    room_def = room.room_defs[idx]
    tiles = room_def.tiles

    if idx == 1:
        room.game.room.levers = [False] * len(find_tiles(tiles, "L"))
    if idx in (3, 4):
        room.game.room.valve_open = [False] * len(find_tiles(tiles, "V"))

    if idx == 2:
        for x, y in find_tiles(tiles, "O"):
            room.game.blocks.append(Block(x=x, y=y))


def spawn_players(room: Room) -> None:
    room_def = room.room_defs[room.game.room.index]
    tiles = room_def.tiles
    a = (1, 1)
    b = (2, 2)
    if find_tiles(tiles, "A"):
        a = find_tiles(tiles, "A")[0]
    if find_tiles(tiles, "B"):
        b = find_tiles(tiles, "B")[0]

    for p in room.players.values():
        if p.role == "guardian":
            p.x, p.y = a[0] + 0.5, a[1] + 0.5
        else:
            p.x, p.y = b[0] + 0.5, b[1] + 0.5
        p.hp, p.downed, p.revive_progress = 3, False, 0.0


def room_order_for_index(idx: int) -> int:
    return [1, 3, 2, 5, 4][idx]


def can_damage(t_s: float, p: PlayerState) -> bool:
    return (t_s - p.last_damage_s) >= 0.6


def try_push_block(room: Room, p: PlayerState, dx: int, dy: int) -> bool:
    if p.role != "guardian" or p.downed:
        return False
    tiles = room.room_defs[room.game.room.index].tiles
    px = int(p.x)
    py = int(p.y)
    tx = px + dx
    ty = py + dy
    for b in room.game.blocks:
        if b.x == tx and b.y == ty:
            nx = b.x + dx
            ny = b.y + dy
            if is_wall(tiles, nx, ny):
                return False
            if any(ob is not b and ob.x == nx and ob.y == ny for ob in room.game.blocks):
                return False
            b.x = nx
            b.y = ny
            return True
    return False


def finalize_fragments(room: Room) -> None:
    orders = sorted(room.game.fragments)
    room.game.final_code = "".join(room.game.fragments[o] for o in orders)


def update_room_logic(room: Room, dt: float, t_s: float) -> None:
    rs = room.game.room
    room_def = room.room_defs[rs.index]
    tiles = room_def.tiles

    # Revive
    alive = [p for p in room.players.values() if not p.downed]
    downed = [p for p in room.players.values() if p.downed]
    if len(downed) == 2:
        spawn_players(room)
        return
    if downed and alive:
        dp = downed[0]
        for ap in alive:
            inp = room.inputs.get(ap.player_id)
            if not inp or not inp.interact:
                continue
            if distance((ap.x, ap.y), (dp.x, dp.y)) <= 1.0:
                dp.revive_progress = clamp(dp.revive_progress + dt / 3.0, 0.0, 1.0)
                if dp.revive_progress >= 1.0:
                    dp.downed = False
                    dp.hp = 1
                    dp.revive_progress = 0.0
            else:
                dp.revive_progress = 0.0

    if rs.index == 0:
        plates = find_tiles(tiles, "P")
        occupied = 0
        for x, y in plates:
            if any(int(p.x) == x and int(p.y) == y and not p.downed for p in room.players.values()):
                occupied += 1
        if occupied >= 2:
            rs.plate_hold_s += dt
        else:
            rs.plate_hold_s = 0.0
        if rs.plate_hold_s >= 1.2:
            rs.door_open = True
            if not rs.solved:
                rs.solved = True
                room.game.earned_orders.add(room_order_for_index(rs.index))

        trap_active = (t_s % 1.6) >= 1.0
        if trap_active:
            for p in room.players.values():
                if p.downed:
                    continue
                if get_tile(tiles, int(p.x), int(p.y)) == "T" and can_damage(t_s, p):
                    p.last_damage_s = t_s
                    damage_player(p, 1)

    if rs.index == 1:
        lever_positions = find_tiles(tiles, "L")
        if not rs.levers:
            rs.levers = [False] * len(lever_positions)
        for p in room.players.values():
            inp = room.inputs.get(p.player_id)
            if not inp:
                continue
            if inp.consume_interact_pressed():
                for i, (lx, ly) in enumerate(lever_positions):
                    if distance((p.x, p.y), (lx + 0.5, ly + 0.5)) <= 1.0:
                        rs.levers[i] = not rs.levers[i]
        target = room_def.hidden_hint["target"]  # type: ignore[index]
        if [1 if v else 0 for v in rs.levers] == target:
            rs.door_open = True
            if not rs.solved:
                rs.solved = True
                room.game.earned_orders.add(room_order_for_index(rs.index))

    if rs.index == 2:
        plates = find_tiles(tiles, "P")
        if plates and room.game.blocks:
            px, py = plates[0]
            if any(b.x == px and b.y == py for b in room.game.blocks):
                rs.door_open = True
                if not rs.solved:
                    rs.solved = True
                    room.game.earned_orders.add(room_order_for_index(rs.index))

    if rs.index == 3:
        valve_positions = find_tiles(tiles, "V")
        if not rs.valve_open:
            rs.valve_open = [False] * len(valve_positions)

        for p in room.players.values():
            if p.role != "guardian" or p.downed:
                continue
            inp = room.inputs.get(p.player_id)
            if not inp or not inp.interact:
                continue
            for i, (vx, vy) in enumerate(valve_positions):
                if rs.valve_open[i]:
                    continue
                if distance((p.x, p.y), (vx + 0.5, vy + 0.5)) <= 1.0:
                    rs.valve_progress[i] = clamp(rs.valve_progress.get(i, 0.0) + dt / 2.0, 0.0, 1.0)
                    if rs.valve_progress[i] >= 1.0:
                        rs.valve_open[i] = True
                        if i not in rs.valve_sequence:
                            rs.valve_sequence.append(i)

        target_order = room_def.hidden_hint["order"]  # type: ignore[index]
        sorted_idx = sorted(range(len(valve_positions)), key=lambda j: (valve_positions[j][0], valve_positions[j][1]))
        label_to_idx = {label: sorted_idx[label - 1] for label in [1, 2, 3] if label - 1 < len(sorted_idx)}
        expected_seq = [label_to_idx[l] for l in target_order if l in label_to_idx]

        if rs.valve_sequence:
            for pos, idx in enumerate(rs.valve_sequence):
                if pos >= len(expected_seq) or idx != expected_seq[pos]:
                    rs.water_level = clamp(rs.water_level + dt * 0.65, 0.0, 1.0)
                    # Immediate reset on wrong order (keeps MVP predictable).
                    rs.valve_open = [False] * len(valve_positions)
                    rs.valve_progress = {}
                    rs.valve_sequence = []
                    if rs.water_level >= 1.0:
                        for pl in room.players.values():
                            if can_damage(t_s, pl):
                                pl.last_damage_s = t_s
                                damage_player(pl, 1)
                        rs.water_level = 0.0
                    break

        if rs.valve_sequence == expected_seq and expected_seq:
            rs.door_open = True
            if not rs.solved:
                rs.solved = True
                room.game.earned_orders.add(room_order_for_index(rs.index))

    if rs.index == 4:
        gate_positions = find_tiles(tiles, "G")
        occupied = 0
        for x, y in gate_positions:
            if any(int(p.x) == x and int(p.y) == y and not p.downed for p in room.players.values()):
                occupied += 1
        if occupied >= 2:
            rs.phase1_hold_s += dt
        else:
            rs.phase1_hold_s = 0.0
        if rs.phase1_hold_s >= 1.5:
            rs.keypad_enabled = True
            if not rs.solved:
                rs.solved = True
                room.game.earned_orders.add(room_order_for_index(rs.index))
                finalize_fragments(room)

        trap_active = (t_s % 2.0) >= 1.35
        shield_tiles = find_tiles(tiles, "H")
        shielded = False
        if shield_tiles:
            sx, sy = shield_tiles[0]
            shielded = any(p.role == "guardian" and int(p.x) == sx and int(p.y) == sy and not p.downed for p in room.players.values())
        if trap_active and not shielded:
            for p in room.players.values():
                if p.downed:
                    continue
                if can_damage(t_s, p):
                    p.last_damage_s = t_s
                    damage_player(p, 1)


def maybe_advance_room(room: Room) -> bool:
    rs = room.game.room
    if not rs.door_open or rs.index >= len(room.room_defs) - 1:
        return False
    room_def = room.room_defs[rs.index]
    exit_tiles = find_tiles(room_def.tiles, "D")
    if not exit_tiles:
        return False
    ex, ey = exit_tiles[0]
    if any(int(p.x) != ex or int(p.y) != ey for p in room.players.values()):
        return False
    rs.index += 1
    reset_room_state(room)
    spawn_players(room)
    return True


async def game_loop(room: Room) -> None:
    reset_room_state(room)
    spawn_players(room)
    await broadcast(
        room,
        {
            "type": "room_def",
            "roomIndex": room.game.room.index,
            "tiles": room.room_defs[room.game.room.index].tiles,
            "hiddenHint": room.room_defs[room.game.room.index].hidden_hint,
        },
    )

    last_s = now_s()
    while True:
        await asyncio.sleep(TICK_DT)
        t_s = now_s()
        dt = min(0.05, t_s - last_s)
        last_s = t_s

        async with room.lock:
            if not room.players:
                continue

            room_def = room.room_defs[room.game.room.index]
            tiles = room_def.tiles

            for pid, p in room.players.items():
                inp = room.inputs.get(pid)
                if not inp:
                    continue
                dx = (1.0 if inp.right else 0.0) - (1.0 if inp.left else 0.0)
                dy = (1.0 if inp.down else 0.0) - (1.0 if inp.up else 0.0)
                if room.game.room.index == 2 and p.role == "guardian" and not p.downed and (dx != 0.0 or dy != 0.0):
                    if abs(dx) > abs(dy):
                        try_push_block(room, p, 1 if dx > 0 else -1, 0)
                    else:
                        try_push_block(room, p, 0, 1 if dy > 0 else -1)
                move_player(tiles, p, dx, dy, dt)

                if inp.consume_ping_pressed():
                    room.game.pings.append(Ping(x=p.x, y=p.y, msg=inp.quick_msg, created_s=t_s, by_player=p.player_id))
                    inp.quick_msg = None

            update_room_logic(room, dt, t_s)
            advanced = maybe_advance_room(room)
            if advanced:
                await broadcast(
                    room,
                    {
                        "type": "room_def",
                        "roomIndex": room.game.room.index,
                        "tiles": room.room_defs[room.game.room.index].tiles,
                        "hiddenHint": room.room_defs[room.game.room.index].hidden_hint,
                    },
                )

            snap = build_snapshot(room, t_s)

        await broadcast(room, snap)


def new_room(code: str) -> Room:
    room_defs = make_room_defs()
    room = Room(code=code, created_s=now_s(), room_defs=room_defs)

    rng = random.Random(code)
    frags = ["".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(2)) for _ in range(5)]
    for idx, order in enumerate([1, 3, 2, 5, 4]):
        room.game.fragments[order] = frags[idx]
        room.game.fragment_clues[order] = roman(order)

    return room


async def ensure_room_task(room: Room) -> None:
    if room.task is None or room.task.done():
        room.task = asyncio.create_task(game_loop(room))


app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    rng = random.Random(f"{now_s()}-{id(ws)}")
    player_id = generate_player_id(rng)
    joined_room: Room | None = None

    try:
        while True:
            msg_text = await ws.receive_text()
            msg = json.loads(msg_text)
            mtype = msg.get("type")

            if mtype == "create_room":
                async with rooms_lock:
                    for _ in range(60):
                        code = generate_room_code(rng)
                        if code not in rooms:
                            room = new_room(code)
                            rooms[code] = room
                            break
                    else:
                        await ws_send(ws, {"type": "error", "text": "Failed to create room. Try again."})
                        continue
                ok = await join_room(ws, room, player_id)
                if ok:
                    joined_room = room
                    await ensure_room_task(room)

            elif mtype == "join_room":
                code = str(msg.get("code", "")).strip().upper()
                async with rooms_lock:
                    room = rooms.get(code)
                if room is None:
                    await ws_send(ws, {"type": "error", "text": "Room code not found."})
                    continue
                ok = await join_room(ws, room, player_id)
                if ok:
                    joined_room = room
                    await ensure_room_task(room)

            elif mtype == "input":
                if joined_room is not None:
                    await handle_input(joined_room, player_id, msg)

            elif mtype == "keypad_submit":
                if joined_room is not None:
                    await handle_keypad_submit(joined_room, player_id, msg)

            else:
                await ws_send(ws, {"type": "error", "text": "Unknown message type."})

    except WebSocketDisconnect:
        pass
    finally:
        if joined_room is not None:
            await disconnect_player(joined_room, player_id, reason="disconnect")


async def join_room(ws: WebSocket, room: Room, player_id: str) -> bool:
    async with room.lock:
        if not room.has_capacity() and player_id not in room.players:
            await ws_send(ws, {"type": "error", "text": "Room is full (2 players max)."})
            return False

        role = "guardian" if not any(p.role == "guardian" for p in room.players.values()) else "scholar"
        room.sockets[player_id] = ws
        room.inputs[player_id] = InputState()
        room.players[player_id] = PlayerState(player_id=player_id, role=role, x=2.5, y=2.5)
        spawn_players(room)

        await ws_send(
            ws,
            {
                "type": "joined",
                "code": room.code,
                "playerId": player_id,
                "role": role,
                "roomIndex": room.game.room.index,
                "roomName": room.room_defs[room.game.room.index].name,
                "tiles": room.room_defs[room.game.room.index].tiles,
                "hiddenHint": room.room_defs[room.game.room.index].hidden_hint,
            },
        )
    await broadcast(room, {"type": "system", "text": f"{role} joined."})
    return True


async def handle_input(room: Room, player_id: str, msg: dict[str, Any]) -> None:
    keys = msg.get("keys", {}) or {}
    quick_msg = msg.get("quickMsg")
    async with room.lock:
        inp = room.inputs.get(player_id)
        if inp is None:
            return
        inp.up = bool(keys.get("up"))
        inp.down = bool(keys.get("down"))
        inp.left = bool(keys.get("left"))
        inp.right = bool(keys.get("right"))
        inp.interact = bool(keys.get("interact"))
        inp.ping = bool(keys.get("ping"))
        if isinstance(quick_msg, str) and quick_msg.strip():
            inp.quick_msg = quick_msg.strip()[:60]


async def handle_keypad_submit(room: Room, player_id: str, msg: dict[str, Any]) -> None:
    code = str(msg.get("code", "")).strip().upper()
    async with room.lock:
        p = room.players.get(player_id)
        if p is None:
            return
        if room.game.room.index != 4 or not room.game.room.keypad_enabled or p.role != "scholar":
            return
        if not room.game.final_code:
            finalize_fragments(room)
        if code == room.game.final_code:
            room.game.game_won = True
            room.game.win_s = now_s()
            await broadcast(room, {"type": "system", "text": "The gate opens. You escaped!"})
        else:
            await broadcast(room, {"type": "system", "text": "Wrong code."})
