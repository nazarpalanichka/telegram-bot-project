from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from handlers.keyboards import get_main_menu_keyboard
from utils.auth import MANAGER_IDS # Імпортуємо множину ID менеджерів

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Надсилає вітальне повідомлення та відповідне головне меню
    в залежності від ролі користувача (клієнт чи менеджер).
    """
    user = update.effective_user
    
    # Перевіряємо, чи є ID користувача у списку менеджерів
    user_is_manager = user.id in MANAGER_IDS
    
    welcome_message = (
        f"👋 Вітаю, {user.first_name}!\n\n"
        "Я бот-асистент компанії <b>iTrans Motors</b>. "
        "Тут ви можете переглянути актуальні авто в наявності та в дорозі, "
        "або розрахувати вартість пригону авто вашої мрії.\n\n"
        "Оберіть дію з меню нижче:"
    )
    
    # Створюємо меню, передаючи статус користувача
    reply_markup = get_main_menu_keyboard(is_manager=user_is_manager)
    
    await update.message.reply_text(
        welcome_message,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

start_handler = CommandHandler('start', start)

