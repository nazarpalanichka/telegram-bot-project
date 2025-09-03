import logging
import math
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.error import BadRequest
import config
from utils.helpers import escape_markdown_v2
from . import calculator
logger = logging.getLogger(__name__)
gs_manager = None
async def auction_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Аукціони Copart 🔵", callback_data="show_auction_copart")],
        [InlineKeyboardButton("Аукціони IAAI 🟡", callback_data="show_auction_iaai")],
    ])
    await update.message.reply_text("Оберіть, список яких аукціонів ви хочете переглянути:", reply_markup=keyboard)
async def show_selected_auction_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    auction_type = query.data.split('_')[-1]
    await query.edit_message_text(f"Завантажую список для {auction_type.upper()}...")
    await list_locations_paged(update, context, auction_type, page=0)
async def list_locations_paged(update: Update, context: ContextTypes.DEFAULT_TYPE, auction_type: str, page: int):
    locations_list = calculator.COPART_LOCATIONS if auction_type == 'copart' else calculator.IAAI_LOCATIONS
    if not locations_list:
        await update.callback_query.edit_message_text(f"Список для {auction_type.upper()} порожній.")
        return
    locations_per_page = 30
    start_index = page * locations_per_page
    current_page_locations = locations_list[start_index : start_index + locations_per_page]
    total_pages = math.ceil(len(locations_list) / locations_per_page)
    text = (f"📜 *Список локацій {auction_type.upper()} \\(Сторінка {page + 1} з {total_pages}\\)*:\n\n" +
            "\n".join([f"📍 `{escape_markdown_v2(loc.split(': ')[1])}`" for loc in current_page_locations]))
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Попередня", callback_data=f"locpage_{auction_type}_{page-1}"))
    if page < (total_pages - 1):
        row.append(InlineKeyboardButton("Наступна ➡️", callback_data=f"locpage_{auction_type}_{page+1}"))
    reply_markup = InlineKeyboardMarkup([row]) if row else None
    try:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.warning("Tried to edit message with the same content in auctions.")
        else:
            raise
async def locations_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, auction_type, page_str = query.data.split("_")
        page = int(page_str)
    except (IndexError, ValueError):
        logger.warning(f"Не вдалося обробити callback_data для пагінації: {query.data}")
        return
    await list_locations_paged(update, context, auction_type, page)
def get_auctions_menu_handler():
    return MessageHandler(filters.Regex('Список аукціонів'), auction_list_menu)
