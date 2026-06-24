# server.py
#
# STAGE 4 — a live trivia round over WebSockets. The server is the
# single authority on timing: it runs its own countdown using asyncio,
# independent of whatever any browser's clock shows. Clients just
# render whatever state the server broadcasts — the same "server is
# the source of truth" principle from Match Room's participant count,
# now applied to something with real stakes (virtual currency).

import os
import asyncio
import json
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv

from database import init_db, SessionLocal, User, Question, Bet, place_bet, resolve_question

load_dotenv()

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.environ["SESSION_SECRET_KEY"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

BET_WINDOW_SECONDS = 15

# ── Live round state ────────────────────────────────────────────
# Unlike Match Room's "rooms" (which were just lists of connections),
# here we ALSO need to track the state of the CURRENT round itself —
# is betting open right now? which question is active? This lives in
# server memory (fine — it's ephemeral, round-by-round state, not
# anything that needs to survive a restart the way balances do).
connected_clients: list[WebSocket] = []
current_question_id: int | None = None
betting_open: bool = False


async def broadcast(message: dict):
    payload = json.dumps(message)
    for client in connected_clients[:]:
        try:
            await client.send_text(payload)
        except Exception:
            if client in connected_clients:
                connected_clients.remove(client)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/leaderboard")
async def get_leaderboard():
    """
    Returns the top users by current balance. This is a READ-ONLY
    query — no state changes here, just asking Postgres to sort and
    hand back existing data. order_by(User.balance.desc()) means
    "highest balance first"; limit(10) caps it to the top 10.
    """
    db = SessionLocal()
    top_users = db.query(User).order_by(User.balance.desc()).limit(10).all()

    leaderboard = [
        {"rank": i + 1, "name": u.name, "email": u.email, "balance": u.balance}
        for i, u in enumerate(top_users)
    ]
    db.close()

    return {"leaderboard": leaderboard}


@app.get("/")
async def homepage(request: Request):
    session_user = request.session.get("user")
    if not session_user:
        return HTMLResponse('<h2>Wager Trivia</h2><p><a href="/login">Sign in with Google</a></p>')

    # Look up the user's CURRENT balance fresh from the database — the
    # cookie only proves identity, never holds the balance itself.
    db = SessionLocal()
    db_user = db.query(User).filter(User.email == session_user["email"]).first()
    balance = db_user.balance if db_user else 0
    db.close()

    # Read the static HTML template and substitute in this user's
    # actual data before sending it to the browser. This is a simple,
    # manual form of "server-side rendering" — no templating engine
    # needed for just two values.
    with open("static/index.html") as f:
        html = f.read()

    html = html.replace("{{USER_EMAIL}}", session_user["email"])
    html = html.replace("{{USER_BALANCE}}", str(balance))

    return HTMLResponse(html)


@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")

    db = SessionLocal()
    db_user = db.query(User).filter(User.email == user_info["email"]).first()
    if db_user is None:
        db_user = User(email=user_info["email"], name=user_info["name"], picture=user_info.get("picture"))
        db.add(db_user)
        db.commit()
    db.close()

    request.session["user"] = dict(user_info)
    return RedirectResponse(url="/")


@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/")


# ── Admin endpoint: start a new round ───────────────────────────
# For tonight, this is unsecured (anyone could hit this URL) — fine for
# a personal demo, but worth flagging explicitly: a real product would
# restrict this to an admin role. Noted, not fixed, given scope.
@app.post("/admin/start-round")
async def start_round():
    global current_question_id, betting_open

    db = SessionLocal()
    # Pick any question that hasn't been resolved yet — simplistic
    # selection for now, good enough for a demo with a handful of
    # pre-loaded questions.
    question = db.query(Question).filter(Question.is_resolved == False).first()

    if question is None:
        db.close()
        return {"error": "No unresolved questions available."}

    question.is_active = True
    db.commit()
    question_data = {
        "id": question.id,
        "text": question.text,
        "option_a": question.option_a,
        "option_b": question.option_b,
    }
    db.close()

    current_question_id = question.id
    betting_open = True

    print(f"[DEBUG] Broadcasting round_start to {len(connected_clients)} client(s).")

    await broadcast({
        "type": "round_start",
        "question": question_data,
        "seconds": BET_WINDOW_SECONDS,
    })

    # asyncio.create_task lets this countdown run IN THE BACKGROUND,
    # without blocking this HTTP request from returning immediately.
    asyncio.create_task(run_countdown(question.id))

    return {"status": "round started", "question_id": question.id}


async def run_countdown(question_id: int):
    """
    The SERVER's own authoritative timer. This is what actually closes
    betting — not anything happening in any browser. Even if every
    connected client's JavaScript froze or lied about the time, this
    is what determines when betting truly ends.
    """
    global betting_open

    await asyncio.sleep(BET_WINDOW_SECONDS)
    betting_open = False

    await broadcast({"type": "betting_closed"})

    # Give a brief pause for dramatic effect before revealing — purely
    # a UX choice, not a technical requirement.
    await asyncio.sleep(2)

    db = SessionLocal()
    question = db.query(Question).filter(Question.id == question_id).first()
    resolve_question(db, question_id)

    # Re-fetch bets AFTER resolving, so payout values are populated.
    bets = db.query(Bet).filter(Bet.question_id == question_id).all()
    results = []
    for bet in bets:
        bettor = db.query(User).filter(User.id == bet.user_id).first()
        results.append({
            "email": bettor.email,           # NEW — lets each client recognize its own row
            "username": bettor.name,
            "chosen_answer": bet.chosen_answer,
            "wager_amount": bet.wager_amount,
            "payout": bet.payout,
            "new_balance": bettor.balance,   # NEW — the actual post-payout balance
        })
    correct_answer = question.correct_answer
    db.close()

    await broadcast({
        "type": "round_resolved",
        "correct_answer": correct_answer,
        "results": results,
    })


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    print(f"[DEBUG] Client connected. Total clients now: {len(connected_clients)}")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            if data["type"] == "place_bet":
                # The CLIENT tells us who they are via the message itself
                # (their email) — in a more hardened version we'd verify
                # this against the actual session cookie tied to this
                # specific WebSocket connection, rather than trusting a
                # client-supplied field. Noted simplification for tonight.
                db = SessionLocal()
                user = db.query(User).filter(User.email == data["email"]).first()

                if not betting_open:
                    await websocket.send_text(json.dumps({"type": "bet_error", "message": "Betting is closed."}))
                    db.close()
                    continue

                try:
                    place_bet(
                        db,
                        user_id=user.id,
                        question_id=current_question_id,
                        chosen_answer=data["answer"],
                        wager_amount=float(data["amount"]),
                    )
                    new_balance = db.query(User).filter(User.id == user.id).first().balance
                    db.close()

                    await websocket.send_text(json.dumps({
                        "type": "bet_confirmed",
                        "new_balance": new_balance
                    }))

                    # Let everyone see the bet happen live — builds energy,
                    # same "watch the room react" feeling as Match Room.
                    await broadcast({
                        "type": "live_bet",
                        "username": user.name,
                        "answer": data["answer"],
                        "amount": data["amount"],
                    })

                except ValueError as e:
                    db.close()
                    await websocket.send_text(json.dumps({"type": "bet_error", "message": str(e)}))

    except WebSocketDisconnect:
        connected_clients.remove(websocket)