import sqlite3
import random
import logging
from datetime import datetime
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)

# ==========================================
#              КОНФИГУРАЦИЯ
# ==========================================
TOKEN = "8605729225:AAEA6Vt4ZVE61Sc9XVtUvd-NUyyVZ9jcIKI"
ADMIN_ID = 6820965428
PAYMENT_CHANNEL_ID = -1002344799043  # ID канала @yabloko_gifts_channel
DB_PATH = "bot_database.db"

logging.basicConfig(level=logging.INFO)

# ==========================================
#             БАЗА ДАННЫХ
# ==========================================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

def init_db():
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, 
        username TEXT, 
        balance REAL DEFAULT 0, 
        invited_by INTEGER, 
        invited_by_l2 INTEGER,
        tasks_completed INTEGER DEFAULT 0
    )""")
    cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, link TEXT, reward REAL, active INTEGER DEFAULT 1)")
    cur.execute("CREATE TABLE IF NOT EXISTS completed_tasks (user_id INTEGER, task_id INTEGER, PRIMARY KEY(user_id, task_id))")
    cur.execute("CREATE TABLE IF NOT EXISTS sponsors (id INTEGER PRIMARY KEY AUTOINCREMENT, link TEXT, active INTEGER DEFAULT 1)")
    cur.execute("CREATE TABLE IF NOT EXISTS lottery (user_id INTEGER)")
    
    defaults = [
        ('ref_l1', '3.5'),
        ('ref_l2', '0.5'),
        ('lottery_price', '1.0'),
        ('lottery_limit', '10')
    ]
    cur.executemany("INSERT OR IGNORE INTO settings VALUES (?, ?)", defaults)
    conn.commit()

init_db()

# ==========================================
#           ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def get_setting(key, default="0"):
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = cur.fetchone()
    return res[0] if res else default

def add_balance(user_id, amount):
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()

async def is_member(context, user_id, channel_link):
    try:
        chat_id = channel_link.replace("https://t.me/", "@") if "t.me/" in channel_link else channel_link
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

# ==========================================
#        ГЛОБАЛЬНАЯ ПРОВЕРКА И ШТРАФЫ
# ==========================================
async def global_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cur.execute("SELECT t.id, t.link, t.reward, t.title FROM tasks t JOIN completed_tasks ct ON t.id = ct.task_id WHERE ct.user_id = ?", (user_id,))
    completed = cur.fetchall()
    
    for tid, link, reward, title in completed:
        if not await is_member(context, user_id, link):
            penalty = reward * 2
            add_balance(user_id, -penalty)
            cur.execute("DELETE FROM completed_tasks WHERE user_id = ? AND task_id = ?", (user_id, tid))
            conn.commit()
            await context.bot.send_message(user_id, f"🚨 **Штраф x2!**\nВы отписались от задания: {title}. С вашего баланса списано **{penalty:.2f}⭐**")
    return True

# ==========================================
#                 МЕНЮ
# ==========================================
def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("👤 Профиль"), KeyboardButton("📋 Задания")],
        [KeyboardButton("🎰 Лотерея"), KeyboardButton("💸 Вывод")]
    ], resize_keyboard=True)

# ==========================================
#               ОБРАБОТЧИКИ
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args
    
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        invited_by = int(args[0]) if args and args[0].isdigit() and int(args[0]) != uid else None
        invited_by_l2 = None
        if invited_by:
            cur.execute("SELECT invited_by FROM users WHERE user_id=?", (invited_by,))
            res = cur.fetchone()
            if res: invited_by_l2 = res[0]
            
        cur.execute("INSERT INTO users (user_id, username, invited_by, invited_by_l2) VALUES (?, ?, ?, ?)",
                    (uid, update.effective_user.username, invited_by, invited_by_l2))
        
        if invited_by:
            add_balance(invited_by, float(get_setting("ref_l1")))
        if invited_by_l2:
            add_balance(invited_by_l2, float(get_setting("ref_l2")))
        conn.commit()

    await update.message.reply_text("🍎 **Yabloko Gifts приветствует тебя!**\nЗарабатывай звезды и меняй их на подарки.", reply_markup=main_menu())

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await global_check(update, context)
    uid = update.effective_user.id
    cur.execute("SELECT balance, tasks_completed FROM users WHERE user_id=?", (uid,))
    bal, tasks = cur.fetchone()
    ref_link = f"https://t.me/{(await context.bot.get_me()).username}?start={uid}"
    
    await update.message.reply_text(
        f"👤 **ЛИЧНЫЙ КАБИНЕТ**\n\n"
        f"💰 Баланс: **{bal:.2f} ⭐**\n"
        f"✅ Выполнено заданий: {tasks}\n\n"
        f"🔗 **Твоя ссылка для приглашений:**\n`{ref_link}`",
        parse_mode="Markdown"
    )

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await global_check(update, context)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("15 💝", callback_data="buy:💝:15"), InlineKeyboardButton("15 🧸", callback_data="buy:🧸:15")],
        [InlineKeyboardButton("25 🌹", callback_data="buy:🌹:25"), InlineKeyboardButton("25 🎁", callback_data="buy:🎁:25")],
        [InlineKeyboardButton("50 ⭐", callback_data="buy:⭐:50")]
    ])
    await update.message.reply_text("💸 **ВЫБЕРИ ПОДАРОК ДЛЯ ВЫВОДА:**", reply_markup=kb)

async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, icon, price = query.data.split(":")
    uid, price = query.from_user.id, float(price)
    user_tag = f"@{query.from_user.username}" if query.from_user.username else f"ID: {uid}"

    cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    if cur.fetchone()[0] < price:
        await query.answer("❌ Недостаточно звезд на балансе!", show_alert=True)
        return

    add_balance(uid, -price)
    await query.message.edit_text(f"✅ **Заявка оформлена!**\nИнформация о выдаче подарка {icon} появится в канале.")

    # Сообщение в канал выплат
    msg = (f"🔥 **НОВАЯ ЗАЯВКА НА ВЫВОД**\n\n"
           f"👤 **Пользователь:** {user_tag}\n"
           f"🎁 **Подарок:** {icon}\n"
           f"📅 **Дата:** {datetime.now().strftime('%d.%m.%Y | %H:%M')}\n\n"
           f"🌀 **Статус:** `Ожидает отправки` ⏳")
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ ОТПРАВИТЬ", callback_data=f"done:{uid}:{icon}")]])
    await context.bot.send_message(chat_id=PAYMENT_CHANNEL_ID, text=msg, reply_markup=kb, parse_mode="Markdown")

async def admin_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Ты не администратор!")
        return
    
    _, target_uid, icon = query.data.split(":")
    new_text = query.message.text.replace("🔥 НОВАЯ ЗАЯВКА НА ВЫВОД", "✅ ЗАЯВКА ВЫПОЛНЕНА")
    new_text = new_text.replace("Ожидает отправки ⏳", "ОТПРАВЛЕНО ✅")
    
    await query.message.edit_text(text=new_text + "\n\n*Администратор подтвердил выдачу.*", reply_markup=None, parse_mode="Markdown")
    try:
        await context.bot.send_message(int(target_uid), f"🎁 **Подарок {icon} успешно отправлен!**\nСпасибо, что приглашаете друзей!")
    except: pass

async def lottery_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT COUNT(*) FROM lottery")
    count = cur.fetchone()[0]
    price = float(get_setting("lottery_price"))
    text = (f"🎰 **ЗВЕЗДНАЯ ЛОТЕРЕЯ**\n\n"
            f"💰 Текущий банк: **{count*price:.2f}⭐**\n"
            f"👥 Участников: `{count}/10`\n\n"
            f"🎟 Стоимость участия: **{price}⭐**\n"
            f"🏆 Победитель забирает 80% банка!")
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎟 КУПИТЬ БИЛЕТ", callback_data="l_buy")]]), parse_mode="Markdown")

async def l_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    price = float(get_setting("lottery_price"))
    
    cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    if cur.fetchone()[0] < price:
        await query.answer("❌ На балансе нет звезд для покупки билета!", show_alert=True)
        return

    add_balance(uid, -price)
    cur.execute("INSERT INTO lottery VALUES (?)", (uid,))
    conn.commit()
    
    cur.execute("SELECT user_id FROM lottery")
    users = [r[0] for r in cur.fetchall()]
    if len(users) >= 10:
        winner = random.choice(users)
        win_sum = (len(users) * price) * 0.8
        admin_sum = (len(users) * price) * 0.2
        add_balance(winner, win_sum)
        add_balance(ADMIN_ID, admin_sum)
        cur.execute("DELETE FROM lottery")
        conn.commit()
        await context.bot.send_message(winner, f"🎉 **ПОЗДРАВЛЯЕМ!** Вы выиграли **{win_sum:.2f}⭐** в лотерее!")
        await context.bot.send_message(ADMIN_ID, f"💰 **Доход от лотереи (20%):** +{admin_sum:.2f}⭐")
        await query.message.edit_text(f"🎰 **Розыгрыш окончен!**\nПобедитель: {winner}\nБанк {win_sum:.2f}⭐ выплачен.")
    else:
        await query.answer("🎟 Билет куплен! Удачи!", show_alert=True)
        await lottery_menu(update, context)

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Text("👤 Профиль"), profile))
    app.add_handler(MessageHandler(filters.Text("🎰 Лотерея"), lottery_menu))
    app.add_handler(MessageHandler(filters.Text("💸 Вывод"), withdraw))
    
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy:"))
    app.add_handler(CallbackQueryHandler(admin_done, pattern="^done:"))
    app.add_handler(CallbackQueryHandler(l_buy, pattern="l_buy"))
    
    print("🚀 БОТ ЗАПУЩЕН!")
    app.run_polling()
