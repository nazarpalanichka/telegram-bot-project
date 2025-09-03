# -*- coding: utf-8 -*-
# handlers/notes.py

import logging
import datetime
import math
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
)
from telegram.error import BadRequest

import config
from .start import cancel_command
from .keyboards import get_employee_keyboard

logger = logging.getLogger(__name__)
gs_manager = None

# --- Допоміжні функції ---

# Заголовки для аркуша "Нотатки"
# ВАЖЛИВО: Переконайтесь, що перший рядок у вашому Google-аркуші "Нотатки"
# має точно такі ж назви колонок.
NOTES_HEADERS = ["ID Нотатки", "ID Менеджера", "Текст нотатки", "Час нагадування", "Дата створення", "Статус"]

def get_reminder_keyboard(note_id: str) -> InlineKeyboardMarkup:
    """Створює клавіатуру для вибору часу нагадування."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 год", callback_data=f"set_remind_{note_id}_1"),
            InlineKeyboardButton("2 год", callback_data=f"set_remind_{note_id}_2"),
            InlineKeyboardButton("4 год", callback_data=f"set_remind_{note_id}_4"),
        ],
        [
            InlineKeyboardButton("Завтра о 10:00", callback_data=f"set_remind_{note_id}_tomorrow"),
            InlineKeyboardButton("Без нагадування", callback_data=f"set_remind_{note_id}_none"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"select_note_{note_id}")]
    ])

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Надсилає нагадування менеджеру та оновлює статус в таблиці."""
    job = context.job
    chat_id = job.data['chat_id']
    note_text = job.data['text']
    note_id = job.data['note_id']
    
    reminder_message = f"🔔 **НАГАДУВАННЯ** 🔔\n\n{note_text}"
    
    await context.bot.send_message(
        chat_id=chat_id, 
        text=reminder_message, 
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Позначити як виконане", callback_data=f"manage_note_done_{note_id}")
        ]])
    )
    
    # Оновлюємо статус в таблиці, що нагадування відправлено
    if gs_manager:
        try:
            note_row = await gs_manager.get_row_by_id(config.SHEET_NAMES['notes'], note_id, id_column_name="ID Нотатки")
            if note_row:
                note_row['record']['Статус'] = 'Нагадування відправлено'
                await gs_manager.update_row(config.SHEET_NAMES['notes'], note_row['row_index'], note_row['record'], NOTES_HEADERS)
        except Exception as e:
            logger.error(f"Не вдалося оновити статус нотатки {note_id} після надсилання нагадування: {e}")

def remove_job_if_exists(note_id: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Видаляє заплановане завдання за його назвою."""
    current_jobs = context.job_queue.get_jobs_by_name(note_id)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True

# --- Основна логіка нотатника ---

async def notes_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показує головне меню нотатника."""
    keyboard = [
        [InlineKeyboardButton("➕ Створити нову", callback_data="notes_create")],
        [InlineKeyboardButton("📋 Мої активні нотатки", callback_data="notes_list_active_0")],
        [InlineKeyboardButton("✅ Архів виконаних", callback_data="notes_list_done_0")],
    ]
    
    message_text = "📝 *Менеджер завдань*\n\nОберіть дію:"
    
    # FIX: Handle both message and callback_query updates
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    elif update.message:
        await update.message.reply_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
    return config.NOTES_MENU

async def create_note_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Починає процес створення нової нотатки."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введіть текст нотатки/завдання:")
    return config.NOTES_CREATE_GET_TEXT

async def get_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує текст і зберігає його, пропонуючи встановити нагадування."""
    user_id = update.effective_user.id
    note_text = update.message.text

    note_data = {
        "ID Менеджера": user_id,
        "Текст нотатки": note_text,
        "Час нагадування": "Без нагадування",
        "Дата створення": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Статус": "Активно"
    }
    
    # Додаємо нотатку в таблицю і отримуємо її ID (номер рядка)
    new_row_index = await gs_manager.add_row(config.SHEET_NAMES['notes'], note_data, NOTES_HEADERS, get_row_index=True)
    
    if not new_row_index:
        await update.message.reply_text("❌ Помилка збереження нотатки. Спробуйте ще раз.")
        return ConversationHandler.END

    # Оновлюємо ID нотатки в самій таблиці
    note_data["ID Нотатки"] = new_row_index
    await gs_manager.update_row(config.SHEET_NAMES['notes'], new_row_index, note_data, NOTES_HEADERS)

    context.user_data['current_note_id'] = new_row_index
    context.user_data['current_note_text'] = note_text

    await update.message.reply_text(
        "✅ Нотатку створено. Коли вам нагадати?",
        reply_markup=get_reminder_keyboard(str(new_row_index))
    )
    return config.NOTES_CREATE_GET_TIME

async def set_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Встановлює нагадування для нотатки."""
    query = update.callback_query
    await query.answer()
    
    try:
        _, _, note_id_str, choice = query.data.split('_')
        note_id = int(note_id_str)
    except (ValueError, IndexError):
        await query.edit_message_text("Помилка: невірний формат даних.")
        return ConversationHandler.END

    note_info = await gs_manager.get_row_by_id(config.SHEET_NAMES['notes'], note_id, id_column_name="ID Нотатки")
    if not note_info:
        await query.edit_message_text("Помилка: нотатку не знайдено.")
        return ConversationHandler.END
    
    note_row = note_info['record']

    # Видаляємо старе нагадування, якщо воно було
    remove_job_if_exists(str(note_id), context)

    delta = None
    reminder_time = None
    time_text = "встановлено"

    if choice == '1': delta = datetime.timedelta(hours=1); time_text = "через 1 годину"
    elif choice == '2': delta = datetime.timedelta(hours=2); time_text = "через 2 години"
    elif choice == '4': delta = datetime.timedelta(hours=4); time_text = "через 4 години"
    elif choice == 'tomorrow':
        now = datetime.datetime.now()
        tomorrow_10_am = now.replace(hour=10, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
        delta = tomorrow_10_am - now
        time_text = "завтра о 10:00"
    
    if delta:
        reminder_time = datetime.datetime.now() + delta
        context.job_queue.run_once(
            send_reminder,
            delta,
            data={'chat_id': query.from_user.id, 'text': note_row['Текст нотатки'], 'note_id': note_id},
            name=str(note_id)
        )
        note_row['Час нагадування'] = reminder_time.strftime("%Y-%m-%d %H:%M:%S")
        note_row['Статус'] = 'Активно (з нагадуванням)'
        await query.edit_message_text(f"✅ Добре, я нагадаю вам {time_text}.")
    else: # 'none'
        note_row['Час нагадування'] = "Без нагадування"
        note_row['Статус'] = 'Активно'
        await query.edit_message_text("✅ Нагадування скасовано.")

    await gs_manager.update_row(config.SHEET_NAMES['notes'], note_info['row_index'], note_row, NOTES_HEADERS)
    
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="Повертаюся в головне меню.",
        reply_markup=get_employee_keyboard(query.from_user.id)
    )
    return ConversationHandler.END

async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE, force_status: str = None, force_page: int = None) -> int:
    """Відображає список активних або виконаних нотаток."""
    query = update.callback_query
    await query.answer()

    status = 'active'
    page = 0

    if force_status is not None and force_page is not None:
        status = force_status
        page = force_page
    else:
        try:
            _, _, status_from_data, page_str = query.data.split('_')
            status = status_from_data
            page = int(page_str)
        except (ValueError, IndexError):
            pass

    await query.message.edit_text("Завантажую список...")

    all_notes = await gs_manager.get_all_records(config.SHEET_NAMES['notes'], NOTES_HEADERS)
    if all_notes is None:
        await query.message.edit_text("Помилка: не вдалося завантажити нотатки. Перевірте наявність заголовків в таблиці.")
        return config.NOTES_MENU

    user_id = query.from_user.id
    
    if status == 'active':
        notes_to_show = [n for n in all_notes if str(n.get("ID Менеджера")) == str(user_id) and "Виконано" not in n.get("Статус", "")]
        title = "Активні нотатки"
    else: # done
        notes_to_show = [n for n in all_notes if str(n.get("ID Менеджера")) == str(user_id) and "Виконано" in n.get("Статус", "")]
        title = "Архів нотаток"
        
    if not notes_to_show:
        await query.message.edit_text(f"Список '{title}' порожній.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="notes_back_to_menu")]]))
        return config.NOTES_LIST

    # Пагінація
    notes_per_page = 5
    start_index = page * notes_per_page
    end_index = start_index + notes_per_page
    paginated_notes = notes_to_show[start_index:end_index]
    total_pages = math.ceil(len(notes_to_show) / notes_per_page)

    keyboard = []
    for note in paginated_notes:
        note_id = note.get("ID Нотатки")
        text = note.get("Текст нотатки", "Без тексту")
        keyboard.append([InlineKeyboardButton(text[:40], callback_data=f"select_note_{note_id}")])

    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"notes_list_{status}_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav_row.append(InlineKeyboardButton("➡️", callback_data=f"notes_list_{status}_{page + 1}"))
    if nav_row: keyboard.append(nav_row)
    
    keyboard.append([InlineKeyboardButton("⬅️ Назад в меню", callback_data="notes_back_to_menu")])

    await query.message.edit_text(f"<b>{title}:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return config.NOTES_LIST

async def select_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показує детальну інформацію та опції для обраної нотатки."""
    query = update.callback_query
    await query.answer()
    note_id = int(query.data.split('_')[-1])

    note_info = await gs_manager.get_row_by_id(config.SHEET_NAMES['notes'], note_id, id_column_name="ID Нотатки")
    if not note_info:
        await query.edit_message_text("Помилка: нотатку не знайдено.")
        return config.NOTES_LIST
    
    note = note_info['record']
    context.user_data['current_note_id'] = note_id
    
    text = (f"📝 <b>Нотатка:</b>\n{note.get('Текст нотатки', '')}\n\n"
            f"<b>Створено:</b> {note.get('Дата створення', 'Н/Д')}\n"
            f"<b>Нагадування:</b> {note.get('Час нагадування', 'Н/Д')}\n"
            f"<b>Статус:</b> {note.get('Статус', 'Н/Д')}")
            
    keyboard = [
        [InlineKeyboardButton("✅ Виконано", callback_data=f"manage_note_done_{note_id}")],
        [InlineKeyboardButton("⏰ Змінити час", callback_data=f"manage_note_reschedule_{note_id}")],
        [InlineKeyboardButton("🗑️ Видалити", callback_data=f"manage_note_delete_{note_id}")],
        [InlineKeyboardButton("⬅️ Назад до списку", callback_data="notes_list_active_0")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return config.NOTES_MANAGE

async def manage_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє дії з нотаткою: виконати, змінити час, видалити."""
    query = update.callback_query
    await query.answer()
    
    try:
        _, _, action, note_id_str = query.data.split('_')
        note_id = int(note_id_str)
    except (ValueError, IndexError):
        return config.NOTES_MANAGE

    note_info = await gs_manager.get_row_by_id(config.SHEET_NAMES['notes'], note_id, id_column_name="ID Нотатки")
    if not note_info:
        await query.edit_message_text("Помилка: нотатку вже видалено або не знайдено.")
        return await list_notes(update, context, force_status='active', force_page=0)
    
    note = note_info['record']

    if action == 'done':
        note['Статус'] = 'Виконано'
        note['Час нагадування'] = 'Неактуально'
        await gs_manager.update_row(config.SHEET_NAMES['notes'], note_info['row_index'], note, NOTES_HEADERS)
        remove_job_if_exists(str(note_id), context)
        return await list_notes(update, context, force_status='active', force_page=0)

    elif action == 'delete':
        await gs_manager.delete_row(config.SHEET_NAMES['notes'], note_info['row_index'])
        remove_job_if_exists(str(note_id), context)
        return await list_notes(update, context, force_status='active', force_page=0)

    elif action == 'reschedule':
        await query.edit_message_text("Оберіть новий час для нагадування:", reply_markup=get_reminder_keyboard(str(note_id)))
        return config.NOTES_RESCHEDULE

    return config.NOTES_MANAGE
    
async def notes_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Повертає користувача в головне меню нотатника."""
    # FIX: Call notes_start directly with the update object
    return await notes_start(update, context)

def get_notes_handler() -> ConversationHandler:
    """Створює обробник для розмови про створення та керування нотатками."""
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Нотатки$"), notes_start)],
        states={
            config.NOTES_MENU: [
                CallbackQueryHandler(create_note_start, pattern="^notes_create$"),
                CallbackQueryHandler(list_notes, pattern="^notes_list_"),
            ],
            config.NOTES_CREATE_GET_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_note_text)],
            config.NOTES_CREATE_GET_TIME: [CallbackQueryHandler(set_reminder, pattern="^set_remind_")],
            config.NOTES_LIST: [
                CallbackQueryHandler(list_notes, pattern="^notes_list_"),
                CallbackQueryHandler(select_note, pattern="^select_note_"),
                CallbackQueryHandler(notes_back_to_menu, pattern="^notes_back_to_menu$"),
            ],
            config.NOTES_MANAGE: [
                CallbackQueryHandler(manage_note, pattern="^manage_note_"),
                CallbackQueryHandler(list_notes, pattern="^notes_list_active_0$"),
            ],
            config.NOTES_RESCHEDULE: [
                CallbackQueryHandler(set_reminder, pattern="^set_remind_"),
                CallbackQueryHandler(select_note, pattern="^select_note_")
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )
