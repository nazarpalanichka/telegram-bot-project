# -*- coding: utf-8 -*-
# handlers/channel_sold_patch.py — простий флоу «Продано»
from telegram import ReplyKeyboardMarkup
from telegram.ext import ConversationHandler, MessageHandler, filters
from handlers.keyboards import get_employee_keyboard
from utils.g_sheets_extras import ensure_columns_exist
import config


CHOOSE_SOLD_LOCATION = 94101


SELL_LOCATION_KBD = ReplyKeyboardMarkup([["Галицька", "Сидора"]], resize_keyboard=True, one_time_keyboard=True)


async def start_sold_flow(update, context):
    rec = context.user_data.get('pending_sold_rec')
    if not rec:
        await update.message.reply_text("Не знайдено запис для фіксації продажу.")
        return ConversationHandler.END
    await update.message.reply_text("Де саме продалось авто?", reply_markup=SELL_LOCATION_KBD)
    return CHOOSE_SOLD_LOCATION


async def choose_sold_location(update, context):
    loc = update.message.text.strip()
    if loc not in {"Галицька", "Сидора"}:
        await update.message.reply_text("Оберіть локацію: Галицька або Сидора")
        return CHOOSE_SOLD_LOCATION
    rec = context.user_data.pop('pending_sold_rec')
    rec['sold_location'] = loc
    rec['sold_manager_id'] = str(update.effective_user.id)
    ensure_columns_exist(
        context.bot_data['gs_manager'],
        sheet_name=config.SHEET_NAMES['published_posts'],
        required_headers=['sold_location', 'sold_manager_id']
    )
    await context.bot_data['gs_manager'].update_record(
        config.SHEET_NAMES['published_posts'],
        rec,
        key_col=config.POST_SHEET_COLS['vin']
    )
    await update.message.reply_text(
        "Продаж зафіксовано.",
        reply_markup=get_employee_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END


def get_sold_patch_handler():
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^\u2705 Підтвердити продаж$'), start_sold_flow)],
        states={CHOOSE_SOLD_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_sold_location)]},
        fallbacks=[],
        allow_reentry=True,
    )