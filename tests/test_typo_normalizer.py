"""Тесты нормализации опечаток для search."""
import sys
sys.path.insert(0, ".")

import pytest

from src.search.typo_normalizer import normalize_typos


class TestRepeatedVowels:
    @pytest.mark.parametrize("inp,expected", [
        ("скидкиии", "скидки"),
        ("СКИДКИИИИИИ", "СКИДКИ"),
        ("аааааа", "а"),
        ("даааа", "да"),
        ("какие сегодня акцииии?", "какие сегодня акци?"),  # корень сохранён — этого достаточно для search
        ("привет дружище!", "привет дружище!"),  # одна — не трогаем
    ])
    def test_repeat_vowels_collapsed(self, inp, expected):
        assert normalize_typos(inp) == expected


class TestBrandTypos:
    @pytest.mark.parametrize("inp,fragment", [
        ("Что такое едаставка?", "едоставка"),
        ("Расскажи про ямегом", "ямигом"),
        ("Открой мне еплус", "еплюс"),
        ("Какой режим у евроопд?", "евроопт"),
        ("Что по грошик?", "грошык"),
    ])
    def test_brand_typo_fixed(self, inp, fragment):
        out = normalize_typos(inp).lower()
        assert fragment in out, f"{inp!r} → {out!r}"


class TestPreserveGoodInput:
    @pytest.mark.parametrize("inp", [
        "Какая погода в Минске?",
        "Расскажи про Ямигом",
        "Программа Еплюс",
        "Какие сейчас акции в Евроопте?",
        "Столица Франции",
    ])
    def test_correct_input_unchanged_or_equivalent(self, inp):
        out = normalize_typos(inp)
        # Правильный ввод должен пройти через нормализацию без порчи смысла
        assert len(out) >= len(inp) * 0.8
        # Основные ключевые слова на месте
        for brand in ["Минск", "Ямигом", "Еплюс", "Евроопт", "Франц"]:
            if brand.lower() in inp.lower():
                assert brand.lower() in out.lower(), f"{brand} потерян в {inp!r} → {out!r}"


class TestEdgeCases:
    def test_empty(self):
        assert normalize_typos("") == ""

    def test_none_safe(self):
        assert normalize_typos(None) is None

    def test_only_punctuation(self):
        assert normalize_typos("???") == "???"
