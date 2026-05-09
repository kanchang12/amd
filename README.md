---
title: PromptCraft
emoji: ✦
colorFrom: purple
colorTo: indigo
sdk: docker
pinned: false
---

# PromptCraft — Master AI in 3 Weeks

**AMD Developer Hackathon · Track 1: AI Agents & Agentic Workflows**

**Stack: Qwen 2.5 72B + Qwen 2.5 7B on AMD Instinct MI300X via AMD Developer Cloud**

---

## What it is

An AI learning platform that teaches prompt engineering, RAG, agents, and LLM APIs through 116 hands-on tasks across 13 tracks. Every submission is evaluated by three specialised AI agents running on AMD.

## The Three Agents (all Qwen on AMD MI300X)

| Agent | Model | Triggers | Purpose |
|---|---|---|---|
| **EvalAgent** | Qwen 2.5 72B | Every submission | Scores 0-100, grades A-F, what worked / what missed |
| **CoachAgent** | Qwen 2.5 7B | Score < 70 only | One targeted hint, never the answer |
| **PathAgent** | Qwen 2.5 72B | Score ≥ 70 only | Reads full progress, recommends next move |

**Agentic loop:** submit → EvalAgent → [CoachAgent OR PathAgent] → repeat

## Why AMD

- EvalAgent uses Qwen 72B — needs MI300X memory bandwidth for fast inference
- CoachAgent uses Qwen 7B — routes to faster/cheaper instance for latency-sensitive hints  
- PathAgent does cross-session reasoning over full progress history
- All three run on AMD Developer Cloud, zero external LLM dependencies

## 13 Tracks, 116 Tasks

Prompt Fundamentals · Prompt Engineering · Image Gen · Code Gen · Data Analysis · RAG · AI Agents · LLM API · Mini Projects · Video & Multimodal · AI Products · Fine-Tuning · AI Safety

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in AMD_API_KEY and DATABASE_URL (Supabase)
python main.py
# → http://localhost:8080
```

## Credentials

| User | Password | Plan |
|---|---|---|
| demo | demo123 | free (3 tasks/track) |
| pro | pro123 | pro (all 116) |

## Payment (Hackathon Demo)

Upgrade button simulates £9.99/month Stripe payment instantly.
Production: replace `/api/upgrade` endpoint with `stripe.checkout.Session.create(...)`.
