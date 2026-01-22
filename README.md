# the-living-temple

Minimal running MVP for a 2-player co-op browser game (Python server + WebSockets).

## Run

From `csapatepito/the-living-temple`:

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
python -m uvicorn server:app --reload --port 8000
```

Open `http://127.0.0.1:8000` in two browser windows:
- Window 1: **Create private room** (you get the room code)
- Window 2: enter the code and **Join**

## Controls
- Move: `WASD` / Arrow keys
- Interact (hold): `E`
- Ping: `Space`
- Quick ping message: press `1-4` then `Space`
