#!/usr/bin/env python3.11
"""Smoke-check PII-фильтра: прогоняет набор кейсов и печатает таблицу.

Использование:
    python3.11 scripts/pii_smoke_check.py

Печатает для каждого кейса:
- исходный текст
- замаскированный текст
- найденные типы ПДн
- PASS/FAIL относительно ожидаемых типов

В конце — итог: сколько кейсов прошли, сколько упали.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Разрешаем запуск без установки пакета
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.filters.pii_filter import mask_pii


# (текст, ожидаемые_типы_обязательно, текст_который_должен_исчезнуть_из_маскированного)
CASES: list[tuple[str, set[str], list[str]]] = [
    # --- Телефоны РБ ---
    ("мой телефон +375 29 123-45-67", {"phone"}, ["375", "123-45-67"]),
    ("звоните 8029 555 44 33", {"phone"}, ["555"]),
    ("+375(33)9876543 — срочно", {"phone"}, ["9876543"]),
    ("+375 17 234 56 78", {"phone"}, ["234"]),

    # --- Международные телефоны ---
    ("напишите на +7 495 123-45-67", {"phone"}, ["495"]),
    ("Германия: +49 30 12345678", {"phone"}, ["12345678"]),

    # --- Email ---
    ("пишите ivan.petrov@mail.ru", {"email"}, ["ivan.petrov", "@mail"]),
    ("info@belhard.com — наш email", {"email"}, ["info@belhard"]),

    # --- Банковские карты (Luhn-valid) ---
    ("карта 4111 1111 1111 1111 оплата", {"card"}, ["4111"]),
    ("номер 4532015112830366", {"card"}, ["4532015112830366"]),

    # --- Карта лояльности ---
    ("моя карта лояльности 1234567890123", {"card"}, ["1234567890123"]),
    ("карта Еплюс №9876543210987", {"card"}, ["9876543210987"]),

    # --- Паспорт РБ ---
    ("паспорт MP1234567", {"passport"}, ["MP1234567"]),
    ("серия HB номер HB9876543", {"passport"}, ["HB9876543"]),

    # --- ID РБ (14 символов) ---
    ("мой ID 1234567A123PB1", {"id_by"}, ["1234567A123PB1"]),

    # --- Адреса ---
    ("доставьте на ул. Ленина, д. 5, кв. 12", {"address"}, ["Ленина"]),
    ("адрес улица Казинца, 52А", {"address"}, ["Казинца"]),
    ("проспект Независимости 48", {"address"}, ["Независимости"]),
    ("переулок Ломоносова д.5", {"address"}, ["Ломоносова"]),

    # --- ФИО по маркеру ---
    ("меня зовут Иван Петров", {"fio"}, ["Иван", "Петров"]),
    ("ФИО: Кукурузин Владимир Юрьевич", {"fio"}, ["Кукурузин"]),

    # --- ФИО по суффиксу фамилии (regex) ---
    ("Петров Иван Иванович купил", {"fio"}, ["Петров"]),
    ("Мамоненко Игорь Викторович подписал", {"fio"}, ["Мамоненко"]),

    # --- ФИО через NER (без маркера и без типового суффикса) ---
    ("Влад Сидоров звонил вчера", {"fio"}, ["Сидоров"]),
    ("Пришёл Алексей Шкитин", {"fio"}, ["Шкитин"]),

    # --- Дата рождения ---
    ("дата рождения: 15.03.1985", {"date"}, ["15.03.1985"]),
    ("д.р. 22.04.1978", {"date"}, ["22.04.1978"]),

    # --- Комбо ---
    (
        "Я Иван Петров, тел +375 29 123-45-67, email ivan@mail.ru, доставить на ул. Ленина, д. 5",
        {"fio", "phone", "email", "address"},
        ["Иван", "375", "ivan@", "Ленина"],
    ),

    # --- НЕ должно маскироваться (типичные запросы покупателей) ---
    ("Какие акции в Евроопте?", set(), []),
    ("Где ближайший Грошык?", set(), []),
    ("Как получить карту Еплюс?", set(), []),
    ("Рецепт драников", set(), []),
    ("Работает ли магазин в воскресенье?", set(), []),
    ("Сколько стоит молоко?", set(), []),
    ("Скидки на мясо?", set(), []),
]


def main() -> int:
    ok = 0
    fail = 0
    width = 60
    print(f"{'Исходный':<{width}} | {'Замаскировано':<{width}} | Типы | Проверка")
    print("-" * (width * 2 + 30))
    for text, must_have, must_disappear in CASES:
        masked, types = mask_pii(text)
        types_set = set(types)

        # Проверка 1: ожидаемые типы присутствуют
        types_ok = must_have.issubset(types_set)
        # Проверка 2: если ожидали пусто — никаких типов нет
        no_false_pos = not (must_have == set() and types_set)
        # Проверка 3: «должно исчезнуть» действительно исчезло
        disappeared_ok = all(s not in masked for s in must_disappear)

        passed = types_ok and no_false_pos and disappeared_ok
        status = "OK" if passed else "FAIL"
        if passed:
            ok += 1
        else:
            fail += 1

        text_short = text[:width]
        masked_short = masked[:width]
        types_str = ",".join(sorted(types_set)) or "-"
        print(f"{text_short:<{width}} | {masked_short:<{width}} | {types_str:<15} | {status}")
        if not passed:
            if not types_ok:
                print(f"   ! Ожидались типы {must_have - types_set}")
            if not no_false_pos:
                print(f"   ! Ложные типы: {types_set}")
            if not disappeared_ok:
                remaining = [s for s in must_disappear if s in masked]
                print(f"   ! Не исчезло из маскированного: {remaining}")

    print()
    print(f"Итог: {ok}/{ok + fail} прошли, {fail} упали.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
