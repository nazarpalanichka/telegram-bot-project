import logging
from telegram.ext import Application, MessageHandler, filters

from config import TOKEN
from utils.g_sheets import setup_gspread_client
from utils.auth import load_managers_from_sheet

# Імпортуємо наші обробники
from handlers.start import start_handler
from handlers.admin import admin_panel, admin_handler # Імпортуємо функцію admin_panel

# Налаштування логування
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

def main() -> None:
    """Запускає бота."""
    # Ініціалізація клієнта Google Sheets
    setup_gspread_client()
    # Завантаження ID менеджерів з таблиці
    load_managers_from_sheet()

    # Створення Application
    application = Application.builder().token(TOKEN).build()

    # --- РЕЄСТРАЦІЯ ОБРОБНИКІВ ---

    # 1. Обробник команди /start
    application.add_handler(start_handler)

    # 2. Обробник команди /admin
    application.add_handler(admin_handler)
    
    # 3. НОВИЙ ОБРОБНИК для кнопки "Адмін-панель"
    # Він реагує на текст і викликає ту саму функцію, що й /admin
    admin_button_handler = MessageHandler(filters.Text(["🔐 Адмін-панель"]), admin_panel)
    application.add_handler(admin_button_handler)

    # Запуск бота
    logger.info("Starting bot...")
    application.run_polling()

if __name__ == "__main__":
    main()

