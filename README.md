# osu! trivia

A daily trivia game for the osu! community. Five open-ended questions, one attempt a day, and the rarer your valid answer, the more it scores. Unofficial fan project, **not affiliated with ppy / osu!**.

Stack: Django 5 · server-rendered pages + small vanilla JS · SQLite locally / Postgres in production · WhiteNoise · gunicorn · Dockerfile for Cloud Run.

## Run it locally (no osu! credentials needed)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # DEBUG=1 and DEV_LOGIN=1 are on by default
python manage.py migrate
python manage.py seed_demo      # schedules 5 demo questions for today (UTC)
python manage.py runserver
```

Open http://localhost:8000, click **Sign in**, then **Developer sign-in (local only)** to become a fake player (tick "admin" to get `/admin/`). Dev sign-in does not exist when `DEBUG` is off.

```bash
python manage.py test           # 93 tests
```

## Real osu! sign-in

1. On osu! go to *Settings → OAuth → New OAuth Application*.
2. Callback URL: `<SITE_URL>/auth/callback/` (e.g. `http://localhost:8000/auth/callback/`).
3. Put the id/secret in `.env` as `OSU_CLIENT_ID` / `OSU_CLIENT_SECRET`. The secret is only ever read server-side; the app requests the `identify` scope only and discards the token right after reading your profile.
4. Make yourself admin: add your osu! user id to `ADMIN_OSU_IDS`, or run `python manage.py make_admin <id or username>` after signing in once.

## Adding questions (admin)

`/admin/` → **Questions** → Add. Set the prompt, then add **accepted answers** with optional **aliases** (one per line) and a **seed tier** 1–5. Give it a **date** (UTC) and **position** 1–5. A day is playable only when all 5 slots are filled. Questions without a date are drafts. The list page has an action to drop selected drafts into the next open slots.

Bulk import (e.g. an LLM-drafted pack): `python manage.py import_questions pack.json --schedule-from 2026-12-01`. See `game/fixtures/demo_questions.json` for the format. **The demo questions are placeholders. Review the answers and tiers before launch.**

Suggestions land in **Core → Suggestions**. *Approve* creates an unscheduled draft question (you still add tiers and a date); *Reject* closes it. Nothing is ever auto-published.

## How scoring works

- Tiers (0 + five): Miss 0 · Osu Chud 10 · Average Player 30 · Mechanically Gifted 60 · Genetics Abuser 85 · Veiny Zany 100 (`game/tiers.py`). Max 500/day.
- Each accepted answer starts at its seed tier. After `BLEND_MIN_PLAYS` (30) valid answers on a question the real share of each answer is blended in, and from `BLEND_FULL_PLAYS` (300) it decides alone (`game/scoring.py`). Points are locked when the player answers.
- Matching (`game/matching.py`): case, accents, punctuation and spaces are ignored; aliases match; typos are forgiven (1 edit for 5–8 letters, 2 for longer; answers of ≤4 letters and numbers must be exact); if a typo is equally close to two answers it is rejected.
- Timer: the server stamps `served_at` when a question is started and only accepts answers up to 25 s + 1.5 s network grace. Reloading the page can't reset it. Answers lock with a compare-and-set update, so replays and double submits are rejected.
- Leaderboards count finished sets (and abandoned sets once their day has ended). Players can hide themselves in account settings.

## Deploy (Cloud Run example)

Needs Postgres (e.g. Cloud SQL) and these env vars: `SECRET_KEY`, `SITE_URL` (https), `DATABASE_URL`, `OSU_CLIENT_ID`, `OSU_CLIENT_SECRET`, `ADMIN_OSU_IDS`, plus optionally `ADMIN_URL`, `CONTACT_EMAIL`, `JURISDICTION`, `EFFECTIVE_DATE`. Keep the secrets in Secret Manager.

```bash
gcloud run deploy osu-trivia --source . --region <region> \
  --set-env-vars SITE_URL=https://your.domain,... --set-secrets SECRET_KEY=...,OSU_CLIENT_SECRET=...
```

`entrypoint.sh` runs migrations and `createcachetable` on start. The DB-backed cache is what makes rate limits shared across instances (for strict limits at scale use Redis). `TRUSTED_PROXY_COUNT` defaults to 1 (Cloud Run); raise it if you add a load balancer in front, or the per-IP limits will see the wrong address.

## Legal pages: read before launch

Privacy, Terms and Cookie pages are **plain-language templates for a small hobby site, not legal advice**. Fill the three highlighted placeholders (`CONTACT_EMAIL`, `JURISDICTION`, `EFFECTIVE_DATE` env vars; until set they show as yellow `[BRACKETED]` markers), then check: the USD $50 liability cap in the Terms, the "about 30 days" host log retention in the Privacy Policy (match your host's real setting), and that they describe your actual hosting provider.

## Font

Exo 2 (SIL OFL) is bundled and self-hosted as the free stand-in. **Torus** is licensed, so it isn't included: if you hold a licence, put the files in `static/fonts/`, copy `static/css/torus.css.example` to `torus.css`, and set `USE_TORUS=1`. Flags use the bundled Twemoji Country Flags font (CC-BY 4.0, credited in the footer). No Google Fonts or other third-party requests are made, other than avatars from `a.ppy.sh`.

## Security notes

CSRF on all POSTs · secure/HttpOnly/SameSite session cookies · HTTPS redirect + HSTS in production · strict CSP (no inline JS/CSS) · OAuth `state` check, open-redirect guard, profile fields validated · accepted answers and tiers never sent to the client before it answers · rate limits per user and per IP on game, login, suggestion, export and delete endpoints · no analytics or tracking (so the cookie notice is informational; if you add analytics later it must be opt-in).

Not included (ideas for later): an OG preview image for share links, email/Discord notifications, per-day question-quality stats in the admin.
