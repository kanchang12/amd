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
from google import genai
from google.genai import types
from tasks import TRACKS, get_task, get_all_tracks

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
DEFAULT_MODEL = "gemini-2.0-flash"
DATABASE_URL  = os.getenv("DATABASE_URL", "")
DEMO_MODE     = os.getenv("DEMO_MODE", "true").lower() == "true"

app = FastAPI(title="PromptCraft")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        yield conn; conn.commit()
    except:
        conn.rollback(); raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS prompt_users (
                    id         SERIAL PRIMARY KEY,
                    username   TEXT UNIQUE NOT NULL,
                    email      TEXT UNIQUE NOT NULL,
                    password   TEXT NOT NULL,
                    plan       TEXT NOT NULL DEFAULT 'free',
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS prompt_progress (
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
            cur.execute("SELECT COUNT(*) FROM prompt_users")
            if cur.fetchone()[0] == 0:
                cur.executemany(
                    "INSERT INTO prompt_users (username,email,password,plan) VALUES (%s,%s,%s,%s)",
                    [("adult", "adult@promptcraft.ai", "learn123", "free")]
                )

@app.on_event("startup")
def startup():
    try: init_db()
    except Exception as e: print(f"DB init skipped: {e}")

def llm(system, messages, temperature=0.7, max_tokens=600):
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
    config = types.GenerateContentConfig(
        temperature=temperature, max_output_tokens=max_tokens,
        system_instruction=system or None,
    )
    return client.models.generate_content(model=DEFAULT_MODEL, contents=contents, config=config).text.strip()

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
            cur.execute("SELECT * FROM prompt_users WHERE username=%s OR email=%s",
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
                    "INSERT INTO prompt_users (username,email,password,plan) VALUES (%s,%s,%s,'free')",
                    (req.username.lower(), req.email.lower(), req.password)
                )
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(400, "Username or email already exists")
    return {"username": req.username.lower(), "email": req.email.lower(), "plan": "free"}

@app.post("/api/upgrade/{username}")
def upgrade(username: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE prompt_users SET plan='pro' WHERE username=%s", (username,))
    return {"success": True, "plan": "pro", "demo": DEMO_MODE,
            "message": "In production this processes £9.99/month via Stripe. Demo: instant upgrade."}

@app.get("/api/tracks")
def tracks():
    return {"tracks": get_all_tracks()}

@app.get("/api/tracks/{track_id}/tasks")
def track_tasks(track_id: str, username: Optional[str] = None):
    if track_id not in TRACKS: raise HTTPException(404, "Track not found")
    track = TRACKS[track_id]
    plan = "free"
    if username:
        try:
            with get_db() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT plan FROM prompt_users WHERE username=%s", (username,))
                    row = cur.fetchone()
                    if row: plan = row["plan"]
        except: pass
    result = [{**t, "locked": plan == "free" and t["id"] > track["free_limit"]} for t in track["tasks"]]
    return {"track_id": track_id, "tasks": result, "plan": plan}

class ProgressSave(BaseModel):
    username: str; track_id: str; task_id: int
    score: int; passed: bool; last_prompt: str; last_output: str

@app.post("/api/progress")
def save_progress(req: ProgressSave):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO prompt_progress (username,track_id,task_id,score,passed,attempts,last_prompt,last_output,updated_at)
                VALUES (%s,%s,%s,%s,%s,1,%s,%s,NOW())
                ON CONFLICT (username,track_id,task_id) DO UPDATE SET
                    score=GREATEST(prompt_progress.score,EXCLUDED.score),
                    passed=GREATEST(prompt_progress.passed::int,EXCLUDED.passed::int)::boolean,
                    attempts=prompt_progress.attempts+1,
                    last_prompt=EXCLUDED.last_prompt,
                    last_output=EXCLUDED.last_output,
                    updated_at=NOW()
            """, (req.username,req.track_id,req.task_id,req.score,req.passed,
                  req.last_prompt[:2000],req.last_output[:2000]))
    return {"saved": True}

@app.get("/api/progress/{username}")
def get_progress(username: str):
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT track_id,task_id,score,passed,attempts FROM prompt_progress WHERE username=%s",
                (username,))
            rows = cur.fetchall()
    return {"username": username, "progress": [dict(r) for r in rows],
            "completed": sum(1 for r in rows if r["passed"]),
            "avg_score": round(sum(r["score"] for r in rows)/len(rows), 1) if rows else 0}

def run_eval_agent(task, user_prompt, ai_output):
    system = "You are EvalAgent. Evaluate prompt submissions strictly. Respond ONLY with valid JSON. No markdown."
    prompt = f"""TASK: {task['title']}
DESCRIPTION: {task['desc']}
SUCCESS CRITERIA: {task['success_criteria']}
LEARNER PROMPT: {user_prompt}
AI OUTPUT: {ai_output[:800]}
Return: {{"score":<0-100>,"passed":<true if >=70>,"grade":<A/B/C/D/F>,"what_worked":"<1-2 sentences>","what_missed":"<1-2 sentences or empty>","verdict":"<one sentence>"}}"""
    raw = llm(system, [{"role":"user","content":prompt}], temperature=0.2, max_tokens=350)
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n"); raw = "\n".join(lines[1:-1] if lines[-1]=="```" else lines[1:])
    try: r = json.loads(raw)
    except: r = {"score":50,"passed":False,"grade":"C","what_worked":"Received.","what_missed":"Parse error.","verdict":"Try again."}
    r["agent"] = "EvalAgent"
    return r

def run_coach_agent(task, user_prompt, score, what_missed):
    system = "You are CoachAgent. Give ONE specific hint. 3 sentences max. Never give the answer."
    prompt = f"Score: {score}/100\nTASK: {task['title']}\nCRITERIA: {task['success_criteria']}\nMISSED: {what_missed}\nPROMPT: {user_prompt[:400]}\nEXAMPLE: {task.get('example','')[:300]}\nGive ONE hint only."
    return llm(system, [{"role":"user","content":prompt}], temperature=0.7, max_tokens=150)

def run_path_agent(username, track_id, task_id, task, score, completed):
    completed_tracks = list(set(r["track_id"] for r in completed))
    next_task = get_task(track_id, task_id+1)
    all_tracks = get_all_tracks()
    unstarted = [t["title"] for t in all_tracks if t["id"] not in completed_tracks and t["id"]!=track_id]
    system = "You are PathAgent. Recommend learner's best next step. 2 sentences max. Be specific."
    prompt = f"Passed task {task_id} '{task['title']}' in '{track_id}' with {score}/100.\nTotal passed: {len(completed)}. Tracks started: {completed_tracks or ['none']}.\nUnstarted: {unstarted[:4]}.\nNext in track: {'Task '+str(task_id+1)+' - '+next_task['title'] if next_task else 'Track complete!'}.\nContinue or branch?"
    rec = llm(system, [{"role":"user","content":prompt}], temperature=0.8, max_tokens=120)
    return {"recommendation": rec,
            "next_task": {"track_id":track_id,"task_id":task_id+1,"title":next_task["title"]} if next_task else None,
            "total_passed": len(completed), "agent": "PathAgent"}

class SubmitRequest(BaseModel):
    username: str; track_id: str; task_id: int; user_prompt: str

@app.post("/api/submit")
def submit(req: SubmitRequest):
    task = get_task(req.track_id, req.task_id)
    if not task: raise HTTPException(404, "Task not found")
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT plan FROM prompt_users WHERE username=%s", (req.username,))
            row = cur.fetchone(); plan = row["plan"] if row else "free"
    if plan=="free" and task["id"] > TRACKS[req.track_id]["free_limit"]:
        raise HTTPException(403, "This task requires Pro. Upgrade to unlock all 116 tasks.")
    ai_output = llm("You are a helpful AI assistant. Answer clearly.",
                    [{"role":"user","content":req.user_prompt}], temperature=0.7, max_tokens=600)
    eval_result = run_eval_agent(task, req.user_prompt, ai_output)
    score = eval_result.get("score",0); passed = eval_result.get("passed",False)
    try:
        save_progress(ProgressSave(username=req.username, track_id=req.track_id, task_id=req.task_id,
            score=score, passed=passed, last_prompt=req.user_prompt, last_output=ai_output))
    except: pass
    coach = run_coach_agent(task, req.user_prompt, score, eval_result.get("what_missed","")) if not passed else None
    path = None
    if passed:
        try:
            with get_db() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT track_id,task_id,score FROM prompt_progress WHERE username=%s AND passed=true", (req.username,))
                    completed = cur.fetchall()
        except: completed=[]
        path = run_path_agent(req.username, req.track_id, req.task_id, task, score, completed)
    return {"ai_output": ai_output, "eval": eval_result, "coach": coach, "path": path}

@app.get("/", response_class=HTMLResponse)
def index(): return FileResponse("static/index.html")

@app.get("/{path:path}", response_class=HTMLResponse)
def catch_all(path: str): return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8080)), reload=False)
