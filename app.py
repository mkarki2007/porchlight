import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, g, redirect, render_template, request, url_for

DB_PATH = Path(__file__).parent / "porchlight.db"

app = Flask(__name__)


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    fresh = not DB_PATH.exists()
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checkin_date TEXT NOT NULL UNIQUE,
            time_of_day TEXT NOT NULL,
            duration_min INTEGER NOT NULL,
            digest TEXT NOT NULL,
            mood INTEGER NOT NULL,
            medication TEXT NOT NULL,
            sleep TEXT NOT NULL,
            topics TEXT NOT NULL,
            flags TEXT NOT NULL,
            transcript TEXT,
            reviewed INTEGER NOT NULL DEFAULT 0,
            answered INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    db.commit()
    if fresh:
        seed(db)
    db.close()


# ---------------------------------------------------------------- seed data

SEED_ROWS = [
    # date,            time,    dur, mood, medication, sleep,   topics,                          digest
    ("2026-08-31", "9:08 AM", 6, 8, "Taken", "Good",
     ["Garden", "Church"],
     "Sunny start to the week — Eleanor was out watering the garden early and mentioned the church bake sale coming up this weekend."),
    ("2026-09-01", "9:15 AM", 5, 7, "Taken", "Good",
     ["Walk with Diane"],
     "Walked with Diane in the morning like usual, then picked up groceries. In good spirits, nothing to note."),
    ("2026-09-02", "9:05 AM", 7, 8, "Taken", "Good",
     ["Family"],
     "Her grandson visited for lunch — lots of stories, Eleanor sounded delighted the whole call."),
    ("2026-09-03", "9:20 AM", 4, 7, "Taken", "Fair",
     ["Reading", "Garden"],
     "Quieter day — reading her book club novel, mentioned the garden needs weeding but wasn't up for it today."),
    ("2026-09-04", "9:10 AM", 6, 8, "Taken", "Good",
     ["Baking"],
     "Great mood — baked a pie for the neighbor's birthday and was excited to deliver it later."),
    ("2026-09-05", "9:25 AM", 5, 7, "Taken", "Good",
     ["Family"],
     "Relaxed Saturday — called her son Mark, planned to watch a movie in the evening."),
    ("2026-09-06", "9:12 AM", 6, 8, "Taken", "Good",
     ["Walk with Diane"],
     "Walked with Diane in the morning, in great spirits, no concerns."),
    ("2026-09-07", "9:18 AM", 5, 7, "Taken", "Good",
     ["Garden"],
     "Relaxed Sunday, mentioned a knee twinge once in passing — nothing major, first time this week."),
    ("2026-09-08", "9:07 AM", 8, 8, "Taken", "Good",
     ["Church", "Family"],
     "Talked about church on Sunday and seeing the neighbors. Slept well."),
    ("2026-09-09", "9:30 AM", 2, 7, "Taken", "Not mentioned",
     [],
     "Short call — said she was heading out the door, seemed rushed but upbeat."),
    ("2026-09-10", "9:14 AM", 5, 6, "Taken", "Fair",
     ["Garden"],
     "Mentioned knee pain after gardening — second time this week. Took her pills a little later than usual, around 11 AM."),
    ("2026-09-11", "9:09 AM", 7, 7, "Taken", "Good",
     ["Baking", "Family"],
     "Good mood — baked bread and chatted about the grandkids' school schedules."),
    ("2026-09-12", "9:22 AM", 4, 6, "Taken", "Fair",
     ["Walk with Diane"],
     "Quieter day, watched the news, mentioned being tired. Skipped her usual Tuesday walk with Diane."),
]

SEED_FLAGS = {
    "2026-09-12": [{"severity": "serious", "text": "Skipped her usual walk with Diane"}],
}

TODAY_CALL_SCRIPT = [
    ("ai", "Good morning, Eleanor! It's your 9 o'clock check-in. How are you doing today?"),
    ("el", "Oh, hello dear. I'm alright, I suppose. Just got in from the garden."),
    ("ai", "The garden! How's it looking this time of year?"),
    ("el", "The tomatoes are finally coming in. I picked a whole bowl this morning."),
    ("ai", "That sounds wonderful. Did you get a chance to talk to anyone yesterday?"),
    ("el", "Ruth called last night, we talked for almost an hour."),
    ("ai", "That's lovely. Did you take your morning pills today?"),
    ("el", "Yes, right after breakfast, like always."),
    ("ai", "Good. And how did you sleep last night?"),
    ("el", "Okay, not great. My knee was acting up again, kept me up a bit."),
    ("ai", "Sorry to hear that — is this the same knee that's been bothering you?"),
    ("el", "Yes, the right one. It's been off and on all week."),
    ("ai", "I'll make a note of that for Sarah. Did you get out for your usual walk with Diane on Tuesday?"),
    ("el", "No, I skipped it. Just wasn't feeling up to it."),
    ("ai", "That's alright. Thanks for chatting with me, Eleanor — talk again tomorrow at nine?"),
    ("el", "Sounds good, dear. Have a nice day."),
]

TODAY_DIGEST = (
    "Eleanor picked up on the second ring, in good spirits — talked about the tomatoes coming in "
    "from her garden and a long call with her sister Ruth. She confirmed she took her morning pills. "
    "Said she slept “okay, not great,” and mentioned her knee “acting up again.”"
)


def seed(db):
    for row in SEED_ROWS:
        d, t, dur, mood, med, sleep, topics, digest = row
        flags = SEED_FLAGS.get(d, [])
        db.execute(
            """INSERT INTO checkins
               (checkin_date, time_of_day, duration_min, digest, mood, medication, sleep, topics, flags, transcript, reviewed, answered)
               VALUES (?,?,?,?,?,?,?,?,?,?,1,1)""",
            (d, t, dur, digest, mood, med, sleep, json.dumps(topics), json.dumps(flags), None),
        )
    db.commit()


# ---------------------------------------------------------------- extraction engine

TOPIC_KEYWORDS = {
    "tomato": "Garden", "garden": "Garden",
    "ruth": "Called sister Ruth",
    "walk": "Walk with Diane", "diane": "Walk with Diane",
    "church": "Church",
    "grandson": "Family", "grandkids": "Family", "mark": "Family",
    "bread": "Baking", "pie": "Baking",
    "book": "Reading",
}
NEG_WORDS = ["not great", "tired", "ache", "aching", "pain", "hurt", "skip", "sad", "alone", "lonely", "rough"]
POS_WORDS = ["wonderful", "great", "lovely", "delighted", "excited", "good spirits", "sounds good"]


def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def recent_knee_mentions(db, before_date, days=7):
    """Count prior days in the window whose digest or flags reference her knee —
    this is what lets a 3rd same-week mention read as a real pattern, not a one-off."""
    since = (datetime.strptime(before_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.execute(
        "SELECT digest, flags FROM checkins WHERE checkin_date >= ? AND checkin_date < ?",
        (since, before_date),
    ).fetchall()
    count = 0
    for r in rows:
        text = r["digest"].lower()
        flagged = any("knee" in f["text"].lower() for f in json.loads(r["flags"]))
        if "knee" in text or flagged:
            count += 1
    return count


def analyze_transcript(db, checkin_date, script):
    """Walks the transcript as question/answer pairs — medication and sleep are
    asked by the AI and confirmed by her, so the signal lives across both lines,
    not just in her own wording."""
    pairs = []
    last_q = ""
    for speaker, text in script:
        if speaker == "ai":
            last_q = text.lower()
        else:
            pairs.append((last_q, text.lower()))

    full_el = " ".join(a for _, a in pairs)

    mood = 7
    for w in NEG_WORDS:
        if w in full_el:
            mood -= 1
    for w in POS_WORDS:
        if w in full_el:
            mood += 1
    mood = max(1, min(10, mood))

    medication = "Not mentioned"
    for q, a in pairs:
        if "pill" in q or "medication" in q or "pill" in a or "medication" in a:
            medication = "Missed" if ("forgot" in a or "didn't take" in a) else "Taken"

    sleep = "Not mentioned"
    for q, a in pairs:
        if "sleep" in q or "slept" in q or "sleep" in a or "slept" in a:
            if any(k in a for k in ["not great", "not well", "barely", "kept me up", "rough", "bad"]):
                sleep = "Fair"
            elif any(k in a for k in ["great", "well", "good"]):
                sleep = "Good"
            else:
                sleep = "Fair"

    topics = sorted({v for k, v in TOPIC_KEYWORDS.items() if k in full_el})

    flags = []
    if "knee" in full_el:
        prior = recent_knee_mentions(db, checkin_date)
        nth = prior + 1
        if nth >= 2:
            severity = "serious" if nth >= 3 else "warning"
            flags.append({
                "severity": severity,
                "text": f"Knee pain mentioned ({ordinal(nth)} time this week, {prior} in the prior 2 weeks)",
            })
    for q, a in pairs:
        if "walk" in q and any(k in a for k in ["skip", "didn't", "wasn't up", "not up"]):
            flags.append({"severity": "serious", "text": "Skipped her usual walk with Diane"})
    if any(k in full_el for k in ["chest", "dizzy", "fell", "faint"]):
        flags.append({"severity": "critical", "text": "Possible medical concern mentioned — review immediately"})

    return mood, medication, sleep, topics, flags


# ---------------------------------------------------------------- helpers

def load_checkins(db):
    rows = db.execute("SELECT * FROM checkins ORDER BY checkin_date DESC").fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["topics"] = json.loads(item["topics"])
        item["flags"] = json.loads(item["flags"])
        item["transcript"] = json.loads(item["transcript"]) if item["transcript"] else None
        out.append(item)
    return out


def day_label(d):
    dt = datetime.strptime(d, "%Y-%m-%d")
    return dt.strftime("%a"), dt.strftime("%b %-d") if hasattr(dt, "strftime") else d


# ---------------------------------------------------------------- routes

@app.route("/")
def today():
    db = get_db()
    checkins = load_checkins(db)
    today_str = date.today().strftime("%Y-%m-%d")
    todays = next((c for c in checkins if c["checkin_date"] == today_str), None)
    streak = 0
    for c in checkins:
        if c["answered"]:
            streak += 1
        else:
            break
    open_alerts = [f for c in checkins[:1] for f in c["flags"]] if todays else []
    return render_template(
        "today.html",
        active="today",
        todays=todays,
        today_str=today_str,
        streak=streak,
        open_alerts=open_alerts,
    )


@app.route("/simulate", methods=["POST"])
def simulate():
    db = get_db()
    today_str = date.today().strftime("%Y-%m-%d")
    existing = db.execute("SELECT id FROM checkins WHERE checkin_date = ?", (today_str,)).fetchone()
    if existing is None:
        mood, medication, sleep, topics, flags = analyze_transcript(db, today_str, TODAY_CALL_SCRIPT)
        time_of_day = datetime.now().strftime("%-I:%M %p")
        duration = random.randint(4, 8)
        db.execute(
            """INSERT INTO checkins
               (checkin_date, time_of_day, duration_min, digest, mood, medication, sleep, topics, flags, transcript, reviewed, answered)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,1)""",
            (
                today_str, time_of_day, duration, TODAY_DIGEST, mood, medication, sleep,
                json.dumps(topics), json.dumps(flags),
                json.dumps([{"speaker": s, "text": t} for s, t in TODAY_CALL_SCRIPT]),
            ),
        )
        db.commit()
    return redirect(url_for("today"))


@app.route("/checkin/<int:checkin_id>/review", methods=["POST"])
def review(checkin_id):
    db = get_db()
    db.execute("UPDATE checkins SET reviewed = 1 WHERE id = ?", (checkin_id,))
    db.commit()
    return redirect(request.referrer or url_for("today"))


@app.route("/checkins")
def checkins_list():
    db = get_db()
    checkins = load_checkins(db)
    return render_template("checkins.html", active="checkins", checkins=checkins)


@app.route("/checkin/<int:checkin_id>/transcript")
def transcript(checkin_id):
    db = get_db()
    row = db.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
    item = dict(row)
    item["transcript"] = json.loads(item["transcript"]) if item["transcript"] else None
    item["flags"] = json.loads(item["flags"])
    return render_template("transcript.html", active="checkins", c=item)


@app.route("/trends")
def trends():
    db = get_db()
    checkins = load_checkins(db)
    series = list(reversed(checkins))[-14:]
    mood_data = [{"date": c["checkin_date"], "mood": c["mood"]} for c in series]

    counts = {}
    for c in series:
        for f in c["flags"]:
            key = f["text"].split(" mentioned")[0].split(" (")[0] if "Knee" in f["text"] else f["text"]
            if "Knee" in f["text"]:
                key = "Knee / joint pain"
            elif "walk" in f["text"].lower():
                key = "Skipped routine walk"
            elif "sleep" in f["text"].lower():
                key = "Trouble sleeping"
            else:
                key = "Other concern"
            counts[key] = counts.get(key, 0) + 1

    return render_template("trends.html", active="trends", mood_data=mood_data, counts=counts)


@app.route("/family")
def family():
    return render_template("family.html", active="family")


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5050)
else:
    init_db()
