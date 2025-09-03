# -*- coding: utf-8 -*-
# handlers/channel.py

import logging
import datetime
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo, Application
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
)
from telegram.error import BadRequest, Forbidden

import config
from utils.helpers import escape_markdown_v2
from utils.sync import synchronize_working_sheets
from .start import cancel_command, start_command
from .keyboards import get_employee_keyboard
from .utils import determine_fuel_type

logger = logging.getLogger(__name__)
gs_manager = None

async def check_reminders(application: Application):
    """
    Periodically checks the "Notes" sheet for reminders that are due.
    """
    gs_manager = application.bot_data.get('gs_manager')
    if not gs_manager:
        logger.error("check_reminders: gs_manager not found in bot_data.")
        return

    logger.info("Running scheduled task: Checking reminders...")
    try:
        notes = await gs_manager.get_all_records(config.SHEET_NAMES['notes'])
        if not notes:
            return

        now = datetime.datetime.now()
        
        for note in notes:
            reminder_time_str = note.get("Час нагадування")
            status = note.get("Статус")
            note_id = note.get("ID Нотатки")

            if status == "Активно" and reminder_time_str:
                try:
                    reminder_time = datetime.datetime.strptime(reminder_time_str, '%Y-%m-%d %H:%M:%S')
                    if reminder_time <= now:
                        manager_id = note.get("ID Менеджера")
                        note_text = note.get("Текст нотатки")
                        
                        if not manager_id or not note_text:
                            logger.warning(f"Skipping reminder for ID {note_id} due to missing data.")
                            continue

                        keyboard = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("✅ Виконано", callback_data=f"remind_done_{note_id}"),
                                InlineKeyboardButton("⏰ Відкласти", callback_data=f"remind_reschedule_{note_id}")
                            ]
                        ])
                        
                        await application.bot.send_message(
                            chat_id=int(manager_id),
                            text=f"⏰ **Нагадування**\n\n📝: _{escape_markdown_v2(note_text)}_",
                            parse_mode='MarkdownV2',
                            reply_markup=keyboard
                        )
                        
                        await gs_manager.update_record_by_key(
                            config.SHEET_NAMES['notes'], 
                            "ID Нотатки", 
                            note_id, 
                            {"Статус": "Нагадування відправлено"}
                        )
                        logger.info(f"Sent reminder for ID {note_id} to manager {manager_id}.")
                except ValueError:
                    logger.error(f"Could not parse time '{reminder_time_str}' for note ID {note_id}.")
                except Exception as e:
                    logger.error(f"Error processing reminder for ID {note_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Failed to check reminders: {e}", exc_info=True)

# --- Helper Functions ---

def get_employee_footer(user_id: int) -> str:
    """Forms the post footer depending on the manager's ID."""
    footers = {
        7461893847: "\n\nЦікавить дане авто? Звертайтеся:\n📞 0953362931 (Назар)\n📲 Telegram: https://t.me/Nazar_Itrans\n📍 Адреса: м. Стрий, вул. Львівська, 186 б",
        972106133: "\n\nЦікавить дане авто? Звертайтеся:\n📞 0662296523 (Влад)\n📲 Telegram: https://t.me/Vl_iTrans",
        7774852966: "\n\nЦікавить дане авто? Звертайтеся:\n📞 0688305126 (Назар)\n📲 Telegram: https://t.me/Nazar_iTrans_Motors",
        521960259: "\n\nЦікавить дане авто? Звертайтеся:\n📞 0675880193 (Володимир)\n📲 Telegram: https://t.me/Volodymyr_iTrans"
    }
    return footers.get(user_id, footers[7461893847])

def build_caption(data: dict, user_id: int) -> str:
    """Creates a caption for a post in the Telegram channel."""
    vin = data.get(config.POST_SHEET_COLS['vin'], "N/A")
    model = data.get(config.POST_SHEET_COLS['model'], "Модель не вказано")
    price_str = data.get(config.POST_SHEET_COLS['price'], "Ціна не вказана")
    modification = data.get(config.POST_SHEET_COLS['modification'], "Деталі не вказано")
    condition = data.get(config.POST_SHEET_COLS['condition'], "")

    try:
        price = f"${int(float(price_str)):,}".replace(',', ' ')
    except (ValueError, TypeError):
        price = price_str
        
    status_prefix = data.get(config.POST_SHEET_COLS['status_prefix'], "✅ В НАЯВНОСТІ")

    caption_parts = [
        f"<b>{model}</b>",
        f"<b>Ціна: {price}</b>\n",
        f"<b>{status_prefix}</b>\n",
        f"{modification}",
    ]
    if condition:
        caption_parts.append(f"\n{condition}")

    caption_parts.append(f"\nVIN: <code>{vin}</code>")
    caption_parts.append(get_employee_footer(user_id))
    
    return "\n".join(caption_parts)

async def repost_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the reposting of a car from the archive or another list."""
    query = update.callback_query
    await query.answer()
    
    vin = query.data.split('_')[-1]
    
    post_info = await gs_manager.find_car_by_vin(vin, search_sheets=[config.SHEET_NAMES['archive'], config.SHEET_NAMES['all_cars']])
    
    if not post_info:
        await query.edit_message_text("Не вдалося знайти інформацію про це авто.")
        return

    record = post_info['record']
    
    context.user_data['post_data'] = record
    context.user_data['post_data'][config.POST_SHEET_COLS['status_prefix']] = record.get(config.POST_SHEET_COLS['status_prefix'], "✅ В НАЯВНОСТІ")
    context.user_data['post_data'][config.POST_SHEET_COLS['media_type']] = record.get(config.POST_SHEET_COLS['media_type'], 'photo')
    context.user_data['post_data'][config.POST_SHEET_COLS['vin']] = vin
    
    await query.edit_message_text("Відновлено дані. Тепер ви можете продовжити публікацію через меню '➕ Додати авто / 📢 Пост'")
    await channel_menu(update, context, from_repost=True)


# --- Conversation Logic for Adding/Publishing ---

async def channel_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, from_repost: bool = False):
    """Displays the main menu for channel operations."""
    user = update.effective_user
    message = update.message or update.callback_query.message

    if user.id not in config.ALLOWED_USER_IDS:
        return ConversationHandler.END

    keyboard_buttons = [
        [InlineKeyboardButton("➕ Додати авто / 📢 Опублікувати", callback_data="add_or_publish_start")],
        [InlineKeyboardButton("🔄 Синхронізувати таблиці", callback_data="sync_sheets_manual")]
    ]
    if context.user_data.get('post_data'):
        keyboard_buttons.insert(1, [InlineKeyboardButton("🗑️ Очистити збережені дані", callback_data="clear_post_data")])
        text = "Ви у меню публікацій. У вас є збережені дані для поста. Оберіть дію:"
    else:
        text = "Ви у меню публікацій. Оберіть дію:"

    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    
    if from_repost:
        await message.reply_text(text, reply_markup=reply_markup)
    else:
        await message.reply_text(text, reply_markup=reply_markup)
    return config.CHANNEL_MENU_STATE

async def add_or_publish_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the process of adding or publishing a car."""
    query = update.callback_query
    await query.answer()

    if context.user_data.get('post_data'):
        await query.edit_message_text(
            "У вас вже є збережені дані. Продовжуємо з ними.\n"
            "Надішліть фото або відео для поста."
        )
        return config.ADD_OR_PUBLISH_GET_PHOTOS

    await query.edit_message_text("Введіть VIN-код автомобіля:")
    return config.ADD_OR_PUBLISH_GET_VIN

async def add_or_publish_get_vin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the VIN code and searches for existing data."""
    vin = update.message.text.strip().upper()
    if len(vin) < 11:
        await update.message.reply_text("VIN-код занадто короткий. Спробуйте ще раз.")
        return config.ADD_OR_PUBLISH_GET_VIN

    await update.message.reply_text("⏳ Шукаю інформацію за цим VIN...")
    
    context.user_data['post_data'] = {config.POST_SHEET_COLS['vin']: vin}
    
    # Search in all relevant sheets
    search_sheets = [
        config.SHEET_NAMES['lot_sydor'], 
        config.SHEET_NAMES['lot_galician'], 
        config.SHEET_NAMES['in_transit_usa'], 
        config.SHEET_NAMES['in_transit_china'], 
        config.SHEET_NAMES['all_cars']
    ]
    post_info = await gs_manager.find_car_by_vin(vin, search_sheets=search_sheets)

    if post_info:
        found_record = post_info['record']
        context.user_data['post_data'].update(found_record)
        
        # Determine fuel type from modification if not present
        if not context.user_data['post_data'].get(config.POST_SHEET_COLS['fuel_type']):
            mod_text = context.user_data['post_data'].get(config.POST_SHEET_COLS['modification'], "")
            fuel_type = determine_fuel_type(mod_text)
            if fuel_type:
                context.user_data['post_data'][config.POST_SHEET_COLS['fuel_type']] = fuel_type

        await update.message.reply_text(
            "Знайдено інформацію про авто. Надішліть фото або відео."
        )
    else:
        await update.message.reply_text(
            "Інформацію не знайдено. Потрібно буде ввести дані вручну.\n"
            "Спочатку надішліть фото або відео."
        )

    return config.ADD_OR_PUBLISH_GET_PHOTOS
    
async def add_or_publish_get_photos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving photos for the post."""
    if not context.user_data.get('post_data'):
        await update.message.reply_text("Спочатку потрібно ввести VIN. Почніть з /start.")
        return ConversationHandler.END

    if 'photo_ids' not in context.user_data['post_data']:
        context.user_data['post_data']['photo_ids'] = []

    photo_id = update.message.photo[-1].file_id
    context.user_data['post_data']['photo_ids'].append(photo_id)
    
    # Set media type
    context.user_data['post_data'][config.POST_SHEET_COLS['media_type']] = 'photo'
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Готово, перейти далі", callback_data="done_media")]
    ])
    await update.message.reply_text(
        f"Фото {len(context.user_data['post_data']['photo_ids'])} додано. Надішліть ще або натисніть 'Готово'.",
        reply_markup=keyboard
    )
    return config.ADD_OR_PUBLISH_GET_PHOTOS

async def add_or_publish_get_video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving a video for the post."""
    if not context.user_data.get('post_data'):
        await update.message.reply_text("Спочатку потрібно ввести VIN. Почніть з /start.")
        return ConversationHandler.END

    video_id = update.message.video.file_id
    context.user_data['post_data']['photo_ids'] = [video_id]
    context.user_data['post_data'][config.POST_SHEET_COLS['media_type']] = 'video'
    
    await update.message.reply_text("Відео додано. Переходимо до наступного кроку.")
    return await add_or_publish_done_media(update, context)

async def add_or_publish_done_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Proceeds after media has been sent."""
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message
        
    post_data = context.user_data.get('post_data', {})
    
    # Check for essential manual data if not found automatically
    if not post_data.get(config.POST_SHEET_COLS['model']):
        await message.reply_text("Введіть повну назву авто (напр. 'BMW X5 2021'):")
        return config.ADD_OR_PUBLISH_MANUAL_MODEL
        
    return await check_and_prompt_for_completeness(update, context)

async def add_or_publish_manual_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['post_data'][config.POST_SHEET_COLS['model']] = update.message.text
    if not context.user_data['post_data'].get(config.POST_SHEET_COLS['price']):
        await update.message.reply_text("Введіть ціну (тільки число, напр. 25000):")
        return config.ADD_OR_PUBLISH_MANUAL_PRICE
    return await check_and_prompt_for_completeness(update, context)

async def add_or_publish_manual_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['post_data'][config.POST_SHEET_COLS['price']] = update.message.text
    # Now that we have model and price, we can check for other fields
    return await check_and_prompt_for_completeness(update, context)

async def check_and_prompt_for_completeness(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Checks if all necessary data is present and prompts for missing fields if needed."""
    post_data = context.user_data['post_data']
    message = update.callback_query.message if update.callback_query else update.message

    required_keys_map = {
        'modification': 'модифікацію (Двигун, Пробіг і т.д.)',
        'condition': 'стан/опис авто',
        'status_prefix': 'локацію (статус)',
    }

    for key, prompt in required_keys_map.items():
        if not post_data.get(config.POST_SHEET_COLS.get(key)):
            if key == 'status_prefix':
                await message.reply_text("Оберіть локацію/статус авто:", reply_markup=get_location_keyboard())
                return config.ADD_OR_PUBLISH_ASK_LOCATION
            else:
                await message.reply_text(f"Будь ласка, введіть {prompt}:")
                # This logic assumes a sequential flow.
                # A more robust implementation would use a state machine or a loop.
                if key == 'modification': return config.ADD_OR_PUBLISH_MANUAL_MILEAGE # Simplified for now
                if key == 'condition': return config.ADD_OR_PUBLISH_ASK_CONDITION
    
    # If all data is present
    return await add_or_publish_confirm_and_publish_message(update, context)


async def add_or_publish_manual_mileage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This state now represents getting the 'modification'
    context.user_data['post_data'][config.POST_SHEET_COLS['modification']] = update.message.text
    # After getting modification, determine fuel type
    fuel_type = determine_fuel_type(update.message.text)
    if fuel_type:
        context.user_data['post_data'][config.POST_SHEET_COLS['fuel_type']] = fuel_type
    return await check_and_prompt_for_completeness(update, context)

async def add_or_publish_ask_condition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['post_data'][config.POST_SHEET_COLS['condition']] = update.message.text
    return await check_and_prompt_for_completeness(update, context)

async def add_or_publish_ask_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    location = query.data.split('_')[-1]
    if location == "cancel":
        await query.edit_message_text("Дію скасовано.")
        return await cancel_command(update, context)
        
    context.user_data['post_data'][config.POST_SHEET_COLS['status_prefix']] = location
    return await check_and_prompt_for_completeness(update, context)

async def add_or_publish_confirm_and_publish_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows the final preview and asks for confirmation."""
    user = update.effective_user
    post_data = context.user_data['post_data']
    message = update.callback_query.message if update.callback_query else update.message

    caption = build_caption(post_data, user.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Опублікувати", callback_data="publish_final")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_action")]
    ])

    media_type = post_data.get(config.POST_SHEET_COLS['media_type'], 'photo')
    media_ids = post_data.get('photo_ids', [])
    
    try:
        if media_ids:
            if media_type == 'video':
                await message.reply_video(video=media_ids[0], caption=caption, parse_mode='HTML', reply_markup=keyboard)
            else:
                if len(media_ids) > 1:
                    media_group = [InputMediaPhoto(media=pid) for pid in media_ids]
                    await message.reply_media_group(media=media_group)
                    await message.reply_text(caption, parse_mode='HTML', reply_markup=keyboard)
                else:
                    await message.reply_photo(photo=media_ids[0], caption=caption, parse_mode='HTML', reply_markup=keyboard)
        else:
            await message.reply_text(caption, parse_mode='HTML', reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error sending preview: {e}")
        await message.reply_text("Не вдалося відправити попередній перегляд. Спробуйте ще раз.", reply_markup=get_employee_keyboard(user.id))
        return ConversationHandler.END

    return config.ADD_OR_PUBLISH_CONFIRM_AND_PUBLISH


async def add_or_publish_publication_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the final publication to the channel and saves data to the sheet."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_action":
        await query.edit_message_text("Публікацію скасовано.")
        context.user_data.pop('post_data', None)
        return ConversationHandler.END

    await query.edit_message_text("⏳ Публікую пост в каналі...")

    user = update.effective_user
    post_data = context.user_data['post_data']
    caption = build_caption(post_data, user.id)
    media_type = post_data.get(config.POST_SHEET_COLS['media_type'], 'photo')
    media_ids = post_data.get('photo_ids', [])

    try:
        if media_ids:
            if media_type == 'video':
                sent_message = await context.bot.send_video(
                    chat_id=config.CHANNEL_ID, video=media_ids[0], caption=caption, parse_mode='HTML'
                )
            else:
                if len(media_ids) > 1:
                    media_group = [InputMediaPhoto(media=pid) for pid in media_ids]
                    sent_messages = await context.bot.send_media_group(chat_id=config.CHANNEL_ID, media=media_group)
                    sent_message = await context.bot.send_message(chat_id=config.CHANNEL_ID, text=caption, parse_mode='HTML')
                else:
                    sent_message = await context.bot.send_photo(
                        chat_id=config.CHANNEL_ID, photo=media_ids[0], caption=caption, parse_mode='HTML'
                    )
        else: # Should not happen, but as a fallback
             sent_message = await context.bot.send_message(chat_id=config.CHANNEL_ID, text=caption, parse_mode='HTML')

        # Save data to Google Sheet
        record_to_save = {
            config.POST_SHEET_COLS['msg_id']: sent_message.message_id,
            config.POST_SHEET_COLS['chat_id']: sent_message.chat_id,
            config.POST_SHEET_COLS['vin']: post_data.get(config.POST_SHEET_COLS['vin']),
            config.POST_SHEET_COLS['emp_id']: user.id,
            config.POST_SHEET_COLS['date']: datetime.datetime.now().isoformat(),
            config.POST_SHEET_COLS['status']: 'active',
            config.POST_SHEET_COLS['photos']: ",".join(post_data.get('photo_ids', [])),
            config.POST_SHEET_COLS['model']: post_data.get(config.POST_SHEET_COLS['model']),
            config.POST_SHEET_COLS['price']: post_data.get(config.POST_SHEET_COLS['price']),
            config.POST_SHEET_COLS['modification']: post_data.get(config.POST_SHEET_COLS['modification']),
            config.POST_SHEET_COLS['condition']: post_data.get(config.POST_SHEET_COLS['condition']),
            config.POST_SHEET_COLS['status_prefix']: post_data.get(config.POST_SHEET_COLS['status_prefix']),
            config.POST_SHEET_COLS['media_type']: media_type,
            config.POST_SHEET_COLS['fuel_type']: post_data.get(config.POST_SHEET_COLS['fuel_type'])
        }

        success = await gs_manager.add_row(config.SHEET_NAMES['published_posts'], record_to_save, config.POST_SHEET_HEADER_ORDER)

        if success:
            await query.edit_message_text(
                "✅ Пост успішно опубліковано в каналі!",
                reply_markup=get_employee_keyboard(user.id)
            )
            # Trigger background sync
            asyncio.create_task(synchronize_working_sheets(gs_manager))
        else:
            await query.edit_message_text(
                "⚠️ Пост опубліковано, але не вдалося зберегти дані в таблицю.",
                reply_markup=get_employee_keyboard(user.id)
            )

    except (BadRequest, Forbidden) as e:
        logger.error(f"Failed to publish to channel: {e}")
        await query.edit_message_text(
            f"❌ Помилка публікації: {e}. Перевірте, чи бот є адміністратором каналу з правами на публікацію.",
            reply_markup=get_employee_keyboard(user.id)
        )
    except Exception as e:
        logger.error(f"An unexpected error occurred during publication: {e}", exc_info=True)
        await query.edit_message_text("❌ Сталася неочікувана помилка.", reply_markup=get_employee_keyboard(user.id))

    context.user_data.pop('post_data', None)
    return ConversationHandler.END


# --- Other Callbacks ---

async def sync_sheets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Запускаю синхронізацію...")
    await query.edit_message_text("🔄 Синхронізація даних запущена у фоновому режимі. Це може зайняти до хвилини.")
    asyncio.create_task(synchronize_working_sheets(gs_manager))

async def clear_post_data_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data.pop('post_data', None)
    await query.answer("Дані очищено.")
    await query.edit_message_text("Збережені дані для поста очищено.", reply_markup=get_employee_keyboard(update.effective_user.id))

async def reminder_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles callback from reminder messages."""
    query = update.callback_query
    await query.answer()
    
    action, note_id = query.data.split('_')[1:]

    if action == 'done':
        success = await gs_manager.update_record_by_key(config.SHEET_NAMES['notes'], "ID Нотатки", note_id, {"Статус": "Виконано"})
        if success:
            await query.edit_message_text(f"✅ Нагадування виконано.\n\n{query.message.text}")
        else:
            await query.edit_message_text("Помилка оновлення статусу.")
    elif action == 'reschedule':
        # This part should ideally lead to the notes rescheduling conversation
        # For now, we'll just acknowledge
        await query.edit_message_text("Функція відкладення нотатки в розробці. Поки що ви можете змінити її вручну через меню 'Нотатки'.")

# --- Conversation Handler Setup ---

def get_add_or_publish_handler() -> ConversationHandler:
    """Creates and returns the main conversation handler for adding/publishing."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(channel_menu, pattern="^channel_menu$"),
            CallbackQueryHandler(add_or_publish_start, pattern="^add_or_publish_start$"),
            CallbackQueryHandler(sync_sheets_callback, pattern="^sync_sheets_manual$"),
            CallbackQueryHandler(clear_post_data_callback, pattern="^clear_post_data$"),
        ],
        states={
            config.CHANNEL_MENU_STATE: [
                CallbackQueryHandler(add_or_publish_start, pattern="^add_or_publish_start$"),
                CallbackQueryHandler(sync_sheets_callback, pattern="^sync_sheets_manual$"),
                CallbackQueryHandler(clear_post_data_callback, pattern="^clear_post_data$"),
            ],
            config.ADD_OR_PUBLISH_GET_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_or_publish_get_vin)],
            config.ADD_OR_PUBLISH_GET_PHOTOS: [
                MessageHandler(filters.PHOTO, add_or_publish_get_photos_handler),
                MessageHandler(filters.VIDEO, add_or_publish_get_video_handler),
                CallbackQueryHandler(add_or_publish_done_media, pattern="^done_media$")
            ],
            config.ADD_OR_PUBLISH_MANUAL_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_or_publish_manual_model)],
            config.ADD_OR_PUBLISH_MANUAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_or_publish_manual_price)],
            config.ADD_OR_PUBLISH_MANUAL_MILEAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_or_publish_manual_mileage)], # Now for modification
            config.ADD_OR_PUBLISH_ASK_CONDITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_or_publish_ask_condition)],
            config.ADD_OR_PUBLISH_ASK_LOCATION: [CallbackQueryHandler(add_or_publish_ask_location, pattern="^set_location_")],
            config.ADD_OR_PUBLISH_CONFIRM_AND_PUBLISH: [CallbackQueryHandler(add_or_publish_publication_callback, pattern="^publish_final$|^cancel_action$")]
        },
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_command)],
        per_message=False,
    )

def get_location_keyboard() -> InlineKeyboardMarkup:
    """Returns the keyboard for selecting car location/status."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ В НАЯВНОСТІ", callback_data="set_location_✅ В НАЯВНОСТІ")],
        [InlineKeyboardButton("🇺🇸 В ДОРОЗІ З США", callback_data="set_location_🇺🇸 В ДОРОЗІ З США")],
        [InlineKeyboardButton("🇨🇳 В ДОРОЗІ З КИТАЮ", callback_data="set_location_🇨🇳 В ДОРОЗІ З КИТАЮ")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="set_location_cancel")]
    ])

