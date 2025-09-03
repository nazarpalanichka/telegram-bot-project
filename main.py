# -*- coding: utf-8 -*-
# main.py

import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    CommandHandler
)

import config
from utils.g_sheets import GoogleSheetManager
from utils.sync import synchronize_working_sheets
from utils.g_sheets_extras import ensure_columns_exist
from handlers.channel_sold_patch import get_sold_patch_handler

# Handlers (модулі)
from handlers import (
    start,
    calculator,
    client,
    auctions,
    catalog,
    cabinet,
    channel,
    ria,
    finance,
    filter as car_filter,
    notes,
    vin_decoder,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def main() -> None:
    """Основна функція запуску бота."""
    gs_manager = GoogleSheetManager(
        "credentials.json",
        "1RpWYzUuFzSabGBHegXhedk5FtxOMaABq-tca-c9LhWI"
    )
    if not await gs_manager.authorize():
        logger.critical("Не вдалося авторизуватися в Google Sheets. Перевірте credentials.json та доступ до таблиці.")
        return

    application = Application.builder().token(config.TOKEN).build()
    
    # Передаємо gs_manager в усі модулі
    modules = [start, calculator, client, auctions, catalog, cabinet, channel, ria, finance, car_filter, notes, vin_decoder]
    for module in modules:
        module.gs_manager = gs_manager
    
    application.bot_data['gs_manager'] = gs_manager

    # --- ПЕРІОДИЧНІ ЗАВДАННЯ ---
    scheduler = AsyncIOScheduler(timezone="Europe/Kiev")
    scheduler.add_job(synchronize_working_sheets, 'interval', minutes=30, args=[gs_manager], name="Sync working sheets")
    # УВІМКНЕНО: Функції реалізовано, планувальник працює
    scheduler.add_job(channel.check_reminders, 'interval', minutes=1, args=[application], name="Check reminders")
    scheduler.add_job(ria.check_autoria_postings, 'interval', hours=6, args=[application], name="Check AutoRIA")
    scheduler.start()
    logger.info("Всі періодичні завдання заплановано.")

    # --- РЕЄСТРАЦІЯ ОБРОБНИКІВ ---
    
    # --- Клієнтські команди ---
    application.add_handler(CommandHandler("start", start.start_command))
    application.add_handler(MessageHandler(filters.Regex("^🚗 Каталог авто в наявності$"), catalog.catalog_start))
    application.add_handler(MessageHandler(filters.Regex("^🧮 Калькулятор авто із США$"), calculator.calculator_start))
    application.add_handler(MessageHandler(filters.Regex("^📝 Підбір авто \\(заявка\\)$"), client.request_start))
    application.add_handler(MessageHandler(filters.Regex("^📞 Зв'язатися з нами$"), start.contact_us_command))
    application.add_handler(car_filter.get_filter_handler()) 
    application.add_handler(catalog.get_catalog_handler())

    # --- Менеджерські команди ---
    application.add_handler(MessageHandler(filters.Regex("^➕ Додати авто / 📢 Пост$"), channel.channel_menu))
    application.add_handler(MessageHandler(filters.Regex("^📊 Фінанси / Угоди$"), finance.finance_menu))
    application.add_handler(MessageHandler(filters.Regex("^🚀 Auto.RIA$"), ria.ria_menu))
    application.add_handler(MessageHandler(filters.Regex("^📋 Мої нотатки$"), notes.notes_start))
    application.add_handler(MessageHandler(filters.Regex("^📈 Статистика$"), cabinet.show_fleet_stats))
    application.add_handler(MessageHandler(filters.Regex("^🔍 Пошук по базі$"), cabinet.search_car_by_vin_start))

    # --- Загальні обробники та ConversationHandlers ---
    application.add_handler(channel.get_add_or_publish_handler())
    application.add_handler(finance.get_finance_handler())
    application.add_handler(ria.get_ria_add_handler())
    application.add_handler(ria.get_ria_publish_draft_handler())
    application.add_handler(ria.get_ria_renew_handler())
    application.add_handler(ria.get_ria_sync_handler())
    application.add_handler(client.get_request_handler())
    application.add_handler(cabinet.get_owner_panel_handler())
    application.add_handler(cabinet.get_fix_fuel_handler())
    application.add_handler(ria.get_ria_link_handler())
    application.add_handler(vin_decoder.get_vin_info_handler())
    application.add_handler(notes.get_notes_handler())
    application.add_handler(get_sold_patch_handler())
    application.add_handler(cabinet.get_search_handler())

    # --- Додаткові обробники callback-ів ---
    application.add_handler(CallbackQueryHandler(auctions.show_selected_auction_list, pattern=r"^show_auction_"))
    application.add_handler(CallbackQueryHandler(auctions.locations_page_callback, pattern=r"^locpage_"))
    application.add_handler(CallbackQueryHandler(channel.reminder_action_callback, pattern=r"^remind_"))
    application.add_handler(CallbackQueryHandler(ria.ria_renew_callback, pattern=r"^ria_renew_"))
    
    await application.bot.set_my_commands([
        BotCommand("start", "Перезапустити бота"),
        BotCommand("cancel", "Скасувати поточну дію")
    ])
    logger.info("Команди меню бота встановлено.")
    
    await application.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Критична помилка при запуску бота: {e}", exc_info=True)

