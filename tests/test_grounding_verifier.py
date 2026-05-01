"""Тесты GroundingVerifier — детекция галлюцинаций по конкретике."""
from src.verify import GroundingVerifier


def _v() -> GroundingVerifier:
    return GroundingVerifier()


def test_safe_phone_passes():
    """Каноническая горячая линия — всегда ок, даже если её нет в источниках."""
    v = _v()
    res = v.verify(
        "Звоните на горячую линию +375 44 788 88 80.",
        kb_text="",
        web_text="",
    )
    assert res.is_grounded is True


def test_hallucinated_phone_flagged():
    """Любой другой номер, которого нет в источниках, — флагируется."""
    v = _v()
    res = v.verify(
        "Свяжитесь по номеру +375 17 123 45 67",
        kb_text="Магазины Евроопт работают круглосуточно",
        web_text="",
    )
    assert res.is_grounded is False
    assert any(i.kind == "phone" for i in res.issues)


def test_safe_percent_99_passes():
    v = _v()
    res = v.verify("Бонусами можно оплатить до 99% покупки.", kb_text="", web_text="")
    assert res.is_grounded is True


def test_grounded_price_from_kb():
    """Цена, упомянутая в KB, — допустима."""
    v = _v()
    res = v.verify(
        "Карта стоит 99 копеек.",
        kb_text="Пластиковая карта Еплюс — 99 копеек на кассе",
        web_text="",
    )
    assert res.is_grounded is True


def test_hallucinated_time_flagged():
    """Конкретные часы работы, которых нет в источниках, — галлюцинация."""
    v = _v()
    res = v.verify(
        "Магазин работает с 09:00 до 22:00",
        kb_text="Гипермаркеты Евроопт работают по адресу...",
        web_text="",
    )
    assert res.is_grounded is False
    assert any(i.kind == "time" for i in res.issues)


def test_grounded_time_from_kb():
    v = _v()
    res = v.verify(
        "Магазин работает с 8:00 до 23:00",
        kb_text="Типовой режим гипермаркетов: 8:00-23:00",
        web_text="",
    )
    assert res.is_grounded is True


def test_auto_fix_replaces_phone():
    v = GroundingVerifier(auto_fix=True)
    res = v.verify(
        "Свяжитесь по номеру +375 17 123 45 67",
        kb_text="",
        web_text="",
    )
    assert res.is_grounded is False
    # При auto_fix фейковый телефон заменяется на безопасный
    assert "+375 44 788 88 80" in res.cleaned_text
    assert "+375 17 123 45 67" not in res.cleaned_text


def test_empty_response():
    v = _v()
    res = v.verify("", kb_text="anything", web_text="anything")
    assert res.is_grounded is True


def test_no_concrete_facts_passes():
    """Ответ без конкретных чисел/телефонов — всегда ок."""
    v = _v()
    res = v.verify(
        "Подробности уточняйте на сайте евроопт.",
        kb_text="",
        web_text="",
    )
    assert res.is_grounded is True


# ===== Адреса (30.04 — фикс галлюцинации «дом 74-98», «корп. 3, пом. 7Н») =====


class TestAddressGrounding:
    """Bug-find 30.04: бот выдумывал «дом 74-98», «корп. 3, пом. 7Н», «пом. 160,161,162»
    при наличии в RAG только «Минск, пр-т Независимости, 91» / «52» / «48» и т.д.
    """

    def test_address_range_always_flagged(self):
        """Диапазон «дом 74-98» — всегда галлюцинация (одного дома быть в диапазоне не может)."""
        v = _v()
        res = v.verify(
            "Минск, пр-т Независимости, дом 74-98",
            kb_text="Минск, пр-т Независимости, 74\nМинск, пр-т Независимости, 91",
            web_text="",
        )
        assert res.is_grounded is False
        assert any(i.kind == "address_range" for i in res.issues)

    def test_address_range_in_short_form(self):
        """Сокращенная запись «, 74-98» тоже ловится."""
        v = _v()
        res = v.verify(
            "Адрес: Минск, пр-т Независимости, 74-98",
            kb_text="пр-т Независимости, 74",
            web_text="",
        )
        assert res.is_grounded is False
        assert any(i.kind == "address_range" for i in res.issues)

    def test_address_with_made_up_pomesheniye_flagged(self):
        """«пом. 7Н» дописанный к реальному «168-3/3» — галлюцинация."""
        v = _v()
        res = v.verify(
            "Минск, пр-т Независимости, 168, корп. 3, пом. 7Н",
            kb_text="Минск, пр-т Независимости, 168-3/3",
            web_text="",
        )
        assert res.is_grounded is False
        assert any(i.kind == "address" for i in res.issues)

    def test_real_address_passes(self):
        """Адрес дословно из RAG — допустим."""
        v = _v()
        res = v.verify(
            "Магазин по адресу Минск, пр-т Независимости, 48.",
            kb_text="Магазин Евроопт. Полный адрес: Минск, пр-т Независимости, 48.",
            web_text="",
        )
        # Может быть некритичный шум от других regex'ов, но address не должен быть в issues
        assert all(
            i.kind not in ("address", "address_range") for i in res.issues
        ), f"Реальный адрес ошибочно помечен: {[(i.kind, i.value) for i in res.issues]}"

    def test_real_address_with_slash_passes(self):
        """Адрес с «/» («23/1», «168-3/3») — это формат записи, не диапазон."""
        v = _v()
        res = v.verify(
            "Минск, пр-т Независимости, 23/1.",
            kb_text="Полный адрес: Минск, пр-т Независимости, 23/1",
            web_text="",
        )
        assert all(
            i.kind not in ("address", "address_range") for i in res.issues
        ), f"Адрес с / ошибочно помечен: {[(i.kind, i.value) for i in res.issues]}"

    def test_address_with_letter_passes(self):
        """Адрес с буквой («10А», «90Б») — не диапазон."""
        v = _v()
        res = v.verify(
            "Адрес: Минск, ул. Ленина, 10А.",
            kb_text="Полный адрес: Минск, ул. Ленина, 10А",
            web_text="",
        )
        assert all(
            i.kind not in ("address", "address_range") for i in res.issues
        )

    def test_made_up_address_not_in_kb_flagged(self):
        """Адрес которого нет в kb — флагируется (даже если в реальной жизни существует)."""
        v = _v()
        res = v.verify(
            "Минск, ул. Несуществующая, 99",
            kb_text="Минск, ул. Ленина, 1\nМинск, пр-т Независимости, 48",
            web_text="",
        )
        assert res.is_grounded is False
        assert any(i.kind == "address" for i in res.issues)


# ===== Auto-fix адресов (01.05 — после bug на проде) =====


class TestAddressAutoFix:
    """Когда auto_fix=True, грязные адреса должны вырезаться из ответа.

    Bug на проде 01.05: бот отвечал на «Есть ли Евроопт на пр-те Независимости?»:
        «1. Минск, пр-т Независимости, 74-98
         2. Минск, пр-т Независимости, 91-4Н
         3. ...»
    Адреса в RAG были «48», «74», «91» (без «-98», без «-4Н»). Detection ловил,
    но без auto_fix ответ всё равно уходил пользователю.
    """

    def test_auto_fix_removes_range_address_line(self):
        v = GroundingVerifier(auto_fix=True)
        text = (
            "Вот адреса:\n"
            "1. Минск, пр-т Независимости, 74-98\n"
            "2. Минск, пр-т Независимости, 91\n"
            "3. Минск, пр-т Независимости, 48"
        )
        kb = "Минск, пр-т Независимости, 91\nМинск, пр-т Независимости, 48"
        res = v.verify(text, kb_text=kb)
        # Диапазон должен быть вырезан
        assert "74-98" not in res.cleaned_text, (
            f"Диапазон не убран:\n{res.cleaned_text}"
        )
        # Реальные адреса остаются
        assert "91" in res.cleaned_text
        assert "48" in res.cleaned_text

    def test_auto_fix_removes_made_up_pomesheniye(self):
        v = GroundingVerifier(auto_fix=True)
        text = (
            "Адреса:\n"
            "1. Минск, пр-т Независимости, 168, корп. 3, пом. 7Н\n"
            "2. Минск, пр-т Независимости, 91"
        )
        kb = "Минск, пр-т Независимости, 168-3/3\nМинск, пр-т Независимости, 91"
        res = v.verify(text, kb_text=kb)
        # «корп. 3, пом. 7Н» — выдуманное, должно уйти
        assert "пом. 7Н" not in res.cleaned_text

    def test_auto_fix_keeps_real_addresses_untouched(self):
        v = GroundingVerifier(auto_fix=True)
        text = "Магазин в Минске: пр-т Независимости, 48"
        kb = "Полный адрес: Минск, пр-т Независимости, 48"
        res = v.verify(text, kb_text=kb)
        assert res.cleaned_text == text  # ничего не меняли

    def test_auto_fix_short_list_replaced_with_redirect(self):
        """Если после удаления галлюцинаций остался <2 пунктов — добавить редирект."""
        v = GroundingVerifier(auto_fix=True)
        text = (
            "Адреса:\n"
            "1. Минск, пр-т Независимости, 74-98\n"
            "2. Минск, пр-т Независимости, 91-4Н"
        )
        kb = "Минск, пр-т Независимости, 91"  # только один реальный
        res = v.verify(text, kb_text=kb)
        # Оба удалены (галлюцинации). Бот должен дать редирект.
        assert "evroopt.by/shops" in res.cleaned_text
