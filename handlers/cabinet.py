# -*- coding: utf-8 -*-
# handlers/cabinet.py

import logging
import datetime
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
)
from telegram.error import BadRequest

import config
from .start import cancel_command, start_command
from .keyboards import get_employee_keyboard
from .channel import (
    build_caption, repost_action,
    channel_menu as channel_menu_func,
    get_add_or_publish_handler,
    add_or_publish_get_photos_handler,
    get_location_keyboard,
    sell_car_start as sell_car_start_func,
    get_sell_car_states
)
from .ria import (
    ria_menu as ria_menu_func,
    get_ria_add_handler,
    get_ria_publish_draft_handler,
    get_ria_renew_handler,
    ria_manual_full_check,
    ria_sync_with_posts
)
from .finance import finance_menu as finance_menu_func
from handlers.utils import determine_fuel_type
from utils.sync import synchronize_working_sheets
from utils.helpers import escape_markdown_v2


logger = logging.getLogger(__name__)
gs_manager = None


# --- Нові "обгортки" для виправлення помилки TypeError ---
async def repost_action_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обгортка для виклику repost_action з правильними аргументами."""
    query = update.callback_query
    vin = query.data.split('_')[-1]
    post_info = context.user_data.get('post_info')
    if not post_info or post_info['record'].get(config.POST_SHEET_COLS['vin']) != vin:
        post_info = await gs_manager.find_car_by_vin(vin, [config.SHEET_NAMES['published_posts']])

    if post_info:
        await repost_action(query, context, vin, post_info)
    else:
        await query.message.reply_text("Помилка: не вдалося знайти інформацію про пост.")

async def repost_as_sold_action_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обгортка для виклику repost_action (as_sold=True) з правильними аргументами."""
    query = update.callback_query
    vin = query.data.split('_')[-1]
    post_info = context.user_data.get('post_info')
    if not post_info or post_info['record'].get(config.POST_SHEET_COLS['vin']) != vin:
        post_info = await gs_manager.find_car_by_vin(vin, [config.SHEET_NAMES['published_posts']])
    
    if post_info:
        await repost_action(query, context, vin, post_info, as_sold=True)
    else:
        await query.message.reply_text("Помилка: не вдалося знайти інформацію про пост.")


# --- Кабінет: Головне меню ---
async def show_cabinet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Відображає головне меню кабінету менеджера."""
    keyboard = [
        [InlineKeyboardButton("🚗 Мої авто", callback_data="cabinet_my_cars")],
        [InlineKeyboardButton("🚛 Прийняти авто з дороги", callback_data="car_arrival_start")],
        [InlineKeyboardButton("🇨🇳 Перевірити VIN (Китай)", callback_data="china_vin_start")],
        [InlineKeyboardButton("💸 Фінанси", callback_data="cabinet_finance_menu")],
        [InlineKeyboardButton("📢 Робота з каналом", callback_data="cabinet_channel_menu")],
        [InlineKeyboardButton("🤖 Робота з RIA", callback_data="cabinet_ria_menu")],
    ]

    message_text = "<b>👤 Мій кабінет</b>\n\nОберіть розділ для роботи:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )

    return config.CABINET_MENU

# --- НОВА ЛОГІКА: Помічник для перевірки VIN з Китаю ---
async def china_vin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Починає розмову для перевірки китайського VIN."""
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("Будь ласка, надішліть 17-значний VIN-код китайського електромобіля:")
    return config.CHINA_VIN_GET_VIN

async def china_vin_get_vin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує VIN і надсилає інструкцію-помічник."""
    vin = update.message.text.strip().upper()

    if len(vin) != 17:
        await update.message.reply_text(
            "❌ **Помилка.**\n"
            "VIN-код повинен містити 17 символів.\n\n"
            "Спробуйте ще раз або натисніть /cancel.",
            parse_mode='Markdown'
        )
        return config.CHINA_VIN_GET_VIN

    url = "https://www.i-nev.cn/car/vin"
    escaped_url_text = escape_markdown_v2(url)

    helper_text = (
        "✅ **Помічник для перевірки на порталі NEV**\n\n"
        "1️⃣ **Натисніть на посилання нижче, щоб відкрити офіційний портал:**\n"
        f"[{escaped_url_text}]({url})\n\n"
        "2️⃣ **Скопіюйте VIN\\-код одним дотиком і вставте його в поле `请输入17位车架号`:**\n"
        f"`{escape_markdown_v2(vin)}`\n\n"
        "3️⃣ **Натисніть синю кнопку `查询` \\(Пошук\\)\\.**\n\n"
        "**Шпаргалка для результатів:**\n"
        "• `企业` — Виробник\n"
        "• `品牌` — Марка \\(Бренд\\)\n"
        "• `车型` — Модель\n"
        "• `电池类型` — **Тип батареї**\n"
        "• `电池总能量\\(kWh\\)` — **Ємність батареї \\(кВт·год\\)**\n"
        "• `续航里程\\(km\\)` — **Запас ходу \\(км\\)**"
    )

    await update.message.reply_text(
        helper_text,
        parse_mode='MarkdownV2',
        disable_web_page_preview=True,
        reply_markup=get_employee_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END

# --- Логіка "Мої авто" ---
async def display_car_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує та відображає список авто, закріплених за менеджером."""
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("⏳ Завантажую список ваших авто...")

    if not gs_manager:
        await query.message.edit_text("Помилка: Немає зв'язку з базою даних.")
        return config.CABINET_CAR_LIST

    user_posts = await gs_manager.find_posts_by_manager_id(query.from_user.id)

    if not user_posts:
        await query.message.edit_text(
            "У вас поки немає закріплених автомобілів в аркуші 'Опубліковані Пости'.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад в кабінет", callback_data="back_to_main_cabinet")]
            ])
        )
        return config.CABINET_CAR_LIST

    keyboard = []
    for post in user_posts:
        car_model = post['record'].get(config.POST_SHEET_COLS['model'], 'Без назви')
        car_vin = post['record'].get(config.POST_SHEET_COLS['vin'], '')
        keyboard.append([InlineKeyboardButton(f"{car_model}", callback_data=f"select_car_{car_vin}")])

    keyboard.append([InlineKeyboardButton("⬅️ Назад в кабінет", callback_data="back_to_main_cabinet")])

    await query.message.edit_text(
        f"<b>Ваші активні автомобілі ({len(user_posts)}):</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return config.CABINET_CAR_LIST

async def select_car_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє вибір авто зі списку."""
    query = update.callback_query
    await query.answer()
    vin = query.data.replace("select_car_", "")
    context.user_data['selected_car_vin'] = vin
    return await show_car_menu(update, context)

async def show_car_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показує детальне меню керування для обраного авто."""
    query = update.callback_query
    if query:
        await query.answer()
        message_to_edit = query.message
    else:
        message_to_edit = await update.message.reply_text("Завантаження меню...")

    vin = context.user_data.get('selected_car_vin')
    if not vin:
        await message_to_edit.edit_text("Помилка: VIN не знайдено. Повертаюся до списку.")
        return await display_car_list(update, context)

    post_info = await gs_manager.find_car_by_vin(vin, search_sheets=[config.SHEET_NAMES['published_posts']])

    if not post_info:
        await message_to_edit.edit_text("Помилка: пост для цього авто не знайдено.")
        return config.CABINET_CAR_LIST

    context.user_data['post_info'] = post_info
    post_record = post_info['record']

    try:
        post_date_str = post_record.get(config.POST_SHEET_COLS['date'], "")
        post_date = datetime.datetime.fromisoformat(post_date_str) if post_date_str else datetime.datetime.now()
        days_ago = (datetime.datetime.now() - post_date).days
        post_date_formatted = f"{post_date.strftime('%d.%m.%Y')} ({days_ago} днів тому)"
    except (ValueError, TypeError):
        post_date_formatted = "Невідомо"

    text = (
        f"<b>Керування:</b> {post_record.get(config.POST_SHEET_COLS['model'], 'N/A')}\n"
        f"<b>VIN:</b> <code>{vin}</code>\n"
        f"<b>Ціна в пості:</b> {post_record.get(config.POST_SHEET_COLS['price'], 'N/A')}$\n"
        f"<b>Дата публікації:</b> {post_date_formatted}\n\n"
        "Оберіть дію:"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Редагувати пост", callback_data="post_edit_select")],
        [InlineKeyboardButton("🔴 Позначити як ПРОДАНО", callback_data=f"sell_car_start_{vin}")],
        [InlineKeyboardButton("🔄 Переопублікувати", callback_data=f"post_action_repost_{vin}")],
        [InlineKeyboardButton("💵 ПРОДАНО (Переопублікувати)", callback_data=f"post_action_repostsold_{vin}")],
        [InlineKeyboardButton("⬅️ Назад до списку авто", callback_data="cabinet_my_cars")]
    ]

    await message_to_edit.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return config.CABINET_CAR_MENU

async def post_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запитує, яке поле редагувати."""
    query = update.callback_query
    await query.answer()

    post_info = context.user_data.get('post_info')
    if not post_info:
        await query.edit_message_text("Помилка: дані втрачено.")
        return config.CABINET_CAR_LIST

    keyboard = []
    editable_fields = {
        "model": "Назву", "price": "Ціну", "modification": "Двигун",
        "mileage": "Пробіг", "drivetrain": "Привід", "gearbox": "Коробку", "condition": "Стан"
    }
    for key, name in editable_fields.items():
        keyboard.append([InlineKeyboardButton(f"✏️ {name}", callback_data=f"post_edit_{key}")])

    vin = post_info['record'].get(config.POST_SHEET_COLS['vin'], '')
    keyboard.append([InlineKeyboardButton("⬅️ Назад до авто", callback_data=f"select_car_{vin}")])

    await query.edit_message_text("Оберіть, що ви хочете відредагувати в пості:", reply_markup=InlineKeyboardMarkup(keyboard))
    return config.CABINET_POST_EDIT_SELECT


async def post_edit_get_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запитує нове значення для обраного поля."""
    query = update.callback_query
    await query.answer()

    field_key = query.data.replace("post_edit_", "")
    field_name_map = {
        "model": "назву", "price": "ціну", "modification": "двигун",
        "mileage": "пробіг", "drivetrain": "привід", "gearbox": "коробку", "condition": "стан"
    }

    context.user_data['post_field_to_edit'] = config.POST_SHEET_COLS[field_key]
    await query.edit_message_text(f"Введіть нове значення для поля '{field_name_map.get(field_key, field_key)}':")
    return config.CABINET_POST_EDIT_GET_VALUE


async def post_edit_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Зберігає нове значення та оновлює пост."""
    new_value = update.message.text
    field_to_edit = context.user_data.get('post_field_to_edit')
    post_info = context.user_data.get('post_info')
    
    vin = post_info.get('record', {}).get(config.POST_SHEET_COLS['vin'])

    if not all([field_to_edit, post_info, vin]):
        await update.message.reply_text("Помилка: дані втрачено. Повертаюся до меню.")
        return await show_cabinet_menu(update, context)

    await update.message.reply_text(f"Оновлюю дані...")

    post_info['record'][field_to_edit] = new_value
    await gs_manager.update_row(
        post_info['sheet_name'], post_info['row_index'],
        post_info['record'], config.POST_SHEET_HEADER_ORDER
    )

    emp_id = int(post_info['record'].get(config.POST_SHEET_COLS['emp_id'], 0))
    status_prefix = post_info['record'].get(config.POST_SHEET_COLS['status_prefix'], '')
    new_caption = build_caption(post_info['record'], emp_id, status_prefix)

    try:
        msg_id_str = post_info['record'].get(config.POST_SHEET_COLS['msg_id'])
        msg_id = int(msg_id_str) if msg_id_str and str(msg_id_str).isdigit() else 0
        if msg_id:
            await context.bot.edit_message_caption(
                chat_id=int(post_info['record'][config.POST_SHEET_COLS['chat_id']]),
                message_id=msg_id,
                caption=new_caption
            )
        await update.message.reply_text("✅ Пост успішно оновлено!")
    except Exception as e:
        logger.error(f"Помилка оновлення посту {vin}: {e}")
        await update.message.reply_text("❌ Помилка оновлення поста в каналі (можливо, це чернетка без поста). Дані в таблиці оновлено.")

    context.user_data['selected_car_vin'] = vin
    return await show_car_menu(update, context)


# --- Панель Власника та Статистика ---

async def owner_panel_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Відображає головне меню панелі власника."""
    keyboard = [
        [InlineKeyboardButton("📊 Загальна статистика по менеджерах", callback_data="owner_summary")],
        [InlineKeyboardButton("💰 Фінансові підсумки", callback_data="owner_financial")],
        [InlineKeyboardButton("🏆 Рейтинг продажів", callback_data="owner_sales_rating")],
    ]
    message_text = "👑 <b>Панель Власника</b>\n\nОберіть звіт для перегляду:"
    
    target_message = update.message or update.callback_query.message
    if update.callback_query:
        await update.callback_query.answer()
        await target_message.edit_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await target_message.reply_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        
    return config.OWNER_PANEL_MAIN

async def owner_show_manager_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показує загальну статистику по кількості авто у менеджерів."""
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("👑 Панель Власника\n⏳ Отримую статистику по менеджерах...")

    summary = await gs_manager.get_managers_summary()
    
    if not summary:
        await query.message.edit_text("Не знайдено жодного авто в базі.")
        return config.OWNER_PANEL_MAIN

    total_cars = sum(summary.values())
    message_text = f"<b>Загальна статистика (з робочих аркушів):</b>\nВсього авто на площадках/в дорозі: <b>{total_cars}</b>\n\n<b>По менеджерах:</b>\n"
    for manager_id, car_count in summary.items():
        name = config.MANAGER_NAMES.get(manager_id, f"ID: {manager_id}")
        message_text += f" • {name}: <b>{car_count} авто</b>\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="owner_back_to_menu")]]
    await query.message.edit_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return config.OWNER_PANEL_MAIN

async def owner_show_financial_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показує фінансові підсумки по авто в наявності та в дорозі."""
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("👑 Панель Власника\n⏳ Розраховую фінансові підсумки...")

    total_value = 0
    location_summary = {}

    for sheet_name in config.WORKING_SHEETS:
        records = await gs_manager.get_all_records(sheet_name, expected_headers=config.CAR_SHEET_HEADER_ORDER)
        if records:
            sheet_total = sum(float(str(rec.get(config.CAR_SHEET_COLS['price'], 0) or 0).replace(" ", "").replace("\xa0", "")) for rec in records)
            total_value += sheet_total
            location_summary[sheet_name] = (len(records), sheet_total)

    message_text = f"💰 <b>Фінансові підсумки</b>\n\n<b>Загальна вартість активів: ${total_value:,.2f}</b>\n\n<b>Розбивка по локаціях:</b>\n"
    for location, (count, value) in location_summary.items():
        message_text += f" • {location} ({count} авто): <b>${value:,.2f}</b>\n"

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="owner_back_to_menu")]]
    await query.message.edit_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return config.OWNER_PANEL_MAIN

async def owner_show_sales_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показує рейтинг продажів менеджерів за поточний місяць."""
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("👑 Панель Власника\n⏳ Аналізую продажі за поточний місяць...")
    
    archive_records = await gs_manager.get_all_records(config.SHEET_NAMES['archive'])
    if not archive_records:
        await query.edit_message_text("В архіві ще немає жодного запису про продажі.")
        return config.OWNER_PANEL_MAIN

    sales_by_manager = {}
    now = datetime.datetime.now()
    
    for record in archive_records:
        try:
            # --- ВИПРАВЛЕНО: Прибрано умову перевірки локації ---
            # Тепер будь-який продаж з архіву буде враховано
            sale_date_str = record.get(config.POST_SHEET_COLS['date'])
            if not sale_date_str: continue

            sale_date = datetime.datetime.fromisoformat(sale_date_str)
            
            if sale_date.year == now.year and sale_date.month == now.month:
                seller_id_str = record.get(config.ARCHIVE_SHEET_COLS['seller_id'])
                if not seller_id_str: continue
                seller_id = int(seller_id_str)
                
                price_str = str(record.get(config.POST_SHEET_COLS['price'], 0) or 0).replace(" ", "").replace("\xa0", "")
                price = float(price_str)
                
                if seller_id not in sales_by_manager:
                    sales_by_manager[seller_id] = {'count': 0, 'total_sum': 0}
                
                sales_by_manager[seller_id]['count'] += 1
                sales_by_manager[seller_id]['total_sum'] += price
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"Could not process archive record for sales rating: {record}. Error: {e}")
            continue

    if not sales_by_manager:
        await query.message.edit_text("Цього місяця ще не було зафіксовано продажів.")
        return config.OWNER_PANEL_MAIN

    message_text = f"🏆 <b>Рейтинг продажів за {now.strftime('%B %Y')}</b>\n\n"
    sorted_sellers = sorted(sales_by_manager.items(), key=lambda item: item[1]['total_sum'], reverse=True)

    for seller_id, data in sorted_sellers:
        name = config.MANAGER_NAMES.get(seller_id, f"ID: {seller_id}")
        message_text += f" • <b>{name}</b>: {data['count']} авто на суму <b>${data['total_sum']:,.2f}</b>\n"

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="owner_back_to_menu")]]
    await query.message.edit_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    return config.OWNER_PANEL_MAIN

async def show_fleet_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує розширену статистику автопарку."""
    message = update.message or update.callback_query.message
    await message.reply_text("📊 Аналізую автопарк, це може зайняти хвилину...")

    if not gs_manager:
        await message.reply_text("Помилка: Немає зв'язку з базою даних.")
        return

    try:
        posts_records = await gs_manager.get_all_records(config.SHEET_NAMES['published_posts']) or []
        ria_ads_records = await gs_manager.get_all_records(config.SHEET_NAMES['autoria_ads']) or []

        active_posts_vins = {
            p[config.POST_SHEET_COLS['vin']].strip().upper()
            for p in posts_records
            if p.get(config.POST_SHEET_COLS['vin']) and p.get(config.POST_SHEET_COLS['status']) in ('active', 'draft_ria', 'draft_manual')
        }
        active_ria_vins = {
            ad[config.POST_SHEET_COLS['vin']].strip().upper()
            for ad in ria_ads_records
            if ad.get(config.POST_SHEET_COLS['vin']) and ad.get(config.POST_SHEET_COLS['status']) == 'active'
        }

        total_unique_vins = active_posts_vins | active_ria_vins
        only_in_posts_vins = active_posts_vins - active_ria_vins
        only_in_ria_vins = active_ria_vins - active_posts_vins
        in_both_vins = active_posts_vins & active_ria_vins

        full_inventory = await gs_manager.get_full_inventory()
        if not full_inventory:
            await message.reply_text("Не знайдено активних авто для аналізу.")
            return

        fuel_stats = {'Бензин': 0, 'Дизель': 0, 'Електро': 0, 'Гібрид': 0, 'Невідомо': 0}
        unknown_cars = []
        for car in full_inventory:
            fuel_type = determine_fuel_type(car.get(config.POST_SHEET_COLS['modification'], ''))
            if fuel_type in fuel_stats:
                fuel_stats[fuel_type] += 1
            else:
                fuel_stats['Невідомо'] += 1
                unknown_cars.append(car)
        
        context.user_data['unknown_fuel_cars'] = unknown_cars

        report_text = (
            f"📊 <b>Загальна статистика автопарку</b>\n\n"
            f"<b>Всього унікальних авто: {len(total_unique_vins)}</b>\n\n"
            f"<b>Аналіз по джерелах:</b>\n"
            f" • 📈 Лише в 'Опубліковані Пости': <b>{len(only_in_posts_vins)}</b>\n"
            f" • 🤖 Лише в 'AutoRIA_Ads' (трекер): <b>{len(only_in_ria_vins)}</b>\n"
            f" • 🔗 Присутні в обох списках: <b>{len(in_both_vins)}</b>\n\n"
            f"<b>Розбивка за типом пального (з усіх унікальних авто):</b>\n"
            f" • ⛽️ Бензин: <b>{fuel_stats['Бензин']}</b>\n"
            f" • 💨 Дизель: <b>{fuel_stats['Дизель']}</b>\n"
            f" • 🔋 Електро: <b>{fuel_stats['Електро']}</b>\n"
            f" • ⚡️ Гібрид: <b>{fuel_stats['Гібрид']}</b>\n"
            f" • ❓ Невідомо: <b>{fuel_stats['Невідомо']}</b>"
        )
        
        keyboard = None
        if fuel_stats['Невідомо'] > 0:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❓ Показати нерозпізнані авто", callback_data="show_unknown_fuel")]])

        await message.reply_text(report_text, parse_mode='HTML', reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error during fleet statistics generation: {e}", exc_info=True)
        await message.reply_text(f"❌ Сталася помилка під час генерації статистики: {e}")


async def show_unknown_fuel_cars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує список авто з нерозпізнаним типом пального з кнопками для виправлення."""
    query = update.callback_query
    await query.answer()
    
    unknown_cars = context.user_data.get('unknown_fuel_cars', [])
    if not unknown_cars:
        await query.message.edit_text("Не знайдено авто з нерозпізнаним типом пального.")
        return

    await query.message.reply_text(
        "<b>❓ Авто з нерозпізнаним типом пального:</b>\nНатисніть на кнопку, щоб виправити поле 'Модифікація'.",
        parse_mode='HTML'
    )

    for car in unknown_cars:
        model = car.get(config.POST_SHEET_COLS['model'], 'Без назви')
        vin = car.get(config.POST_SHEET_COLS['vin'], 'Без VIN')
        modification = car.get(config.POST_SHEET_COLS['modification'], 'Без опису')
        
        message_text = f" • {model} (<code>{vin}</code>)\n   <i>Поточна модифікація: «{modification or 'пусто'}»</i>"
        
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✏️ Виправити", callback_data=f"fix_fuel_{vin}")
        ]])
        
        await query.message.reply_text(message_text, parse_mode='HTML', reply_markup=keyboard)


# --- Функція: Прибуття авто ---

async def car_arrival_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Починає процес реєстрації прибуття авто."""
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("🚛 Введіть VIN-код автомобіля, який прибув (пошук тільки серед авто 'в дорозі'):")
    return config.ARRIVAL_ASK_VIN

async def car_arrival_get_vin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Знаходить авто і запитує нову локацію."""
    vin = update.message.text.strip().upper()
    transit_locations = [config.SHEET_NAMES['in_transit_usa'], config.SHEET_NAMES['in_transit_china']]
    
    post_info = await gs_manager.find_car_by_vin(vin, search_sheets=[config.SHEET_NAMES['published_posts']])
    
    if not post_info or post_info['record'].get(config.POST_SHEET_COLS['location']) not in transit_locations:
        await update.message.reply_text("❌ Авто з таким VIN не знайдено серед тих, що 'в дорозі'. Перевірте VIN або статус авто.")
        return config.ARRIVAL_ASK_VIN

    context.user_data['arrival_post_info'] = post_info
    model = post_info['record'].get(config.POST_SHEET_COLS['model'])
    
    keyboard = [
        [InlineKeyboardButton(config.SHEET_NAMES['sydora_yard'], callback_data=f"arrival_loc_{config.SHEET_NAMES['sydora_yard']}")],
        [InlineKeyboardButton(config.SHEET_NAMES['halytska_yard'], callback_data=f"arrival_loc_{config.SHEET_NAMES['halytska_yard']}")]
    ]
    await update.message.reply_text(
        f"Знайдено: <b>{model}</b>.\n\nКуди перемістити авто?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return config.ARRIVAL_GET_LOCATION

async def car_arrival_get_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує локацію і запитує нову ціну."""
    query = update.callback_query
    await query.answer()
    
    new_location = query.data.replace("arrival_loc_", "")
    context.user_data['arrival_new_location'] = new_location
    
    await query.message.edit_text("Введіть нову ціну 'під ключ' в USD (тільки число):")
    return config.ARRIVAL_GET_PRICE

async def car_arrival_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує ціну і запитує нові фото."""
    try:
        new_price = float(update.message.text.strip())
        context.user_data['arrival_new_price'] = new_price
        context.user_data.setdefault('post_data', {})[config.POST_SHEET_COLS['photos']] = []
        await update.message.reply_text("Добре. Тепер надішліть нові, 'живі' фотографії авто (до 10 шт.).\nКоли закінчите, натисніть /done.")
        return config.ARRIVAL_GET_PHOTOS
    except ValueError:
        await update.message.reply_text("❌ Неправильний формат. Введіть число, наприклад: 25500")
        return config.ARRIVAL_GET_PRICE

async def car_arrival_done_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершує додавання фото, оновлює дані та завершує процес."""
    new_photos = context.user_data.get('post_data', {}).get(config.POST_SHEET_COLS['photos'])
    if not new_photos:
        await update.message.reply_text("Ви не додали жодного фото. Надішліть хоча б одне, або /cancel, щоб скасувати.")
        return config.ARRIVAL_GET_PHOTOS

    await update.message.reply_text("⏳ Оновлюю інформацію про автомобіль...")
    
    post_info = context.user_data['arrival_post_info']
    record_to_update = post_info['record']
    
    record_to_update[config.POST_SHEET_COLS['location']] = context.user_data['arrival_new_location']
    record_to_update[config.POST_SHEET_COLS['price']] = context.user_data['arrival_new_price']
    record_to_update[config.POST_SHEET_COLS['photos']] = ",".join(new_photos)
    record_to_update[config.POST_SHEET_COLS['date']] = datetime.datetime.now().isoformat()
    record_to_update[config.POST_SHEET_COLS['status_prefix']] = "✅ В НАЯВНОСТІ"

    success = await gs_manager.update_row(
        config.SHEET_NAMES['published_posts'],
        post_info['row_index'],
        record_to_update,
        config.POST_SHEET_HEADER_ORDER
    )

    if success:
        await update.message.reply_text(
            "✅ Успішно! Авто переміщено. Зміни на робочих аркушах з'являться після наступної синхронізації.",
            reply_markup=get_employee_keyboard(update.effective_user.id)
        )
        asyncio.create_task(synchronize_working_sheets(gs_manager))
    else:
        await update.message.reply_text(
            "❌ Помилка при оновленні даних в таблиці.",
            reply_markup=get_employee_keyboard(update.effective_user.id)
        )
        
    context.user_data.clear()
    return ConversationHandler.END

# --- Логіка керування існуючими постами ---
async def post_manage_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Починає процес керування існуючим постом."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введіть ВІН-код (або останні 4+ цифри) опублікованого авто або чернетки:")
    return config.MANAGE_ASK_VIN

async def manage_find_post_by_vin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Знаходить пост за VIN і показує меню керування."""
    vin_query = update.message.text.strip().upper()
    await update.message.reply_text(f"🔍 Шукаю пост або чернетку за VIN ...{vin_query}")
    if not gs_manager:
        await update.message.reply_text("Помилка: немає зв'язку з Google Sheets.")
        return ConversationHandler.END
    
    post_info = await gs_manager.find_car_by_vin(vin_query, [config.SHEET_NAMES['published_posts']])
    
    if post_info and post_info['record'].get(config.POST_SHEET_COLS['status']) in ['active', 'draft_ria', 'draft_manual']:
        context.user_data['post_info'] = post_info
        context.user_data['selected_car_vin'] = post_info['record'].get(config.POST_SHEET_COLS['vin'])
        await show_post_manage_menu(update, context, post_info)
        return config.MANAGE_SHOW_ACTIONS

    await update.message.reply_text("😕 Не знайдено активного поста або чернетки з таким VIN.")
    return config.MANAGE_ASK_VIN

async def show_post_manage_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, post_info: dict):
    """Відображає меню керування для знайденого поста."""
    record = post_info['record']
    vin = record.get(config.POST_SHEET_COLS['vin'], "N/A")
    model = record.get(config.POST_SHEET_COLS['model'], "N/A")
    price = record.get(config.POST_SHEET_COLS['price'], "N/A")
    location = record.get(config.POST_SHEET_COLS['location'], "Не вказано")
    status = record.get(config.POST_SHEET_COLS['status'], "Невідомо")
        
    message_text = (f"⚙️ *Керування постом*\n\n"
                    f"🚗 *Авто:* {escape_markdown_v2(model)}\n"
                    f"🔢 *VIN:* `{escape_markdown_v2(vin)}`\n"
                    f"💵 *Ціна:* {escape_markdown_v2(str(price))}\n"
                    f"📍 *Поточне розташування:* {escape_markdown_v2(location)}\n"
                    f"🚦 *Статус:* `{status}`\n\n"
                    f"Оберіть дію:")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗺️ Змінити розташування/фото", callback_data=f"manage_move_{vin}")],
        [InlineKeyboardButton("✏️ Редагувати дані поста", callback_data="post_edit_select")],
        [InlineKeyboardButton("🔴 Позначити як ПРОДАНО", callback_data=f"sell_car_start_{vin}")],
        [InlineKeyboardButton("🔄 Переопублікувати", callback_data=f"manage_repost_{vin}")],
        [InlineKeyboardButton("💵 ПРОДАНО (Переопублікувати)", callback_data=f"manage_repostsold_{vin}")],
        [InlineKeyboardButton("⬅️ Новий пошук", callback_data="post_manage_start_new")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_action")]
    ])
    
    target_message = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await target_message.edit_text(message_text, parse_mode='MarkdownV2', reply_markup=keyboard)
    else:
        await target_message.reply_text(message_text, parse_mode='MarkdownV2', reply_markup=keyboard)

async def manage_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє дії з меню керування постом."""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'post_manage_start_new':
        await query.edit_message_text("Введіть ВІН-код поста:")
        return config.MANAGE_ASK_VIN
    if query.data == 'cancel_action':
        await query.edit_message_text("Скасовано.")
        return ConversationHandler.END
        
    post_info = context.user_data.get('post_info')
    if not post_info:
        await query.edit_message_text("Помилка: дані втрачено. /cancel")
        return ConversationHandler.END
        
    vin = post_info['record'].get(config.POST_SHEET_COLS['vin'], '')
    
    if query.data.startswith("manage_move_"):
        context.user_data['edit_post_data'] = post_info['record'].copy()
        await query.edit_message_text("Оберіть нове розташування для авто:", reply_markup=get_location_keyboard())
        return config.MANAGE_MOVE_ASK_LOCATION
    
    if query.data.startswith("manage_repost_"):
        await repost_action(query, context, vin, post_info)
        return ConversationHandler.END
    elif query.data.startswith("manage_repostsold_"):
        await repost_action(query, context, vin, post_info, as_sold=True)
        return ConversationHandler.END
            
    return config.MANAGE_SHOW_ACTIONS

async def manage_move_get_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє вибір нової локації, запитує нові фото."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_action":
        await query.edit_message_text("Переміщення скасовано.")
        return ConversationHandler.END

    new_location = query.data.replace("set_location_", "")
    context.user_data['edit_post_data'][config.POST_SHEET_COLS['location']] = new_location
    
    context.user_data.setdefault('post_data', {})[config.POST_SHEET_COLS['photos']] = []

    await query.edit_message_text(
        f"Розташування змінено на '{new_location}'.\n\n"
        "Тепер надішліть нові фото (від 1 до 10). "
        "Якщо фото не змінились, натисніть /skip.\n\n"
        "Коли закінчите завантажувати, натисніть /done."
    )
    return config.MANAGE_MOVE_GET_PHOTOS

async def manage_move_skip_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пропускає оновлення фото, зберігає зміни."""
    await update.message.reply_text("Фото залишаться без змін. Зберігаю...")
    
    post_info = context.user_data.get('post_info')
    edit_data = context.user_data.get('edit_post_data')
    
    edit_data[config.POST_SHEET_COLS['photos']] = post_info['record'].get(config.POST_SHEET_COLS['photos'], '')

    await gs_manager.update_row(
        config.SHEET_NAMES['published_posts'],
        post_info['row_index'],
        edit_data,
        config.POST_SHEET_HEADER_ORDER
    )
    
    await update.message.reply_text(
        "✅ Розташування оновлено! Зміни на робочих аркушах з'являться після наступної синхронізації.",
        reply_markup=get_employee_keyboard(update.effective_user.id)
    )
    
    asyncio.create_task(synchronize_working_sheets(gs_manager))
    
    return ConversationHandler.END

async def manage_move_done_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершує додавання нових фото, оновлює пост."""
    new_photos = context.user_data.get('post_data', {}).get(config.POST_SHEET_COLS['photos'])
    if not new_photos:
        await update.message.reply_text("Ви не додали жодного фото. Якщо фото не змінились, натисніть /skip.")
        return config.MANAGE_MOVE_GET_PHOTOS

    await update.message.reply_text("Оновлюю пост з новими фото...")
    
    post_info = context.user_data.get('post_info')
    edit_data = context.user_data.get('edit_post_data')
    
    edit_data[config.POST_SHEET_COLS['photos']] = ",".join(new_photos)
    edit_data[config.POST_SHEET_COLS['date']] = datetime.datetime.now().isoformat()
    
    if edit_data.get(config.POST_SHEET_COLS['status']) == 'active':
        try:
            await context.bot.delete_message(
                chat_id=int(post_info['record'][config.POST_SHEET_COLS['chat_id']]),
                message_id=int(post_info['record'][config.POST_SHEET_COLS['msg_id']])
            )
        except Exception as e:
            logger.warning(f"Не вдалося видалити старий пост при оновленні фото: {e}")

        new_caption = build_caption(edit_data, int(edit_data[config.POST_SHEET_COLS['emp_id']]), edit_data.get(config.POST_SHEET_COLS['status_prefix'], ''))
        media_group = [InputMediaPhoto(media=new_photos[0], caption=new_caption)] + [InputMediaPhoto(media=pid) for pid in new_photos[1:]]
        posted_messages = await context.bot.send_media_group(chat_id=config.CHANNEL_ID, media=media_group)
        
        edit_data[config.POST_SHEET_COLS['msg_id']] = posted_messages[0].message_id
    
    await gs_manager.update_row(
        config.SHEET_NAMES['published_posts'],
        post_info['row_index'],
        edit_data,
        config.POST_SHEET_HEADER_ORDER
    )

    await update.message.reply_text(
        "✅ Розташування та фото успішно оновлено! Зміни на робочих аркушах з'являться після наступної синхронізації.",
        reply_markup=get_employee_keyboard(update.effective_user.id)
    )
    
    asyncio.create_task(synchronize_working_sheets(gs_manager))
    
    return ConversationHandler.END


# --- Функції для створення обробників ---
def get_cabinet_handler() -> ConversationHandler:
    """Створює єдиний, спрощений обробник для всього функціоналу кабінету."""
    add_publish_handler = get_add_or_publish_handler()
    ria_add_handler = get_ria_add_handler()
    ria_publish_draft_handler = get_ria_publish_draft_handler()
    ria_renew_handler = get_ria_renew_handler()
    sell_car_states = get_sell_car_states()

    cabinet_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("Мій кабінет"), show_cabinet_menu)],
        states={
            config.CABINET_MENU: [
                CallbackQueryHandler(display_car_list, pattern="^cabinet_my_cars$"),
                CallbackQueryHandler(car_arrival_start, pattern="^car_arrival_start$"),
                CallbackQueryHandler(channel_menu_func, pattern="^cabinet_channel_menu$"),
                CallbackQueryHandler(ria_menu_func, pattern="^cabinet_ria_menu$"),
                CallbackQueryHandler(finance_menu_func, pattern="^cabinet_finance_menu$"),
                CallbackQueryHandler(china_vin_start, pattern="^china_vin_start$"),
            ],
            config.CHINA_VIN_GET_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, china_vin_get_vin)],
            config.CABINET_CAR_LIST: [
                CallbackQueryHandler(select_car_callback, pattern="^select_car_"),
                CallbackQueryHandler(show_cabinet_menu, pattern="^back_to_main_cabinet$"),
            ],
            config.CABINET_CAR_MENU: [
                CallbackQueryHandler(display_car_list, pattern="^cabinet_my_cars$"),
                CallbackQueryHandler(post_edit_select, pattern="^post_edit_select$"),
                CallbackQueryHandler(repost_action_wrapper, pattern=r"^post_action_repost_"),
                CallbackQueryHandler(repost_as_sold_action_wrapper, pattern=r"^post_action_repostsold_"),
                CallbackQueryHandler(sell_car_start_func, pattern="^sell_car_start_")
            ],
            config.CABINET_POST_EDIT_SELECT: [
                CallbackQueryHandler(post_edit_get_value, pattern="^post_edit_"),
                CallbackQueryHandler(select_car_callback, pattern="^select_car_")
            ],
            config.CABINET_POST_EDIT_GET_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_edit_save_value)],
            
            config.CHANNEL_MENU_STATE: [
                add_publish_handler,
                CallbackQueryHandler(post_manage_start, pattern="^post_manage_start$"),
                CallbackQueryHandler(show_cabinet_menu, pattern="^back_to_main_cabinet$")
            ],
            config.MANAGE_ASK_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, manage_find_post_by_vin)], 
            config.MANAGE_SHOW_ACTIONS: [
                CallbackQueryHandler(manage_action_callback, pattern="^manage_"),
                CallbackQueryHandler(post_edit_select, pattern="^post_edit_select$"),
                CallbackQueryHandler(sell_car_start_func, pattern="^sell_car_start_")
            ],
            config.MANAGE_MOVE_ASK_LOCATION: [CallbackQueryHandler(manage_move_get_location, pattern="^set_location_|^cancel_action$")],
            config.MANAGE_MOVE_GET_PHOTOS: [
                MessageHandler(filters.PHOTO, add_or_publish_get_photos_handler),
                CommandHandler("done", manage_move_done_photos),
                CommandHandler("skip", manage_move_skip_photos)
            ],
            
            config.RIA_MENU_STATE: [
                ria_add_handler,
                ria_publish_draft_handler,
                ria_renew_handler,
                CallbackQueryHandler(ria_manual_full_check, pattern="^ria_check_full$"),
                CallbackQueryHandler(ria_sync_with_posts, pattern="^ria_sync_start$"),
                CallbackQueryHandler(show_cabinet_menu, pattern="^back_to_main_cabinet$")
            ],
            
            config.ARRIVAL_ASK_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_arrival_get_vin)],
            config.ARRIVAL_GET_LOCATION: [CallbackQueryHandler(car_arrival_get_location, pattern="^arrival_loc_")],
            config.ARRIVAL_GET_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_arrival_get_price)],
            config.ARRIVAL_GET_PHOTOS: [
                MessageHandler(filters.PHOTO, add_or_publish_get_photos_handler),
                CommandHandler("done", car_arrival_done_photos)
            ],
            
            **add_publish_handler.states,
            **ria_add_handler.states,
            **ria_publish_draft_handler.states,
            **ria_renew_handler.states
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )
    
    cabinet_handler.states.update(sell_car_states)
    
    return cabinet_handler

def get_owner_panel_handler() -> ConversationHandler:
    """Створює окремий обробник для панелі власника."""
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("Панель Власника"), owner_panel_menu)],
        states={
            config.OWNER_PANEL_MAIN: [
                CallbackQueryHandler(owner_show_manager_summary, pattern="^owner_summary$"),
                CallbackQueryHandler(owner_show_financial_summary, pattern="^owner_financial$"),
                CallbackQueryHandler(owner_show_sales_rating, pattern="^owner_sales_rating$"),
                CallbackQueryHandler(owner_panel_menu, pattern="^owner_back_to_menu$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )

# --- НОВИЙ ОБРОБНИК: Виправлення типу пального ---
async def fix_fuel_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Починає розмову для виправлення модифікації."""
    query = update.callback_query
    await query.answer()
    vin = query.data.replace("fix_fuel_", "")
    context.user_data['vin_to_fix_fuel'] = vin

    post_info = await gs_manager.find_car_by_vin(vin, [config.SHEET_NAMES['published_posts']])
    if not post_info:
        await query.edit_message_text("Помилка: не вдалося знайти це авто.")
        return ConversationHandler.END
    
    model = post_info['record'].get(config.POST_SHEET_COLS['model'], vin)
    await query.edit_message_text(f"Введіть правильну модифікацію для <b>{model}</b> (напр., '2.0 Бензин'):", parse_mode='HTML')
    
    return config.FIX_FUEL_AWAIT_MODIFICATION

async def fix_fuel_get_modification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує нову модифікацію та оновлює дані."""
    vin = context.user_data.get('vin_to_fix_fuel')
    new_modification = update.message.text
    user = update.effective_user

    if not vin:
        await update.message.reply_text("Помилка: втрачено VIN. Скасовано.", reply_markup=get_employee_keyboard(user.id))
        return ConversationHandler.END

    await update.message.reply_text(f"⏳ Оновлюю дані для VIN <code>{vin}</code>...", parse_mode='HTML')
    
    post_info = await gs_manager.find_car_by_vin(vin, [config.SHEET_NAMES['published_posts']])
    if not post_info:
        await update.message.reply_text("Помилка: не вдалося знайти це авто в таблиці.", reply_markup=get_employee_keyboard(user.id))
        return ConversationHandler.END

    record = post_info['record']
    record[config.POST_SHEET_COLS['modification']] = new_modification
    
    new_fuel_type = determine_fuel_type(new_modification)
    if new_fuel_type:
        record[config.POST_SHEET_COLS['fuel_type']] = new_fuel_type

    success = await gs_manager.update_row(
        post_info['sheet_name'],
        post_info['row_index'],
        record,
        config.POST_SHEET_HEADER_ORDER
    )

    if success:
        await update.message.reply_text(f"✅ Модифікацію для <b>{record.get(config.POST_SHEET_COLS['model'])}</b> оновлено на '<code>{new_modification}</code>'.", parse_mode='HTML', reply_markup=get_employee_keyboard(user.id))
        asyncio.create_task(synchronize_working_sheets(gs_manager))
    else:
        await update.message.reply_text("❌ Не вдалося оновити дані в таблиці.", reply_markup=get_employee_keyboard(user.id))

    context.user_data.pop('vin_to_fix_fuel', None)
    return ConversationHandler.END

def get_fix_fuel_handler() -> ConversationHandler:
    """Створює обробник розмови для виправлення типу пального."""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(fix_fuel_start, pattern="^fix_fuel_")],
        states={
            config.FIX_FUEL_AWAIT_MODIFICATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, fix_fuel_get_modification)]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )

