from telegram.ext import MessageHandler, filters

def get_tables_handler():
    """Повертає порожній обробник, оскільки цей модуль застарів."""
    return MessageHandler(filters.Regex('^NEVER_MATCH_THIS_STRING$'), lambda u, c: None)