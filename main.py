from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import os, json, psycopg2, psycopg2.extras
from contextlib import contextmanager
from openai import OpenAI

from tasks import TRACKS, TASKS, get_task, get_all_tracks

# ── AMD Developer Cloud client (OpenAI-compatible, Qwen on MI300X) ────────────
amd_client = OpenAI(
    api_key=os.getenv("AMD_API_KEY", ""),
    base_url=os.getenv("AMD_BASE_URL", "https://api.amd.developer.cloud/v1"),
)
DEFAULT_MODEL  = os.getenv("DEFAULT_MODEL",  "Qwen/Qwen2.5-72B-Instruct")
FAST_MODEL     = os.getenv("FAST_MODEL",     "Qwen/Qwen2.5-7B-Instruct")
DATABASE_URL   = os.getenv("DATABASE_URL", "")
DEMO_MODE      = os.getenv("DEMO_MODE", "true").lower() == "true"

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="PromptCraft — AMD Hackathon")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── DB (Supabase direct URL) ──────────────────────────────────────────────────
@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id         SERIAL PRIMARY KEY,
                    username   TEXT UNIQUE NOT NULL,
                    email      TEXT UNIQUE NOT NULL,
                    password   TEXT NOT NULL,
                    plan       TEXT NOT NULL DEFAULT 'free',
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS progress (
                    id          SERIAL PRIMARY KEY,
                    username    TEXT NOT NULL,
                    track_id    TEXT NOT NULL,
                    task_id     INTEGER NOT NULL,
                    score       INTEGER DEFAULT 0,
                    passed      BOOLEAN DEFAULT FALSE,
                    attempts    INTEGER DEFAULT 0,
                    last_prompt TEXT DEFAULT '',
                    last_output TEXT DEFAULT '',
                    updated_at  TIMESTAMP DEFAULT NOW(),
                    UNIQUE(username, track_id, task_id)
                );
            """)
            cur.execute("SELECT COUNT(*) FROM users")
            if cur.fetchone()[0] == 0:
                cur.executemany(
                    "INSERT INTO users (username,email,password,plan) VALUES (%s,%s,%s,%s)",
                    [("demo","demo@demo.com","demo123","free"),
                     ("pro","pro@demo.com","pro123","pro")]
                )

@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:
        print(f"DB init skipped: {e}")

# ── Core LLM call — AMD Developer Cloud ──────────────────────────────────────
def llm(system: str, messages: List[dict],
        model: str = None, temperature: float = 0.7, max_tokens: int = 600) -> str:
    model = model or DEFAULT_MODEL
    msgs = [{"role": "system", "content": system}] + messages
    response = amd_client.chat.completions.create(
        model=model,
        messages=msgs,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()

# ── Auth ──────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class SignupRequest(BaseModel):
    username: str
    email: str
    password: str

@app.post("/api/login")
def login(req: LoginRequest):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username=%s OR email=%s",
                        (req.username.lower(), req.username.lower()))
            user = cur.fetchone()
    if not user or user["password"] != req.password:
        raise HTTPException(401, "Wrong username or password")
    return {"username": user["username"], "email": user["email"], "plan": user["plan"]}

@app.post("/api/signup")
def signup(req: SignupRequest):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (username,email,password,plan) VALUES (%s,%s,%s,'free')",
                    (req.username.lower(), req.email.lower(), req.password)
                )
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(400, "Username or email already exists")
    return {"username": req.username.lower(), "email": req.email.lower(), "plan": "free"}

# ── Payment ───────────────────────────────────────────────────────────────────
@app.post("/api/upgrade/{username}")
def upgrade(username: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET plan='pro' WHERE username=%s", (username,))
    return {
        "success": True, "plan": "pro", "demo": DEMO_MODE,
        "message": "In production this processes £9.99/month via Stripe. Demo: instant upgrade."
    }

# ── Tracks & Tasks ────────────────────────────────────────────────────────────
@app.get("/api/tracks")
def tracks():
    return {"tracks": get_all_tracks()}

@app.get("/api/tracks/{track_id}/tasks")
def track_tasks(track_id: str, username: Optional[str] = None):
    if track_id not in TRACKS:
        raise HTTPException(404, "Track not found")
    track = TRACKS[track_id]
    plan = "free"
    if username:
        try:
            with get_db() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT plan FROM users WHERE username=%s", (username,))
                    row = cur.fetchone()
                    if row: plan = row["plan"]
        except: pass
    result = [{**t, "locked": plan == "free" and t["id"] > track["free_limit"]}
              for t in track["tasks"]]
    return {"track_id": track_id, "tasks": result, "plan": plan}

# ── Progress ──────────────────────────────────────────────────────────────────
class ProgressSave(BaseModel):
    username: str; track_id: str; task_id: int
    score: int; passed: bool; last_prompt: str; last_output: str

@app.post("/api/progress")
def save_progress(req: ProgressSave):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO progress (username,track_id,task_id,score,passed,attempts,last_prompt,last_output,updated_at)
                VALUES (%s,%s,%s,%s,%s,1,%s,%s,NOW())
                ON CONFLICT (username,track_id,task_id) DO UPDATE SET
                    score=GREATEST(progress.score,EXCLUDED.score),
                    passed=GREATEST(progress.passed::int,EXCLUDED.passed::int)::boolean,
                    attempts=progress.attempts+1,
                    last_prompt=EXCLUDED.last_prompt,
                    last_output=EXCLUDED.last_output,
                    updated_at=NOW()
            """, (req.username, req.track_id, req.task_id, req.score, req.passed,
                  req.last_prompt[:2000], req.last_output[:2000]))
    return {"saved": True}

@app.get("/api/progress/{username}")
def get_progress(username: str):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT track_id,task_id,score,passed,attempts,updated_at FROM progress WHERE username=%s",
                (username,))
            rows = cur.fetchall()
    return {
        "username": username,
        "progress": [dict(r) for r in rows],
        "completed": sum(1 for r in rows if r["passed"]),
        "total_attempts": sum(r["attempts"] for r in rows),
        "avg_score": round(sum(r["score"] for r in rows)/len(rows),1) if rows else 0
    }

# ─────────────────────────────────────────────────────────────────────────────
# THREE AGENTS — all running Qwen on AMD MI300X via AMD Developer Cloud
# ─────────────────────────────────────────────────────────────────────────────

# ── AGENT 1: EvalAgent ────────────────────────────────────────────────────────
# Evaluates every submission. Scores 0-100. Grades A-F. Pass = score >= 70.
# Uses Qwen 72B for accuracy. Returns strict JSON.
def run_eval_agent(task: dict, user_prompt: str, ai_output: str) -> dict:
    system = """You are EvalAgent — a strict, fair AI learning evaluator powered by Qwen on AMD MI300X.
You evaluate prompt submissions against explicit success criteria.
You respond ONLY with valid JSON. No preamble, no markdown fences."""

    user = f"""TASK: {task['title']}
DESCRIPTION: {task['desc']}
SUCCESS CRITERIA: {task['success_criteria']}

LEARNER'S PROMPT:
{user_prompt}

AI OUTPUT FROM THAT PROMPT:
{ai_output[:800]}

Return this exact JSON:
{{
  "score": <integer 0-100>,
  "passed": <true if score >= 70 else false>,
  "grade": <"A" if >=90, "B" if >=80, "C" if >=70, "D" if >=60, "F" otherwise>,
  "what_worked": "<1-2 sentences on strengths>",
  "what_missed": "<1-2 sentences on gaps, empty string if passed perfectly>",
  "verdict": "<one punchy sentence overall verdict>"
}}"""

    raw = llm(system, [{"role":"user","content":user}],
              model=DEFAULT_MODEL, temperature=0.2, max_tokens=350)
    # Strip markdown fences if model wraps in them
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1]=="```" else lines[1:])
    try:
        result = json.loads(raw)
    except:
        result = {"score":50,"passed":False,"grade":"C",
                  "what_worked":"Submission received.",
                  "what_missed":"Evaluation parsing failed — try again.",
                  "verdict":"Unable to fully evaluate."}
    result["agent"] = "EvalAgent"
    result["model"] = DEFAULT_MODEL
    return result

# ── AGENT 2: CoachAgent ───────────────────────────────────────────────────────
# Fires ONLY when score < 70. Gives one targeted hint. Never gives the answer.
# Uses fast Qwen 7B — speed matters for UX here.
def run_coach_agent(task: dict, user_prompt: str, score: int, what_missed: str) -> str:
    system = """You are CoachAgent — a tough, direct AI tutor running on AMD MI300X.
You give exactly ONE specific, actionable hint — never the full answer.
No generic encouragement. No praise padding. Just the one thing they need to fix.
Maximum 3 sentences."""

    user = f"""Learner scored {score}/100.

TASK: {task['title']}
WHAT THEY NEEDED: {task['desc']}
SUCCESS CRITERIA: {task['success_criteria']}
WHAT THEIR PROMPT MISSED: {what_missed}
THEIR PROMPT: {user_prompt[:400]}
EXAMPLE OF A GOOD PROMPT: {task.get('example','Not provided')[:300]}

Give ONE specific hint (3 sentences max). Do NOT rewrite their prompt. Do NOT solve it for them."""

    return llm(system, [{"role":"user","content":user}],
               model=FAST_MODEL, temperature=0.7, max_tokens=180)

# ── AGENT 3: PathAgent ────────────────────────────────────────────────────────
# Fires ONLY when score >= 70. Reads full progress. Recommends next move.
# Uses Qwen 72B — needs reasoning across all completed tasks.
def run_path_agent(username: str, track_id: str, task_id: int,
                   task: dict, score: int, completed: list) -> dict:
    completed_tracks = list(set(r["track_id"] for r in completed))
    total_passed = len(completed)
    next_task = get_task(track_id, task_id + 1)
    all_tracks = get_all_tracks()
    unstarted = [t["title"] for t in all_tracks if t["id"] not in completed_tracks and t["id"] != track_id]

    system = """You are PathAgent — a personal AI learning strategist running on AMD MI300X.
You analyse a learner's full progress and recommend their optimal next step.
Be specific. Name the actual track or task. Maximum 2 sentences."""

    user = f"""Learner just passed task {task_id} '{task['title']}' in '{track_id}' with score {score}/100.
Total tasks passed: {total_passed}
Tracks started: {completed_tracks or ['none']}
Tracks not yet started: {unstarted[:5]}
Next task in this track: {'Task ' + str(task_id+1) + ' - ' + next_task['title'] if next_task else 'Track complete!'}

Recommend: continue this track or branch to a new one? Name the specific next task or track."""

    recommendation = llm(system, [{"role":"user","content":user}],
                         model=DEFAULT_MODEL, temperature=0.8, max_tokens=120)

    return {
        "recommendation": recommendation,
        "next_task": {"track_id": track_id, "task_id": task_id+1, "title": next_task["title"]} if next_task else None,
        "total_passed": total_passed,
        "agent": "PathAgent",
        "model": DEFAULT_MODEL,
    }

# ── /api/submit — orchestrates all three agents ───────────────────────────────
class SubmitRequest(BaseModel):
    username: str
    track_id: str
    task_id: int
    user_prompt: str

@app.post("/api/submit")
def submit(req: SubmitRequest):
    task = get_task(req.track_id, req.task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    # Check lock
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT plan FROM users WHERE username=%s", (req.username,))
            row = cur.fetchone()
            plan = row["plan"] if row else "free"
    if plan == "free" and task["id"] > TRACKS[req.track_id]["free_limit"]:
        raise HTTPException(403, "This task requires Pro. Upgrade to unlock all 116 tasks.")

    # Step 1: Run the learner's prompt through Qwen on AMD
    ai_output = llm(
        "You are a helpful AI assistant. Answer clearly and completely.",
        [{"role":"user","content":req.user_prompt}],
        model=DEFAULT_MODEL, temperature=0.7, max_tokens=600
    )

    # Step 2: EvalAgent scores it
    eval_result = run_eval_agent(task, req.user_prompt, ai_output)
    score = eval_result.get("score", 0)
    passed = eval_result.get("passed", False)

    # Step 3: Save progress
    try:
        save_progress(ProgressSave(
            username=req.username, track_id=req.track_id, task_id=req.task_id,
            score=score, passed=passed,
            last_prompt=req.user_prompt, last_output=ai_output
        ))
    except: pass

    # Step 4: CoachAgent (score < 70 only)
    coach = None
    if not passed:
        coach = run_coach_agent(task, req.user_prompt, score, eval_result.get("what_missed",""))

    # Step 5: PathAgent (passed only)
    path = None
    if passed:
        try:
            with get_db() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT track_id,task_id,score FROM progress WHERE username=%s AND passed=true",
                                (req.username,))
                    completed = cur.fetchall()
        except: completed = []
        path = run_path_agent(req.username, req.track_id, req.task_id, task, score, completed)

    return {"ai_output": ai_output, "eval": eval_result, "coach": coach, "path": path}

# ── Static ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(): return FileResponse("static/index.html")

@app.get("/{path:path}", response_class=HTMLResponse)
def catch_all(path: str): return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8080)), reload=False)
