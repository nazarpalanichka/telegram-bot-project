# -*- coding: utf-8 -*-
# utils/helpers.py

import re

def escape_markdown_v2(text: str) -> str:
    """
    Escapes characters for Telegram's MarkdownV2 formatting.
    Ensures that any input is converted to a string before processing.
    """
    if not isinstance(text, str):
        text = str(text)
    
    # List of characters to escape for MarkdownV2
    # ВИПРАВЛЕНО: Додано символи '+' та '.' до списку екранування
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    
    # Use re.sub() to add a backslash before each special character
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)
