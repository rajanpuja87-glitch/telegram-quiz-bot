import json, re, requests, random, os
from PyPDF2 import PdfReader
from PIL import Image
import pytesseract
from telegram import Update, Poll
from telegram.ext import (
    Updater, CommandHandler, MessageHandler,
    Filters, PollAnswerHandler, CallbackContext
)

# ================= CONFIG =================
BOT_TOKEN = "PASTE_TELEGRAM_BOT_TOKEN"
GROQ_API_KEY = "PASTE_GROQ_API_KEY"

OWNER_IDS = [111111111, 222222222]   # 👈 अपने numeric Telegram IDs

QUESTION_TIME = 20
STATE_FILE = "quiz_state.json"
# =========================================

# ---------- EMOJI MAP ----------
EMOJI_MAP = {
    "शेर": "🦁", "lion": "🦁",
    "नदी": "🌊", "river": "🌊",
    "भारत": "🇮🇳", "india": "🇮🇳",
    "पृथ्वी": "🌍", "earth": "🌍",
    "विज्ञान": "🔬", "science": "🔬",
    "कृषि": "🌾", "agriculture": "🌾",
}

# vague / unsafe patterns
BAD_PATTERNS = ["हाल ही", "सफल परीक्षण", "किस शहर", "कहाँ किया"]

GROUPS = {}

# ---------- UTIL ----------
def is_owner(user):
    return user and user.id in OWNER_IDS

def clean(t):
    return re.sub(r"\s+", " ", t).strip()

def trim(t, n=90):
    t = clean(t)
    return t[:n] + "..." if len(t) > n else t

def detect_emoji(text):
    low = text.lower()
    for k, e in EMOJI_MAP.items():
        if k in low:
            return e
    return "❓"

def group(chat_id):
    if chat_id not in GROUPS:
        GROUPS[chat_id] = {
            "notes": "",
            "stock": [],
            "quiz": [],
            "current": 0,
            "scores": {},
            "poll_correct": {}
        }
    return GROUPS[chat_id]

# ---------- OPTION VALIDATION ----------
def is_meaningful_option(opt):
    opt = opt.strip().lower()
    if len(opt) <= 2:
        return False
    bad = ["a", "b", "c", "d", "option a", "option b", "option c", "option d"]
    return opt not in bad

def is_exam_safe(q):
    if not q.get("question"):
        return False

    opts = q.get("options", [])
    if len(opts) != 4:
        return False

    for o in opts:
        if not is_meaningful_option(o):
            return False

    # vague question + no confirmed exam → reject
    if not q.get("exam"):
        for bad in BAD_PATTERNS:
            if bad in q["question"]:
                return False

    return q.get("answer") in [0, 1, 2, 3]

# ---------- JSON EXTRACT ----------
def extract_json(text):
    m = re.search(r"\[\s*{.*?}\s*\]", text, re.S)
    return json.loads(m.group()) if m else []

# ---------- STATE SAVE / LOAD ----------
def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(GROUPS, f, ensure_ascii=False)

def load_state():
    global GROUPS
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            GROUPS.update(json.load(f))

# ---------- AI QUESTION GENERATION ----------
def generate_questions(text, count):
    if len(text) > 3000:
        text = text[:3000]

    prompt = f"""
You are an expert Indian competitive exam question setter.

VERY IMPORTANT RULES:
- Mention exam name & year ONLY if the question was ACTUALLY asked in a real exam.
- If you are NOT 100% sure, leave the exam field EMPTY ("").
- Do NOT guess the exam.
- Do NOT repeat the same exam for all questions.

Create {count} exam-level MCQ questions.

Rules:
- 4 meaningful options (not A/B/C/D)
- One correct answer
- Questions must be factual and clear
- Output ONLY valid JSON (no text outside)

Format:
[
  {{
    "question": "...?",
    "options": ["option1","option2","option3","option4"],
    "answer": 0,
    "exam": ""
  }}
]

CONTENT:
{text}
"""

    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        },
        timeout=60
    )

    raw = r.json()["choices"][0]["message"]["content"]
    return extract_json(raw)[:count]

# ---------- COMMANDS ----------
def start(update, context):
    if not is_owner(update.effective_user):
        return
    update.message.reply_text(
        "🎓 FINAL AI EXAM QUIZ BOT\n\n"
        "/uploadtext\n/uploadpdf\n/uploadphoto\n"
        "/makequiz 5  (store questions)\n"
        "/startquiz  (start exam)"
    )

def uploadtext(update, context):
    if not is_owner(update.effective_user):
        return
    context.user_data["wait_text"] = True
    update.message.reply_text("✍️ Text भेजो")

def save_text(update, context):
    if not is_owner(update.effective_user):
        return
    if context.user_data.get("wait_text"):
        group(update.effective_chat.id)["notes"] = update.message.text
        context.user_data["wait_text"] = False
        update.message.reply_text("✅ Text saved")

def uploadpdf(update, context):
    if not is_owner(update.effective_user):
        return
    update.message.reply_text("📄 PDF भेजो")

def handle_pdf(update, context):
    if not is_owner(update.effective_user):
        return
    g = group(update.effective_chat.id)
    f = update.message.document.get_file()
    f.download("data.pdf")
    r = PdfReader("data.pdf")
    g["notes"] = "\n".join(p.extract_text() or "" for p in r.pages)
    update.message.reply_text("✅ PDF read")

def uploadphoto(update, context):
    if not is_owner(update.effective_user):
        return
    update.message.reply_text("🖼 Photo भेजो")

def handle_photo(update, context):
    if not is_owner(update.effective_user):
        return
    g = group(update.effective_chat.id)
    p = update.message.photo[-1].get_file()
    p.download("img.jpg")
    g["notes"] = pytesseract.image_to_string(Image.open("img.jpg"))
    update.message.reply_text("✅ Image read")

# ---------- STORE QUESTIONS ----------
def makequiz(update, context):
    if not is_owner(update.effective_user):
        return
    g = group(update.effective_chat.id)

    try:
        n = int(context.args[0])
    except:
        update.message.reply_text("Use /makequiz 5")
        return

    qs = generate_questions(g["notes"], n)
    qs = [q for q in qs if is_exam_safe(q)]

    if not qs:
        update.message.reply_text("⚠️ अच्छे exam-level प्रश्न नहीं बन पाए")
        return

    g["stock"].extend(qs)
    save_state()

    update.message.reply_text(
        f"✅ {len(qs)} प्रश्न STORE हुए\n"
        f"📦 Total stored: {len(g['stock'])}"
    )

# ---------- START QUIZ ----------
def startquiz(update, context):
    if not is_owner(update.effective_user):
        return
    g = group(update.effective_chat.id)

    if not g["stock"]:
        update.message.reply_text("❌ कोई stored प्रश्न नहीं")
        return

    g["quiz"] = g["stock"][:]
    g["stock"] = []
    g["current"] = g.get("current", 0)
    g["scores"] = g.get("scores", {})
    g["poll_correct"] = {}

    send_question(update.effective_chat.id, context)

# ---------- SEND QUESTION ----------
def send_question(chat_id, context):
    g = group(chat_id)

    if g["current"] >= len(g["quiz"]):
        show_result(chat_id, context)
        return

    q = g["quiz"][g["current"]]
    emoji = detect_emoji(q["question"])
    exam_tag = f"\n📌 Asked in: {q['exam']}" if q.get("exam") else ""

    opts = list(enumerate(q["options"]))
    random.shuffle(opts)
    new_opts = [o for _, o in opts]
    correct_id = [i for i,(idx,_) in enumerate(opts) if idx == q["answer"]][0]

    poll = context.bot.send_poll(
        chat_id,
        f"{emoji} Q{g['current']+1}. {q['question']}{exam_tag}",
        [f"{e} {trim(o)}" for e,o in zip(["🅰️","🅱️","🅲","🅳"], new_opts)],
        type=Poll.QUIZ,
        correct_option_id=correct_id,
        is_anonymous=False,
        open_period=QUESTION_TIME
    )

    g["poll_correct"][poll.poll.id] = correct_id
    g["current"] += 1
    save_state()

    context.job_queue.run_once(
        lambda ctx: send_question(chat_id, ctx),
        QUESTION_TIME + 1
    )

# ---------- ANSWER ----------
def poll_answer(update, context):
    for g in GROUPS.values():
        pid = update.poll_answer.poll_id
        if pid in g["poll_correct"]:
            u = update.poll_answer.user
            g["scores"].setdefault(u.id, {"name":u.first_name,"score":0})
            if g["poll_correct"][pid] == update.poll_answer.option_ids[0]:
                g["scores"][u.id]["score"] += 1
            save_state()
            break

# ---------- RESULT ----------
def show_result(chat_id, context):
    g = group(chat_id)
    txt = "🏆 FINAL RESULT 🏆\n\n"
    for i,u in enumerate(
        sorted(g["scores"].values(), key=lambda x:x["score"], reverse=True),1):
        txt += f"{i}. {u['name']} – {u['score']}\n"
    context.bot.send_message(chat_id, txt)

    GROUPS.pop(chat_id, None)
    save_state()

# ---------- MAIN ----------
load_state()

up = Updater(BOT_TOKEN, use_context=True)
dp = up.dispatcher

dp.add_handler(CommandHandler("start", start))
dp.add_handler(CommandHandler("uploadtext", uploadtext))
dp.add_handler(CommandHandler("uploadpdf", uploadpdf))
dp.add_handler(CommandHandler("uploadphoto", uploadphoto))
dp.add_handler(CommandHandler("makequiz", makequiz))
dp.add_handler(CommandHandler("startquiz", startquiz))

dp.add_handler(MessageHandler(Filters.text & ~Filters.command, save_text))
dp.add_handler(MessageHandler(Filters.document.pdf, handle_pdf))
dp.add_handler(MessageHandler(Filters.photo, handle_photo))
dp.add_handler(PollAnswerHandler(poll_answer))

print("🤖 FINAL AI-DRIVEN EXAM QUIZ BOT RUNNING")
up.start_polling()
up.idle()
