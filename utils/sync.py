# -*- coding: utf-8 -*-
# utils/sync.py

import logging
from datetime import datetime
import asyncio

from .g_sheets import GoogleSheetManager
import config

logger = logging.getLogger(__name__)

async def synchronize_working_sheets(gs_manager: GoogleSheetManager):
    """
    Розумна синхронізація, яка робить "Опубліковані Пости" джерелом правди.
    Вона не очищує аркуші, а додає, оновлює та видаляє рядки.
    """
    logger.info("🚀 Starting smart data synchronization for working sheets...")
    try:
        posts_sheet_name = config.SHEET_NAMES["published_posts"]
        all_posts = await gs_manager.get_all_records(posts_sheet_name, expected_headers=config.POST_SHEET_HEADER_ORDER)

        if all_posts is None:
            logger.critical("SYNC ABORTED: Failed to fetch records from 'Published Posts'.")
            return

        relevant_statuses = {'active', 'draft_ria', 'draft_manual'}
        actual_posts = [p for p in all_posts if p.get(config.POST_SHEET_COLS['status']) in relevant_statuses]

        posts_by_vin = {
            p.get(config.POST_SHEET_COLS['vin'], '').strip().upper(): p
            for p in actual_posts if p.get(config.POST_SHEET_COLS['vin'])
        }
        logger.info(f"Found {len(posts_by_vin)} relevant posts to sync.")

        for sheet_name in config.SYNC_ENABLED_SHEETS:
            logger.info(f"--- Synchronizing sheet: '{sheet_name}' ---")
            await asyncio.sleep(1.5)

            try:
                cars_on_sheet_records = await gs_manager.get_all_records(sheet_name, expected_headers=config.CAR_SHEET_HEADER_ORDER)
                
                if cars_on_sheet_records is None:
                    logger.error(f"Failed to fetch records from '{sheet_name}'. Skipping.")
                    continue

                cars_on_sheet_by_vin = {
                    rec.get(config.CAR_SHEET_COLS['vin'], '').strip().upper(): (rec, i + 2)
                    for i, rec in enumerate(cars_on_sheet_records) if rec.get(config.CAR_SHEET_COLS['vin'])
                }

                rows_to_delete_indices = []
                batch_cell_updates = []
                
                # Перевірка існуючих рядків: оновити або видалити
                for vin, (existing_record, row_index) in cars_on_sheet_by_vin.items():
                    post_data = posts_by_vin.get(vin)

                    if not post_data or post_data.get(config.POST_SHEET_COLS['location']) != sheet_name:
                        rows_to_delete_indices.append(row_index)
                        continue

                    modification_details = [
                        f"Стан: {post_data.get(config.POST_SHEET_COLS['condition'], 'N/A')}",
                        f"Двигун: {post_data.get(config.POST_SHEET_COLS['modification'], 'N/A')}",
                        f"Пробіг: {post_data.get(config.POST_SHEET_COLS['mileage'], 'N/A')}",
                        f"Привід: {post_data.get(config.POST_SHEET_COLS['drivetrain'], 'N/A')}",
                        f"Коробка: {post_data.get(config.POST_SHEET_COLS['gearbox'], 'N/A')}"
                    ]
                    target_row_data = {
                        config.CAR_SHEET_COLS['model']: post_data.get(config.POST_SHEET_COLS['model'], 'N/A'),
                        config.CAR_SHEET_COLS['vin']: vin,
                        config.CAR_SHEET_COLS['price']: post_data.get(config.POST_SHEET_COLS['price'], 'N/A'),
                        config.CAR_SHEET_COLS['modification']: " | ".join(filter(None, modification_details)),
                        config.CAR_SHEET_COLS['link']: post_data.get(config.POST_SHEET_COLS['ria_link'], ''),
                        config.CAR_SHEET_COLS['manager_id']: post_data.get(config.POST_SHEET_COLS['emp_id'], ''),
                        config.CAR_SHEET_COLS['last_update']: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }

                    current_data_for_compare = {k: str(existing_record.get(k, '')) for k in target_row_data if k != config.CAR_SHEET_COLS['last_update']}
                    target_data_for_compare = {k: str(v) for k, v in target_row_data.items() if k != config.CAR_SHEET_COLS['last_update']}

                    if current_data_for_compare != target_data_for_compare:
                        logger.info(f"Updating car VIN {vin} on sheet '{sheet_name}' at row {row_index}.")
                        update_values = [target_row_data.get(h, '') for h in config.CAR_SHEET_HEADER_ORDER]
                        batch_cell_updates.append({'range': f'A{row_index}', 'values': [update_values]})

                # Додавання нових рядків
                for vin, post_data in posts_by_vin.items():
                    if post_data.get(config.POST_SHEET_COLS['location']) == sheet_name and vin not in cars_on_sheet_by_vin:
                        logger.info(f"Adding new car with VIN {vin} to sheet '{sheet_name}'.")
                        modification_details = [
                            f"Стан: {post_data.get(config.POST_SHEET_COLS['condition'], 'N/A')}",
                            f"Двигун: {post_data.get(config.POST_SHEET_COLS['modification'], 'N/A')}",
                            f"Пробіг: {post_data.get(config.POST_SHEET_COLS['mileage'], 'N/A')}",
                            f"Привід: {post_data.get(config.POST_SHEET_COLS['drivetrain'], 'N/A')}",
                            f"Коробка: {post_data.get(config.POST_SHEET_COLS['gearbox'], 'N/A')}"
                        ]
                        new_row_data = {
                            config.CAR_SHEET_COLS['model']: post_data.get(config.POST_SHEET_COLS['model'], 'N/A'),
                            config.CAR_SHEET_COLS['vin']: vin,
                            config.CAR_SHEET_COLS['price']: post_data.get(config.POST_SHEET_COLS['price'], 'N/A'),
                            config.CAR_SHEET_COLS['modification']: " | ".join(filter(None, modification_details)),
                            config.CAR_SHEET_COLS['link']: post_data.get(config.POST_SHEET_COLS['ria_link'], ''),
                            config.CAR_SHEET_COLS['manager_id']: post_data.get(config.POST_SHEET_COLS['emp_id'], ''),
                            config.CAR_SHEET_COLS['last_update']: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        await gs_manager.add_row(sheet_name, new_row_data, config.CAR_SHEET_HEADER_ORDER)

                # Виконання пакетних операцій
                if batch_cell_updates:
                    await gs_manager.batch_update_cells(sheet_name, batch_cell_updates)
                if rows_to_delete_indices:
                    await gs_manager.batch_delete_rows(sheet_name, rows_to_delete_indices)

            except Exception as e:
                logger.error(f"An error occurred while processing sheet '{sheet_name}': {e}", exc_info=True)

        logger.info("✅ Smart synchronization complete.")

    except Exception as e:
        logger.critical(f"A critical error occurred during the synchronization process: {e}", exc_info=True)
