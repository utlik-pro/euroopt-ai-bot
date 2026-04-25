"""Intent router — классификация типа запроса для адаптивной маршрутизации."""
from src.router.intent import IntentRouter, Intent
from src.router.brand_detector import detect_brand, detect_city, detect_format

__all__ = ["IntentRouter", "Intent", "detect_brand", "detect_city", "detect_format"]
