# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio

async def _get_header(ws) -> list[str]:
    """Асинхронно отримує заголовки з аркуша."""
    headers = await ws.row_values(1)
    return [h.strip() for h in headers]

async def _set_header(ws, headers: list[str]) -> None:
    """Асинхронно встановлює заголовки."""
    await ws.update('1:1', [headers])

async def _append_missing_headers(ws, headers: list[str], required: list[str]) -> list[str]:
    """Додає відсутні заголовки до аркуша."""
    missing = [h for h in required if h not in headers]
    if not missing:
        return headers
    new_headers = headers + missing
    await _set_header(ws, new_headers)
    return new_headers

async def ensure_columns_exist_async(gs_manager, sheet_name: str, required_headers: list[str]) -> None:
    """
    Переконується, що в аркуші існують всі необхідні колонки.
    Використовує новий асинхронний метод open_worksheet.
    """
    ws = await gs_manager.open_worksheet(sheet_name)
    if ws:
        headers = await _get_header(ws)
        await _append_missing_headers(ws, headers, required_headers)

def ensure_columns_exist(gs_manager, sheet_name: str, required_headers: list[str]) -> None:
    """
    Запускає асинхронну перевірку колонок у синхронному контексті,
    створюючи нове завдання, якщо цикл подій вже запущений.
    """
    async def _run():
        await ensure_columns_exist_async(gs_manager, sheet_name, required_headers)
    
    try:
        loop = asyncio.get_running_loop()
        # Якщо цикл запущено, створюємо завдання, яке виконається у фоні
        loop.create_task(_run())
    except RuntimeError:
        # Якщо циклу немає, запускаємо новий для виконання цієї задачі
        asyncio.run(_run())

