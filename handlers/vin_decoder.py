# -*- coding: utf-8 -*-
# handlers/vin_decoder.py

import logging
import requests
import html
from telegram import Update
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler
)

import config
from .start import cancel_command
from .keyboards import client_keyboard, get_employee_keyboard

logger = logging.getLogger(__name__)

# Визначаємо стани розмови
GET_VIN_FOR_DECODING = range(1)

# API ендпоінти NHTSA
NHTSA_DECODE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
NHTSA_RECALLS_URL_BY_MODEL = "https://api.nhtsa.gov/recalls/v2/byVehicle"


# Словник для перекладу термінів з API
TRANSLATION_DICT = {
    # Fuel Types
    "Gasoline": "Бензин",
    "Diesel": "Дизель",
    "Flexible Fuel Vehicle (FFV)": "Адаптивний (Flex-Fuel)",
    "Hybrid": "Гібрид",
    "Electric": "Електро",
    "Plug-in Hybrid": "Плагін-гібрид",
    # Drive Types
    "FWD/Front-Wheel Drive": "Передній привід (FWD)",
    "RWD/Rear-Wheel Drive": "Задній привід (RWD)",
    "AWD/All-Wheel Drive": "Повний привід (AWD)",
    "4WD/4-Wheel Drive/4x4": "Повний привід (4WD/4x4)",
    # Vehicle Types
    "PASSENGER CAR": "Легковий автомобіль",
    "MULTIPURPOSE PASSENGER VEHICLE (MPV)": "Багатоцільовий автомобіль (MPV/SUV)",
    "TRUCK": "Вантажівка / Пікап",
    # Transmission
    "Automatic": "Автоматична",
    "Manual": "Механічна",
    "Continuously Variable Transmission (CVT)": "Варіатор (CVT)",
    "Automated Manual Transmission (AMT)": "Роботизована (AMT)",
}

def translate_term(term: str) -> str:
    """Перекладає термін, використовуючи словник, або повертає оригінал."""
    return TRANSLATION_DICT.get(term, term)

async def start_vin_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Починає розмову для отримання інформації по VIN."""
    await update.message.reply_text("Будь ласка, надішліть 17-значний VIN-код автомобіля з США:")
    return GET_VIN_FOR_DECODING

async def get_vin_and_decode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує VIN, робить запит до API та повертає результат."""
    vin = update.message.text.strip().upper()
    user_id = update.effective_user.id

    if len(vin) != 17 or not vin.isalnum():
        await update.message.reply_text("❌ Помилка. VIN-код повинен складатися з 17 літер та цифр. Спробуйте ще раз або натисніть /cancel.")
        return GET_VIN_FOR_DECODING

    await update.message.reply_text("🔎 Роблю запити до бази NHTSA... Зачекайте хвилинку.")

    try:
        # --- Перший запит: Основні дані ---
        response = requests.get(NHTSA_DECODE_URL.format(vin=vin))
        response.raise_for_status()
        data = response.json()

        if not data or not data.get('Results'):
            await update.message.reply_text("🤷‍♂️ Інформацію по цьому VIN не знайдено.")
            return ConversationHandler.END

        car_info = data['Results'][0]
        
        if not car_info.get('Make') or car_info.get('ErrorCode') != '0':
            error_text = car_info.get('ErrorText', 'Невідома помилка')
            await update.message.reply_text(
                f"🤷‍♂️ Не вдалося розшифрувати VIN.\nПричина: {error_text}.\n\n"
                "Перевірте, чи VIN-код коректний і належить автомобілю з ринку США."
            )
            return ConversationHandler.END

        # --- Форматуємо відповідь з перекладом ---
        message_parts = [
            f"<b>⚙️ Інформація по VIN:</b> <code>{html.escape(vin)}</code>\n",
            f"<b>- Рік:</b> {html.escape(car_info.get('ModelYear', 'Н/Д'))}",
            f"<b>- Марка:</b> {html.escape(car_info.get('Make', 'Н/Д'))}",
            f"<b>- Модель:</b> {html.escape(car_info.get('Model', 'Н/Д'))}",
            f"<b>- Комплектація (Trim):</b> {html.escape(car_info.get('Trim', 'Н/Д'))}",
            f"<b>- Тип кузова:</b> {html.escape(translate_term(car_info.get('VehicleType', 'Н/Д')))}",
            f"<b>- Двигун (циліндри):</b> {html.escape(car_info.get('EngineCylinders', 'Н/Д'))}",
            f"<b>- Об'єм двигуна (л):</b> {html.escape(car_info.get('DisplacementL', 'Н/Д'))}",
            f"<b>- Тип пального:</b> {html.escape(translate_term(car_info.get('FuelTypePrimary', 'Н/Д')))}",
            f"<b>- Привід:</b> {html.escape(translate_term(car_info.get('DriveType', 'Н/Д')))}",
            f"<b>- Трансмісія:</b> {html.escape(translate_term(car_info.get('TransmissionStyle', 'Н/Д')))}",
            f"<b>- Країна виробник:</b> {html.escape(car_info.get('PlantCountry', 'Н/Д'))}"
        ]
        
        # --- Другий запит: Відкличні кампанії (Recalls) ---
        try:
            # Отримуємо дані для запиту з першого результату
            make = car_info.get('Make')
            model = car_info.get('Model')
            year = car_info.get('ModelYear')

            if make and model and year:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                params = {
                    'make': make,
                    'model': model,
                    'modelYear': year
                }
                recall_response = requests.get(NHTSA_RECALLS_URL_BY_MODEL, params=params, headers=headers, timeout=10)
                
                # *** ВИПРАВЛЕННЯ: Обробляємо помилку 403, але не зупиняємо виконання ***
                if recall_response.status_code == 403:
                    logger.warning(f"Отримано 403 Forbidden при запиті відкличних кампаній для {make} {model} {year}.")
                    message_parts.append("\n🚨 <b>Перевірка на відкличні кампанії:</b>")
                    message_parts.append("- ⚠️ Сервіс тимчасово недоступний.")
                else:
                    recall_response.raise_for_status()
                    recall_data = recall_response.json()
                    recall_count = recall_data.get('Count', 0)

                    message_parts.append("\n🚨 <b>Перевірка на відкличні кампанії:</b>")
                    if recall_count > 0:
                        message_parts.append(f"- Знайдено відкритих відкличних кампаній для цієї моделі: <b>{recall_count}</b>")
                    else:
                        message_parts.append("- ✅ Відкритих відкличних кампаній для цієї моделі не знайдено.")
            else:
                raise ValueError("Make, Model, or Year is missing for recall lookup.")

        except (requests.exceptions.RequestException, ValueError) as recall_e:
            logger.warning(f"Не вдалося перевірити відкличні кампанії для VIN {vin}: {recall_e}")
            message_parts.append("\n🚨 <b>Перевірка на відкличні кампанії:</b>\n- Не вдалося отримати дані.")

        message_parts.append("\n<i>Дані надано NHTSA (США)</i>")
        final_message = "\n".join(filter(None, message_parts))
        
        reply_markup = get_employee_keyboard(user_id) if user_id in config.ALLOWED_USER_IDS else client_keyboard
        
        await update.message.reply_html(final_message, reply_markup=reply_markup)

    except requests.exceptions.RequestException as e:
        logger.error(f"Помилка запиту до NHTSA API: {e}")
        await update.message.reply_text("❌ Сталася помилка мережі при спробі отримати дані. Спробуйте пізніше.")
    except Exception as e:
        logger.error(f"Невідома помилка при розшифровці VIN: {e}", exc_info=True)
        await update.message.reply_text("❌ Сталася невідома помилка. Спробуйте пізніше.")

    return ConversationHandler.END


def get_vin_info_handler() -> ConversationHandler:
    """Створює обробник для розмови про розшифровку VIN."""
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^VIN Інфо 🇺🇸$"), start_vin_info)],
        states={
            GET_VIN_FOR_DECODING: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_vin_and_decode)]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )
