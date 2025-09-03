# -*- coding: utf-8 -*-
"""
handlers/keyboards.py — модуль з клавіатурами для бота.
Повністю перероблено для розділення меню клієнта та менеджера.
"""
from telegram import ReplyKeyboardMarkup
import config

# --- НОВЕ КЛІЄНТСЬКЕ МЕНЮ ---
client_keyboard: ReplyKeyboardMarkup = ReplyKeyboardMarkup(
    [
        ["🚗 Каталог авто в наявності"],
        ["🧮 Калькулятор авто із США"],
        ["📝 Підбір авто (заявка)"],
        ["📞 Зв'язатися з нами"]
    ],
    resize_keyboard=True,
)

# --- УНІВЕРСАЛЬНІ КЛАВІАТУРИ ---
yes_no_keyboard: ReplyKeyboardMarkup = ReplyKeyboardMarkup(
    [["Так", "Ні"]], resize_keyboard=True, one_time_keyboard=True
)

auction_choice_keyboard: ReplyKeyboardMarkup = ReplyKeyboardMarkup(
    [["Copart", "IAAI"]], resize_keyboard=True, one_time_keyboard=True
)


# --- НОВЕ МЕНЮ СПІВРОБІТНИКА ---
def get_employee_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """
    Повертає клавіатуру для співробітника.
    Власник бачить додаткові кнопки.
    """
    keyboard_layout = [
        ["➕ Додати авто / 📢 Пост", "📊 Фінанси / Угоди"],
        ["🚀 Auto.RIA", "📋 Мої нотатки"],
        ["📈 Статистика", "🔍 Пошук по базі"],
    ]
    # Якщо це власник, можна додати спец. кнопки
    # if user_id == config.OWNER_ID:
    #     keyboard_layout.append(["👑 Панель власника"])

    return ReplyKeyboardMarkup(keyboard_layout, resize_keyboard=True)
