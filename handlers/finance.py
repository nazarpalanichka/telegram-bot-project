# -*- coding: utf-8 -*-
# handlers/finance.py

import logging
import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters, CommandHandler
)
from telegram.error import TelegramError, BadRequest

import config
from .start import cancel_command
from .keyboards import get_employee_keyboard

logger = logging.getLogger(__name__)
gs_manager = None

# Оновлений список заголовків для аркуша "Оплати"
PAYMENT_SHEET_HEADERS = ["Назва авто", "ВІН-код", "Клієнт", "Джерело", "Загальна вартість", "Сплачено", "Залишок", "Статус", "Трекер", "ID Менеджера", "Історія оплат", "Дата створення", "ID повідомлення в каналі"]

# --- Допоміжні функції для сповіщень ---

def build_finance_notification_text(deal_record: dict, manager_name: str, action_text: str = "Створено нову угоду") -> str:
    """Формує стандартизований текст сповіщення для фінансового каналу."""
    total_paid = float(deal_record.get('Сплачено', 0))
    total_price = float(deal_record.get('Загальна вартість', 0))
    remainder = total_price - total_paid

    return (f"💼 *{action_text}*\n\n"
            f"*{deal_record.get('Назва авто', 'Авто')}*\n"
            f"*VIN:* `{deal_record['ВІН-код']}`\n"
            f"*Клієнт:* {deal_record['Клієнт']}\n"
            f"*Джерело:* {deal_record['Джерело']}\n"
            f"*Сума:* ${total_price:,.2f}\n"
            f"*Сплачено:* ${total_paid:,.2f}\n"
            f"*Залишок:* `${remainder:,.2f}`\n"
            f"*Менеджер:* {manager_name}")

async def send_or_edit_finance_notification(context: ContextTypes.DEFAULT_TYPE, deal_record: dict, manager_name: str, action_text: str) -> int | None:
    """Надсилає нове або редагує існуюче сповіщення в фінансовому каналі."""
    if config.FINANCE_CHANNEL_ID == 0:
        return None

    notification_text = build_finance_notification_text(deal_record, manager_name, action_text)
    message_id = deal_record.get("ID повідомлення в каналі")

    try:
        if message_id:
            await context.bot.edit_message_text(
                chat_id=config.FINANCE_CHANNEL_ID,
                message_id=int(message_id),
                text=notification_text,
                parse_mode='Markdown'
            )
            return int(message_id)
        else:
            message = await context.bot.send_message(
                chat_id=config.FINANCE_CHANNEL_ID,
                text=notification_text,
                parse_mode='Markdown'
            )
            return message.message_id
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.warning("Finance notification not modified.")
            return int(message_id) if message_id else None
        else:
            logger.error(f"Не вдалося відредагувати сповіщення {message_id}: {e}. Спроба надіслати нове.")
            try:
                message = await context.bot.send_message(
                    chat_id=config.FINANCE_CHANNEL_ID,
                    text=notification_text,
                    parse_mode='Markdown'
                )
                return message.message_id
            except TelegramError as e_new:
                logger.error(f"Не вдалося надіслати нове сповіщення: {e_new}")
                return None
    except TelegramError as e:
        logger.error(f"Не вдалося надіслати/відредагувати сповіщення: {e}")
        return None

# --- Головне меню фінансів ---
async def finance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("➕ Нова угода", callback_data="finance_action_new")],
        [InlineKeyboardButton("💵 Додати оплату", callback_data="finance_action_add")],
        [InlineKeyboardButton("🚚 Додати/Змінити трекер", callback_data="finance_action_tracker")],
        [InlineKeyboardButton("📊 Переглянути угоду", callback_data="finance_action_view")],
        [InlineKeyboardButton("📋 Мої угоди", callback_data="finance_my_deals")],
        [InlineKeyboardButton("⬅️ Назад в кабінет", callback_data="back_to_main_cabinet")]
    ]
    await query.message.edit_text("💸 *Фінанси та Оплати*\n\nОберіть дію:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return config.FINANCE_MENU

# --- Універсальні функції ---
async def ask_for_vin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    action = query.data.split('_')[-1]
    context.user_data['finance_action'] = action
    
    action_text_map = {
        'new': 'створити нову',
        'add': 'додати оплату до',
        'view': 'переглянути',
        'tracker': 'додати трекер до'
    }
    
    await query.message.edit_text(f"Введіть повний або останні 4+ цифри ВІН-коду, щоб {action_text_map.get(action, '')} угоду:")
    return config.FINANCE_GET_VIN

async def find_deal_by_vin_query(vin_query: str) -> dict | None:
    """Знаходить угоду за повним або частковим VIN-кодом."""
    vin_query = vin_query.strip().upper()
    if not vin_query:
        return None
        
    payments_sheet = config.SHEET_NAMES['payments']
    all_deals = await gs_manager.get_all_records(payments_sheet, expected_headers=PAYMENT_SHEET_HEADERS)
    
    # Пошук по повному VIN
    for i, deal in enumerate(all_deals):
        deal_vin = str(deal.get("ВІН-код", "")).strip()
        if deal_vin == vin_query:
            return {"record": deal, "row_index": i + 2}
    
    # Пошук по частині VIN
    if len(vin_query) >= 4:
        for i, deal in enumerate(all_deals):
            deal_vin = str(deal.get("ВІН-код", "")).strip()
            if deal_vin and deal_vin.endswith(vin_query):
                return {"record": deal, "row_index": i + 2}
                
    return None

async def get_vin_and_proceed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    vin_query = update.message.text.strip().upper()
    action = context.user_data.get('finance_action')
    
    context.user_data['messages_to_delete'] = [update.message.message_id]
    
    existing_deal = await find_deal_by_vin_query(vin_query)
    context.user_data['existing_deal'] = existing_deal
    
    if action == 'new':
        if existing_deal:
            await update.message.reply_text(f"❌ Угода для VIN, що закінчується на `{vin_query}`, вже існує.", parse_mode='Markdown')
            return ConversationHandler.END
        
        if len(vin_query) != 17:
            await update.message.reply_text("❌ Для створення нової угоди потрібен повний ВІН-код (17 символів).")
            return config.FINANCE_GET_VIN
        context.user_data['vin'] = vin_query
        return await new_deal_ask_model(update, context)
        
    elif action in ['add', 'view', 'tracker']:
        if not existing_deal:
            await update.message.reply_text(f"❌ Угоду для VIN `{vin_query}` не знайдено.", parse_mode='Markdown')
            return ConversationHandler.END
        if action == 'add':
            return await add_payment_ask_amount(update, context)
        elif action == 'view':
            return await view_deal_details(update, context)
        else: # tracker
            return await add_tracker_ask_value(update, context)
    
    await update.message.reply_text("Невідома дія. Повертаюся в меню.")
    return ConversationHandler.END

# --- Логіка створення нової угоди ---
async def new_deal_ask_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введіть, будь ласка, повну назву авто (напр. BYD Yuan Plus Subtop 2024):")
    return config.FINANCE_NEW_DEAL_GET_MODEL

async def new_deal_get_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['model_name'] = update.message.text.strip()
    keyboard = [[InlineKeyboardButton("США 🇺🇸", callback_data="source_США")], [InlineKeyboardButton("Китай 🇨🇳", callback_data="source_Китай")]]
    await update.message.reply_text("Дякую. Тепер вкажіть, звідки авто?", reply_markup=InlineKeyboardMarkup(keyboard))
    return config.FINANCE_NEW_DEAL_SOURCE

async def new_deal_get_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['source'] = query.data.split('_')[1]
    await query.message.edit_text("Введіть повну вартість авто 'під ключ' (USD, тільки число):")
    return config.FINANCE_NEW_DEAL_PRICE

async def new_deal_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = float(update.message.text.strip())
        context.user_data['total_price'] = price
        await update.message.reply_text("Введіть ім'я та контактні дані клієнта:")
        return config.FINANCE_NEW_DEAL_CLIENT
    except ValueError:
        await update.message.reply_text("❌ Неправильний формат. Введіть число, наприклад: 25000")
        return config.FINANCE_NEW_DEAL_PRICE

async def new_deal_get_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['client'] = update.message.text.strip()
    ud = context.user_data
    text = (f"✅ *Перевірте дані нової угоди*\n\n"
            f"Авто: *{ud['model_name']}*\n"
            f"VIN: `{ud['vin']}`\n"
            f"Джерело: *{ud['source']}*\n"
            f"Клієнт: *{ud['client']}*\n"
            f"Загальна вартість: *${ud['total_price']:,.2f}*\n\n"
            f"Все вірно? Створюємо угоду?")
    
    keyboard = [[InlineKeyboardButton("✅ Так, створити", callback_data="confirm_new_deal")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return config.FINANCE_NEW_DEAL_CONFIRM

async def new_deal_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = query.from_user
    await query.answer()
    await query.message.edit_text("Зберігаю нову угоду...")

    ud = context.user_data
    new_deal_data = {
        "Назва авто": ud.get('model_name', 'Н/Д'),
        "ВІН-код": ud['vin'],
        "Клієнт": ud['client'],
        "Джерело": ud['source'],
        "Загальна вартість": ud['total_price'],
        "Сплачено": 0,
        "Залишок": ud['total_price'],
        "Статус": "В процесі",
        "Трекер": "",
        "ID Менеджера": user.id,
        "Історія оплат": "",
        "Дата створення": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ID повідомлення в каналі": ""
    }
    
    new_msg_id = await send_or_edit_finance_notification(context, new_deal_data, user.full_name, "Створено нову угоду")
    if new_msg_id:
        new_deal_data["ID повідомлення в каналі"] = new_msg_id

    success = await gs_manager.add_row(config.SHEET_NAMES['payments'], new_deal_data, PAYMENT_SHEET_HEADERS)
    
    if success:
        await query.message.edit_text("✅ Угоду успішно створено!")
        await context.bot.send_message(chat_id=user.id, text="Повертаюся в головне меню.", reply_markup=get_employee_keyboard(user.id))
    else:
        await query.message.edit_text("❌ Помилка збереження. Спробуйте ще раз.")
        await context.bot.send_message(chat_id=user.id, text="Повертаюся в головне меню.", reply_markup=get_employee_keyboard(user.id))
        
    return ConversationHandler.END

# --- Логіка додавання оплати ---
async def add_payment_ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    deal = context.user_data['existing_deal']['record']
    text = (f"💵 *Додавання оплати для* `{deal.get('Назва авто', deal['ВІН-код'])}`\n"
            f"Клієнт: {deal['Клієнт']}\n"
            f"Залишок: *${float(deal['Залишок']):,.2f}*\n\n"
            f"Введіть суму нового платежу (USD):")
    msg = await update.message.reply_text(text, parse_mode='Markdown')
    context.user_data['messages_to_delete'].append(msg.message_id)
    return config.FINANCE_ADD_PAYMENT_AMOUNT

async def add_payment_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        context.user_data['payment_amount'] = amount
        context.user_data['messages_to_delete'].append(update.message.message_id)
        msg = await update.message.reply_text("Введіть коментар до платежу (напр. '10% аванс', 'Оплата доставки'):")
        context.user_data['messages_to_delete'].append(msg.message_id)
        return config.FINANCE_ADD_PAYMENT_COMMENT
    except ValueError:
        await update.message.reply_text("❌ Неправильний формат. Введіть число, наприклад: 1500.50")
        return config.FINANCE_ADD_PAYMENT_AMOUNT

async def add_payment_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    comment = update.message.text.strip()
    amount = context.user_data['payment_amount']
    deal_info = context.user_data['existing_deal']
    deal_record = deal_info['record']
    user = update.effective_user
    
    context.user_data['messages_to_delete'].append(update.message.message_id)

    for msg_id in context.user_data.get('messages_to_delete', []):
        try:
            await context.bot.delete_message(chat_id=user.id, message_id=msg_id)
        except TelegramError as e:
            logger.warning(f"Не вдалося видалити повідомлення {msg_id}: {e}")

    total_paid = float(deal_record.get('Сплачено', 0)) + amount
    total_price = float(deal_record.get('Загальна вартість', 0))
    remainder = total_price - total_paid

    payment_entry = f"({datetime.datetime.now().strftime('%Y-%m-%d')}: ${amount:,.2f} - {comment})"
    new_history = f"{deal_record.get('Історія оплат', '')}; {payment_entry}".strip('; ')

    deal_record['Сплачено'] = total_paid
    deal_record['Залишок'] = remainder
    deal_record['Історія оплат'] = new_history
    if remainder <= 0:
        deal_record['Статус'] = 'Оплачено'

    new_msg_id = await send_or_edit_finance_notification(context, deal_record, user.full_name, "Зафіксовано оплату")
    if new_msg_id:
        deal_record["ID повідомлення в каналі"] = new_msg_id

    success = await gs_manager.update_row(config.SHEET_NAMES['payments'], deal_info['row_index'], deal_record, PAYMENT_SHEET_HEADERS)

    if success:
        await context.bot.send_message(chat_id=user.id, text=f"✅ Оплату успішно додано! Новий залишок: ${remainder:,.2f}", reply_markup=get_employee_keyboard(user.id))
    else:
        await context.bot.send_message(chat_id=user.id, text="❌ Помилка оновлення. Спробуйте ще раз.", reply_markup=get_employee_keyboard(user.id))
        
    return ConversationHandler.END

# --- Логіка перегляду угод ---
async def view_deal_details(update: Update, context: ContextTypes.DEFAULT_TYPE, from_list: bool = False) -> int:
    query = update.callback_query
    if from_list:
        vin = query.data.split('_')[-1]
        deal_info = await find_deal_by_vin_query(vin)
        try:
            await query.message.edit_text("Завантажую деталі...")
        except TelegramError:
            await query.message.delete()
    else:
        deal_info = context.user_data['existing_deal']

    if not deal_info:
        await update.effective_message.reply_text("Помилка: не вдалося знайти угоду.")
        return ConversationHandler.END

    context.user_data['current_deal_info'] = deal_info
    deal = deal_info['record']
    history_text = "\n".join([f"  • {item.strip()}" for item in deal.get('Історія оплат', 'Історія порожня').split(';') if item])
    
    text = (f"📊 *Стан угоди для* `{deal.get('Назва авто', deal['ВІН-код'])}`\n\n"
            f"👤 *Клієнт:* {deal['Клієнт']}\n"
            f"🌍 *Джерело:* {deal['Джерело']}\n"
            f"💲 *Загальна вартість:* ${float(deal.get('Загальна вартість', 0)):,.2f}\n"
            f"✅ *Сплачено:* ${float(deal.get('Сплачено', 0)):,.2f}\n"
            f"⏳ *Залишок:* `${float(deal.get('Залишок', 0)):,.2f}`\n"
            f"📈 *Статус:* {deal['Статус']}\n"
            f"🚚 *Трекер:* `{deal.get('Трекер') or 'Не додано'}`\n\n"
            f"📜 *Історія оплат:*\n{history_text}")
    
    keyboard = [[InlineKeyboardButton("✏️ Редагувати", callback_data="finance_edit_deal")]]
    
    target_message = query.message if from_list else update.message
    try:
        await target_message.edit_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except (AttributeError, TelegramError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

    return config.FINANCE_VIEW_DEAL

async def show_my_deals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()
    await query.message.edit_text("🔍 Шукаю ваші угоди...")

    all_deals = await gs_manager.get_all_records(config.SHEET_NAMES['payments'], expected_headers=PAYMENT_SHEET_HEADERS)
    
    my_deals = [
        d for d in all_deals 
        if str(d.get("ID Менеджера", "")).strip() == user_id 
        and str(d.get("Статус", "")).strip() == "В процесі"
    ]

    if not my_deals:
        await query.message.edit_text("У вас немає активних угод.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_finance_menu")]]))
        return config.FINANCE_MY_DEALS_LIST

    keyboard = []
    for deal in my_deals:
        vin = deal['ВІН-код']
        model = deal.get('Назва авто', vin)
        keyboard.append([InlineKeyboardButton(f"{model} ({vin[-6:]})", callback_data=f"view_my_deal_{vin}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_finance_menu")])
    await query.message.edit_text(f"Ваші активні угоди ({len(my_deals)}):", reply_markup=InlineKeyboardMarkup(keyboard))
    return config.FINANCE_MY_DEALS_LIST

# --- Логіка редагування та трекера ---
async def edit_deal_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    deal_info = context.user_data['current_deal_info']
    vin = deal_info['record']['ВІН-код']
    keyboard = [
        [InlineKeyboardButton("Клієнта", callback_data="edit_field_Клієнт")],
        [InlineKeyboardButton("Загальну вартість", callback_data="edit_field_Загальна вартість")],
        [InlineKeyboardButton("Статус", callback_data="edit_field_Статус")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"back_to_view_deal_{vin}")]
    ]
    await query.message.edit_text("Що саме ви хочете змінити?", reply_markup=InlineKeyboardMarkup(keyboard))
    return config.FINANCE_EDIT_DEAL_MENU

async def edit_deal_ask_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field_to_edit = query.data.split('_')[-1]
    context.user_data['field_to_edit'] = field_to_edit
    await query.message.edit_text(f"Введіть нове значення для поля '{field_to_edit}':")
    return config.FINANCE_EDIT_DEAL_GET_VALUE

async def edit_deal_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_value = update.message.text.strip()
    field = context.user_data['field_to_edit']
    deal_info = context.user_data['current_deal_info']
    deal_record = deal_info['record']
    user = update.effective_user

    old_value = deal_record.get(field, 'Н/Д')
    deal_record[field] = new_value

    if field == "Загальна вартість":
        try:
            total_price = float(new_value)
            total_paid = float(deal_record.get('Сплачено', 0))
            deal_record['Залишок'] = total_price - total_paid
        except ValueError:
            await update.message.reply_text("❌ Помилка! Вартість має бути числом.")
            return config.FINANCE_EDIT_DEAL_GET_VALUE
    
    await update.message.reply_text("Оновлюю дані...")
    
    new_msg_id = await send_or_edit_finance_notification(context, deal_record, user.full_name, f"Оновлено поле: {field}")
    if new_msg_id:
        deal_record["ID повідомлення в каналі"] = new_msg_id

    success = await gs_manager.update_row(config.SHEET_NAMES['payments'], deal_info['row_index'], deal_record, PAYMENT_SHEET_HEADERS)

    if success:
        await update.message.reply_text("✅ Дані успішно оновлено!", reply_markup=get_employee_keyboard(user.id))
    else:
        await update.message.reply_text("❌ Помилка оновлення.", reply_markup=get_employee_keyboard(user.id))

    return ConversationHandler.END

async def add_tracker_ask_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    deal = context.user_data['existing_deal']['record']
    current_tracker = deal.get('Трекер') or "не додано"
    msg = await update.message.reply_text(f"Поточний трекер: `{current_tracker}`\nВведіть новий номер трекера:", parse_mode='Markdown')
    context.user_data['messages_to_delete'].append(msg.message_id)
    return config.FINANCE_ADD_TRACKER_GET_VALUE

async def add_tracker_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_tracker = update.message.text.strip()
    deal_info = context.user_data['existing_deal']
    deal_record = deal_info['record']
    user = update.effective_user

    context.user_data['messages_to_delete'].append(update.message.message_id)

    for msg_id in context.user_data.get('messages_to_delete', []):
        try:
            await context.bot.delete_message(chat_id=user.id, message_id=msg_id)
        except TelegramError as e:
            logger.warning(f"Не вдалося видалити повідомлення {msg_id}: {e}")

    deal_record['Трекер'] = new_tracker
    
    new_msg_id = await send_or_edit_finance_notification(context, deal_record, user.full_name, "Оновлено трекер")
    if new_msg_id:
        deal_record["ID повідомлення в каналі"] = new_msg_id

    success = await gs_manager.update_row(config.SHEET_NAMES['payments'], deal_info['row_index'], deal_record, PAYMENT_SHEET_HEADERS)

    if success:
        await context.bot.send_message(chat_id=user.id, text="✅ Трекер успішно додано/оновлено!", reply_markup=get_employee_keyboard(user.id))
    else:
        await context.bot.send_message(chat_id=user.id, text="❌ Помилка оновлення.", reply_markup=get_employee_keyboard(user.id))
    
    return ConversationHandler.END


# --- Створення обробника ---
def get_finance_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(finance_menu, pattern="^cabinet_finance_menu$")],
        states={
            config.FINANCE_MENU: [
                CallbackQueryHandler(ask_for_vin, pattern="^finance_action_(new|add|view|tracker)$"),
                CallbackQueryHandler(show_my_deals, pattern="^finance_my_deals$"),
            ],
            config.FINANCE_GET_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_vin_and_proceed)],
            config.FINANCE_NEW_DEAL_GET_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_deal_get_model)],
            config.FINANCE_NEW_DEAL_SOURCE: [CallbackQueryHandler(new_deal_get_source, pattern="^source_")],
            config.FINANCE_NEW_DEAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_deal_get_price)],
            config.FINANCE_NEW_DEAL_CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_deal_get_client)],
            config.FINANCE_NEW_DEAL_CONFIRM: [CallbackQueryHandler(new_deal_save, pattern="^confirm_new_deal$")],
            config.FINANCE_ADD_PAYMENT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_payment_get_amount)],
            config.FINANCE_ADD_PAYMENT_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_payment_save)],
            config.FINANCE_MY_DEALS_LIST: [
                CallbackQueryHandler(finance_menu, pattern="^back_to_finance_menu$"),
                CallbackQueryHandler(lambda u, c: view_deal_details(u, c, from_list=True), pattern="^view_my_deal_")
            ],
            config.FINANCE_VIEW_DEAL: [
                CallbackQueryHandler(edit_deal_menu, pattern="^finance_edit_deal$")
            ],
            config.FINANCE_EDIT_DEAL_MENU: [
                CallbackQueryHandler(lambda u, c: view_deal_details(u, c, from_list=True), pattern="^back_to_view_deal_"),
                CallbackQueryHandler(edit_deal_ask_value, pattern="^edit_field_")
            ],
            config.FINANCE_EDIT_DEAL_GET_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_deal_save_value)],
            config.FINANCE_ADD_TRACKER_GET_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tracker_save_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        map_to_parent={ConversationHandler.END: config.CABINET_MENU},
        allow_reentry=True
    )
