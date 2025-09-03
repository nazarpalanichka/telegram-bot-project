# utils/auth.py
import logging
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

from utils.g_sheets import get_worksheet_by_name
from config import SHEET_NAMES

# This set will store the integer IDs of allowed managers
manager_ids = set()

def load_managers_from_sheet():
    """
    Loads manager telegram IDs from the Google Sheet 'Менеджери'
    and populates the manager_ids set.
    This function should be called once when the bot starts.
    """
    global manager_ids
    logging.info("Attempting to load managers from Google Sheet...")
    try:
        # Get the 'Менеджери' worksheet using the existing utility function
        worksheet = get_worksheet_by_name(SHEET_NAMES["managers"])
        
        # Get all values from the first column (user_id), skipping the header row
        user_ids_from_sheet = worksheet.col_values(1)[1:]
        
        # Create a temporary set of valid integer IDs
        temp_manager_ids = set()
        for user_id_str in user_ids_from_sheet:
            if user_id_str and user_id_str.isdigit():
                temp_manager_ids.add(int(user_id_str))
        
        if not temp_manager_ids:
            logging.warning("No manager IDs found in the Google Sheet. Access might be restricted.")
        
        manager_ids = temp_manager_ids
        logging.info(f"Successfully loaded {len(manager_ids)} manager IDs: {manager_ids}")

    except Exception as e:
        logging.error(f"FATAL: Could not load managers from Google Sheet: {e}")
        # In case of an error, we clear the list to prevent unauthorized access
        # Or, alternatively, you could load from a backup file or use a hardcoded owner ID
        manager_ids.clear()
        logging.warning("Manager list is empty due to an error. Only owner might have access if hardcoded elsewhere.")


def manager_required(func):
    """
    Decorator that restricts access to a handler to managers only.
    Manager IDs are checked against the global 'manager_ids' set.
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # Ensure the user object and ID exist
        if not update.effective_user or not update.effective_user.id:
            logging.warning("Could not determine user ID for an update.")
            return

        user_id = update.effective_user.id
        
        # Check if the user's ID is in our set of manager IDs
        if user_id not in manager_ids:
            logging.warning(f"Access denied for user {user_id}. Not in manager list.")
            await update.message.reply_text("⛔️ У вас немає доступу до цієї команди.")
            return
        
        # If the check passes, execute the original handler function
        return await func(update, context, *args, **kwargs)
    
    return wrapped
