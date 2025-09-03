# -*- coding: utf-8 -*-
# handlers/filter.py

import logging
import re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, Message
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
)
from telegram.error import BadRequest

import config
from .start import cancel_command, start_command
from .keyboards import client_keyboard
from .catalog import build_browse_caption, display_browse_item, browse_callback_handler, details_callback_handler
from .utils import determine_fuel_type

logger = logging.getLogger(__name__)
gs_manager = None

KNOWN_BRANDS = {
    "Audi", "BMW", "Volkswagen", "Nissan", "Chevrolet", "Ford", "Toyota", "Honda",
    "Mercedes-Benz", "Lexus", "Hyundai", "Kia", "Mazda", "Subaru", "Volvo", "Tesla",
    "BYD", "GAC", "Zeekr", "Polestar", "Cadillac", "Jeep", "Dodge", "Chrysler",
    "GMC", "Buick", "Acura", "Infiniti", "Mitsubishi", "Porsche", "Land Rover",
    "Jaguar", "Fiat", "Mini", "Smart", "Renault", "Peugeot", "Citroen"
}

def extract_brand_from_model(model_string: str) -> str | None:
    """Витягує назву марки з повного рядка моделі."""
    if not model_string:
        return None
    for brand in ["Mercedes-Benz", "Land Rover"]:
        if model_string.upper().startswith(brand.upper()):
            return brand
    words = re.split(r'[\s-]+', model_string)
    for word in words:
        if word.capitalize() in KNOWN_BRANDS:
            return word.capitalize()
    return None

async def filter_start(update: Update | Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Починає процес фільтрації, показуючи вибір критерію."""
    message = update.message if isinstance(update, Update) else update
    
    keyboard = [
        [InlineKeyboardButton("Пошук за маркою авто", callback_data="filter_by_brand")],
        [InlineKeyboardButton("Пошук за типом пального", callback_data="filter_by_fuel")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_action")]
    ]
    await message.reply_text(
        "<b>🔍 Фільтр авто</b>\n\nОберіть критерій для пошуку:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return config.FILTER_SELECT_BRAND

async def get_active_cars(context: ContextTypes.DEFAULT_TYPE) -> list | None:
    """Отримує та кешує список активних авто з аркуша 'Опубліковані Пости'."""
    if 'filter_all_cars' in context.user_data:
        return context.user_data['filter_all_cars']
    
    if not gs_manager: return None

    all_posts = await gs_manager.get_all_records(config.SHEET_NAMES['published_posts'], expected_headers=config.POST_SHEET_HEADER_ORDER)
    if all_posts is None: return None

    active_cars = [p for p in all_posts if p.get(config.POST_SHEET_COLS['status']) == 'active']
    context.user_data['filter_all_cars'] = active_cars
    return active_cars

async def filter_show_brands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показує список доступних марок авто."""
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("⏳ Завантажую список доступних марок...")

    active_cars = await get_active_cars(context)
    if active_cars is None:
        await query.message.edit_text("Вибачте, сервіс тимчасово недоступний.")
        return ConversationHandler.END
    if not active_cars:
        await query.message.edit_text("На жаль, зараз немає активних пропозицій.")
        return ConversationHandler.END

    brands = sorted(list(set(
        brand for car in active_cars
        if (brand := extract_brand_from_model(car.get(config.POST_SHEET_COLS['model'], ''))) is not None
    )))

    if not brands:
        await query.message.edit_text("Не вдалося визначити марки авто.")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(brand, callback_data=f"filter_brand_{brand}")] for brand in brands]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="filter_back_to_start")])

    await query.message.edit_text("Оберіть марку автомобіля:", reply_markup=InlineKeyboardMarkup(keyboard))
    return config.FILTER_SELECT_BRAND
    
async def filter_show_fuel_types(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показує кнопки для фільтрації за типом пального."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("⛽️ Бензин", callback_data="filter_fuel_Бензин"), InlineKeyboardButton("💨 Дизель", callback_data="filter_fuel_Дизель")],
        [InlineKeyboardButton("⚡️ Електро", callback_data="filter_fuel_Електро"), InlineKeyboardButton("Hybrid", callback_data="filter_fuel_Гібрид")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="filter_back_to_start")]
    ]
    await query.message.edit_text("Оберіть тип пального:", reply_markup=InlineKeyboardMarkup(keyboard))
    return config.FILTER_SELECT_BRAND

async def filter_select_brand_or_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє вибір критерію фільтрації."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_action":
        await query.message.delete()
        await context.bot.send_message(chat_id=query.from_user.id, text="Фільтрацію скасовано.", reply_markup=client_keyboard)
        return ConversationHandler.END
    
    if query.data == "filter_back_to_start":
        await query.message.delete()
        return await filter_start(query.message, context)
        
    if query.data.startswith("filter_brand_"):
        return await filter_show_models(update, context)
    elif query.data.startswith("filter_fuel_"):
        return await filter_show_results(update, context)
    elif query.data == "filter_by_brand":
        return await filter_show_brands(update, context)
    elif query.data == "filter_by_fuel":
        return await filter_show_fuel_types(update, context)

    return config.FILTER_SELECT_BRAND

async def filter_show_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє вибір марки та показує список моделей."""
    query = update.callback_query
    selected_brand = query.data.replace("filter_brand_", "")
    context.user_data['filter_selected_brand'] = selected_brand
    
    active_cars = await get_active_cars(context)
    models = sorted(list(set(
        car.get(config.POST_SHEET_COLS['model'], '')
        for car in active_cars
        if car.get(config.POST_SHEET_COLS['model'], '').upper().startswith(selected_brand.upper())
    )))
    if not models:
        await query.message.edit_text("Не знайдено моделей для цієї марки.")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(model, callback_data=f"filter_model_{model}")] for model in models]
    keyboard.append([InlineKeyboardButton("⬅️ Назад до марок", callback_data="filter_by_brand")])

    await query.message.edit_text(f"Ви обрали: <b>{selected_brand}</b>.\n\nТепер оберіть модель:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return config.FILTER_SELECT_MODEL

async def filter_show_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє вибір та запускає перегляд результатів."""
    query = update.callback_query
    await query.answer()

    active_cars = await get_active_cars(context)
    if active_cars is None:
        await query.message.edit_text("Помилка завантаження даних.")
        return ConversationHandler.END

    filtered_cars = []
    if query.data.startswith("filter_model_"):
        selected_model = query.data.replace("filter_model_", "")
        filtered_cars = [car for car in active_cars if car.get(config.POST_SHEET_COLS['model'], '') == selected_model]
    elif query.data.startswith("filter_fuel_"):
        selected_fuel = query.data.replace("filter_fuel_", "")
        filtered_cars = [car for car in active_cars if determine_fuel_type(car.get(config.POST_SHEET_COLS['modification'])) == selected_fuel]

    if not filtered_cars:
        await query.message.edit_text("Не знайдено авто за вашим фільтром.")
        return ConversationHandler.END

    await query.message.delete()
    context.user_data['catalog_cars'] = filtered_cars
    
    class FakeQuery:
        def __init__(self, message, original_query):
            self.message = message; self.from_user = original_query.from_user
        async def answer(self): pass
        async def edit_message_media(self, *args, **kwargs): return await self.message.edit_media(*args, **kwargs)
        async def edit_message_text(self, *args, **kwargs): return await self.message.edit_text(*args, **kwargs)

    temp_message = await context.bot.send_message(chat_id=query.from_user.id, text="Завантаження результатів...")
    fake_update = Update(update.update_id, callback_query=FakeQuery(temp_message, query))
    
    await display_browse_item(fake_update, context, 0)
    return config.CATALOG_BROWSE

def get_filter_handler() -> ConversationHandler:
    """Створює обробник для фільтрації авто."""
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔍 Фільтр авто$'), filter_start)],
        states={
            config.FILTER_SELECT_BRAND: [CallbackQueryHandler(filter_select_brand_or_fuel, pattern=r"^filter_")],
            config.FILTER_SELECT_MODEL: [
                CallbackQueryHandler(filter_show_results, pattern=r"^filter_model_"),
                CallbackQueryHandler(filter_show_brands, pattern=r"^filter_by_brand$")
            ],
            config.CATALOG_BROWSE: [CallbackQueryHandler(browse_callback_handler, pattern=r"^cat_")],
            config.CATALOG_DETAILS_VIEW: [CallbackQueryHandler(details_callback_handler, pattern=r"^cat_back_")]
        },
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )

