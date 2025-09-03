# -*- coding: utf-8 -*-
# handlers/ria.py

import logging
import datetime
import asyncio
import requests
import html
from functools import partial
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler, Application
)
from telegram.error import BadRequest, Forbidden

import config
from utils.helpers import escape_markdown_v2
from utils.sync import synchronize_working_sheets
from .start import cancel_command, start_command
from .keyboards import get_employee_keyboard
# Імпортуємо всі необхідні функції з channel.py
from .channel import (
    add_or_publish_get_photos_handler, add_or_publish_done_media,
    add_or_publish_get_video_handler, add_or_publish_manual_model,
    add_or_publish_ask_condition, add_or_publish_publication_callback,
    add_or_publish_ask_location, get_location_keyboard, handle_completion_choice,
    ask_for_missing_field, get_missing_field_value, check_and_prompt_for_completeness,
    add_or_publish_confirm_and_publish_message, add_or_publish_media_type_callback
)

logger = logging.getLogger(__name__)
gs_manager = None

# --- Допоміжні функції для роботи з API ---

async def make_ria_request(url: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Робить безпечний запит до API Auto.RIA з обробкою лімітів."""
    max_retries = 3
    base_wait_time = 60
    for i in range(max_retries):
        try:
            await asyncio.sleep(1.2)
            response = requests.get(url)
            if response.status_code == 429:
                wait_time = base_wait_time * (i + 1)
                logger.warning(f"RIA API rate limit hit. Retry {i+1}/{max_retries}. Waiting {wait_time}s.")
                if context and chat_id:
                    await context.bot.send_message(chat_id=chat_id, text=f"⏳ API Auto.RIA перевантажено. Спроба через {wait_time // 60} хв.")
                await asyncio.sleep(wait_time)
                continue
            if response.status_code == 404:
                logger.warning(f"Запит до {url} повернув 404 Not Found.")
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Помилка запиту до API {url}: {e}")
            return None
    logger.critical(f"Не вдалося виконати запит до {url} після {max_retries} спроб.")
    if context and chat_id:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Не вдалося отримати дані з Auto.RIA. Спробуйте пізніше.")
    return None

# --- Нові допоміжні функції для публікації чернеток ---

async def ria_draft_skip_to_condition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Викликається після додавання фото до чернетки RIA через /done.
    Пропускає ручне введення даних і запитує стан авто.
    """
    if not context.user_data.get('post_data', {}).get(config.POST_SHEET_COLS['photos']):
        await update.message.reply_text("Ви не додали жодного фото. Надішліть хоча б одне, або /cancel.")
        return config.ADD_OR_PUBLISH_GET_PHOTOS

    await update.message.reply_text("✅ Фото додано. Тепер опишіть стан авто:")
    return config.RIA_DRAFT_ASK_CONDITION

async def ria_draft_get_video_and_ask_condition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Викликається після додавання відео до чернетки RIA.
    Пропускає ручне введення даних і запитує стан авто.
    """
    if update.message.video:
        context.user_data['post_data'][config.POST_SHEET_COLS['photos']] = update.message.video.file_id
        await update.message.reply_text("✅ Відео додано. Тепер опишіть стан авто:")
        return config.RIA_DRAFT_ASK_CONDITION
    else:
        await update.message.reply_text("Будь ласка, надішліть відеофайл.")
        return config.ADD_OR_PUBLISH_GET_VIDEO

async def ria_draft_get_condition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує стан авто для чернетки і переходить до попереднього перегляду."""
    new_condition = update.message.text.strip()
    context.user_data['post_data'][config.POST_SHEET_COLS['condition']] = new_condition
    await update.message.reply_text("✅ Стан оновлено. Готую попередній перегляд...")
    return await add_or_publish_confirm_and_publish_message(update, context)


# --- Меню та логіка додавання авто з RIA ---

async def ria_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Відображає меню для роботи з Auto.RIA."""
    from .cabinet import show_cabinet_menu
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    keyboard = [
        [InlineKeyboardButton("➕ Додати авто за ID з RIA", callback_data="ria_add_by_id_start")],
        [InlineKeyboardButton("📝 Опублікувати мої чернетки", callback_data="ria_publish_my_draft_start")],
        [InlineKeyboardButton("🔄 Оновити термін дії оголошення", callback_data="ria_renew_start")],
        [InlineKeyboardButton("🗂️ Перевірити архів та сповіщення", callback_data="ria_check_full")],
        [InlineKeyboardButton("🔍 Синхронізувати з RIA", callback_data="ria_sync_start")]
    ]

    if user_id == config.OWNER_ID:
        keyboard.append([InlineKeyboardButton("📋 Опублікувати всі чернетки (Власник)", callback_data="ria_publish_draft_start")])

    keyboard.append([InlineKeyboardButton("⬅️ Назад в кабінет", callback_data="back_to_main_cabinet")])

    if query.data == "back_to_main_cabinet":
        return await show_cabinet_menu(update, context)

    await query.message.edit_text("🤖 Оберіть дію для роботи з Auto.ria:", reply_markup=InlineKeyboardMarkup(keyboard))
    return config.RIA_MENU_STATE

async def ria_add_by_id_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запитує ID оголошення для додавання."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введіть ID оголошення з Auto.RIA:")
    return config.RIA_ADD_GET_ID

async def ria_add_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує ID, перевіряє інформацію та дублікати."""
    try:
        auto_id = int(update.message.text.strip())
    except (ValueError, TypeError):
        await update.message.reply_text("❌ Неправильний формат. Введіть лише цифровий ID.")
        return config.RIA_ADD_GET_ID

    await update.message.reply_text(f"🔍 Шукаю інформацію для ID: {auto_id}...")
    info_url = f"https://developers.ria.com/auto/info?api_key={config.AUTORIA_API_KEY}&auto_id={auto_id}"
    ad_info = await make_ria_request(info_url, context=context, chat_id=update.effective_chat.id)

    if not ad_info:
        await update.message.reply_text("❌ Не вдалося знайти оголошення з таким ID.")
        return ConversationHandler.END

    vin_code = ad_info.get('VIN')
    if vin_code:
        await update.message.reply_text(f"Перевіряю VIN `{vin_code}` на дублікати...", parse_mode='Markdown')
        existing_car = await gs_manager.find_car_by_vin(vin_code, [config.SHEET_NAMES['published_posts']])
        if existing_car and existing_car['record'].get(config.POST_SHEET_COLS['status']) not in ['sold', 'archived']:
            record = existing_car['record']
            status = record.get(config.POST_SHEET_COLS['status'], 'невідомо')
            model = record.get(config.POST_SHEET_COLS['model'], 'це авто')

            message_text = (
                f"❌ *Помилка\\!* Авто з таким VIN вже існує в системі\\.\n\n"
                f"🚗 *Авто:* {escape_markdown_v2(model)}\n"
                f"🚦 *Статус:* `{status}`\n\n"
                f"Додавання дублікату з RIA скасовано\\. Ви можете керувати існуючим авто через відповідне меню\\."
            )
            await update.message.reply_text(message_text, parse_mode='MarkdownV2')
            return ConversationHandler.END

    status_id = ad_info.get('stateData', {}).get('statusId', 0)
    is_active = (status_id == 1)

    if not is_active:
        logger.warning(f"RIA API повернуло неактивний статус ({status_id}) для ID {auto_id}.")
        expire_date_str = ad_info.get('expireDate')
        if expire_date_str:
            try:
                expire_datetime = datetime.datetime.strptime(expire_date_str, '%Y-%m-%d %H:%M:%S')
                if expire_datetime > datetime.datetime.now():
                    logger.info(f"Оголошення {auto_id} має неактивний статус, але дата закінчення в майбутньому. Вважаємо його активним.")
                    is_active = True
            except (ValueError, TypeError):
                 logger.warning(f"Не вдалося перевірити expireDate для {auto_id}, значення: {expire_date_str}")

    if not is_active:
        status_name = ad_info.get('stateData', {}).get('status', 'невідомий')
        await update.message.reply_text(f"❌ Оголошення не є активним (статус: {status_name}). Додавання неможливе.")
        return ConversationHandler.END

    context.user_data['ad_info'] = ad_info
    auto_data = ad_info.get('autoData', {})

    mark_name = ad_info.get('markName', '')
    model_name = ad_info.get('modelName', '')
    year = auto_data.get('year')
    model = f"{mark_name} {model_name} {year}" if year else f"{mark_name} {model_name}"

    add_date_str = ad_info.get('addDate', '')
    expire_date_str = ad_info.get('expireDate', '')
    try:
        add_date_dt = datetime.datetime.strptime(add_date_str, '%Y-%m-%d %H:%M:%S')
        expire_date_dt = datetime.datetime.strptime(expire_date_str, '%Y-%m-%d %H:%M:%S')
        context.user_data['add_date_dt'] = add_date_dt
        context.user_data['expire_date_dt'] = expire_date_dt
        add_date_formatted = add_date_dt.strftime('%d.%m.%Y %H:%M')
        expire_date_formatted = expire_date_dt.strftime('%d.%m.%Y %H:%M')
    except (ValueError, TypeError):
        context.user_data['add_date_dt'] = None
        context.user_data['expire_date_dt'] = None
        add_date_formatted = "Невідомо"
        expire_date_formatted = "Невідомо"

    message_text = (f"<b>Знайдено активне авто:</b>\n\n"
                    f"<b>Модель:</b> {model}\n"
                    f"<b>Ціна:</b> {ad_info.get('USD', 'N/A')} USD\n"
                    f"<b>VIN:</b> <code>{ad_info.get('VIN', 'N/A')}</code>\n"
                    f"<b>Опубліковано:</b> {add_date_formatted}\n"
                    f"<b>Публікація до:</b> {expire_date_formatted}\n\n"
                    f"Прив'язати це авто до вас ({update.effective_user.full_name})?")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Так, прив'язати", callback_data="assign_yes")],
        [InlineKeyboardButton("❌ Ні, залишити вільним", callback_data="assign_no")],
        [InlineKeyboardButton("⬅️ Скасувати", callback_data="cancel_action")]
    ])

    await update.message.reply_text(message_text, parse_mode='HTML', reply_markup=keyboard)
    return config.RIA_ADD_CONFIRM_AND_ASSIGN

async def ria_add_save_drafts_and_ask_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Зберігає чернетки в таблицях і питає, чи публікувати пост."""
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_action":
        await query.edit_message_text("Скасовано.")
        return ConversationHandler.END

    ad_info = context.user_data.get('ad_info')
    if not ad_info:
        await query.edit_message_text("Помилка: дані про авто втрачено.")
        return ConversationHandler.END

    assign_to_user = query.data == 'assign_yes'
    employee_id = query.from_user.id if assign_to_user else ''

    await query.edit_message_text("💾 Зберігаю чернетку в базі даних...")

    auto_data = ad_info.get('autoData', {})
    vin = ad_info.get('VIN')
    mark_name = ad_info.get('markName', '')
    model_name = ad_info.get('modelName', '')
    year = auto_data.get('year')
    model = f"{mark_name} {model_name} {year}" if year else f"{mark_name} {model_name}"
    modification = auto_data.get('fuelName', '')

    technical_condition_data = ad_info.get('technicalCondition') or {}
    condition_annotation = technical_condition_data.get('annotation', 'Ввести менеджером')

    post_draft_data = {
        config.POST_SHEET_COLS['vin']: vin,
        config.POST_SHEET_COLS['emp_id']: employee_id,
        config.POST_SHEET_COLS['status']: 'draft_ria',
        config.POST_SHEET_COLS['model']: model,
        config.POST_SHEET_COLS['price']: ad_info.get('USD', '0'),
        config.POST_SHEET_COLS['modification']: modification,
        config.POST_SHEET_COLS['mileage']: f"{auto_data.get('raceInt', 0) * 1000} км",
        config.POST_SHEET_COLS['drivetrain']: auto_data.get('driveName', ''),
        config.POST_SHEET_COLS['gearbox']: auto_data.get('gearboxName', ''),
        config.POST_SHEET_COLS['condition']: condition_annotation,
        config.POST_SHEET_COLS['status_prefix']: '✅ В НАЯВНОСТІ',
        config.POST_SHEET_COLS['ria_auto_id']: ad_info.get('autoId', ''),
        config.POST_SHEET_COLS['ria_link']: ad_info.get('linkToView', ''),
        config.POST_SHEET_COLS['location']: ''
    }
    await gs_manager.add_row(config.SHEET_NAMES['published_posts'], post_draft_data, config.POST_SHEET_HEADER_ORDER)
    context.user_data['vin_of_new_draft'] = vin

    expire_datetime = context.user_data.get('expire_date_dt')
    autoria_ad_record = {**post_draft_data, **{
        config.POST_SHEET_COLS['date']: expire_datetime.isoformat() if expire_datetime else datetime.datetime.now().isoformat(),
        config.POST_SHEET_COLS['status']: 'active',
        config.POST_SHEET_COLS['notify_date']: 'none',
    }}
    await gs_manager.add_row(config.SHEET_NAMES['autoria_ads'], autoria_ad_record, config.POST_SHEET_HEADER_ORDER)
    logger.info(f"Added RIA ad {ad_info.get('autoId', 'N/A')} to tracking sheet.")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Так, опублікувати", callback_data="publish_now_yes")],
        [InlineKeyboardButton("❌ Ні, залишити чернеткою", callback_data="publish_now_no")]
    ])
    await query.edit_message_text(
        "✅ Чернетку створено.\n\n"
        "Опублікувати цей автомобіль в каналі зараз?",
        reply_markup=keyboard
    )
    return config.RIA_ADD_ASK_PUBLISH

async def ria_add_handle_publish_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє вибір користувача щодо негайної публікації."""
    query = update.callback_query
    await query.answer()
    
    vin_to_publish = context.user_data.get('vin_of_new_draft')
    if not vin_to_publish:
        await query.edit_message_text("Помилка: VIN чернетки втрачено.")
        return ConversationHandler.END

    post_info = await gs_manager.find_car_by_vin(vin_to_publish, [config.SHEET_NAMES['published_posts']])
    if not post_info:
        await query.edit_message_text("Помилка: не вдалося знайти щойно створену чернетку.")
        return ConversationHandler.END

    context.user_data['post_data'] = post_info['record']
    context.user_data['post_row_index'] = post_info['row_index']
    
    if query.data == "publish_now_yes":
        context.user_data['ria_prefilled'] = True
        await query.edit_message_text("Добре. Тепер надішліть від 1 до 10 фото. Коли закінчите, натисніть /done.")
        return config.ADD_OR_PUBLISH_GET_PHOTOS
    else: # publish_now_no
        await query.edit_message_text("Гаразд, залишаю чернеткою.\n\nТепер вкажіть, де знаходиться авто:", reply_markup=get_location_keyboard())
        return config.RIA_ADD_GET_LOCATION

async def ria_add_set_location_and_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Встановлює локацію для чернетки і завершує розмову."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_action":
        await query.edit_message_text("Створення чернетки завершено без вказання локації.")
        asyncio.create_task(synchronize_working_sheets(gs_manager))
        return ConversationHandler.END

    location = query.data.replace("set_location_", "")
    
    post_info_record = context.user_data.get('post_data')
    post_info_row_index = context.user_data.get('post_row_index')

    if not post_info_record or not post_info_row_index:
        await query.edit_message_text("❌ Помилка: дані про чернетку втрачено.")
        return ConversationHandler.END

    await query.edit_message_text(f"Оновлюю розташування на '{location}'...")
    
    post_info_record[config.POST_SHEET_COLS['location']] = location
    
    success = await gs_manager.update_row(
        config.SHEET_NAMES['published_posts'],
        post_info_row_index,
        post_info_record,
        config.POST_SHEET_HEADER_ORDER
    )

    if success:
        await query.edit_message_text(f"✅ Успішно! Чернетку створено та розміщено в '{location}'.")
        asyncio.create_task(synchronize_working_sheets(gs_manager))
    else:
        await query.edit_message_text("❌ Помилка при оновленні розташування в таблиці.")
        
    return ConversationHandler.END

async def ria_publish_draft_start(update: Update, context: ContextTypes.DEFAULT_TYPE, my_drafts_only: bool = False) -> int:
    """Показує список чернеток для публікації."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    await query.edit_message_text("🔍 Шукаю чернетки з Auto.ria...")

    all_posts = await gs_manager.get_all_records(config.SHEET_NAMES['published_posts'], expected_headers=config.POST_SHEET_HEADER_ORDER)
    drafts = [p for p in all_posts if p.get(config.POST_SHEET_COLS['status']) == 'draft_ria']

    if my_drafts_only:
        drafts = [d for d in drafts if str(d.get(config.POST_SHEET_COLS['emp_id'])) == str(user_id)]

    if not drafts:
        message = "😕 Не знайдено ваших особистих чернеток." if my_drafts_only else "😕 Не знайдено жодної чернетки."
        await query.edit_message_text(message)
        return ConversationHandler.END

    buttons = []
    for draft in drafts:
        model = draft.get(config.POST_SHEET_COLS['model'], 'Без назви')
        vin = draft.get(config.POST_SHEET_COLS['vin'], 'N/A')
        buttons.append([InlineKeyboardButton(f"{model} ({vin[-6:]})", callback_data=f"publish_ria_draft_{vin}")])

    buttons.append([InlineKeyboardButton("❌ Скасувати", callback_data="cancel_action")])
    await query.edit_message_text("Оберіть авто для публікації:", reply_markup=InlineKeyboardMarkup(buttons))
    return config.RIA_PUBLISH_DRAFT_SELECT

async def ria_publish_draft_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запускає процес публікації обраної чернетки, питаючи про тип медіа."""
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_action":
        await query.edit_message_text("Скасовано.")
        return ConversationHandler.END

    vin_to_publish = query.data.replace("publish_ria_draft_", "")
    post_info = await gs_manager.find_car_by_vin(vin_to_publish, [config.SHEET_NAMES['published_posts']])

    if not post_info or post_info['record'].get(config.POST_SHEET_COLS['status']) != 'draft_ria':
        await query.edit_message_text("Помилка: чернетку не знайдено.", reply_markup=get_employee_keyboard(query.from_user.id))
        return ConversationHandler.END

    context.user_data['post_data'] = post_info['record']
    context.user_data['post_row_index'] = post_info['row_index']

    if not context.user_data['post_data'].get(config.POST_SHEET_COLS['emp_id']):
        context.user_data['post_data'][config.POST_SHEET_COLS['emp_id']] = query.from_user.id
        await query.message.reply_text(f"✅ Авто '{post_info['record'].get(config.POST_SHEET_COLS['model'])}' тепер прив'язано до вас.")
        await gs_manager.update_row(config.SHEET_NAMES['published_posts'], post_info['row_index'], context.user_data['post_data'], config.POST_SHEET_HEADER_ORDER)

    # Запитуємо про тип медіа
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Фото (до 10 шт.)", callback_data="media_type_photo")],
        [InlineKeyboardButton("🎬 Відео (1 шт.)", callback_data="media_type_video")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_action")]
    ])
    await query.edit_message_text("Що ви хочете додати до оголошення?", reply_markup=keyboard)
    return config.ADD_OR_PUBLISH_MEDIA_TYPE_CHOICE

# --- Щоденні завдання та оновлення ---

async def _archive_expired_logic(application: Application) -> str:
    """Основна логіка перевірки та архівації оголошень. Повертає звіт."""
    logger.info("Running archival check logic...")
    if not gs_manager:
        return "Помилка: немає зв'язку з Google Sheets."
    
    archived_count = 0
    try:
        all_tracked_ads = await gs_manager.get_all_records(config.SHEET_NAMES['autoria_ads'], expected_headers=config.POST_SHEET_HEADER_ORDER)
        now = datetime.datetime.now()
        
        if not all_tracked_ads:
            return "✅ Перевірку завершено. Оголошень для відстеження не знайдено."
            
        for i, ad in enumerate(list(all_tracked_ads)):
            row_index = i + 2
            if ad.get(config.POST_SHEET_COLS['status']) != 'active':
                continue
            
            expiry_date_str = ad.get(config.POST_SHEET_COLS['date'])
            if not expiry_date_str:
                continue

            try:
                expiry_datetime = datetime.datetime.fromisoformat(expiry_date_str)
                if expiry_datetime < now:
                    vin = ad.get(config.POST_SHEET_COLS['vin'], 'N/A')
                    model = ad.get(config.POST_SHEET_COLS['model'], 'Авто')
                    link = ad.get(config.POST_SHEET_COLS['ria_link'], '')
                    if link and not link.startswith('http'):
                        link = f"https://auto.ria.com{link}"

                    safe_model = escape_markdown_v2(model)
                    safe_vin = escape_markdown_v2(vin)

                    message = (f"🗂️ *В архіві*\nОголошення для *{safe_model}* \\(VIN: `{safe_vin}`\\) "
                               f"переміщено в архів \\(термін дії минув\\)\\.")
                    if link:
                        message += f"\n[Відновити оголошення]({link})"

                    await application.bot.send_message(chat_id=config.RIA_ARCHIVE_CHANNEL_ID, text=message, parse_mode='MarkdownV2')
                    
                    ad[config.POST_SHEET_COLS['status']] = 'archived'
                    await gs_manager.update_row(config.SHEET_NAMES['autoria_ads'], row_index, ad, config.POST_SHEET_HEADER_ORDER)
                    
                    logger.info(f"Оголошення {ad.get(config.POST_SHEET_COLS['ria_auto_id'])} (VIN: {vin}) позначено як 'archived'.")
                    archived_count += 1
            except (ValueError, TypeError, BadRequest, Forbidden) as e:
                logger.warning(f"Помилка обробки оголошення {ad.get(config.POST_SHEET_COLS['ria_auto_id'])}: {e}")
    except Exception as e:
        logger.error(f"Критична помилка під час логіки архівації: {e}", exc_info=True)
        return "Сталася критична помилка під час перевірки."

    if archived_count > 0:
        return f"✅ Перевірку завершено. Заархівовано {archived_count} оголошень."
    else:
        return "✅ Перевірку завершено. Застарілих оголошень не знайдено."

async def archive_expired_ads_by_date(application: Application):
    """Автоматично архівує оголошення, термін дії яких минув (запланована задача)."""
    logger.info("Запуск архівації оголошень за датою...")
    await _archive_expired_logic(application)

async def _check_upcoming_expiry_logic(application: Application) -> str:
    """Основна логіка перевірки оголошень, що скоро закінчуються. Повертає звіт."""
    logger.info("Running upcoming expiry check logic...")
    if not gs_manager:
        return "Помилка: немає зв'язку з Google Sheets."
    
    sent_24h = 0
    sent_12h = 0
    try:
        all_tracked_ads = await gs_manager.get_all_records(config.SHEET_NAMES['autoria_ads'], expected_headers=config.POST_SHEET_HEADER_ORDER)
        now = datetime.datetime.now()
        
        if not all_tracked_ads:
            return "Сповіщень немає: оголошень для відстеження не знайдено."
            
        for i, ad in enumerate(all_tracked_ads):
            row_index = i + 2
            if ad.get(config.POST_SHEET_COLS['status']) != 'active': continue
            expiry_date_str = ad.get(config.POST_SHEET_COLS['date'])
            if not expiry_date_str: continue
            try:
                expiry_datetime = datetime.datetime.fromisoformat(expiry_date_str)
                time_left = expiry_datetime - now
                vin = ad.get(config.POST_SHEET_COLS['vin'], 'N/A')
                link = ad.get(config.POST_SHEET_COLS['ria_link'], '')
                if link and not link.startswith('http'):
                    link = f"https://auto.ria.com{link}"

                notification_status = ad.get(config.POST_SHEET_COLS['notify_date'], 'none')
                manager_id = ad.get(config.POST_SHEET_COLS['emp_id'])
                model = ad.get(config.POST_SHEET_COLS['model'], 'Авто')
                auto_id = ad.get(config.POST_SHEET_COLS['ria_auto_id'])
                message, notification_level, keyboard = None, None, None

                safe_model = escape_markdown_v2(model)
                safe_vin = escape_markdown_v2(vin)

                if auto_id and link:
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Оновити на RIA та перевірити", url=link)],
                        [InlineKeyboardButton("✅ Я вже оновив, перевір дату", callback_data=f"ria_renew_{auto_id}")]
                    ])

                if 12 * 3600 < time_left.total_seconds() <= 24 * 3600 and notification_status not in ['sent_24h', 'sent_12h']:
                    message = (f"🔔 *Увага\\!* \\~24 години\nОголошення для *{safe_model}* \\(VIN: `{safe_vin}`\\) буде в архіві завтра\\.\n👉 [Перейти до оголошення]({link})")
                    notification_level = 'sent_24h'
                    sent_24h += 1
                elif 0 < time_left.total_seconds() <= 12 * 3600 and notification_status != 'sent_12h':
                    message = (f"⏳ *Увага\\!* \\~12 годин\nОголошення для *{safe_model}* \\(VIN: `{safe_vin}`\\) буде в архіві сьогодні\\.\n👉 [Перейти до оголошення]({link})")
                    notification_level = 'sent_12h'
                    sent_12h += 1

                if message and notification_level:
                    await application.bot.send_message(chat_id=config.RIA_ARCHIVE_CHANNEL_ID, text=message, parse_mode='MarkdownV2', reply_markup=keyboard)
                    if manager_id:
                        try:
                            await application.bot.send_message(chat_id=int(manager_id), text=message, parse_mode='MarkdownV2', reply_markup=keyboard)
                        except (BadRequest, Forbidden) as e:
                            logger.warning(f"Не вдалося надіслати сповіщення менеджеру {manager_id}: {e}")

                    ad[config.POST_SHEET_COLS['notify_date']] = notification_level
                    await gs_manager.update_row(config.SHEET_NAMES['autoria_ads'], row_index, ad, config.POST_SHEET_HEADER_ORDER)
            except (ValueError, TypeError, BadRequest, Forbidden) as e:
                logger.warning(f"Помилка обробки оголошення {ad.get(config.POST_SHEET_COLS['ria_auto_id'], 'N/A')}: {e}")
    except Exception as e:
        logger.error(f"Критична помилка під час перевірки терміну дії оголошень: {e}", exc_info=True)
        return "Сталася критична помилка під час перевірки сповіщень."
    
    return f"Надіслано сповіщень:\n • ~24 години: {sent_24h}\n • ~12 годин: {sent_12h}"


async def check_upcoming_expiry(application: Application):
    """Щоденна перевірка оголошень, термін дії яких скоро закінчується (запланована задача)."""
    logger.info("Запуск перевірки оголошень, що скоро потрапять в архів...")
    await _check_upcoming_expiry_logic(application)

async def ria_manual_full_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник для ручного запуску повної перевірки (архів + сповіщення)."""
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("⏳ Розпочинаю повну перевірку RIA...")
    
    archive_report = await _archive_expired_logic(context.application)
    expiry_report = await _check_upcoming_expiry_logic(context.application)
    
    # ВИПРАВЛЕНО: Додано час для унікальності звіту та виправлено UX
    now_str = datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    full_report = (
        f"<b>Звіт станом на {now_str}</b>\n\n"
        f"{archive_report}\n\n"
        f"{expiry_report}"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад до меню RIA", callback_data="back_to_ria_menu")]
    ])
    
    await query.message.edit_text(full_report, parse_mode='HTML', reply_markup=keyboard)

async def perform_ria_renewal(update: Update, context: ContextTypes.DEFAULT_TYPE, auto_id: int, ad_to_update: dict, row_index_to_update: int):
    """Виконує логіку оновлення дати оголошення."""
    target_message = update.callback_query.message if update.callback_query else update.message

    info_url = f"https://developers.ria.com/auto/info?api_key={config.AUTORIA_API_KEY}&auto_id={auto_id}"
    ad_info = await make_ria_request(info_url, context=context, chat_id=update.effective_chat.id)

    if not ad_info:
        await target_message.reply_text("❌ Не вдалося отримати оновлену інформацію з Auto.RIA. Можливо, оголошення видалено.")
        return

    new_expire_date_str = ad_info.get('expireDate')
    if not new_expire_date_str:
        await target_message.reply_text("❌ Помилка: RIA API не повернуло нову дату закінчення.")
        return

    try:
        new_expire_datetime = datetime.datetime.strptime(new_expire_date_str, '%Y-%m-%d %H:%M:%S')
        ad_to_update[config.POST_SHEET_COLS['date']] = new_expire_datetime.isoformat()
        ad_to_update[config.POST_SHEET_COLS['notify_date']] = 'renewed'
        ad_to_update[config.POST_SHEET_COLS['status']] = 'active'
        
        if not ad_to_update.get(config.POST_SHEET_COLS['ria_auto_id']):
             ad_to_update[config.POST_SHEET_COLS['ria_auto_id']] = ad_info.get('autoId', '')
             logger.info(f"Healed missing auto_id for VIN {ad_to_update.get(config.POST_SHEET_COLS['vin'])}")

        success = await gs_manager.update_row(
            config.SHEET_NAMES['autoria_ads'], row_index_to_update, ad_to_update, config.POST_SHEET_HEADER_ORDER
        )

        if success:
            new_date_formatted = new_expire_datetime.strftime('%d.%m.%Y %H:%M')
            success_text = f"✅ Успішно оновлено!\nНова дата закінчення: *{new_date_formatted}*"
            if update.callback_query:
                await update.callback_query.edit_message_text(update.callback_query.message.text + f"\n\n*{success_text}*", parse_mode='Markdown')
            else:
                await target_message.reply_text(success_text, parse_mode='Markdown')
        else:
            await target_message.reply_text("❌ Помилка при оновленні даних в таблиці.")
    except (ValueError, TypeError):
        await target_message.reply_text(f"❌ Помилка: Не вдалося розпізнати формат нової дати: `{new_expire_date_str}`.")

async def ria_renew_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопки 'Я оновив'."""
    query = update.callback_query
    await query.answer("Перевіряю...")
    try:
        auto_id = int(query.data.split('_')[-1])
        
        all_ads = await gs_manager.get_all_records(config.SHEET_NAMES['autoria_ads'], expected_headers=config.POST_SHEET_HEADER_ORDER)
        ad_to_update, row_index_to_update = None, -1
        for i, ad in enumerate(all_ads):
            if str(ad.get(config.POST_SHEET_COLS['ria_auto_id'])) == str(auto_id):
                ad_to_update, row_index_to_update = ad, i + 2
                break
        
        if not ad_to_update:
            await query.edit_message_text("Помилка: Не вдалося знайти це оголошення в базі даних для оновлення.")
            return

        await perform_ria_renewal(update, context, auto_id, ad_to_update, row_index_to_update)

    except (ValueError, IndexError):
        await query.edit_message_text("Помилка: Неправильний ID авто.")

async def ria_renew_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запускає процес ручного оновлення дати."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введіть ID оголошення, VIN-код або посилання на нього для оновлення:")
    return config.RIA_RENEW_GET_ID

async def ria_renew_find_and_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Знаходить оголошення за запитом і оновлює його."""
    query_text = update.message.text.strip()
    await update.message.reply_text(f"🔍 Шукаю оголошення за запитом `{query_text}`...", parse_mode='Markdown')

    ad_to_update, row_index_to_update = None, -1
    
    # Спочатку шукаємо в трекері
    all_ads = await gs_manager.get_all_records(config.SHEET_NAMES['autoria_ads'], expected_headers=config.POST_SHEET_HEADER_ORDER)
    if all_ads:
        for i, ad in enumerate(all_ads):
            ria_auto_id = str(ad.get(config.POST_SHEET_COLS['ria_auto_id'], ''))
            vin_code = str(ad.get(config.POST_SHEET_COLS['vin'], '')).upper()
            ria_link = ad.get(config.POST_SHEET_COLS['ria_link'], '')

            if (ria_auto_id == query_text or
                vin_code == query_text.upper() or
                (f"_{query_text}.html" in ria_link and query_text.isdigit())):
                ad_to_update, row_index_to_update = ad, i + 2
                break
    
    if ad_to_update:
        auto_id_str = str(ad_to_update.get(config.POST_SHEET_COLS['ria_auto_id'], ''))
        
        if not auto_id_str or not auto_id_str.isdigit():
            context.user_data['ad_to_update_info'] = {'record': ad_to_update, 'row_index': row_index_to_update}
            await update.message.reply_text(
                "Знайшов це авто у вашому трекері, але ID оголошення відсутній або некоректний.\n\n"
                "Будь ласка, введіть **ID оголошення з Auto.RIA**, щоб прив'язати його та оновити."
            )
            return config.RIA_RENEW_GET_MISSING_ID
        
        auto_id = int(auto_id_str)
        await update.message.reply_text(f"Знайдено в трекері! Оновлюю дані для ID: {auto_id}...")
        await perform_ria_renewal(update, context, auto_id, ad_to_update, row_index_to_update)
        return ConversationHandler.END

    post_info = await gs_manager.find_car_by_vin(query_text, [config.SHEET_NAMES['published_posts']])
    if post_info:
        context.user_data['post_to_link'] = post_info
        await update.message.reply_text(
            "Знайшов це авто у вашій основній базі, але воно не відстежується.\n\n"
            "Будь ласка, введіть **ID оголошення з Auto.RIA**, щоб прив'язати його до цього авто та оновити."
        )
        return config.RIA_LINK_GET_ID

    await update.message.reply_text("❌ Оголошення з таким ID, VIN або посиланням не знайдено в жодній базі.")
    return ConversationHandler.END

async def ria_renew_get_missing_id_and_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує відсутній ID RIA для існуючого запису в трекері та оновлює його."""
    try:
        auto_id = int(update.message.text.strip())
    except (ValueError, TypeError):
        await update.message.reply_text("❌ Неправильний формат. Введіть лише цифровий ID.")
        return config.RIA_RENEW_GET_MISSING_ID

    update_info = context.user_data.get('ad_to_update_info')
    if not update_info:
        await update.message.reply_text("Помилка: дані про авто для оновлення втрачено.")
        return ConversationHandler.END

    ad_to_update = update_info['record']
    row_index_to_update = update_info['row_index']
    
    ad_to_update[config.POST_SHEET_COLS['ria_auto_id']] = auto_id
    
    await update.message.reply_text(f"Прив'язую ID {auto_id} та оновлюю дані...")
    await perform_ria_renewal(update, context, auto_id, ad_to_update, row_index_to_update)

    context.user_data.clear()
    return ConversationHandler.END

async def ria_link_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує ID для прив'язки, створює запис в трекері та оновлює."""
    try:
        auto_id = int(update.message.text.strip())
    except (ValueError, TypeError):
        await update.message.reply_text("❌ Неправильний формат. Введіть лише цифровий ID.")
        return config.RIA_LINK_GET_ID
    
    post_info = context.user_data.get('post_to_link')
    if not post_info:
        await update.message.reply_text("Помилка: дані про авто для прив'язки втрачено.")
        return ConversationHandler.END

    await update.message.reply_text(f"Прив'язую ID {auto_id} до авто та оновлюю дані...")
    
    new_ad_record = post_info['record'].copy()
    new_ad_record[config.POST_SHEET_COLS['ria_auto_id']] = auto_id
    
    await gs_manager.add_row(config.SHEET_NAMES['autoria_ads'], new_ad_record, config.POST_SHEET_HEADER_ORDER)

    newly_added_ad_info = await gs_manager.find_car_by_vin(new_ad_record[config.POST_SHEET_COLS['vin']], [config.SHEET_NAMES['autoria_ads']])
    
    if newly_added_ad_info:
        await perform_ria_renewal(update, context, auto_id, newly_added_ad_info['record'], newly_added_ad_info['row_index'])
    else:
        await update.message.reply_text("❌ Помилка: не вдалося знайти щойно створений запис для оновлення.")

    return ConversationHandler.END

async def ria_sync_with_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Compares 'Published Posts' with 'AutoRIA_Ads' and reports discrepancies.
    """
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Розпочинаю синхронізацію, це може зайняти хвилину...")

    if not gs_manager:
        await query.edit_message_text("❌ Помилка: Немає зв'язку з Google Sheets.")
        return

    try:
        posts_records = await gs_manager.get_all_records(config.SHEET_NAMES['published_posts']) or []
        ria_ads_records = await gs_manager.get_all_records(config.SHEET_NAMES['autoria_ads']) or []

        posts_vins = {
            p.get(config.POST_SHEET_COLS['vin']).strip().upper()
            for p in posts_records
            if p.get(config.POST_SHEET_COLS['vin']) and p.get(config.POST_SHEET_COLS['status']) in ('active', 'draft_ria')
        }

        ria_vins = {
            ad.get(config.POST_SHEET_COLS['vin']).strip().upper()
            for ad in ria_ads_records
            if ad.get(config.POST_SHEET_COLS['vin']) and ad.get(config.POST_SHEET_COLS['status']) == 'active'
        }

        missing_in_ria_vins = posts_vins - ria_vins

        report_lines = []
        keyboard = None
        if not missing_in_ria_vins:
            report_lines.append("✅ <b>Все синхронізовано!</b>\n\nВсі активні пости відстежуються в аркуші 'AutoRIA_Ads'.")
        else:
            report_lines.append(f"⚠️ <b>Знайдено розбіжності ({len(missing_in_ria_vins)} авто):</b>\n\n"
                                "Ці авто є в 'Опубліковані Пости', але відсутні в 'AutoRIA_Ads'. "
                                "Можливо, їх варто додати для відстеження терміну дії оголошення на RIA.\n")

            vin_to_model_map = {
                p.get(config.POST_SHEET_COLS['vin']).strip().upper(): p.get(config.POST_SHEET_COLS['model'], 'Без назви')
                for p in posts_records if p.get(config.POST_SHEET_COLS['vin'])
            }
            context.user_data['missing_ria_vins'] = list(missing_in_ria_vins)
            context.user_data['vin_to_model_map'] = vin_to_model_map


            for vin in sorted(list(missing_in_ria_vins)):
                model = vin_to_model_map.get(vin, 'Невідома модель')
                safe_model = html.escape(model)
                safe_vin = html.escape(vin)
                report_lines.append(f"• {safe_model} (<code>{safe_vin}</code>)")
            
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 Прив'язати авто до RIA", callback_data="ria_link_start")
            ]])

        final_report = "\n".join(report_lines)
        
        if len(final_report) > 4096:
            await query.edit_message_text(report_lines[0])
            chunk = ""
            for line in report_lines[1:]:
                if len(chunk) + len(line) > 4000:
                    await context.bot.send_message(chat_id=query.from_user.id, text=chunk, parse_mode='HTML')
                    chunk = line
                else:
                    chunk += f"\n{line}"
            if chunk:
                await context.bot.send_message(chat_id=query.from_user.id, text=chunk, parse_mode='HTML', reply_markup=keyboard)

        else:
             await query.edit_message_text(final_report, parse_mode='HTML', reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error during RIA sync with posts: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Сталася помилка під час синхронізації: {e}")


# --- Функції для створення обробників ---

def get_ria_add_handler():
    """Створює обробник для додавання авто з RIA."""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(ria_add_by_id_start, pattern="^ria_add_by_id_start$")],
        states={
            config.RIA_ADD_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ria_add_get_id)],
            config.RIA_ADD_CONFIRM_AND_ASSIGN: [CallbackQueryHandler(ria_add_save_drafts_and_ask_publish, pattern=r"^(assign_yes|assign_no|cancel_action)$")],
            config.RIA_ADD_ASK_PUBLISH: [CallbackQueryHandler(ria_add_handle_publish_choice, pattern=r"^(publish_now_yes|publish_now_no)$")],
            config.RIA_ADD_GET_LOCATION: [CallbackQueryHandler(ria_add_set_location_and_finish, pattern=r"^set_location_|^cancel_action$")],
            config.ADD_OR_PUBLISH_GET_PHOTOS: [MessageHandler(filters.PHOTO, add_or_publish_get_photos_handler), CommandHandler("done", ria_draft_skip_to_condition)],
            config.ADD_OR_PUBLISH_GET_VIDEO: [MessageHandler(filters.VIDEO, ria_draft_get_video_and_ask_condition)],
            config.RIA_DRAFT_ASK_CONDITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ria_draft_get_condition)],
            config.ADD_OR_PUBLISH_CONFIRM_AND_PUBLISH: [CallbackQueryHandler(add_or_publish_publication_callback, pattern="^publish_final$|^cancel_action$")]
        },
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_command)],
        per_message=False,
        allow_reentry=True
    )

def get_ria_publish_draft_handler():
    """Створює обробник для публікації існуючих чернеток."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(partial(ria_publish_draft_start, my_drafts_only=False), pattern="^ria_publish_draft_start$"),
            CallbackQueryHandler(partial(ria_publish_draft_start, my_drafts_only=True), pattern="^ria_publish_my_draft_start$")
        ],
        states={
            config.RIA_PUBLISH_DRAFT_SELECT: [CallbackQueryHandler(ria_publish_draft_select, pattern=r"^publish_ria_draft_|^cancel_action$")],
            config.ADD_OR_PUBLISH_MEDIA_TYPE_CHOICE: [CallbackQueryHandler(add_or_publish_media_type_callback, pattern=r"^media_type_")],
            config.ADD_OR_PUBLISH_GET_PHOTOS: [MessageHandler(filters.PHOTO, add_or_publish_get_photos_handler), CommandHandler("done", ria_draft_skip_to_condition)],
            config.ADD_OR_PUBLISH_GET_VIDEO: [MessageHandler(filters.VIDEO, ria_draft_get_video_and_ask_condition)],
            config.RIA_DRAFT_ASK_CONDITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ria_draft_get_condition)],
            config.ADD_OR_PUBLISH_CONFIRM_AND_PUBLISH: [CallbackQueryHandler(add_or_publish_publication_callback, pattern="^publish_final$|^cancel_action$")]
        },
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_command)],
        per_message=False,
        allow_reentry=True
    )

def get_ria_renew_handler():
    """Створює обробник для оновлення дати оголошення з меню."""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(ria_renew_start, pattern="^ria_renew_start$")],
        states={
            config.RIA_RENEW_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ria_renew_find_and_update)],
            config.RIA_LINK_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ria_link_get_id)],
            config.RIA_RENEW_GET_MISSING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ria_renew_get_missing_id_and_update)],
        },
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_command)],
        per_message=False,
        allow_reentry=True
    )

async def ria_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the RIA linking conversation."""
    query = update.callback_query
    await query.answer()

    missing_vins = context.user_data.get('missing_ria_vins')
    if not missing_vins:
        await query.edit_message_text("Помилка: список авто для прив'язки не знайдено. Спробуйте запустити синхронізацію знову.")
        return ConversationHandler.END

    await query.edit_message_text("Добре. Введіть VIN-код авто зі списку вище, яке ви хочете прив'язати до оголошення на RIA:")
    return config.RIA_LINK_GET_VIN

async def ria_link_get_vin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the VIN input from the user."""
    vin_input = update.message.text.strip().upper()
    missing_vins = context.user_data.get('missing_ria_vins')

    if vin_input not in missing_vins:
        await update.message.reply_text("❌ Цей VIN-код відсутній у списку для прив'язки. Будь ласка, скопіюйте VIN зі звіту вище або натисніть /cancel.")
        return config.RIA_LINK_GET_VIN

    context.user_data['vin_to_link'] = vin_input
    model_name = context.user_data.get('vin_to_model_map', {}).get(vin_input, 'це авто')
    await update.message.reply_text(f"✅ Добре, прив'язуємо {model_name}.\n\nТепер введіть ID оголошення з Auto.RIA:")
    return config.RIA_LINK_GET_RIA_ID

async def ria_link_get_ria_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the RIA ID input, creates the record, and finishes."""
    try:
        auto_id = int(update.message.text.strip())
    except (ValueError, TypeError):
        await update.message.reply_text("❌ Неправильний формат. Введіть лише цифровий ID.")
        return config.RIA_LINK_GET_RIA_ID

    vin_to_link = context.user_data.get('vin_to_link')
    if not vin_to_link:
        await update.message.reply_text("Помилка: VIN авто втрачено. /cancel")
        return ConversationHandler.END

    await update.message.reply_text(f"⏳ Перевіряю ID {auto_id} на RIA та готую дані...")

    # 1. Fetch RIA ad info to get expireDate and link
    info_url = f"https://developers.ria.com/auto/info?api_key={config.AUTORIA_API_KEY}&auto_id={auto_id}"
    ad_info = await make_ria_request(info_url, context=context, chat_id=update.effective_chat.id)

    if not ad_info:
        await update.message.reply_text("❌ Не вдалося знайти оголошення з таким ID на RIA. Перевірте ID та спробуйте ще раз.")
        return config.RIA_LINK_GET_RIA_ID
    
    # 2. Fetch the original post data from "Published Posts"
    post_info = await gs_manager.find_car_by_vin(vin_to_link, [config.SHEET_NAMES['published_posts']])
    if not post_info:
        await update.message.reply_text(f"❌ Помилка: не вдалося знайти оригінальний пост для VIN {vin_to_link} в таблиці.")
        return ConversationHandler.END

    # 3. Create the new record for "AutoRIA_Ads"
    new_ad_record = post_info['record'].copy()
    
    # Update with RIA data
    new_ad_record[config.POST_SHEET_COLS['ria_auto_id']] = ad_info.get('autoId', '')
    new_ad_record[config.POST_SHEET_COLS['ria_link']] = ad_info.get('linkToView', '')
    
    try:
        expire_date_str = ad_info.get('expireDate')
        expire_datetime = datetime.datetime.strptime(expire_date_str, '%Y-%m-%d %H:%M:%S')
        new_ad_record[config.POST_SHEET_COLS['date']] = expire_datetime.isoformat()
    except (ValueError, TypeError, KeyError):
        logger.warning(f"Could not parse expireDate from RIA for {auto_id}. Using current time.")
        new_ad_record[config.POST_SHEET_COLS['date']] = datetime.datetime.now().isoformat()

    new_ad_record[config.POST_SHEET_COLS['status']] = 'active'
    new_ad_record[config.POST_SHEET_COLS['notify_date']] = 'linked' 

    # 4. Save to sheet
    success = await gs_manager.add_row(config.SHEET_NAMES['autoria_ads'], new_ad_record, config.POST_SHEET_HEADER_ORDER)

    if success:
        await update.message.reply_text(f"✅ Успішно! Авто {new_ad_record.get('model')} прив'язано до оголошення RIA і додано до відстеження.")
    else:
        await update.message.reply_text("❌ Помилка збереження даних в таблицю 'AutoRIA_Ads'.")

    context.user_data.clear()
    return ConversationHandler.END

def get_ria_link_handler() -> ConversationHandler:
    """Creates a handler for the RIA linking conversation."""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(ria_link_start, pattern="^ria_link_start$")],
        states={
            config.RIA_LINK_GET_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ria_link_get_vin)],
            config.RIA_LINK_GET_RIA_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ria_link_get_ria_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )
