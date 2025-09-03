# -*- coding: utf-8 -*-
# handlers/start.py

import logging
from telegram import Update, LinkPreviewOptions
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler
)
import config
from utils.helpers import escape_markdown_v2
# Використовуємо нові, розділені клавіатури
from .keyboards import client_keyboard, get_employee_keyboard

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обробляє команду /start, показуючи відповідне меню."""
    user = update.effective_user
    context.user_data.clear()

    if user.id in config.ALLOWED_USER_IDS:
        logger.info(f"Співробітник {user.id} ({user.full_name}) розпочав роботу.")
        employee_keyboard = get_employee_keyboard(user.id)
        await update.message.reply_html(
            f"<b>Вітаю, {user.full_name}!</b> 👋\n\nВи увійшли в робочу панель. Оберіть дію:",
            reply_markup=employee_keyboard
        )
    else:
        logger.info(f"Клієнт {user.id} ({user.full_name}) розпочав роботу.")
        start_message = (
            "<b>Вітаю!</b> 👋\n\n"
            "Я - ваш персональний помічник від <b>iTrans Motors</b>.\n\n"
            "Чим можу допомогти?"
        )
        await update.message.reply_html(start_message, reply_markup=client_keyboard)

    return ConversationHandler.END


async def contact_us_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробляє кнопку 'Зв'язатися з нами'."""
    contact_text = (
        "<b>Наші контакти:</b>\n\n"
        "📞 <a href='tel:+380953362931'>095 336 29 31</a> - Назар\n"
        "📲 Telegram: @Nazar_Itrans\n\n"
        "📍 <b>Адреса:</b>\n"
        "м. Стрий, вул. Львівська, 186 б\n\n"
        "Чекаємо на ваш дзвінок або повідомлення!"
    )
    await update.message.reply_html(contact_text, disable_web_page_preview=True)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Скасовує поточну операцію та повертає в головне меню."""
    user = update.effective_user
    context.user_data.clear()
    
    is_employee = user.id in config.ALLOWED_USER_IDS
    reply_markup = get_employee_keyboard(user.id) if is_employee else client_keyboard
    
    message_text = "Дію скасовано. Повертаюся в головне меню."
    
    # Якщо команда була викликана з кнопки, редагуємо повідомлення
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(message_text)
            # Додатково надсилаємо повідомлення, щоб показати клавіатуру
            await context.bot.send_message(chat_id=user.id, text="Головне меню:", reply_markup=reply_markup)
        except Exception:
            # Якщо редагування не вдалося (напр., повідомлення застаріло), просто надсилаємо нове
            await update.message.reply_text(message_text, reply_markup=reply_markup)
    else:
         await update.message.reply_text(message_text, reply_markup=reply_markup)

    return ConversationHandler.END
