from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# --- ОСНОВНА КЛАВІАТУРА (REPLY KEYBOARD) ---

def get_main_menu_keyboard(is_manager: bool = False):
    """
    Створює та повертає розмітку клавіатури головного меню.
    Якщо is_manager=True, додає кнопку доступу до адмін-панелі.
    """
    # Спільні кнопки для всіх користувачів
    keyboard = [
        [KeyboardButton("🚗 Каталог Авто"), KeyboardButton("💰 Калькулятор")],
        [KeyboardButton(" аукціони"), KeyboardButton("👤 Мій кабінет")],
        [KeyboardButton("📞 Зв'язатися з менеджером")]
    ]
    
    # Якщо користувач є менеджером, додаємо йому кнопку адмін-панелі
    if is_manager:
        admin_button = [KeyboardButton("🔐 Адмін-панель")]
        # Вставляємо адмін-кнопку першим рядом для пріоритету
        keyboard.insert(0, admin_button)
            
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )


# --- ВБУДОВАНІ КЛАВІАТУРИ (INLINE KEYBOARDS) ---

def get_admin_panel_keyboard():
    """
    Створює та повертає клавіатуру для адмін-панелі.
    """
    keyboard = [
        [InlineKeyboardButton("➕ Додати авто", callback_data="admin_add_car")],
        [InlineKeyboardButton("✏️ Редагувати авто", callback_data="admin_edit_car")],
        [InlineKeyboardButton("🏁 Позначити як продане", callback_data="admin_mark_sold")],
        [InlineKeyboardButton("💰 Керування фінансами", callback_data="admin_finance_menu")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
    ]
    return InlineKeyboardMarkup(keyboard)

