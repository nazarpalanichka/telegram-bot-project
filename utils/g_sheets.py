# utils/g_sheets.py
import logging
from typing import Dict, Any, List
import gspread_asyncio
from google.oauth2.service_account import Credentials

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_creds(credentials_path: str):
    """Створює об'єкт credentials для gspread_asyncio."""
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive"
    ]
    return Credentials.from_service_account_file(credentials_path, scopes=scopes)

class GoogleSheetManager:
    """Асинхронний клас для управління всіма операціями з Google Sheets."""
    def __init__(self, credentials_path: str, spreadsheet_key: str):
        self.spreadsheet_key = spreadsheet_key
        self.credentials_path = credentials_path
        self.agcm = gspread_asyncio.AsyncioGspreadClientManager(lambda: get_creds(self.credentials_path))
        self.spreadsheet = None
        self.is_authorized = False

    async def authorize(self) -> bool:
        """Асинхронно авторизується та відкриває таблицю."""
        if self.is_authorized:
            return True
        try:
            agc = await self.agcm.authorize()
            self.spreadsheet = await agc.open_by_key(self.spreadsheet_key)
            self.is_authorized = True
            logger.info("Асинхронна авторизація в Google Sheets успішна.")
            return True
        except Exception as e:
            logger.error(f"Помилка асинхронної авторизації: {e}")
            self.is_authorized = False
            return False

    async def open_worksheet(self, sheet_name: str):
        """Асинхронно відкриває аркуш за назвою."""
        if not self.is_authorized or not self.spreadsheet:
            if not await self.authorize():
                return None
        try:
            return await self.spreadsheet.worksheet(sheet_name)
        except gspread_asyncio.gspread.exceptions.WorksheetNotFound:
            logger.error(f"Аркуш з назвою '{sheet_name}' не знайдено.")
            return None
        except Exception as e:
            logger.error(f"Невідома помилка при відкритті аркуша '{sheet_name}': {e}")
            return None

    async def get_all_records(self, sheet_name: str, expected_headers: List[str] = None) -> List[Dict[str, Any]] | None:
        """Асинхронно отримує всі записи з аркуша у вигляді списку словників."""
        try:
            worksheet = await self.open_worksheet(sheet_name)
            if not worksheet:
                return None
            
            records = await worksheet.get_all_records()
            # Перевірка та доповнення заголовків, якщо необхідно
            if expected_headers and records:
                actual_headers = list(records[0].keys())
                if set(expected_headers) != set(actual_headers):
                     logger.warning(f"Заголовки в аркуші '{sheet_name}' не співпадають з очікуваними.")
            return records
        except Exception as e:
            logger.error(f"Помилка при отриманні всіх записів з '{sheet_name}': {e}", exc_info=True)
            return None
            
    async def add_row(self, sheet_name: str, data: Dict[str, Any], headers_order: List[str]) -> bool:
        """Асинхронно додає новий рядок в аркуш."""
        try:
            worksheet = await self.open_worksheet(sheet_name)
            if not worksheet:
                return False
            
            row_to_add = [data.get(header, "") for header in headers_order]
            await worksheet.append_row(row_to_add, value_input_option='USER_ENTERED')
            logger.info(f"Новий рядок успішно додано в аркуш '{sheet_name}'.")
            return True
        except Exception as e:
            logger.error(f"Помилка при додаванні рядка в '{sheet_name}': {e}")
            return False

    async def update_record_by_key(self, sheet_name: str, key_column: str, key_value: Any, new_data: Dict[str, Any]) -> bool:
        """Знаходить рядок за унікальним ключем та оновлює його."""
        try:
            worksheet = await self.open_worksheet(sheet_name)
            if not worksheet: return False

            all_records = await self.get_all_records(sheet_name)
            if not all_records: return False
            
            headers = list(all_records[0].keys())
            if key_column not in headers:
                logger.error(f"Ключова колонка '{key_column}' не знайдена в заголовках аркуша '{sheet_name}'.")
                return False

            row_index_to_update = -1
            for i, record in enumerate(all_records):
                if str(record.get(key_column)) == str(key_value):
                    row_index_to_update = i + 2  # +1 for header, +1 for 0-based index
                    break
            
            if row_index_to_update == -1:
                logger.warning(f"Запис з {key_column}='{key_value}' не знайдено в '{sheet_name}'.")
                return False

            # Отримуємо поточні значення рядка та оновлюємо їх
            current_row_values = await worksheet.row_values(row_index_to_update)
            record_to_update = dict(zip(headers, current_row_values))
            record_to_update.update(new_data)
            
            updated_row_values = [record_to_update.get(h, '') for h in headers]
            
            # Оновлюємо діапазон
            range_to_update = f'A{row_index_to_update}:{chr(ord("A") + len(headers) - 1)}{row_index_to_update}'
            await worksheet.update(range_to_update, [updated_row_values], value_input_option='USER_ENTERED')
            
            logger.info(f"Успішно оновлено рядок {row_index_to_update} в '{sheet_name}' (де {key_column}='{key_value}').")
            return True

        except Exception as e:
            logger.error(f"Помилка при оновленні запису в '{sheet_name}' за ключем '{key_column}': {e}", exc_info=True)
            return False

