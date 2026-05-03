import os
import random
import sqlite3
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

ADMIN_ID = 6820965428
PAYMENT_CHANNEL_ID = "@yabloko_gifts_channel"
DB_PATH = "bot_database.db"

logging.basicConfig(level=logging.INFO)


conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()


def init_db():
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0,
        invited_by INTEGER,
        invited_by_l2 INTEGER,
        tasks_completed INTEGER DEFAULT 0,
        last_bonus TEXT,
        created_at TEXT
    )
    """)

    cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        link TEXT,
        reward REAL DEFAULT 0.35,
        active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS completed_tasks (
        user_id INTEGER,
        task_id INTEGER,
        PRIMARY KEY(user_id, task_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sponsors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        link TEXT,
        active INTEGER DEFAULT 1
    )
    """)

    cur.execute("CREATE TABLE IF NOT EXISTS lottery (user_id INTEGER)")

    defaults = [
        ("ref_l1", "3.5"),
        ("ref_l2", "0.5"),
        ("lottery_price", "1.0"),
        ("lottery_limit", "10"),
        ("daily_bonus", "0.10"),
    ]
    cur.executemany("INSERT OR IGNORE INTO settings VALUES (?, ?)", defaults)
    conn.commit()


def get_setting(key, default="0"):
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, str(default)))
    conn.commit()
    return str(default)


def set_setting(key, value):
    cur.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
    conn.commit()


def add_balance(user_id, amount):
    cur.execute("INSERT OR IGNORE INTO users (user_id, balance, created_at) VALUES (?, 0, ?)", (user_id, datetime.now().isoformat()))
    cur.execute("UPDATE users SET balance = MAX(balance + ?, 0) WHERE user_id = ?", (amount, user_id))
    conn.commit()


def get_balance(user_id):
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


def normalize_channel(link):
    link = (link or "").strip()
    if link.startswith("@"):
        return link
    if "t.me/" in link:
        parsed = urlparse(link if link.startswith("http") else "https://" + link)
        name = parsed.path.strip("/").split("/")[0]
        if name and not name.startswith("+"):
            return "@" + name
    return link


async def is_member(context, user_id, channel_link):
    try:
        chat_id = normalize_channel(channel_link)
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


def main_menu():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("👤 Профиль"), KeyboardButton("📋 Задания")],
            [KeyboardButton("🎰 Лотерея"), KeyboardButton("💸 Вывод")],
            [KeyboardButton("🎁 Бонус дня"), KeyboardButton("👥 Пригласить друзей")],
        ],
        resize_keyboard=True,
    )


def admin_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("➕ Добавить задание", callback_data="admin_add_task")],
            [InlineKeyboardButton("📋 Управление заданиями", callback_data="admin_tasks")],
            [InlineKeyboardButton("➕ Добавить спонсора", callback_data="admin_add_sponsor")],
            [InlineKeyboardButton("📋 Управление спонсорами", callback_data="admin_sponsors")],
            [InlineKeyboardButton("🎰 Настройки лотереи", callback_data="admin_lottery")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        ]
    )


def gifts_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("15⭐ 💝", callback_data="buy:💝 Сердечко:15"),
                InlineKeyboardButton("15⭐ 🧸", callback_data="buy:🧸 Мишка:15"),
            ],
            [
                InlineKeyboardButton("25⭐ 🌹", callback_data="buy:🌹 Розочка:25"),
                InlineKeyboardButton("25⭐ 🎁", callback_data="buy:🎁 Подарок:25"),
            ],
            [
                InlineKeyboardButton("50⭐", callback_data="buy::50"),
            ],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = update.effective_user.username
    args = context.args

    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    exists = cur.fetchone()

    if not exists:
        invited_by = int(args[0]) if args and args[0].isdigit() and int(args[0]) != uid else None
        invited_by_l2 = None

        if invited_by:
            cur.execute("SELECT invited_by FROM users WHERE user_id=?", (invited_by,))
            row = cur.fetchone()
            if row:
                invited_by_l2 = row[0]

        cur.execute(
            "INSERT INTO users (user_id, username, balance, invited_by, invited_by_l2, created_at) VALUES (?, ?, 0, ?, ?, ?)",
            (uid, username, invited_by, invited_by_l2, datetime.now().isoformat()),
        )

        if invited_by:
            try:
                await context.bot.send_message(
                    invited_by,
                    f"👥 У вас новый реферал!\nВы получите {get_setting('ref_l1')}⭐ после его активности.",
                )
            except Exception:
                pass

        conn.commit()
    else:
        cur.execute("UPDATE users SET username=? WHERE user_id=?", (username, uid))
        conn.commit()

    await update.message.reply_text(
        "🍎 Yabloko Gifts приветствует тебя!\n\n"
        "Зарабатывай звёзды, выполняй задания, участвуй в лотерее и выводи подарки.\n\n"
        "👇 Выбери действие:",
        reply_markup=main_menu(),
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    cur.execute("SELECT balance, tasks_completed FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()

    if not row:
        await update.message.reply_text("Нажмите /start")
        return

    balance, tasks_completed = row

    cur.execute("SELECT COUNT(*) FROM users WHERE invited_by=?", (uid,))
    referrals = cur.fetchone()[0]

    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={uid}"

    await update.message.reply_text(
        "👤 Ваш профиль\n\n"
        f"🆔 ID: {uid}\n"
        f"⭐ Баланс: {balance:.2f}⭐\n"
        f"📋 Выполнено заданий: {tasks_completed}\n"
        f"👥 Рефералов: {referrals}\n\n"
        f"🔗 Ваша ссылка:\n{ref_link}",
        reply_markup=main_menu(),
    )


async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={uid}"

    await update.message.reply_text(
        f"👥 Приглашайте друзей и получайте звёзды.\n\nВаша ссылка:\n{ref_link}",
        reply_markup=main_menu(),
    )


async def daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cur.execute("SELECT last_bonus FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    last_bonus = row[0] if row else None

    now = datetime.now()

    if last_bonus:
        last = datetime.fromisoformat(last_bonus)
        next_bonus = last + timedelta(hours=24)
        if now < next_bonus:
            left = next_bonus - now
            hours = left.seconds // 3600
            minutes = (left.seconds % 3600) // 60
            await update.message.reply_text(f"Вы уже получили бонус дня.\nСледующий бонус через: {hours}ч {minutes}м")
            return

    reward = float(get_setting("daily_bonus", "0.10"))
    add_balance(uid, reward)
    cur.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (now.isoformat(), uid))
    conn.commit()

    await update.message.reply_text(f"🎁 Бонус дня получен: +{reward}⭐")


async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_next_task(update.effective_user.id, context, update.message)


async def show_next_task(user_id, context, message):
    cur.execute("""
        SELECT id, title, link, reward FROM tasks
        WHERE active=1 AND id NOT IN (
            SELECT task_id FROM completed_tasks WHERE user_id=?
        )
        ORDER BY id ASC
        LIMIT 1
    """, (user_id,))
    row = cur.fetchone()

    if not row:
        await message.reply_text("❌ Заданий больше нет", reply_markup=main_menu())
        return

    task_id, title, link, reward = row

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 Перейти", url=link)],
            [
                InlineKeyboardButton("✅ Проверить", callback_data=f"task_check:{task_id}"),
                InlineKeyboardButton("⏭ Пропустить", callback_data=f"task_skip:{task_id}"),
            ],
        ]
    )

    await message.reply_text(
        f"📋 Задание\n\n{title}\n\n⭐ Награда: {reward}⭐",
        reply_markup=kb,
    )


async def task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    action, task_id = query.data.split(":")
    task_id = int(task_id)

    if action == "task_skip":
        try:
            await query.message.delete()
        except Exception:
            pass
        await show_next_task(uid, context, query.message)
        return

    cur.execute("SELECT title, link, reward FROM tasks WHERE id=? AND active=1", (task_id,))
    task = cur.fetchone()

    if not task:
        await query.message.reply_text("Задание не найдено.")
        return

    title, link, reward = task

    ok = await is_member(context, uid, link)
    if not ok:
        await query.answer("❌ Вы не подписались. Сначала подпишитесь.", show_alert=True)
        return

    cur.execute("SELECT 1 FROM completed_tasks WHERE user_id=? AND task_id=?", (uid, task_id))
    if cur.fetchone():
        await query.answer("Вы уже выполнили это задание.", show_alert=True)
        return

    cur.execute("INSERT INTO completed_tasks VALUES (?, ?)", (uid, task_id))
    cur.execute("UPDATE users SET balance = balance + ?, tasks_completed = tasks_completed + 1 WHERE user_id=?", (reward, uid))
    conn.commit()

    try:
        await query.message.delete()
    except Exception:
        pass

    await query.message.reply_text(f"✅ Задание выполнено!\n⭐ Начислено: +{reward}⭐")
    await show_next_task(uid, context, query.message)


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    balance = get_balance(uid)

    await update.message.reply_text(
        "🎁 Вывести Звёзды\n\n"
        f"Заработано: {balance:.2f}⭐\n\n"
        f"Канал с выводами: {PAYMENT_CHANNEL_ID}\n\n"
        "Выберите подарок для вывода:",
        reply_markup=gifts_keyboard(),
    )


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, gift, price = query.data.split(":")
    uid = query.from_user.id
    price = float(price)

    cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    balance = float(row[0]) if row else 0.0

    if balance < price:
        await query.answer("❌ Недостаточно звёзд на балансе!", show_alert=True)
        return

    add_balance(uid, -price)

    username = f"@{query.from_user.username}" if query.from_user.username else "без username"
    gift_line = f"🎁 Подарок: {gift}\n" if gift.strip() else ""

    msg = (
        "💸 Новая заявка на вывод\n\n"
        f"👤 Пользователь: {username}\n"
        f"🆔 ID: {uid}\n"
        f"⭐ Сумма: {price:.0f}⭐\n"
        f"{gift_line}"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y | %H:%M')}\n\n"
        "Статус: ⏳ Ожидает отправки"
    )

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ ОТПРАВИТЬ", callback_data=f"done:{uid}:{gift}:{int(price)}")]]
    )

    await context.bot.send_message(PAYMENT_CHANNEL_ID, msg, reply_markup=kb)
    await query.message.edit_text("✅ Заявка оформлена!\nОжидайте выдачу подарка.")


async def admin_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    parts = query.data.split(":")
    target_uid = int(parts[1])
    gift = parts[2]
    price = parts[3]

    new_text = query.message.text.replace("💸 Новая заявка на вывод", "✅ Заявка выполнена")
    new_text = new_text.replace("Статус: ⏳ Ожидает отправки", "Статус: ✅ Отправлено")

    await query.message.edit_text(new_text, reply_markup=None)

    try:
        await context.bot.send_message(target_uid, "✅ Ваша выплата отправлена!")
    except Exception:
        pass


async def lottery_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT COUNT(*) FROM lottery")
    count = cur.fetchone()[0]
    price = float(get_setting("lottery_price", "1"))
    limit = int(float(get_setting("lottery_limit", "10")))

    text = (
        "🎰 Звёздная лотерея\n\n"
        f"💰 Текущий банк: {count * price:.2f}⭐\n"
        f"👥 Участников: {count}/{limit}\n\n"
        f"🎟 Стоимость участия: {price}⭐\n"
        "🏆 Победитель забирает 80% банка!"
    )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎟 Купить билет", callback_data="l_buy")]]),
    )


async def l_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    price = float(get_setting("lottery_price", "1"))
    limit = int(float(get_setting("lottery_limit", "10")))

    if get_balance(uid) < price:
        await query.answer("❌ На балансе нет звёзд для билета!", show_alert=True)
        return

    add_balance(uid, -price)
    cur.execute("INSERT INTO lottery VALUES (?)", (uid,))
    conn.commit()

    cur.execute("SELECT user_id FROM lottery")
    users = [r[0] for r in cur.fetchall()]

    if len(users) >= limit:
        winner = random.choice(users)
        win_sum = (len(users) * price) * 0.8
        admin_sum = (len(users) * price) * 0.2

        add_balance(winner, win_sum)
        add_balance(ADMIN_ID, admin_sum)

        cur.execute("DELETE FROM lottery")
        conn.commit()

        await context.bot.send_message(winner, f"🎉 Вы выиграли {win_sum:.2f}⭐ в лотерее!")
        await context.bot.send_message(ADMIN_ID, f"💰 Доход от лотереи: +{admin_sum:.2f}⭐")
        await query.message.edit_text("🎰 Розыгрыш окончен! Победитель получил банк.")
    else:
        await query.answer("🎟 Билет куплен!", show_alert=True)


# ===== АДМИНКА =====
ADD_TASK_TITLE, ADD_TASK_LINK, ADD_TASK_REWARD = range(1, 4)
ADD_SPONSOR_TITLE, ADD_SPONSOR_LINK = range(4, 6)
SET_LOTTERY_PRICE, SET_LOTTERY_LIMIT = range(6, 8)
BROADCAST_PREVIEW = 8


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа")
        return
    await update.message.reply_text("Админка:", reply_markup=admin_menu())


async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return ConversationHandler.END

    data = query.data

    if data == "admin_stats":
        cur.execute("SELECT COUNT(*) FROM users")
        users_count = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
        total_balance = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tasks")
        tasks_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sponsors")
        sponsors_count = cur.fetchone()[0]

        await query.message.reply_text(
            "📊 Статистика\n\n"
            f"👥 Пользователей: {users_count}\n"
            f"⭐ Баланс всего: {total_balance:.2f}⭐\n"
            f"📋 Заданий: {tasks_count}\n"
            f"📢 Спонсоров: {sponsors_count}"
        )
        return ConversationHandler.END

    if data == "admin_add_task":
        await query.message.reply_text("Введите название задания:")
        return ADD_TASK_TITLE

    if data == "admin_tasks":
        cur.execute("SELECT id, title, reward, active FROM tasks ORDER BY id DESC")
        rows = cur.fetchall()
        if not rows:
            await query.message.reply_text("Заданий нет.")
            return ConversationHandler.END

        buttons = []
        for tid, title, reward, active in rows:
            status = "🟢" if active else "🔴"
            buttons.append([InlineKeyboardButton(f"{status} #{tid} {title} — {reward}⭐", callback_data=f"taskm:{tid}")])
        await query.message.reply_text("📋 Управление заданиями:", reply_markup=InlineKeyboardMarkup(buttons))
        return ConversationHandler.END

    if data == "admin_add_sponsor":
        await query.message.reply_text("Введите название спонсора:")
        return ADD_SPONSOR_TITLE

    if data == "admin_sponsors":
        cur.execute("SELECT id, title, link, active FROM sponsors ORDER BY id DESC")
        rows = cur.fetchall()
        if not rows:
            await query.message.reply_text("Спонсоров нет.")
            return ConversationHandler.END

        buttons = []
        for sid, title, link, active in rows:
            status = "🟢" if active else "🔴"
            buttons.append([InlineKeyboardButton(f"{status} #{sid} {title}", callback_data=f"sponsorm:{sid}")])
        await query.message.reply_text("📋 Управление спонсорами:", reply_markup=InlineKeyboardMarkup(buttons))
        return ConversationHandler.END

    if data == "admin_lottery":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💰 Цена билета", callback_data="set_lottery_price")],
                [InlineKeyboardButton("👥 Лимит участников", callback_data="set_lottery_limit")],
            ]
        )
        await query.message.reply_text("🎰 Настройки лотереи:", reply_markup=kb)
        return ConversationHandler.END

    if data == "set_lottery_price":
        await query.message.reply_text("Введите новую цену билета:")
        return SET_LOTTERY_PRICE

    if data == "set_lottery_limit":
        await query.message.reply_text("Введите лимит участников:")
        return SET_LOTTERY_LIMIT

    if data == "admin_broadcast":
        await query.message.reply_text("📢 Отправьте сообщение для рассылки. Можно текст или фото.")
        return BROADCAST_PREVIEW

    return ConversationHandler.END


async def task_manage_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    tid = int(query.data.split(":")[1])
    cur.execute("SELECT id, title, link, reward, active FROM tasks WHERE id=?", (tid,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("Задание не найдено.")
        return

    tid, title, link, reward, active = row
    toggle = "Выключить" if active else "Включить"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"🔁 {toggle}", callback_data=f"tasktoggle:{tid}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"taskdelete:{tid}")],
        ]
    )

    await query.message.reply_text(
        f"📋 Задание #{tid}\n\nНазвание: {title}\nСсылка: {link}\nЦена: {reward}⭐\nСтатус: {'вкл' if active else 'выкл'}",
        reply_markup=kb,
    )


async def task_action_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    action, tid = query.data.split(":")
    tid = int(tid)

    if action == "taskdelete":
        cur.execute("DELETE FROM tasks WHERE id=?", (tid,))
        cur.execute("DELETE FROM completed_tasks WHERE task_id=?", (tid,))
        conn.commit()
        await query.message.edit_text("🗑 Задание удалено.")
        return

    if action == "tasktoggle":
        cur.execute("SELECT active FROM tasks WHERE id=?", (tid,))
        row = cur.fetchone()
        if row:
            new_active = 0 if row[0] else 1
            cur.execute("UPDATE tasks SET active=? WHERE id=?", (new_active, tid))
            conn.commit()
            await query.message.edit_text("✅ Статус задания изменён.")


async def sponsor_manage_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    sid = int(query.data.split(":")[1])
    cur.execute("SELECT id, title, link, active FROM sponsors WHERE id=?", (sid,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("Спонсор не найден.")
        return

    sid, title, link, active = row
    toggle = "Выключить" if active else "Включить"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"🔁 {toggle}", callback_data=f"sponsortoggle:{sid}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"sponsordelete:{sid}")],
        ]
    )

    await query.message.reply_text(
        f"📢 Спонсор #{sid}\n\nНазвание: {title}\nСсылка: {link}\nСтатус: {'вкл' if active else 'выкл'}",
        reply_markup=kb,
    )


async def sponsor_action_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    action, sid = query.data.split(":")
    sid = int(sid)

    if action == "sponsordelete":
        cur.execute("DELETE FROM sponsors WHERE id=?", (sid,))
        conn.commit()
        await query.message.edit_text("🗑 Спонсор удалён.")
        return

    if action == "sponsortoggle":
        cur.execute("SELECT active FROM sponsors WHERE id=?", (sid,))
        row = cur.fetchone()
        if row:
            new_active = 0 if row[0] else 1
            cur.execute("UPDATE sponsors SET active=? WHERE id=?", (new_active, sid))
            conn.commit()
            await query.message.edit_text("✅ Статус спонсора изменён.")


async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_title"] = update.message.text
    await update.message.reply_text("Введите ссылку задания:")
    return ADD_TASK_LINK


async def add_task_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["task_link"] = update.message.text
    await update.message.reply_text("Введите награду за задание:")
    return ADD_TASK_REWARD


async def add_task_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reward = float(update.message.text.replace(",", "."))
    except Exception:
        await update.message.reply_text("Введите число.")
        return ADD_TASK_REWARD

    cur.execute(
        "INSERT INTO tasks (title, link, reward, active) VALUES (?, ?, ?, 1)",
        (context.user_data["task_title"], context.user_data["task_link"], reward),
    )
    conn.commit()
    await update.message.reply_text("✅ Задание добавлено.")
    return ConversationHandler.END


async def add_sponsor_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["sponsor_title"] = update.message.text
    await update.message.reply_text("Введите ссылку спонсора:")
    return ADD_SPONSOR_LINK


async def add_sponsor_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute(
        "INSERT INTO sponsors (title, link, active) VALUES (?, ?, 1)",
        (context.user_data["sponsor_title"], update.message.text),
    )
    conn.commit()
    await update.message.reply_text("✅ Спонсор добавлен.")
    return ConversationHandler.END


async def set_lottery_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("lottery_price", update.message.text.replace(",", "."))
    await update.message.reply_text("✅ Цена билета обновлена.")
    return ConversationHandler.END


async def set_lottery_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("lottery_limit", update.message.text)
    await update.message.reply_text("✅ Лимит обновлён.")
    return ConversationHandler.END


async def broadcast_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    context.user_data["broadcast_chat_id"] = update.effective_chat.id
    context.user_data["broadcast_message_id"] = update.effective_message.message_id

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Отправить всем", callback_data="broadcast_confirm"),
          InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel")]]
    )

    await update.message.reply_text("👀 Предпросмотр:")
    await context.bot.copy_message(update.effective_chat.id, update.effective_chat.id, update.effective_message.message_id)
    await update.message.reply_text("Отправить всем пользователям?", reply_markup=kb)
    return ConversationHandler.END


async def broadcast_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    if query.data == "broadcast_cancel":
        await query.message.edit_text("❌ Рассылка отменена.")
        return

    chat_id = context.user_data.get("broadcast_chat_id")
    message_id = context.user_data.get("broadcast_message_id")

    if not chat_id or not message_id:
        await query.message.edit_text("❌ Сообщение не найдено.")
        return

    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()

    sent = 0
    failed = 0

    for (user_id,) in users:
        try:
            await context.bot.copy_message(user_id, chat_id, message_id)
            sent += 1
        except Exception:
            failed += 1

    await query.message.edit_text(f"✅ Рассылка завершена\n\nОтправлено: {sent}\nОшибок: {failed}")


async def user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if "Профиль" in text:
        await profile(update, context)
    elif "Задания" in text:
        await tasks_menu(update, context)
    elif "Лотерея" in text:
        await lottery_menu(update, context)
    elif "Вывод" in text:
        await withdraw(update, context)
    elif "Бонус" in text:
        await daily_bonus(update, context)
    elif "Пригласить" in text:
        await referral(update, context)
    else:
        await update.message.reply_text("Выберите действие из меню.", reply_markup=main_menu())


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан")

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_cb, pattern="^admin_")],
        states={
            ADD_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_title)],
            ADD_TASK_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_link)],
            ADD_TASK_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_reward)],
            ADD_SPONSOR_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sponsor_title)],
            ADD_SPONSOR_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_sponsor_link)],
            SET_LOTTERY_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_lottery_price)],
            SET_LOTTERY_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_lottery_limit)],
            BROADCAST_PREVIEW: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_preview)],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))

    app.add_handler(admin_conv)

    app.add_handler(CallbackQueryHandler(task_callback, pattern="^task_"))
    app.add_handler(CallbackQueryHandler(task_manage_cb, pattern="^taskm:"))
    app.add_handler(CallbackQueryHandler(task_action_cb, pattern="^task(toggle|delete):"))
    app.add_handler(CallbackQueryHandler(sponsor_manage_cb, pattern="^sponsorm:"))
    app.add_handler(CallbackQueryHandler(sponsor_action_cb, pattern="^sponsor(toggle|delete):"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy:"))
    app.add_handler(CallbackQueryHandler(admin_done, pattern="^done:"))
    app.add_handler(CallbackQueryHandler(l_buy, pattern="^l_buy$"))
    app.add_handler(CallbackQueryHandler(broadcast_cb, pattern="^broadcast_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_text))

    print("🚀 БОТ ЗАПУЩЕН!")
    app.run_polling()


if __name__ == "__main__":
    main()
