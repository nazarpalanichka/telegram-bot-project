# -*- coding: utf-8 -*-
# handlers/catalog.py
# Повна версія, сумісна з filter.py та Google Sheets

import logging
from typing import Any, Dict, List
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest
import config
from .keyboards import client_keyboard
from .start import start_command

logger = logging.getLogger(__name__)
gs_manager = None

# --- СТАНИ РОЗМОВИ ---
CATALOG_BROWSE, CATALOG_DETAILS_VIEW = range(9100, 9102)

# --- Утиліти для роботи з даними ---

def _col(rec: Dict[str, Any], key: str, default: str = "") -> str:
    """Безпечно отримує значення з запису за ключем з конфігурації."""
    mapping = config.POST_SHEET_COLS.get(key, key)
    val = rec.get(mapping, default)
    return str(val or default).strip()

def build_browse_caption(rec: Dict[str, Any]) -> str:
    """Будує HTML-підпис для картки авто в режимі перегляду."""
    model = _col(rec, 'model', 'Модель не вказано')
    price = _col(rec, 'price', 'Ціна не вказана')
    year = _col(rec, 'year', '') # Рік може бути частиною моделі
    
    # Спробуємо витягнути рік з моделі, якщо він є
    if not year:
        parts = model.split()
        if len(parts) > 1 and parts[-1].isdigit() and len(parts[-1]) == 4:
            year = parts[-1]
            model = " ".join(parts[:-1])

    price_str = f"${int(float(price)):,}".replace(',', ' ') if price.replace('.', '', 1).isdigit() else price

    # Формування рядка з характеристиками
    details_parts = []
    if fuel := _col(rec, 'fuel_type'): details_parts.append(fuel)
    if mileage := _col(rec, 'mileage'):
        try:
            mileage_val = int(float(str(mileage).replace(' ', '')))
            details_parts.append(f"{mileage_val:,} км".replace(',', ' '))
        except (ValueError, TypeError):
             details_parts.append(mileage) # Якщо пробіг нечисловий
    if gearbox := _col(rec, 'gearbox'): details_parts.append(gearbox)
    if drivetrain := _col(rec, 'drivetrain'): details_parts.append(drivetrain)
    
    details_line = " | ".join(filter(None, details_parts))

    return (
        f"<b>{model} {year}</b>\n"
        f"<b>Ціна: {price_str}</b>\n\n"
        f"<i>{details_line}</i>"
    )

def build_details_caption(rec: Dict[str, Any]) -> str:
    """Будує розширений HTML-підпис для деталей авто."""
    base_caption = build_browse_caption(rec)
    condition = _col(rec, 'condition')
    vin = _col(rec, 'vin')
    
    details = f"\n\n<b>VIN:</b> <code>{vin}</code>" if vin else ""
    if condition:
        details += f"\n<b>Опис:</b>\n{condition}"
    
    return base_caption + details

# --- Основні функції відображення ---

async def display_browse_item(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int | None = None) -> None:
    """
    Основна функція для відображення картки авто.
    Оновлює повідомлення з фото, підписом та кнопками.
    """
    query = update.callback_query
    if query:
        await query.answer()

    browse_results = context.user_data.get('browse_results', [])
    if not browse_results:
        msg = "На жаль, за вашим запитом нічого не знайдено."
        if query:
            await query.edit_message_text(msg, reply_markup=None)
        else:
            await update.message.reply_text(msg)
        return

    current_index = index if index is not None else context.user_data.get('browse_index', 0)
    context.user_data['browse_index'] = current_index
    
    record = browse_results[current_index]
    caption = build_browse_caption(record)
    
    # --- Клавіатура ---
    total = len(browse_results)
    nav_row = []
    if current_index > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_prev_{current_index-1}"))
    nav_row.append(InlineKeyboardButton(f"{current_index + 1} / {total}", callback_data="cat_ignore"))
    if current_index < total - 1:
        nav_row.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"cat_next_{current_index+1}"))

    keyboard = InlineKeyboardMarkup([
        nav_row,
        [InlineKeyboardButton("📋 Детальніше", callback_data=f"cat_details_{current_index}")],
        [InlineKeyboardButton("☎️ Зв'язатися з менеджером", url="https://t.me/Nazar_Itrans")]
    ])

    # --- Фото ---
    photo_ids = str(_col(record, 'photos', '')).split(',')
    photo_id = photo_ids[0] if photo_ids and photo_ids[0] else "https://placehold.co/1280x720/222/fff?text=iTrans+Motors"

    try:
        if query:
            # Спроба оновити медіа. Якщо не виходить - оновлюємо текст.
            try:
                await query.edit_message_media(media=InputMediaPhoto(media=photo_id, caption=caption, parse_mode='HTML'), reply_markup=keyboard)
            except BadRequest as e:
                if "message is not modified" in str(e):
                    pass # Нічого не робимо, якщо повідомлення не змінилось
                else: # Якщо інша помилка (напр. невалідний photo_id), оновлюємо текст
                    await query.edit_message_text(caption, parse_mode='HTML', reply_markup=keyboard)
        else:
            await update.message.reply_photo(photo_id, caption=caption, parse_mode='HTML', reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error displaying car item (photo_id: {photo_id}): {e}", exc_info=True)
        # Аварійний варіант без фото
        await update.effective_message.reply_html(caption, reply_markup=keyboard)


# --- Callback-и ---

async def browse_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє кнопки 'Вперед' та 'Назад'."""
    query = update.callback_query
    try:
        _, direction, index_str = query.data.split('_')
        new_index = int(index_str)
        await display_browse_item(update, context, new_index)
    except (ValueError, IndexError) as e:
        logger.warning(f"Invalid browse callback data: {query.data}, error: {e}")
        await query.answer("Помилка навігації.")
    
    return CATALOG_BROWSE

async def details_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє кнопку 'Детальніше'."""
    query = update.callback_query
    try:
        _, _, index_str = query.data.split('_')
        index = int(index_str)
        record = context.user_data.get('browse_results', [])[index]
        caption = build_details_caption(record)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад до каталогу", callback_data=f"cat_back_{index}")],
            [InlineKeyboardButton("☎️ Зв'язатися з менеджером", url="https://t.me/Nazar_Itrans")]
        ])
        await query.edit_message_text(caption, parse_mode='HTML', reply_markup=keyboard)
        return CATALOG_DETAILS_VIEW
    except (ValueError, IndexError, TypeError) as e:
        logger.error(f"Error in details view for callback {query.data}: {e}")
        await query.answer("Не вдалося завантажити деталі.")
        return CATALOG_BROWSE

async def back_to_browse_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Повертає до режиму перегляду з деталей."""
    query = update.callback_query
    try:
        _, _, index_str = query.data.split('_')
        index = int(index_str)
        # Видаляємо старе повідомлення і надсилаємо нове, бо не можна змінити текстове повідомлення на медіа
        await query.message.delete()
        await display_browse_item(update, context, index)

    except Exception as e:
        logger.error(f"Error returning to browse view: {e}")
        await query.answer("Помилка повернення.")

    return CATALOG_BROWSE

async def ignore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пустий callback для кнопок, які не мають нічого робити (напр. лічильник)."""
    await update.callback_query.answer()


# --- Точка входу в каталог ---

async def catalog_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запускає каталог, завантажуючи дані з Google Sheets."""
    await update.message.reply_text("⏳ Завантажую актуальні авто, будь ласка, зачекайте...")
    try:
        # Завантажуємо тільки активні пости
        all_cars = await gs_manager.get_all_records(config.SHEET_NAMES['published_posts'])
        active_cars = [car for car in all_cars if _col(car, 'status') == 'active']
        
        if not active_cars:
            await update.message.reply_text("Наразі немає авто в наявності.", reply_markup=client_keyboard)
            return ConversationHandler.END
            
        context.user_data['browse_results'] = active_cars
        context.user_data['browse_index'] = 0
        
        # Видаляємо повідомлення "Завантажую..." і показуємо перше авто
        await update.effective_message.delete()
        await display_browse_item(update, context, 0)
        
        return CATALOG_BROWSE

    except Exception as e:
        logger.error(f"Failed to start catalog: {e}", exc_info=True)
        await update.message.reply_text("Виникла помилка при завантаженні каталогу. Спробуйте пізніше.", reply_markup=client_keyboard)
        return ConversationHandler.END


def get_catalog_handler() -> ConversationHandler:
    """Створює та повертає ConversationHandler для каталогу."""
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Каталог авто$"), catalog_start)],
        states={
            CATALOG_BROWSE: [
                CallbackQueryHandler(browse_callback_handler, pattern=r"^cat_(next|prev)_"),
                CallbackQueryHandler(details_callback_handler, pattern=r"^cat_details_"),
                CallbackQueryHandler(ignore_callback, pattern=r"^cat_ignore$")
            ],
            CATALOG_DETAILS_VIEW: [
                 CallbackQueryHandler(back_to_browse_handler, pattern=r"^cat_back_")
            ]
        },
        fallbacks=[CommandHandler("start", start_command)],
        per_message=False
    )

