from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler

from utils.auth import manager_required
from handlers.keyboards import get_admin_panel_keyboard

@manager_required
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Надсилає менеджеру головну панель керування
    з вбудованими кнопками (Inline Keyboard).
    """
    user = update.effective_user
    text = f"🔐 Вітаю в адмін-панелі, {user.first_name}!\n\nОберіть дію:"
    
    # Отримуємо розмітку клавіатури для адмін-панелі
    reply_markup = get_admin_panel_keyboard()
    
    # Надсилаємо повідомлення з клавіатурою
    await update.message.reply_text(text, reply_markup=reply_markup)

# Створюємо екземпляр обробника для команди /admin
admin_handler = CommandHandler('admin', admin_panel)

