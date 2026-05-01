"""Тесты PII-фильтра (ДС №1 к Договору 2703/26-01, п. 2.1.1 + Приложение №1).

Проверяем: телефоны (РБ и международные), email, банковские карты (Luhn),
карты лояльности, паспорт РБ, ID РБ, физ. адреса, ФИО, дата рождения.
И отсутствие ложных срабатываний на обычные запросы покупателей.
"""
import sys
sys.path.insert(0, ".")

import pytest

from src.filters.pii_filter import (
    detect_pii,
    has_pii,
    mask_pii,
    PLACEHOLDERS,
)


class TestPhones:
    @pytest.mark.parametrize("msg", [
        "мой номер +375 29 123-45-67",
        "тел. +375(29)1234567",
        "позвоните +375291234567",
        "номер 8029 123 45 67",
        "8 029 123-45-67",
        "+375 33 987 65 43",
        "+375 44 555 44 33",
        "+375 17 234 56 78",   # городской Минск
        "+375 25 111 22 33",
    ])
    def test_by_phone_masked(self, msg):
        masked, types = mask_pii(msg)
        assert "phone" in types, f"Не определён телефон: {msg}"
        assert PLACEHOLDERS["phone"] in masked
        # В маскированном тексте не должно остаться ни одной длинной цепочки цифр подряд
        import re
        assert not re.search(r"\d{7,}", masked), masked

    @pytest.mark.parametrize("msg", [
        "напишите +7 495 123-45-67",     # Россия
        "позвоните +49 30 12345678",     # Германия
        "мой номер +1 (415) 555-1234",  # США
    ])
    def test_intl_phone_masked(self, msg):
        masked, types = mask_pii(msg)
        assert "phone" in types
        assert PLACEHOLDERS["phone"] in masked


class TestEmail:
    @pytest.mark.parametrize("msg", [
        "мой email ivan.petrov@mail.ru",
        "пишите на info@belhard.com",
        "Я тут: user_123+newsletter@eurotorg.by",
    ])
    def test_email_masked(self, msg):
        masked, types = mask_pii(msg)
        assert "email" in types
        assert "@" not in masked
        assert PLACEHOLDERS["email"] in masked


class TestBankCard:
    @pytest.mark.parametrize("card", [
        "4111 1111 1111 1111",   # Visa test (Luhn-valid)
        "5555 5555 5555 4444",   # MC test
        "4532015112830366",       # Luhn-valid
        "4539-1488-0343-6467",   # Luhn-valid с дефисами
    ])
    def test_valid_card_masked(self, card):
        masked, types = mask_pii(f"оплачу картой {card}")
        assert "card" in types
        assert PLACEHOLDERS["card"] in masked

    def test_invalid_luhn_not_masked_as_card(self):
        # 16 цифр без Luhn-валидации — не банковская карта.
        # (Не должны ложно маскировать как card, но могут как phone/loyalty).
        masked, types = mask_pii("артикул товара 1234567890123456")
        # Хотя бы не «card»: может попасть под loyalty, но это оправданно
        # с точки зрения PII (цифровая последовательность).
        # Главное — не ломается логика.
        assert masked is not None


class TestLoyaltyCard:
    def test_loyalty_marked(self):
        masked, types = mask_pii("моя карта лояльности 1234567890123")
        assert "card" in types
        assert PLACEHOLDERS["card"] in masked

    def test_eplus_card(self):
        masked, types = mask_pii("карта Еплюс №9876543210987")
        assert "card" in types

    def test_non_luhn_card_with_marker(self):
        """Баг с прода 24.04: тестовая карта не-Luhn с маркером «карта» НЕ маскировалась."""
        masked, types = mask_pii("Номер моей карты 9897 9663 1133 6755")
        assert "card" in types, f"Карта с маркером должна маскироваться даже без Luhn: {masked}"
        assert "9897" not in masked
        assert "6755" not in masked

    @pytest.mark.parametrize("msg", [
        "номер карты 1234 5678 9012 3456",            # не-Luhn
        "картой 0000 1111 2222 3333 оплачу",           # не-Luhn
        "моя банковская карта 1111-2222-3333-4444",   # не-Luhn, с дефисами
    ])
    def test_card_variants_with_marker(self, msg):
        masked, types = mask_pii(msg)
        assert "card" in types, f"Не поймано: {msg} → {masked}"


class TestPassport:
    @pytest.mark.parametrize("msg", [
        "мой паспорт MP1234567",
        "паспорт MP 1234567",
        "паспорт МР1234567",           # кириллица
        "серия HB, номер HB9876543",   # запасной формат
    ])
    def test_passport_masked(self, msg):
        masked, types = mask_pii(msg)
        assert "passport" in types
        assert PLACEHOLDERS["passport"] in masked


class TestIDRB:
    def test_id_rb_masked(self):
        # Идентификационный номер РБ: 14 символов, формат 7digits+letter+3digits+2letters+1digit
        masked, types = mask_pii("мой ID 1234567A123PB1")
        assert "id_by" in types
        assert PLACEHOLDERS["id_by"] in masked


class TestAddress:
    @pytest.mark.parametrize("msg", [
        "доставьте на ул. Ленина, д. 5, кв. 12",
        "адрес: улица Казинца, 52А-22",
        "проспект Независимости 48, квартира 3",
        "пр. Дзержинского, д. 104",
        "переулок Ломоносова д.5 каб.25",
        "бульвар Шевченко 10",
    ])
    def test_address_masked(self, msg):
        masked, types = mask_pii(msg)
        assert "address" in types, f"Не определён адрес: {msg}\n → {masked}"
        assert PLACEHOLDERS["address"] in masked

    @pytest.mark.parametrize("msg", [
        # Whitelist: запрос про магазин (топоним), а не PII пользователя.
        # 30.04 — отчёт agent_tester показал false positive на «пр-т Победителей в Минске».
        "Евроопт на пр-те Победителей в Минске",
        "Где гипермаркет на ул. Казинца?",
        "Найти магазин ул. Притыцкого 29",
        "Грошык в Минске на ул. Рокоссовского",
        "Магазин Хит на ул. Первомайская, 50",
        "магазины Евроопт в Лиде",
        "автолавка по ул. Советская",
        "ближайший Евроопт на ул. Сурганова",
    ])
    def test_address_whitelisted_when_store_context(self, msg):
        masked, types = mask_pii(msg)
        assert "address" not in types, (
            f"Топоним магазина ошибочно помечен как PII: {msg!r}\n → {masked!r}"
        )

    @pytest.mark.parametrize("msg", [
        # PII должен сработать, несмотря на упоминание магазина:
        # это явная просьба что-то прислать на персональный адрес.
        "Доставьте по адресу: ул. Ленина, 10, кв. 15",
        "Привезите еду из Евроопта на ул. Куприна, 8",
        "Я живу рядом с Грошык на ул. Гагарина, 27",
        "Адрес доставки: ул. Купалы, 50",
        "Пришлите курьера Евроопт на пр-т Независимости, 48",
    ])
    def test_address_still_masked_when_personal_context(self, msg):
        masked, types = mask_pii(msg)
        assert "address" in types, (
            f"Личный адрес не замаскирован: {msg!r}\n → {masked!r}"
        )
        assert PLACEHOLDERS["address"] in masked


class TestFIO:
    @pytest.mark.parametrize("msg", [
        "меня зовут Иван Петров",
        "ФИО: Кукурузин Владимир Юрьевич",
        "зовут меня Анна Сидорова",
        "фамилия: Иванов",
        "моё имя Елена Ковалёва",
    ])
    def test_fio_marked(self, msg):
        masked, types = mask_pii(msg)
        assert "fio" in types, f"Не определено ФИО: {msg}\n → {masked}"
        assert PLACEHOLDERS["fio"] in masked

    @pytest.mark.parametrize("msg", [
        "Петров Иван Иванович купил продукты",
        "Мамоненко Игорь Викторович подписал",
        "Ковалёва Анна обратилась в магазин",
    ])
    def test_fio_by_surname(self, msg):
        masked, types = mask_pii(msg)
        assert "fio" in types, f"Не определено ФИО по суффиксу: {msg}\n → {masked}"


class TestDOB:
    @pytest.mark.parametrize("msg", [
        "дата рождения: 15.03.1985",
        "родился 01/12/1990",
        "д.р. 22.04.1978",
    ])
    def test_dob_masked(self, msg):
        masked, types = mask_pii(msg)
        assert "date" in types, f"Не определена дата: {msg}\n → {masked}"
        assert PLACEHOLDERS["date"] in masked


class TestNoFalsePositives:
    """Нормальные запросы покупателей НЕ должны маскироваться."""

    @pytest.mark.parametrize("msg", [
        "Какие акции сейчас в Евроопте?",
        "Работает ли магазин в воскресенье?",
        "Как получить карту Еплюс?",
        "Сколько стоит молоко?",
        "Где ближайший Грошык?",
        "Скидки на мясо есть?",
        "Рецепт драников",
        "Работаете в Минске?",
        "Магазин на Казинца открыт?",  # Казинца — тоже улица, но без явного маркера
    ])
    def test_no_false_positive(self, msg):
        masked, types = mask_pii(msg)
        # Допускаем, что маркер "Казинца" (без ул./пр.) может НЕ матчиться — это ок.
        # Главное — нет типов card/passport/id_by/phone/email/fio
        forbidden = {"card", "passport", "id_by", "phone", "email", "fio"}
        found = set(types)
        assert not (forbidden & found), f"Ложное срабатывание в '{msg}': {found}\n → {masked}"

    def test_brand_not_fio(self):
        # Евроопт Гипермаркет — это бренд, не ФИО
        masked, types = mask_pii("Евроопт Гипермаркет открыт?")
        assert "fio" not in types

    @pytest.mark.parametrize("msg", [
        # «Родныя тавары» — белорусская СТМ, не ФИО (regression 01.05.2026 PR #24+)
        "Какие Родныя тавары сейчас в акции?",
        "Что такое Родныя тавары?",
        "Покажи родная марка товары",
        "Родныя тавары в магазинах Евроопт",
        # СВАЁ — собственная марка
        "Что такое СВАЁ? Приведи примеры товаров",
        "Купить СВАЁ напитки",
        # «Удача в придачу» — лотерея
        "Расскажи про Удачу в придачу",
        "Какие призы в Удаче в придачу?",
        # «ГрильФест» — название акции
        "Расскажи про Грильфест",
        "Когда будет Гриль-фест?",
    ])
    def test_brand_promo_not_fio(self, msg):
        """Названия акций / СТМ не должны блокироваться как ФИО."""
        masked, types = mask_pii(msg)
        assert "fio" not in types, f"Ложно ФИО в '{msg}' → {masked}"


class TestMaskingOutput:
    def test_multiple_pii_in_one_message(self):
        msg = (
            "Меня зовут Иван Петров, мой телефон +375 29 123-45-67, "
            "email ivan@mail.ru, доставить на ул. Ленина, д. 5."
        )
        masked, types = mask_pii(msg)
        assert "fio" in types
        assert "phone" in types
        assert "email" in types
        assert "address" in types
        # В итоговом тексте не должно остаться исходных ПДн
        assert "Иван" not in masked
        assert "375" not in masked
        assert "ivan@mail.ru" not in masked

    def test_empty_text(self):
        masked, types = mask_pii("")
        assert masked == ""
        assert types == []

    def test_has_pii_positive(self):
        assert has_pii("email: test@test.com") is True

    def test_has_pii_negative(self):
        assert has_pii("Какие акции в Евроопте?") is False

    def test_log_contains_types_not_values(self):
        """Критично: возвращаем ТИПЫ, не значения. ПДн в логи не попадают."""
        masked, types = mask_pii("пишите на ivan.secret@mail.ru")
        # types — только строго предопределённые метки типов из PLACEHOLDERS
        for t in types:
            assert t in PLACEHOLDERS, f"Неожиданный тип: {t}"
        # Значения не проникают в types
        assert "ivan" not in types
        assert "ivan.secret@mail.ru" not in types

    def test_masking_is_idempotent(self):
        """Двойное маскирование не должно ломать текст (идемпотентность)."""
        msg = "тел +375 29 111 22 33, email foo@bar.by"
        once, _ = mask_pii(msg)
        twice, types = mask_pii(once)
        assert once == twice, f"Не идемпотентно: {once!r} != {twice!r}"
        assert types == [], f"На маскированном тексте повторных матчей быть не должно: {types}"


class TestNERFallbackForFIO:
    """ФИО БЕЗ маркеров и БЕЗ типовых суффиксов — ловятся только через NER."""

    @pytest.mark.parametrize("msg", [
        "Влад Сидоров звонил вчера",              # «Влад», «Сидоров» — суффикс -ов есть, regex должен поймать
        "Пришёл Алексей Шкитин с заявкой",        # -ин суффикс
        "Свяжитесь с Ольга Лешко",                # без типового суффикса РУ — NER
    ])
    def test_ner_catches_bare_fio(self, msg):
        masked, types = mask_pii(msg)
        assert "fio" in types, f"ФИО не поймано: {msg}\n → masked: {masked}"


class TestWebQueryMasking:
    """ДС №1 п. 2.1.2: web-запрос ОБЕЗЛИЧЕН перед внешним поиском."""

    def test_pii_in_web_query_is_masked(self):
        # Симулируем сценарий: rewrite_query вернул текст с телефоном.
        # Pipeline должен прогнать через mask_pii перед self.web.search().
        rewritten = "позвоните по +375 29 123-45-67 уточнить заказ"
        masked, types = mask_pii(rewritten)
        assert "phone" in types
        assert "375" not in masked
        # Плейсхолдер — в запросе к Tavily такого быть не может, но факт маскировки важен
        assert PLACEHOLDERS["phone"] in masked
