import asyncio
import os
import json
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackContext
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

# === НАСТРОЙКИ ===
TOKEN = os.getenv("TOKEN")
SPREADSHEET_NAME = "WorkHours"
WARSAW = pytz.timezone('Europe/Warsaw')

# === Google Sheets ===
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_json = os.getenv("GOOGLE_CREDS_JSON")
creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
client = gspread.authorize(creds)
sheet = client.open(SPREADSHEET_NAME).sheet1

# === Этапы регистрации ===
NAME, LASTNAME, UNIQUE_ID, HOURS = range(4)

# === Клавиатура ===
main_menu_keyboard = ReplyKeyboardMarkup([
    ['📝 Регистрация', '🕒 Ввести часы'],
    ['📅 Календарь', '❌ Отмена']
], resize_keyboard=True)

# === Сообщения ===
REGISTRATION_DONE_MSG = "✅ Регистрация завершена!"
INVALID_USER_MSG = "⚠️ Вы не зарегистрированы. Пожалуйста, сначала пройдите регистрацию."
CANCEL_MSG = "❌ Действие отменено."

# === Команды ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Добро пожаловать! Выберите действие:",
        reply_markup=main_menu_keyboard
    )

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введите ваше имя:")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['first_name'] = update.message.text
    await update.message.reply_text("Введите вашу фамилию:")
    return LASTNAME

async def get_lastname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['last_name'] = update.message.text
    await update.message.reply_text("Введите ваш уникальный номер (например, KW43):")
    return UNIQUE_ID

async def get_unique_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    first_name = context.user_data['first_name']
    last_name = context.user_data['last_name']
    unique_id = update.message.text
    today = datetime.now(WARSAW).strftime("%d.%m.%Y")

    values = [str(user_id), first_name, last_name, unique_id, today]
    sheet.append_row(values)

    await update.message.reply_text(REGISTRATION_DONE_MSG, reply_markup=main_menu_keyboard)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(CANCEL_MSG, reply_markup=main_menu_keyboard)
    return ConversationHandler.END

async def enter_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите количество отработанных часов за сегодня:")
    return HOURS

async def save_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    hours = update.message.text

    try:
        hours = float(hours)
        if hours < 0 or hours > 24:
            await update.message.reply_text("⚠️ Введите корректное количество часов (от 0 до 24).", reply_markup=main_menu_keyboard)
            return HOURS
    except ValueError:
        await update.message.reply_text("⚠️ Введите число.", reply_markup=main_menu_keyboard)
        return HOURS

    today_col = datetime.now(WARSAW).day + 5
    cell = sheet.find(user_id)
    
    if cell:
        sheet.update_cell(cell.row, today_col, hours)

        # Обновляем сумму часов за месяц
        total_hours = sum(
            float(sheet.cell(cell.row, col).value or 0)
            for col in range(6, today_col + 1)
        )

        sheet.update_cell(cell.row, 4, total_hours)  # Обновляем столбец с итоговыми часами
        await update.message.reply_text(f"✅ Часы успешно записаны! Сумма за месяц: {total_hours} ч.", reply_markup=main_menu_keyboard)
    else:
        await update.message.reply_text(INVALID_USER_MSG, reply_markup=main_menu_keyboard)

    return ConversationHandler.END

async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    cell = sheet.find(user_id)
    
    if not cell:
        await update.message.reply_text(INVALID_USER_MSG, reply_markup=main_menu_keyboard)
        return

    row = sheet.row_values(cell.row)
    calendar = "📅 Ваши часы за месяц:\n"
    total_hours = 0

    for day in range(1, 32):
        col = day + 5
        try:
            value = row[col - 1]
            calendar += f"{day}: {value if value else '-'}\n"
            total_hours += float(value or 0)
        except IndexError:
            calendar += f"{day}: -\n"

    calendar += f"\n**Сумма за месяц: {total_hours} ч.**"
    await update.message.reply_text(calendar, parse_mode='Markdown', reply_markup=main_menu_keyboard)

async def send_reminders(context: CallbackContext):
    all_records = sheet.get_all_records()
    for record in all_records:
        try:
            user_id = int(record['user_id'])
            await context.bot.send_message(user_id, "⏰ Напоминание: введите количество отработанных часов!")
        except Exception as e:
            print(f"Ошибка при отправке напоминания {user_id}: {e}")

async def export_month(context: CallbackContext):
    now = datetime.now(WARSAW)
    if now.day == 31:
        workbook = client.open(SPREADSHEET_NAME)
        old_sheet = workbook.sheet1
        new_sheet = workbook.add_worksheet(title=f"Отчёт {now.strftime('%B')}", rows="200", cols="50")
        data = old_sheet.get_all_values()
        new_sheet.update('A1', data)

# === НАСТРОЙКА ПРИЛОЖЕНИЯ ===
app = ApplicationBuilder().token(TOKEN).build()

# === ОБРАБОТЧИКИ ===
conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler('register', register),
        MessageHandler(filters.Regex('^📝 Регистрация$'), register)
    ],
    states={
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        LASTNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_lastname)],
        UNIQUE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_unique_id)],
    },
    fallbacks=[
        CommandHandler('cancel', cancel),
        MessageHandler(filters.Regex('^❌ Отмена$'), cancel)
    ]
)

hours_handler = ConversationHandler(
    entry_points=[
        CommandHandler('hours', enter_hours),
        MessageHandler(filters.Regex('^🕒 Ввести часы$'), enter_hours)
    ],
    states={
        HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_hours)]
    },
    fallbacks=[
        CommandHandler('cancel', cancel),
        MessageHandler(filters.Regex('^❌ Отмена$'), cancel)
    ]
)

app.add_handler(CommandHandler("start", start))
app.add_handler(conv_handler)
app.add_handler(hours_handler)
app.add_handler(CommandHandler("calendar", show_calendar))
app.add_handler(MessageHandler(filters.Regex('^📅 Календарь$'), show_calendar))

# === ПЛАНИРОВЩИК ЗАДАЧ ===
scheduler = AsyncIOScheduler()
scheduler.add_job(send_reminders, trigger='cron', hour=23, minute=0, timezone=WARSAW)
scheduler.add_job(export_month, trigger='cron', day=31, hour=23, minute=5, timezone=WARSAW)

# === ОСНОВНАЯ ФУНКЦИЯ ===
async def main():
    scheduler.start()
    await app.run_polling()

# === ЗАПУСК ===
if __name__ == "__main__":
    asyncio.run(main())
