# User feedback

Every result page has a feedback button, every site has "Something
wrong with this site?", and a failed analysis has "Report this
problem". Each report stores a rating, quick tags, a comment and a
snapshot of what the page showed: the question, how it was interpreted,
the search area, the top sites and, for site feedback, that site's
location, polygon, scores, evidence and confidence, plus the app
version (git commit).

IP addresses are used only to limit visitors to 20 reports a day
(`FEEDBACK_PER_IP`) and are never stored. Signed-in team members are
not limited. Sending feedback does not use up a question.

## Storage

| Setting | Storage |
|---|---|
| `DATABASE_URL=postgresql://...` | Postgres (use this on Render) |
| not set | SQLite file `.cache/feedback.sqlite3` (local development) |

Render's free tier wipes its disk on every restart and deploy, so
production needs `DATABASE_URL`.

Setting up a free database (Neon; Supabase works the same way):

1. Create a free project at https://neon.tech.
2. Copy the connection string (`postgresql://...?sslmode=require`).
3. Add it as `DATABASE_URL` in Render's environment settings and in
   the local `.env` (so the report script can read it).

The table is created on the first report.

## Reading feedback

Team members can read it from `GET /feedback` (signed in), or run:

```
python scripts/feedback_report.py                  # new since the last review
python scripts/feedback_report.py --mark-reviewed  # after acting on it
```

The script groups reports by tag, analysis type, place, version and
repeated sites, lists every entry, and writes the full entries with
their context to `.cache/feedback-export.json`.
