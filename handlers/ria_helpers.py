# -*- coding: utf-8 -*-
# handlers/ria_helpers.py — хелпер для збереження RIA полів


def apply_ria_fields(rec: dict, auto_id: int | str, full_link: str) -> dict:
    rec = dict(rec)
    rec['ria_auto_id'] = str(auto_id)
    if full_link and 'auto.ria.com' not in full_link:
        full_link = 'https://auto.ria.com' + (full_link if full_link.startswith('/') else '/' + full_link)
    rec['ria_link'] = full_link