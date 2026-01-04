import json, re, requests, random, os
from PyPDF2 import PdfReader
from PIL import Image
import pytesseract
from telegram import Poll
from telegram.ext import (
    Updater, CommandHandler, MessageHandler,
    Filters, PollAnswerHandler
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

OWNER_IDS = [111111111, 222222222]   # अपने numeric IDs
QUESTION_TIME = 20
STATE_FILE = "quiz_state.json"
# =========================================

EMOJI_MAP = {
    "भारत": "🇮🇳", "india": "🇮🇳",
    "नदी": "🌊", "river": "🌊",
    "विज्ञान": "🔬", "science": "🔬",
    "शेर": "🦁", "lion": "🦁"
}

GROUPS = {}

# ---------- UTIL ----------
def is_owner(user):
    return user and user.id in OWNER_IDS

def detect_emoji(q):
    q = q.lower()
    for k,e in EMOJI_MAP.items():
        if k in q:
            return e
    return "❓"

def group(cid):
    if cid not in GROUPS:
        GROUPS[cid] = {
            "notes": "",
            "stock": [],
            "quiz": [],
            "last_quiz": [],   # ⭐ set reuse
            "current": 0,
            "scores": {},
            "poll_correct": {},
            "resume_wait": False
        }
    return GROUPS[cid]

# ---------- STATE ----------
def save_state():
    with open(STATE_FILE,"w",encoding="utf-8") as f:
        json.dump(GROUPS,f,ensure_ascii=False)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE,"r",encoding="utf-8") as f:
            GROUPS.update(json.load(f))

# ---------- AI ----------
def extract_json(txt):
    m=re.search(r"\[\s*{.*?}\s*\]",txt,re.S)
    return json.loads(m.group()) if m else []

def generate_questions(text,count):
    prompt=f"""
Create {count} exam level MCQ.
Rules:
- 4 meaningful options
- 1 correct answer
- Output ONLY JSON.

CONTENT:
{text}
"""
    r=requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},
        json={"model":"llama-3.1-8b-instant","messages":[{"role":"user","content":prompt}],"temperature":0.2},
        timeout=60
    )
    return extract_json(r.json()["choices"][0]["message"]["content"])[:count]

# ---------- NOTES ----------
def upload_text(update,ctx):
    if is_owner(update.effective_user):
        group(update.effective_chat.id)["notes"] += "\n" + update.message.text
        save_state()
        update.message.reply_text("✅ Notes added")

# ---------- MAKE QUIZ ----------
def makequiz(update,ctx):
    if not is_owner(update.effective_user): return
    g=group(update.effective_chat.id)
    try: n=int(ctx.args[0])
    except:
        update.message.reply_text("Use /makequiz 5"); return

    qs=generate_questions(g["notes"],n)
    g["stock"]+=qs
    g["last_quiz"]=g["stock"][:]   # ⭐ save reusable set
    save_state()
    update.message.reply_text(f"✅ {len(qs)} प्रश्न बने | Total stock: {len(g['stock'])}")

# ---------- START QUIZ ----------
def startquiz(update,ctx):
    if not is_owner(update.effective_user): return
    g=group(update.effective_chat.id)

    # Resume check
    if g["quiz"] and g["current"] < len(g["quiz"]):
        g["resume_wait"]=True
        update.message.reply_text(
            "⚠️ पिछला क्विज अधूरा है\n"
            "1️⃣ Resume\n2️⃣ Restart\nReply 1 या 2"
        )
        return

    # Reuse last set
    if g["last_quiz"]:
        g["quiz"]=g["last_quiz"][:]
        g["current"]=0
        g["scores"]={}
        g["poll_correct"]={}
        save_state()
        send_q(update.effective_chat.id,ctx)
        return

    update.message.reply_text("❌ कोई set उपलब्ध नहीं है")

def resume_choice(update,ctx):
    g=group(update.effective_chat.id)
    if not g.get("resume_wait"): return
    g["resume_wait"]=False
    if update.message.text=="2":
        g["current"]=0
        g["scores"]={}
    save_state()
    send_q(update.effective_chat.id,ctx)

# ---------- QUIZ ----------
def send_q(cid,ctx):
    g=group(cid)
    if g["current"]>=len(g["quiz"]):
        show_result(cid,ctx); return

    q=g["quiz"][g["current"]]
    opts=list(enumerate(q["options"]))
    random.shuffle(opts)
    new=[o for _,o in opts]
    corr=[i for i,(x,_) in enumerate(opts) if x==q["answer"]][0]

    poll=ctx.bot.send_poll(
        cid,
        f"{detect_emoji(q['question'])} Q{g['current']+1}. {q['question']}",
        new,
        type=Poll.QUIZ,
        correct_option_id=corr,
        is_anonymous=False,
        open_period=QUESTION_TIME
    )

    g["poll_correct"][poll.poll.id]=corr
    g["current"]+=1
    save_state()
    ctx.job_queue.run_once(lambda c: send_q(cid,c),QUESTION_TIME+1)

def poll_answer(update,ctx):
    for g in GROUPS.values():
        pid=update.poll_answer.poll_id
        if pid in g["poll_correct"]:
            u=update.poll_answer.user
            g["scores"].setdefault(u.id,{"name":u.first_name,"score":0})
            if g["poll_correct"][pid]==update.poll_answer.option_ids[0]:
                g["scores"][u.id]["score"]+=1
            save_state()
            break

def show_result(cid,ctx):
    g=group(cid)
    txt="🏆 FINAL SCORE 🏆\n\n"
    for i,u in enumerate(sorted(g["scores"].values(),key=lambda x:x["score"],reverse=True),1):
        txt+=f"{i}. {u['name']} – {u['score']}/{len(g['quiz'])}\n"
    ctx.bot.send_message(cid,txt)
    g["current"]=0
    save_state()

# ---------- MAIN ----------
load_state()
up=Updater(BOT_TOKEN,use_context=True)
dp=up.dispatcher

dp.add_handler(CommandHandler("makequiz",makequiz))
dp.add_handler(CommandHandler("startquiz",startquiz))
dp.add_handler(MessageHandler(Filters.text & ~Filters.command,upload_text))
dp.add_handler(MessageHandler(Filters.text & ~Filters.command,resume_choice))
dp.add_handler(PollAnswerHandler(poll_answer))

print("🤖 QUIZ BOT RUNNING (RESUME + REUSE SET)")
up.start_polling()
up.idle()
