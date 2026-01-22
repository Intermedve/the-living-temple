const lobby = document.getElementById("lobby");
const game = document.getElementById("game");
const createBtn = document.getElementById("createBtn");
const joinBtn = document.getElementById("joinBtn");
const codeInput = document.getElementById("codeInput");
const lobbyStatus = document.getElementById("lobbyStatus");

const roomNameEl = document.getElementById("roomName");
const roomCodeEl = document.getElementById("roomCode");
const youRoleEl = document.getElementById("youRole");
const youHpEl = document.getElementById("youHp");
const fragmentsEl = document.getElementById("fragments");
const logEl = document.getElementById("log");

const keypadPanel = document.getElementById("keypad");
const keypadInput = document.getElementById("keypadInput");
const keypadSubmit = document.getElementById("keypadSubmit");
const keypadStatus = document.getElementById("keypadStatus");

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

const TILE = 48;
const COLORS = {
  wall: "#1b2235",
  floor: "#0e1422",
  plate: "#3a2a6f",
  exit: "#3b7c60",
  trap: "#3a1a20",
  trapActive: "#ff4d6d",
  leverOff: "#5c6f93",
  leverOn: "#18d39e",
  gate: "#7c5cff",
  shield: "#246bff",
  keypad: "#d8d0ff",
  guardian: "#ffb703",
  scholar: "#00d4ff",
  downed: "#7a8097",
  ping: "#f9dc5c",
};

let ws = null;
let playerId = null;
let role = null;
let roomCode = null;
let tiles = null;
let hiddenHint = null;
let snapshot = null;

const keys = { up: false, down: false, left: false, right: false, interact: false, ping: false };
let pendingQuickMsg = null;
const quickPresets = ["Allj a lapra!", "Huzd a kart!", "Varj!", "Kesz!"];

function logLine(text) {
  const div = document.createElement("div");
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function setLobbyStatus(text) {
  lobbyStatus.textContent = text;
}

function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => setLobbyStatus("Connected.");
  ws.onclose = () => setLobbyStatus("Disconnected.");
  ws.onerror = () => setLobbyStatus("WebSocket error.");
  ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
}

function onMessage(msg) {
  if (msg.type === "error") {
    setLobbyStatus(msg.text);
    logLine(`Error: ${msg.text}`);
    return;
  }
  if (msg.type === "system") {
    logLine(msg.text);
    return;
  }
  if (msg.type === "joined") {
    playerId = msg.playerId;
    role = msg.role;
    roomCode = msg.code;
    tiles = msg.tiles;
    hiddenHint = msg.hiddenHint;
    roomNameEl.textContent = msg.roomName;
    roomCodeEl.textContent = `#${roomCode}`;
    youRoleEl.textContent = role;
    lobby.classList.add("hidden");
    game.classList.remove("hidden");
    logLine(`Joined room ${roomCode} as ${role}.`);
    return;
  }
  if (msg.type === "room_def") {
    tiles = msg.tiles;
    hiddenHint = msg.hiddenHint;
    snapshot = null;
    logLine(`Entered room ${msg.roomIndex + 1}.`);
    return;
  }
  if (msg.type === "snapshot") {
    snapshot = msg;
    roomNameEl.textContent = msg.roomName || "";
    updateHud();
    return;
  }
}

function updateHud() {
  if (!snapshot) return;
  const me = snapshot.players.find((p) => p.id === playerId);
  if (me) youHpEl.textContent = me.downed ? "DOWN" : `HP ${me.hp}/3`;

  fragmentsEl.innerHTML = "";
  if (snapshot.fragments && snapshot.fragments.length) {
    const title = document.createElement("div");
    title.innerHTML = `<span class="label">Fragments</span>`;
    fragmentsEl.appendChild(title);
    for (const f of snapshot.fragments) {
      const row = document.createElement("div");
      row.textContent = `${f.clue}: ${f.value}`;
      fragmentsEl.appendChild(row);
    }
  }
}

function drawTile(ch, x, y) {
  let c = COLORS.floor;
  if (ch === "#") c = COLORS.wall;
  if (ch === "P") c = COLORS.plate;
  if (ch === "D") c = COLORS.exit;
  if (ch === "T") c = COLORS.trap;
  if (ch === "L") c = COLORS.leverOff;
  if (ch === "G") c = COLORS.gate;
  if (ch === "H") c = COLORS.shield;
  if (ch === "K") c = COLORS.keypad;
  ctx.fillStyle = c;
  ctx.fillRect(x * TILE, y * TILE, TILE - 1, TILE - 1);
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#0b1020";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (tiles) {
    for (let y = 0; y < tiles.length; y++) {
      const row = tiles[y];
      for (let x = 0; x < row.length; x++) drawTile(row[x], x, y);
    }
  }

  if (snapshot) {
    if (snapshot.tilesLegend && snapshot.levers) {
      const pos = snapshot.tilesLegend.levers || [];
      for (let i = 0; i < pos.length; i++) {
        const [x, y] = pos[i];
        ctx.fillStyle = snapshot.levers[i] ? COLORS.leverOn : COLORS.leverOff;
        ctx.fillRect(x * TILE + 12, y * TILE + 12, TILE - 24, TILE - 24);
      }
    }

    if (snapshot.blocks) {
      for (const b of snapshot.blocks) {
        ctx.fillStyle = "#9f8b5a";
        ctx.fillRect(b.x * TILE + 6, b.y * TILE + 6, TILE - 12, TILE - 12);
      }
    }

    if (snapshot.pings) {
      for (const p of snapshot.pings) {
        ctx.fillStyle = COLORS.ping;
        ctx.beginPath();
        ctx.arc(p.x * TILE, p.y * TILE, 10, 0, Math.PI * 2);
        ctx.fill();
        if (p.msg) {
          ctx.fillStyle = "rgba(10,14,26,0.8)";
          ctx.fillRect(p.x * TILE + 12, p.y * TILE - 18, 160, 18);
          ctx.fillStyle = COLORS.ping;
          ctx.font = "12px ui-sans-serif";
          ctx.fillText(p.msg, p.x * TILE + 16, p.y * TILE - 5);
        }
      }
    }

    for (const p of snapshot.players) {
      ctx.fillStyle = p.downed ? COLORS.downed : p.role === "guardian" ? COLORS.guardian : COLORS.scholar;
      ctx.beginPath();
      ctx.arc(p.x * TILE, p.y * TILE, 14, 0, Math.PI * 2);
      ctx.fill();
      if (p.downed) {
        ctx.strokeStyle = COLORS.trapActive;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(p.x * TILE, p.y * TILE, 18, -Math.PI / 2, -Math.PI / 2 + p.revive * Math.PI * 2);
        ctx.stroke();
      }
    }

    // Scholar-only hint box.
    const me = snapshot.players.find((p) => p.id === playerId);
    if (me && me.role === "scholar" && hiddenHint) {
      ctx.fillStyle = "rgba(10,14,26,0.75)";
      ctx.fillRect(12, 12, 380, 62);
      ctx.fillStyle = "#d8d0ff";
      ctx.font = "13px ui-sans-serif";
      if (hiddenHint.target) ctx.fillText(`Hidden: levers = ${hiddenHint.target.join("-")}`, 20, 36);
      if (hiddenHint.order) ctx.fillText(`Hidden: valves order = ${hiddenHint.order.join(" → ")}`, 20, 56);
    }

    // Keypad toggle (room5).
    const canUseKeypad =
      snapshot.roomIndex === 4 &&
      me &&
      me.role === "scholar" &&
      snapshot.phase1 &&
      snapshot.phase1.keypadEnabled;
    keypadPanel.classList.toggle("hidden", !canUseKeypad);
    if (!canUseKeypad) keypadStatus.textContent = "";
    if (snapshot.gameWon) keypadStatus.textContent = "ESCAPED!";
  }

  requestAnimationFrame(draw);
}

function bindKeys() {
  window.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    if (e.key === "w" || e.key === "ArrowUp") keys.up = true;
    if (e.key === "s" || e.key === "ArrowDown") keys.down = true;
    if (e.key === "a" || e.key === "ArrowLeft") keys.left = true;
    if (e.key === "d" || e.key === "ArrowRight") keys.right = true;
    if (e.key.toLowerCase() === "e") keys.interact = true;
    if (e.code === "Space") keys.ping = true;
    if (["1", "2", "3", "4"].includes(e.key)) {
      pendingQuickMsg = quickPresets[parseInt(e.key, 10) - 1];
      logLine(`Preset ready: "${pendingQuickMsg}" then Space to ping.`);
    }
  });
  window.addEventListener("keyup", (e) => {
    if (e.key === "w" || e.key === "ArrowUp") keys.up = false;
    if (e.key === "s" || e.key === "ArrowDown") keys.down = false;
    if (e.key === "a" || e.key === "ArrowLeft") keys.left = false;
    if (e.key === "d" || e.key === "ArrowRight") keys.right = false;
    if (e.key.toLowerCase() === "e") keys.interact = false;
  });
}

function startInputLoop() {
  setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN || !playerId) return;
    wsSend({ type: "input", keys, quickMsg: pendingQuickMsg });
    pendingQuickMsg = null;
    keys.ping = false;
  }, 60);
}

createBtn.onclick = () => {
  if (!ws) connectWs();
  wsSend({ type: "create_room" });
  setLobbyStatus("Creating room...");
};

joinBtn.onclick = () => {
  const code = codeInput.value.trim().toUpperCase();
  if (!code) return;
  if (!ws) connectWs();
  wsSend({ type: "join_room", code });
  setLobbyStatus(`Joining ${code}...`);
};

keypadSubmit.onclick = () => {
  const code = keypadInput.value.trim().toUpperCase();
  if (!code) return;
  wsSend({ type: "keypad_submit", code });
  keypadStatus.textContent = "Submitted.";
};

bindKeys();
startInputLoop();
requestAnimationFrame(draw);

