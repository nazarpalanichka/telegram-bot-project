# -*- coding: utf-8 -*-
# handlers/calculator.py

import logging
import re
import datetime
import math
from functools import partial
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler
)
from thefuzz import process

import config
from utils.helpers import escape_markdown_v2
from utils.g_sheets import GoogleSheetManager
from .start import cancel_command, start_command
from .keyboards import client_keyboard, get_employee_keyboard, yes_no_keyboard, auction_choice_keyboard

logger = logging.getLogger(__name__)
gs_manager = None

# --- Глобальні змінні для калькулятора ---
ALL_AUCTION_RATES = {}
COPART_LOCATIONS = []
IAAI_LOCATIONS = []

# Оновлений список фіксованих витрат на основі ваших даних
FIXED_COSTS = {
    "Вартість послуг компанії": 500,
    "Парковка в порту США": 200,
    "Вигрузка в порту (Бремерхафен)": 500,
    "Доставка автовозом (Бременхафен-Стрий)": 900,
    "Витрати по митниці (Україна/Польща)": 80,
}

# Фіксовані витрати для PRO-калькулятора (без послуг компанії)
PRO_FIXED_COSTS = FIXED_COSTS.copy()
if "Вартість послуг компанії" in PRO_FIXED_COSTS:
    del PRO_FIXED_COSTS["Вартість послуг компанії"]


# --- Функції завантаження та розрахунків ---

async def load_auction_data(gs_manager_instance: GoogleSheetManager):
    """Завантажує дані про тарифи аукціонів з Google Sheets."""
    global ALL_AUCTION_RATES, COPART_LOCATIONS, IAAI_LOCATIONS
    
    copart_rates = await parse_auction_data_from_gsheet(gs_manager_instance, config.SHEET_NAMES['copart'], "Copart")
    iaai_rates = await parse_auction_data_from_gsheet(gs_manager_instance, config.SHEET_NAMES['iaai'], "IAAI")

    ALL_AUCTION_RATES = {**copart_rates, **iaai_rates}
    COPART_LOCATIONS = sorted(list(copart_rates.keys()))
    IAAI_LOCATIONS = sorted(list(iaai_rates.keys()))

    if not ALL_AUCTION_RATES:
        logger.critical("Не вдалося завантажити жодних тарифів з Google Sheets.")
    else:
        logger.info(f"Успішно завантажено {len(ALL_AUCTION_RATES)} локацій з Google Sheets.")

async def parse_auction_data_from_gsheet(gs_manager_instance: GoogleSheetManager, sheet_name: str, auction_name: str) -> dict:
    rates = {}
    logger.info(f"Спроба завантажити тарифи з аркуша Google: '{sheet_name}'")
    try:
        sheet = await gs_manager_instance.get_sheet(sheet_name)
        if not sheet:
            logger.error(f"Аркуш '{sheet_name}' не знайдено.")
            return {}
        
        all_values = await gs_manager_instance._run_in_executor(sheet.get_all_values)
        if not all_values or len(all_values) < 2:
            logger.warning(f"Аркуш '{sheet_name}' порожній або містить тільки заголовок.")
            return {}

        all_rows = all_values[1:] 
        
        for i, row in enumerate(all_rows, 2):
            if len(row) < 5: continue
            
            location = row[1].strip()
            port = row[2].strip()
            rate_range_str = row[4].strip()
            
            if not all([location, port, rate_range_str]): continue
            
            rate_range_str = re.sub(r'[^\d-]', '', rate_range_str)
            if not rate_range_str: continue
            
            try:
                if '-' in rate_range_str:
                    rate_parts = rate_range_str.split('-')
                    if len(rate_parts) < 2 or not rate_parts[1]: continue
                    rate_range = (int(rate_parts[0]), int(rate_parts[1]))
                else:
                    rate_range = (int(rate_range_str), int(rate_range_str))
            except (ValueError, IndexError):
                logger.warning(f"Не вдалося розібрати діапазон '{rate_range_str}' у рядку {i} аркуша '{sheet_name}'")
                continue
                
            full_location_name = f"{auction_name}: {location}"
            rates[full_location_name] = {"port": port, "range": rate_range}

    except Exception as e:
        logger.error(f"Критична помилка під час завантаження даних з аркуша '{sheet_name}': {e}", exc_info=True)
        return {}
        
    logger.info(f"З аркуша '{sheet_name}' для '{auction_name}' завантажено {len(rates)} тарифів.")
    return rates

def calculate_copart_fees_detailed(bid: float) -> dict:
    """Розраховує збори Copart з повною деталізацією."""
    fees_map = {
        50: 1, 100: 1, 200: 25, 300: 60, 350: 85, 400: 100, 450: 125, 500: 135, 550: 145, 600: 155,
        700: 170, 800: 195, 900: 215, 1000: 230, 1200: 250, 1300: 270, 1400: 285, 1500: 300, 1600: 315,
        1700: 330, 1800: 350, 2000: 370, 2400: 390, 2500: 425, 3000: 460, 3500: 519, 4000: 569,
        4500: 619, 5000: 669, 5500: 650, 6000: 675, 6500: 700, 7000: 720, 7500: 755, 8000: 775,
        8500: 800, 9000: 820, 10000: 820, 10500: 850, 11000: 850, 11500: 850, 12000: 860, 12500: 875, 15000: 890,
    }
    buyer_fee = next((fee for limit, fee in fees_map.items() if bid < limit), bid * 0.06)
    
    vbid_map = {100:0, 500:50, 1000:65, 1500:85, 2000:95, 4000:110, 6000:125, 8000:145}
    virtual_bid_fee = next((fee for limit, fee in vbid_map.items() if bid < limit), 160)
    
    gate_fee = 95
    doc_fee = 10
    other_fee = 15
    
    total = round(buyer_fee + gate_fee + doc_fee + other_fee + virtual_bid_fee, 2)
    
    return {
        "total": total,
        "Збір покупця": buyer_fee,
        "Збір за віртуальну ставку": virtual_bid_fee,
        "Портовий збір (Gate)": gate_fee,
        "Інші збори (документи і т.д.)": doc_fee + other_fee
    }

def calculate_iaai_fees_detailed(bid: float) -> dict:
    """Розраховує збори IAAI з повною деталізацією."""
    if bid <= 0: return {"total": 0}
    base_fee_map = {
        100: 49, 200: 79, 300: 99, 400: 139, 500: 159, 600: 179, 700: 199, 800: 219, 900: 239,
        1000: 259, 1200: 289, 1400: 309, 1500: 319, 1600: 329, 1800: 349, 2000: 379, 2400: 399,
        2500: 419, 3000: 469, 3500: 519, 4000: 569, 4500: 619, 5000: 669,
    }
    base_fee = next((fee for limit, fee in base_fee_map.items() if bid < limit), 669 + (math.ceil((bid - 5000) / 500) * 50))
    internet_fee = 89
    service_fee = 95
    total = round(base_fee + internet_fee + service_fee, 2)
    
    return {
        "total": total,
        "Базовий збір": base_fee,
        "Інтернет-збір": internet_fee,
        "Сервісний збір": service_fee
    }

def calculate_auction_to_port_cost(location: str, rates_data: dict, pro_mode: bool = False) -> float | None:
    rate_info = rates_data.get(location)
    if rate_info and "range" in rate_info:
        _, upper = rate_info["range"]
        if pro_mode:
            return float(upper)
        else:
            return max(float(upper + 100), 500.0)
    logger.warning(f"Не вдалося знайти дані для доставки для: {location}")
    return None

def calculate_ukrainian_customs_taxes(year: int, engine_type: str, engine_volume: float | None, battery_capacity: float | None, customs_value: float) -> dict:
    """
    Розраховує митні платежі згідно з актуальним законодавством України.
    """
    mito = 0
    akcyz = 0
    pdv = 0
    EUR_TO_USD_RATE = 1.08  # Орієнтовний курс, який варто періодично оновлювати

    current_year = datetime.datetime.now().year
    age = current_year - year
    age_koeff = max(1, min(age, 15))

    engine_type_lower = engine_type.lower()

    # 1. Ввізне мито (Мито) - 10% від митної вартості (крім електромобілів)
    if engine_type_lower != "електро":
        mito = customs_value * 0.10
    
    akcyz_eur = 0

    # 2. Акцизний збір (Акциз)
    if engine_type_lower == "бензин":
        if engine_volume:
            base_rate = 50 if engine_volume <= 3000 else 100
            vol_koeff = engine_volume / 1000.0
            akcyz_eur = base_rate * vol_koeff * age_koeff
    
    elif engine_type_lower == "дизель":
        if engine_volume:
            base_rate = 75 if engine_volume <= 3500 else 150
            vol_koeff = engine_volume / 1000.0
            akcyz_eur = base_rate * vol_koeff * age_koeff

    elif engine_type_lower == "гібрид":
        # Фіксована ставка для гібридних авто
        akcyz_eur = 100

    elif engine_type_lower == "електро":
        # Ставка 1 євро за 1 кВт·год ємності батареї
        if battery_capacity:
            akcyz_eur = battery_capacity * 1
    
    akcyz = akcyz_eur * EUR_TO_USD_RATE

    # 3. ПДВ - 20% від суми (митна вартість + мито + акциз) (крім електромобілів)
    if engine_type_lower != "електро":
        pdv = (customs_value + mito + akcyz) * 0.20
    
    # 4. Загальна сума та деталізація
    broker_fee = 150  # Умовна вартість послуг брокера
    total = mito + akcyz + pdv + broker_fee
    
    return {
        "total": round(total, 2), 
        "duty": round(mito, 2), 
        "excise": round(akcyz, 2), 
        "vat": round(pdv, 2), 
        "broker_fee": broker_fee
    }

# --- Обробники розмови калькулятора ---

async def start_calculation_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, pro_mode: bool) -> int:
    context.user_data.clear()
    context.user_data['pro_mode'] = pro_mode
    await update.message.reply_text("Для якого аукціону робимо розрахунок?", reply_markup=auction_choice_keyboard)
    return config.ASK_AUCTION_TYPE

async def handle_auction_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.lower()
    if choice not in ['copart', 'iaai']:
        await update.message.reply_text("Будь ласка, оберіть аукціон за допомогою кнопок.", reply_markup=auction_choice_keyboard)
        return config.ASK_AUCTION_TYPE
    context.user_data['auction_type'] = choice
    await update.message.reply_text("Введіть прогнозовану ставку (USD):", reply_markup=ReplyKeyboardRemove())
    return config.ASK_BID

async def handle_bid_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        bid = float(update.message.text.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Будь ласка, введіть числове значення, наприклад: 15000")
        return config.ASK_BID
    if bid <= 0:
        await update.message.reply_text("Ставка має бути додатньою.")
        return config.ASK_BID
    context.user_data['bid'] = bid
    auction_type = context.user_data.get('auction_type')
    locations = COPART_LOCATIONS if auction_type == 'copart' else IAAI_LOCATIONS
    keyboard = [[KeyboardButton(loc)] for loc in locations[:20]]
    keyboard.append([KeyboardButton("Інша локація (ввести текстом)")])
    await update.message.reply_text("📍 Чудово! Тепер оберіть локацію аукціону:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return config.ASK_LOCATION

async def handle_location_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip()
    auction_type = context.user_data.get('auction_type')

    # FIX: Handle case where conversation state is lost and auction_type is None
    if not auction_type:
        logger.warning("auction_type not found in user_data during handle_location_input. Ending conversation.")
        await update.message.reply_text(
            "Вибачте, сталася помилка і я втратив дані про аукціон. Будь ласка, почніть розрахунок спочатку."
        )
        return await cancel_command(update, context)
    
    locations_to_search = COPART_LOCATIONS if auction_type == 'copart' else IAAI_LOCATIONS
    
    found_location = None
    if user_input in locations_to_search:
        found_location = user_input
    else:
        best_match = process.extractOne(user_input, locations_to_search)
        
        if best_match and best_match[1] > 80:
            found_location = best_match[0]
            await update.message.reply_text(f"Знайдено локацію: `{found_location}`. Продовжуємо розрахунок.", parse_mode='Markdown')
        else:
            potential_location = f"{auction_type.capitalize()}: {user_input}"
            best_match_with_prefix = process.extractOne(potential_location, locations_to_search)
            if best_match_with_prefix and best_match_with_prefix[1] > 85:
                 found_location = best_match_with_prefix[0]
                 await update.message.reply_text(f"Знайдено локацію: `{found_location}`. Продовжуємо розрахунок.", parse_mode='Markdown')

    if not found_location:
        error_text = f"Локацію '{escape_markdown_v2(user_input)}' не знайдено\\. Будь ласка, перевірте назву або скористайтесь кнопками зі списку\\."
        await update.message.reply_text(error_text, parse_mode='MarkdownV2')
        return config.ASK_LOCATION

    context.user_data['location'] = found_location
    question_text = "🛡️ Додати страхування?"
    await update.message.reply_text(question_text, reply_markup=yes_no_keyboard)
    return config.ASK_INSURANCE

async def handle_insurance_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip().lower()
    if choice not in ['так', 'ні']:
        await update.message.reply_text("Оберіть 'Так' або 'Ні'.")
        return config.ASK_INSURANCE
    context.user_data['insurance'] = (choice == 'так')
    await update.message.reply_text("📅 Майже готово! Введіть рік випуску авто (напр. 2018):", reply_markup=ReplyKeyboardRemove())
    return config.ASK_YEAR

async def handle_year_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        year = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Невірний формат. Введіть рік числом.")
        return config.ASK_YEAR
    if not 1980 <= year <= datetime.datetime.now().year + 1:
        await update.message.reply_text(f"Введіть коректний рік (від 1980 до {datetime.datetime.now().year + 1}).")
        return config.ASK_YEAR
    context.user_data['year'] = year
    keyboard = ReplyKeyboardMarkup([["Бензин", "Дизель"], ["Електро", "Гібрид"]], one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("⚙️ Останній крок! Оберіть тип двигуна:", reply_markup=keyboard)
    return config.ASK_ENGINE_TYPE

async def handle_engine_type_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    engine_type = update.message.text.strip().lower()
    if engine_type not in ["бензин", "дизель", "електро", "гібрид"]:
        await update.message.reply_text("Будь ласка, оберіть тип двигуна за допомогою кнопок.")
        return config.ASK_ENGINE_TYPE
    context.user_data['engine_type'] = engine_type
    
    if engine_type in ["бензин", "дизель"]:
        await update.message.reply_text("Введіть об'єм двигуна в см³ (напр. 1998):", reply_markup=ReplyKeyboardRemove())
        return config.ASK_VOLUME
    elif engine_type == "електро":
        await update.message.reply_text("Введіть ємність батареї в кВт-год (напр. 75): 🔌", reply_markup=ReplyKeyboardRemove())
        return config.ASK_ELECTRIC_PARAMS
    elif engine_type == "гібрид":
        await update.message.reply_text("Для гібриду об'єм не потрібен. Рахую...", reply_markup=ReplyKeyboardRemove())
        context.user_data['engine_volume'] = None
        context.user_data['battery_capacity'] = None
        return await perform_calculation_and_display(update, context)

async def handle_volume_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        volume = float(update.message.text.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Невірний формат. Введіть число (напр. 1998).")
        return config.ASK_VOLUME
    if volume < 0:
        await update.message.reply_text("Об'єм не може бути від'ємним.")
        return config.ASK_VOLUME
    context.user_data['engine_volume'] = volume
    await update.message.reply_text("⏳ Рахую вартість...")
    return await perform_calculation_and_display(update, context)

async def handle_electric_params_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        capacity = float(update.message.text.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Невірний формат. Введіть число (напр. 75).")
        return config.ASK_ELECTRIC_PARAMS
    if capacity <= 0:
        await update.message.reply_text("Ємність має бути додатньою.")
        return config.ASK_ELECTRIC_PARAMS
    context.user_data['battery_capacity'] = capacity
    context.user_data['engine_volume'] = None
    await update.message.reply_text("⏳ Рахую вартість...")
    return await perform_calculation_and_display(update, context)

async def perform_calculation_and_display(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    try:
        is_pro_mode = data.get('pro_mode', False)
        bid, location, year, engine_type, auction_type = data['bid'], data['location'], data['year'], data['engine_type'], data['auction_type']
        engine_volume, battery_capacity, insurance_chosen = data.get('engine_volume'), data.get('battery_capacity'), data['insurance']
        
        auction_fees_details = {}
        auction_fees = 0

        if is_pro_mode:
            if auction_type == 'iaai':
                auction_fees_details = calculate_iaai_fees_detailed(bid)
            else:
                auction_fees_details = calculate_copart_fees_detailed(bid)
            auction_fees = auction_fees_details.get("total", 0)
        else:
            auction_fees_func = calculate_iaai_fees_detailed if auction_type == 'iaai' else calculate_copart_fees_detailed
            auction_fees = auction_fees_func(bid)["total"]

        if is_pro_mode:
            swift_fee = (bid + auction_fees) * 0.03
            insurance_cost = (bid + auction_fees) * 0.02 if insurance_chosen else 0
            fixed_costs_total = sum(PRO_FIXED_COSTS.values())
        else:
            swift_fee = ((bid + auction_fees) * 0.03) + 100
            insurance_cost = (bid + auction_fees) * 0.02 if insurance_chosen else 0
            fixed_costs_total = sum(FIXED_COSTS.values())

        auction_to_port_cost = calculate_auction_to_port_cost(location, ALL_AUCTION_RATES, pro_mode=is_pro_mode)
        if auction_to_port_cost is None:
            await update.message.reply_text(f"Не вдалося розрахувати доставку з {escape_markdown_v2(location)}\\.", parse_mode='MarkdownV2')
            return await cancel_command(update, context)
        
        port_name = ALL_AUCTION_RATES.get(location, {}).get("port", "N/A")
        ocean_freight_cost = 1600 if port_name == "Los Angeles" else 900
        customs_value_base = bid + auction_fees + auction_to_port_cost + ocean_freight_cost
        customs_details = calculate_ukrainian_customs_taxes(year, engine_type, engine_volume, battery_capacity, customs_value_base)
        
        total_cost = (bid + auction_fees + swift_fee + insurance_cost + fixed_costs_total + auction_to_port_cost + ocean_freight_cost + customs_details["total"])
        
        if is_pro_mode:
            res = {
                "Ставка": bid, "Доставка США": auction_to_port_cost, 
                "SWIFT (3%)": swift_fee, "Страхування (2%)": insurance_cost, "Доставка морем": ocean_freight_cost, 
                "Мито": customs_details["duty"], "Акциз": customs_details["excise"], "ПДВ": customs_details["vat"], 
                "Брокер": customs_details["broker_fee"],
                "Разом розмитнення": customs_details["total"],
                "Загальна вартість": total_cost
            }

            message_parts = [
                f"<b>PRO РОЗРАХУНОК (СОБІВАРТІСТЬ)</b>",
                f"<i>Аукціон: {auction_type.upper()}</i>\n",
                f"<b>1. Витрати в США:</b>",
                f" • Ставка: <code>${res['Ставка']:,.2f}</code>"
            ]

            # Деталізація зборів аукціону
            message_parts.append(f" • <b>Збори аукціону (Разом: <code>${auction_fees:,.2f}</code>):</b>")
            for fee_name, fee_value in auction_fees_details.items():
                if fee_name != "total":
                    message_parts.append(f"    - {fee_name}: <code>${fee_value:,.2f}</code>")

            message_parts.extend([
                f" • Доставка по США ({location}): <code>${res['Доставка США']:,.2f}</code>",
                f" • SWIFT (3%): <code>${res['SWIFT (3%)']:,.2f}</code>",
                f" • Страхування (2%): <code>${res['Страхування (2%)']:,.2f}</code>\n",
                f"<b>2. Логістика:</b>",
                f" • Доставка морем: <code>${res['Доставка морем']:,.2f}</code>\n",
                f"<b>3. Розмитнення (Разом: <code>${res['Разом розмитнення']:,.2f}</code>):</b>",
                f" • Мито: <code>${res['Мито']:,.2f}</code>",
                f" • Акциз: <code>${res['Акциз']:,.2f}</code>",
                f" • ПДВ: <code>${res['ПДВ']:,.2f}</code>",
                f" • Брокер: <code>${res['Брокер']:,.2f}</code>\n",
                f"<b>4. Інші послуги (без комісії):</b>"
            ])

            # Деталізація фіксованих витрат
            for cost_name, cost_value in PRO_FIXED_COSTS.items():
                message_parts.append(f" • {cost_name}: <code>${cost_value:,.2f}</code>")

            message_parts.extend([
                "――――――――――――――――――",
                f"<b>ЗАГАЛЬНА СОБІВАРТІСТЬ:</b> <code>${res['Загальна вартість']:,.2f}</code>"
            ])
            
            message = "\n".join(message_parts)
            await update.message.reply_text(message, parse_mode='HTML')
            
            if gs_manager:
                await update.message.reply_text("Зберегти цей розрахунок?", reply_markup=yes_no_keyboard)
                return config.SAVE_CALC_CHOICE
            return ConversationHandler.END
        else:
            # Клієнтський розрахунок залишається без змін
            message = (f"🎉 <b>Ваш розрахунок готовий!</b> 🎉\n\n"
                       f"Орієнтовна вартість авто в Україні \"під ключ\":\n"
                       f"💵 <b>${total_cost:,.2f}</b> 💵\n\n"
                       f"✅ <b>У вартість входить:</b>\n"
                       f"  - Послуги компанії, покупка, доставка\n"
                       f"  - Розмитнення, усі збори та комісії\n\n"
                       f"⚠️ <b>У вартість НЕ входить ремонт.</b>\n\n"
                       f"Для детальної консультації звертайтесь:\n"
                       f"📞 <b>0953362931 (Назар)</b>\n"
                       f"📲 <b>Telegram:</b> @Nazar_Itrans")
            await update.message.reply_text(message, parse_mode='HTML', reply_markup=client_keyboard)
            return ConversationHandler.END
    except Exception as e:
        logger.error(f"Помилка при розрахунку: {e}", exc_info=True)
        await update.message.reply_text("Вибачте, сталася помилка. Спробуйте знову /start.")
        return await cancel_command(update, context)

async def save_calc_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip().lower() == 'так':
        await update.message.reply_text("Введіть ВІН-код автомобіля:", reply_markup=ReplyKeyboardRemove())
        return config.SAVE_CALC_VIN
    await update.message.reply_text("Розрахунок не збережено.", reply_markup=get_employee_keyboard(update.effective_user.id))
    return ConversationHandler.END

async def save_calc_vin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['vin'] = update.message.text.strip().upper()
    await update.message.reply_text("Введіть назву авто:")
    return config.SAVE_CALC_MODEL

async def save_calc_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['model'] = update.message.text.strip()
    await update.message.reply_text("Введіть ім'я або контакт клієнта:")
    return config.SAVE_CALC_CLIENT

async def save_calculation_to_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    raw_results = data.get('calculation_results_raw', {})
    
    row_data = {
        config.CAR_SHEET_COLS["vin"]: data.get('vin'), 
        config.CAR_SHEET_COLS["model"]: data.get('model'),
        config.CAR_SHEET_COLS["price"]: f"{data.get('total_cost', 0):.2f}",
        config.CAR_SHEET_COLS["notes"]: f"Розрахунок для: {update.message.text.strip()}",
        "Дата оновлення": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    employee_keyboard = get_employee_keyboard(update.effective_user.id)
    if gs_manager and await gs_manager.add_row(config.SHEET_NAMES['in_transit_usa'], row_data, config.CAR_SHEET_HEADER_ORDER):
        await update.message.reply_text("✅ Успішно збережено!", reply_markup=employee_keyboard)
    else:
        await update.message.reply_text("❌ Помилка збереження.", reply_markup=employee_keyboard)
    return ConversationHandler.END


def get_calculator_handler() -> ConversationHandler:
    """Створює та повертає обробник розмови для калькулятора."""
    
    calc_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("PRO Розрахунок"), partial(start_calculation_flow, pro_mode=True)),
            MessageHandler(filters.Regex("Розрахувати вартість"), partial(start_calculation_flow, pro_mode=False))
        ],
        states={
            config.ASK_AUCTION_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_auction_type)],
            config.ASK_BID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bid_input)],
            config.ASK_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_location_input)],
            config.ASK_INSURANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_insurance_input)],
            config.ASK_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_year_input)],
            config.ASK_ENGINE_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_engine_type_input)],
            config.ASK_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_volume_input)],
            config.ASK_ELECTRIC_PARAMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_electric_params_input)],
            config.SAVE_CALC_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_calc_choice)],
            config.SAVE_CALC_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_calc_vin)],
            config.SAVE_CALC_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_calc_model)],
            config.SAVE_CALC_CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_calculation_to_sheet)]
        },
        fallbacks=[CommandHandler("start", start_command), CommandHandler("cancel", cancel_command)],
        allow_reentry=True
    )
    return calc_handler
