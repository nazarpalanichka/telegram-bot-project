# -*- coding: utf-8 -*-
# handlers/utils.py

import logging

logger = logging.getLogger(__name__)

def determine_fuel_type(modification_text: str) -> str | None:
    """
    Аналізує текст модифікації та повертає стандартизований тип пального.
    """
    if not isinstance(modification_text, str):
        return None
        
    text_lower = modification_text.lower()
    
    # Ключові слова для електромобілів
    electric_keys = ['електро', 'electric', 'квт', 'kwh', 'kw', 'кв/г']
    if any(key in text_lower for key in electric_keys):
        return 'Електро'
        
    if 'гібрид' in text_lower or 'hybrid' in text_lower or 'hev' in text_lower:
        return 'Гібрид'
    if 'дизель' in text_lower or 'diesel' in text_lower:
        return 'Дизель'
    if 'бензин' in text_lower or 'gasoline' in text_lower:
        return 'Бензин'
        
    return None