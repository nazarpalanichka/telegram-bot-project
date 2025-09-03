# -*- coding: utf-8 -*-
# handlers/client.py

import logging
import datetime
import html
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
)
import config
from .start import cancel_command, start_command
from .keyboards import client_keyboard, yes_no_keyboard

logger = logging.getLogger(__name__)
gs_manager = None

async def client_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not gs_manager:
        await update.message.reply_text("Вибачте, функція пошуку тимчасово недоступна.", reply_markup=client_keyboard)
        return ConversationHandler.END
    await update.message.reply_text("Введіть назву авто або останні 4+ цифри ВІН-коду:")
    return config.CLIENT_SEARCH_QUERY

async def client_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    sheets_to_search = [config.SHEET_NAMES['published_posts']]
    await update.message.reply_text("🔍 Шукаю, зачекайте...")
    
    result = await gs_manager.find_car_by_vin(update.message.text, search_sheets=sheets_to_search) if gs_manager else None
    
    # --- ВИПРАВЛЕНО: Клієнти бачать тільки активні пости ---
    if not result or result['record'].get(config.POST_SHEET_COLS['status']) != 'active':
        await update.message.reply_text("На жаль, нічого не знайдено серед активних пропозицій.", reply_markup=client_keyboard)
        return ConversationHandler.END

    record = result["record"]
    
    # --- Динамічний конструктор HTML-повідомлення ---
    message_parts = []
    
    if model := record.get(config.POST_SHEET_COLS['model']):
        message_parts.append(f"<b>{html.escape(str(model))}</b>")

    if price := record.get(config.POST_SHEET_COLS['price']):
        message_parts.append(f"<b>Ціна:</b> {html.escape(str(price))} 💵")

    if modification := record.get(config.POST_SHEET_COLS['modification']):
        message_parts.append(f"<b>Двигун:</b> {html.escape(str(modification))}")

    if mileage := record.get(config.POST_SHEET_COLS['mileage']):
        message_parts.append(f"<b>Пробіг:</b> {html.escape(str(mileage))}")

    if drivetrain := record.get(config.POST_SHEET_COLS['drivetrain']):
        message_parts.append(f"<b>Привід:</b> {html.escape(str(drivetrain))}")

    if gearbox := record.get(config.POST_SHEET_COLS['gearbox']):
        message_parts.append(f"<b>Коробка:</b> {html.escape(str(gearbox))}")

    if condition := record.get(config.POST_SHEET_COLS['condition']):
        message_parts.append(f"<b>Стан:</b> {html.escape(str(condition))}")
        
    if vin := record.get(config.POST_SHEET_COLS['vin']):
        message_parts.append(f"<b>ВІН-код:</b> <code>{html.escape(str(vin))}</code>")

    message = "\n\n".join(message_parts)
    
    if not message:
        message = "Знайдено авто, але для нього немає даних для відображення."

    keyboard = None
    if relative_link := record.get(config.POST_SHEET_COLS["ria_link"]):
        if "auto.ria.com" not in relative_link:
            full_link = f"https://auto.ria.com{relative_link}"
        else:
            full_link = relative_link
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Переглянути на RIA", url=full_link)]])
    
    photo_ids_str = record.get(config.POST_SHEET_COLS['photos'], "")
    first_photo_id = photo_ids_str.split(',')[0] if photo_ids_str else None

    try:
        if first_photo_id:
            await update.message.reply_photo(
                photo=first_photo_id,
                caption=message,
                parse_mode='HTML',
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text(message, parse_mode='HTML', reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Помилка під час відправки результату пошуку: {e}. Спроба відправити простий текст.")
        clean_message = message.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
        await update.message.reply_text(clean_message)

    await update.message.reply_text("Щось ще?", reply_markup=client_keyboard)
    return ConversationHandler.END


async def request_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data['request'] = {}
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Підбір авто з США 🇺🇸", callback_data='req_country_usa')],
        [InlineKeyboardButton("Підбір авто з Китаю 🇨🇳", callback_data='req_country_china')]
    ])
    await update.message.reply_text("Оберіть, звідки ви хочете привезти автомобіль:", reply_markup=keyboard)
    return config.SELECT_COUNTRY

async def select_country_usa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['request']['origin'] = "США"
    await query.edit_message_text("🇺🇸 **Підбір авто з США**\n\nЯка марка та модель вас цікавить? (напр. Audi Q7)")
    return config.REQ_USA_ASK_MODEL

async def req_usa_get_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['model'] = update.message.text
    await update.message.reply_text("Який тип та об'єм двигуна? (напр. Бензин 3.0)")
    return config.REQ_USA_ASK_ENGINE

async def req_usa_get_engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['engine'] = update.message.text
    await update.message.reply_text("Якого року випуску розглядаєте авто? (напр. 2017-2019)")
    return config.REQ_USA_ASK_YEAR

async def req_usa_get_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['year'] = update.message.text
    await update.message.reply_text("Який ваш орієнтовний бюджет 'під ключ' в USD?")
    return config.REQ_USA_ASK_BUDGET

async def req_usa_get_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['budget'] = update.message.text
    await update.message.reply_text("Залиште ваш контактний номер телефону:")
    return config.REQ_USA_ASK_CONTACT

async def req_usa_get_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['contact'] = update.message.text
    req = context.user_data['request']
    summary_text = (f"Перевірте вашу заявку (США):\n\n"
                    f"🚗 **Авто:** {req['model']}\n"
                    f"⚙️ **Двигун:** {req['engine']}\n"
                    f"📅 **Рік:** {req['year']}\n"
                    f"💰 **Бюджет:** {req['budget']} USD\n"
                    f"📞 **Контакт:** {req['contact']}\n\nВсе вірно?")
    await update.message.reply_text(summary_text, parse_mode='HTML', reply_markup=yes_no_keyboard)
    return config.REQ_USA_CONFIRM

async def select_country_china(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['request']['origin'] = "Китай"
    await query.edit_message_text("🇨🇳 **Підбір авто з Китаю**\n\nЯка марка та модель вас цікавить? (напр. BYD Seal)")
    return config.REQ_CHINA_ASK_MODEL

async def req_china_get_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['model'] = update.message.text
    await update.message.reply_text("Якого року випуску? (напр. 2022-2024)")
    return config.REQ_CHINA_ASK_YEAR

async def req_china_get_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['year'] = update.message.text
    await update.message.reply_text("Який бажаний пробіг, км? (напр. до 20000)")
    return config.REQ_CHINA_ASK_MILEAGE

async def req_china_get_mileage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['mileage'] = update.message.text
    await update.message.reply_text("Яка бажана ємність батареї, кВт·год? (напр. 75)")
    return config.REQ_CHINA_ASK_BATTERY

async def req_china_get_battery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['battery'] = update.message.text
    await update.message.reply_text("Який ваш орієнтовний бюджет 'під ключ' в USD?")
    return config.REQ_CHINA_ASK_BUDGET

async def req_china_get_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['budget'] = update.message.text
    await update.message.reply_text("Залиште ваш контактний номер телефону:")
    return config.REQ_CHINA_ASK_CONTACT

async def req_china_get_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['request']['contact'] = update.message.text
    req = context.user_data['request']
    summary_text = (f"Перевірте вашу заявку (Китай):\n\n"
                    f"🚗 **Авто:** {req['model']}\n"
                    f"📅 **Рік:** {req['year']}\n"
                    f"📉 **Пробіг:** {req['mileage']} км\n"
                    f"🔋 **Батарея:** {req['battery']} кВт·год\n"
                    f"💰 **Бюджет:** {req['budget']} USD\n"
                    f"📞 **Контакт:** {req['contact']}\n\nВсе вірно?")
    await update.message.reply_text(summary_text, parse_mode='HTML', reply_markup=yes_no_keyboard)
    return config.REQ_CHINA_CONFIRM

async def req_confirm_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if 'так' not in update.message.text.lower():
        await update.message.reply_text("Заявку скасовано.", reply_markup=client_keyboard)
        return ConversationHandler.END
    await update.message.reply_text("✅ Дякуємо! Вашу заявку прийнято.", reply_markup=client_keyboard)
    user, req = update.effective_user, context.user_data['request']
    origin = req.get('origin', 'N/A')
    details = ""
    if origin == "США":
        details = (f"🚗 **Авто:** {req.get('model', 'N/A')}\n"
                   f"⚙️ **Двигун:** {req.get('engine', 'N/A')}\n"
                   f"📅 **Рік:** {req.get('year', 'N/A')}\n")
    else:
        details = (f"🚗 **Авто:** {req.get('model', 'N/A')}\n"
                   f"📅 **Рік:** {req.get('year', 'N/A')}\n"
                   f"📉 **Пробіг:** {req.get('mileage', 'N/A')} км\n"
                   f"🔋 **Батарея:** {req.get('battery', 'N/A')} кВт·год\n")
    notification_text = (f"🔔 *Нова заявка ({origin})!*\n\n"
                         f"👤 **Клієнт:** {user.full_name} (@{user.username or 'N/A'})\n"
                         f"{details}"
                         f"💰 **Бюджет:** {req.get('budget', 'N/A')} USD\n"
                         f"📞 **Контакт:** `{req.get('contact', 'N/A')}`")
    try:
        await context.bot.send_message(chat_id=config.REQUESTS_CHANNEL_ID, text=notification_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Не вдалося надіслати заявку в канал {config.REQUESTS_CHANNEL_ID}: {e}")
    
    if gs_manager:
        sheet_data = {**req, **{"Дата": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Ім'я клієнта": user.full_name, "Username": f"@{user.username or 'N/A'}", "Статус": "Нова"}}
        sheet_name = config.SHEET_NAMES['requests']
        sheet = await gs_manager.get_sheet(sheet_name)
        if sheet:
            await gs_manager.add_row(sheet_name, sheet_data, list(sheet_data.keys()))
        else:
            logger.error(f"Sheet '{sheet_name}' not found. Request from {user.full_name} was not saved to Google Sheets.")
            try:
                await context.bot.send_message(
                    chat_id=config.OWNER_ID,
                    text=f"⚠️ Увага! Не вдалося зберегти заявку від {user.full_name}, оскільки аркуш '{sheet_name}' не знайдено."
                )
            except Exception as e:
                logger.error(f"Could not send missing sheet notification to owner: {e}")

    return ConversationHandler.END

def get_client_search_handler():
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔍 Наявний пошук$'), client_search_start)],
        states={config.CLIENT_SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, client_search_query)]},
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )

def get_request_handler():
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📝 Підбір авто$'), request_start)],
        states={
            config.SELECT_COUNTRY: [
                CallbackQueryHandler(select_country_usa, pattern='^req_country_usa$'),
                CallbackQueryHandler(select_country_china, pattern='^req_country_china$')
            ],
            config.REQ_USA_ASK_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_usa_get_model)],
            config.REQ_USA_ASK_ENGINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_usa_get_engine)],
            config.REQ_USA_ASK_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_usa_get_year)],
            config.REQ_USA_ASK_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_usa_get_budget)],
            config.REQ_USA_ASK_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_usa_get_contact)],
            config.REQ_USA_CONFIRM: [MessageHandler(filters.Regex('^(Так|Ні|так|ні)$'), req_confirm_and_send)],
            config.REQ_CHINA_ASK_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_china_get_model)],
            config.REQ_CHINA_ASK_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_china_get_year)],
            config.REQ_CHINA_ASK_MILEAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_china_get_mileage)],
            config.REQ_CHINA_ASK_BATTERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_china_get_battery)],
            config.REQ_CHINA_ASK_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_china_get_budget)],
            config.REQ_CHINA_ASK_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, req_china_get_contact)],
            config.REQ_CHINA_CONFIRM: [MessageHandler(filters.Regex('^(Так|Ні|так|ні)$'), req_confirm_and_send)]
        },
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_command)],
        per_message=False,
        allow_reentry=True
    )

