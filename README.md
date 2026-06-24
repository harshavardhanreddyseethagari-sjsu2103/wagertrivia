# Wager Trivia — Live Betting Trivia with Real Accounts

A real-time trivia app where you sign in with Google, get virtual
currency, and bet on live trivia rounds against other signed-in users.
Balances persist across sessions, winners are paid out automatically,
and a leaderboard tracks who's actually good at this.

**Live demo:** _add your Render URL here_

## What this project covers

| Layer | What's happening |
|---|---|
| Authentication | Google OAuth (via Authlib) — no passwords ever touch this app; identity is verified by Google, not self-declared |
| Database | Postgres, via SQLAlchemy's ORM — `users`, `questions`, and `bets` tables, with foreign keys enforcing relational integrity |
| Currency safety | Balance updates happen as atomic SQL `UPDATE ... SET balance = balance - X` operations, not read-modify-write in Python — avoids race conditions where simultaneous bets could corrupt a balance |
| Data integrity | A `CHECK (balance >= 0)` constraint enforced by Postgres itself — a backstop independent of application code |
| Real-time rounds | WebSockets broadcast question start, live bets as they happen, betting-closed, and final results to every connected client |
| Server-authoritative timing | The betting countdown is run by the server (`asyncio.sleep`), not trusted to any client's clock — nobody can exploit a slow or manipulated browser timer |
| External content | Trivia questions are pulled live from the Open Trivia Database API, transformed from multiple-choice into a two-option format, with the correct answer randomly assigned to slot A or B (so it can't be statistically guessed over time) |
| Leaderboard | A simple read-only query ordering users by balance |

## Architecture notes / honest limitations

- **`/admin/start-round` is unsecured** — anyone who finds the endpoint
  could trigger a round. Fine for a personal demo with people you
  trust; a real product would gate this behind an admin role.
- **Round state (current question, betting-open flag) lives in server
  memory**, not the database — it's ephemeral by design, since it
  doesn't need to survive a restart the way balances do. Balances and
  bets, by contrast, are fully persisted in Postgres.
- **The client supplies its own email when placing a bet**, rather
  than the server deriving it strictly from the session tied to that
  specific WebSocket connection. A hardened version would verify
  identity per-connection rather than trusting a client-supplied field.
- **Payout is fixed-odds** (1.8x on a correct bet), not a pooled pot
  split among winners — simpler to reason about and demo.

## Running locally

1. Set up a Google OAuth client (Google Cloud Console → APIs & Services
   → Credentials), with `http://localhost:8000/auth/callback` as an
   authorized redirect URI.
2. Create a `.env` file with `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   and a generated `SESSION_SECRET_KEY`.
3. Have Postgres running locally, with a database named `wagertrivia`.

```bash
pip install -r requirements.txt
python3 seed_from_api.py     # pulls real trivia questions from OpenTDB
uvicorn server:app --reload
# visit http://localhost:8000/
```

## Running with Docker

```bash
docker build -t wagertrivia .
docker run -p 8000:8000 --env-file .env wagertrivia
```

## Re-seeding questions

```bash
python3 seed_from_api.py
# or, against a remote database:
DATABASE_URL="your-database-url" python3 seed_from_api.py
```