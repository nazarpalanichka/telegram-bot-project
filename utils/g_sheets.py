import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging
from config import SHEET_NAMES
from datetime import datetime
import time

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Області доступу для API та шлях до файлу облікових даних
SCOPE = ["https://spreadsheets.google.com/feeds",
         'https://www.googleapis.com/auth/spreadsheets',
         "https://www.googleapis.com/auth/drive.file",
         "https://www.googleapis.com/auth/drive"]
CREDS_FILE = 'credentials.json'

# Глобальна змінна для об'єкта таблиці
spreadsheet = None

def authorize_gspread():
    """Авторизується в Google Sheets і повертає об'єкт таблиці."""
    global spreadsheet
    if spreadsheet:
        return spreadsheet
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
        client = gspread.authorize(creds)
        spreadsheet = client.open("iTrans_Motors")
        logging.info("Успішно підключено до Google таблиці 'iTrans_Motors'")
        return spreadsheet
    except Exception as e:
        logging.error(f"Не вдалося підключитися до Google таблиці: {e}")
        return None

# Ініціалізація підключення при завантаженні модуля
spreadsheet = authorize_gspread()

# --- УНІВЕРСАЛЬНА ФУНКЦІЯ ДОСТУПУ ДО АРКУШІВ ---
def get_worksheet_by_name(sheet_name):
    """
    Отримує аркуш (worksheet) за його назвою.
    Це централізована функція для доступу до будь-якого аркуша.
    """
    if not spreadsheet:
        logging.error("Об'єкт таблиці не ініціалізовано. Спроба повторної авторизації...")
        authorize_gspread()
        if not spreadsheet:
            logging.error("Повторна авторизація не вдалася. Неможливо отримати аркуш.")
            return None
    
    try:
        return spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        logging.error(f"Аркуш з назвою '{sheet_name}' не знайдено.")
        return None
    except Exception as e:
        logging.error(f"Сталася помилка при отриманні аркуша '{sheet_name}': {e}")
        return None

# --- ВАШІ ІСНУЮЧІ ФУНКЦІЇ, ОНОВЛЕНІ ДЛЯ НАДІЙНОСТІ ---

def find_car_in_sheets(vin_code):
    """Шукає автомобіль за VIN-кодом у всіх відповідних аркушах."""
    sheets_to_search = [
        SHEET_NAMES['all_cars'],
        SHEET_NAMES['sydora_site'],
        SHEET_NAMES['halytska_site'],
        SHEET_NAMES['in_transit_usa'],
        SHEET_NAMES['in_transit_china']
    ]
    for sheet_name in sheets_to_search:
        worksheet = get_worksheet_by_name(sheet_name)
        if not worksheet:
            continue
        try:
            cell = worksheet.find(vin_code, in_column=2)
            if cell:
                row_values = worksheet.row_values(cell.row)
                headers = worksheet.row_values(1)
                car_data = dict(zip(headers, row_values))
                return car_data, worksheet
        except gspread.exceptions.CellNotFound:
            continue
        except Exception as e:
            logging.error(f"Помилка пошуку VIN {vin_code} в аркуші '{sheet_name}': {e}")
    return None, None

def update_car_in_sheet(vin_code, updates):
    """Оновлює дані автомобіля за VIN-кодом."""
    car_data, worksheet = find_car_in_sheets(vin_code)
    if not worksheet or not car_data:
        logging.error(f"Автомобіль з VIN {vin_code} не знайдено для оновлення.")
        return False
    try:
        cell = worksheet.find(vin_code, in_column=2)
        headers = worksheet.row_values(1)
        for key, value in updates.items():
            if key in headers:
                col = headers.index(key) + 1
                worksheet.update_cell(cell.row, col, value)
        # Оновлюємо дату
        if 'Дата оновлення' in headers:
            col = headers.index('Дата оновлення') + 1
            worksheet.update_cell(cell.row, col, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logging.info(f"Дані для VIN {vin_code} успішно оновлено.")
        return True
    except Exception as e:
        logging.error(f"Помилка оновлення даних для VIN {vin_code}: {e}")
        return False

def add_new_car_to_sheet(car_data, sheet_name_key):
    """Додає новий автомобіль у вказаний аркуш."""
    sheet_name = SHEET_NAMES.get(sheet_name_key)
    if not sheet_name:
        logging.error(f"Невідомий ключ аркуша: {sheet_name_key}")
        return False
    
    worksheet = get_worksheet_by_name(sheet_name)
    if not worksheet:
        return False
        
    try:
        headers = worksheet.row_values(1)
        row_to_add = []
        for header in headers:
            row_to_add.append(car_data.get(header, ""))
        worksheet.append_row(row_to_add)
        logging.info(f"Новий автомобіль {car_data.get('ВІН-код')} додано до '{sheet_name}'.")
        return True
    except Exception as e:
        logging.error(f"Помилка додавання авто до '{sheet_name}': {e}")
        return False
        
def add_note_to_sheet(user_id, note_text, reminder_time=None):
    """Додає нотатку користувача в аркуш 'Нотатки'."""
    worksheet = get_worksheet_by_name(SHEET_NAMES["notes"])
    if not worksheet:
        return None
    try:
        all_notes = worksheet.get_all_records()
        new_id = max([note.get('ID Нотатки', 0) for note in all_notes] + [0]) + 1
        
        row_data = [
            new_id,
            user_id,
            note_text,
            reminder_time.strftime("%Y-%m-%d %H:%M:%S") if reminder_time else "Неактуально",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Активно" if reminder_time else "Без нагадування"
        ]
        worksheet.append_row(row_data)
        logging.info(f"Нотатку з ID {new_id} для користувача {user_id} додано.")
        return new_id
    except Exception as e:
        logging.error(f"Помилка додавання нотатки для користувача {user_id}: {e}")
        return None

# ... та інші ваші функції ...
# Переконайтесь, що всі вони використовують get_worksheet_by_name()
# для отримання доступу до аркушів.

