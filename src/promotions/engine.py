import json
from datetime import date, datetime
from pathlib import Path

import structlog

logger = structlog.get_logger()

PROMOTIONS_DIR = Path("data/promotions")


class PromotionEngine:
    def __init__(self):
        self.promotions: list[dict] = []
        self.load_promotions()

    def load_promotions(self):
        """Load promotions from JSON files in data/promotions/."""
        self.promotions = []
        if not PROMOTIONS_DIR.exists():
            PROMOTIONS_DIR.mkdir(parents=True, exist_ok=True)
            return

        for file in PROMOTIONS_DIR.glob("*.json"):
            try:
                with open(file, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.promotions.extend(data)
                elif isinstance(data, dict):
                    self.promotions.append(data)
            except Exception as e:
                logger.error("promotion_load_error", file=str(file), error=str(e))

        # Filter out expired promotions
        today = date.today().isoformat()
        self.promotions = [
            p for p in self.promotions
            if p.get("end_date", "9999-12-31") >= today
        ]

        logger.info("promotions_loaded", count=len(self.promotions))

    def get_relevant_promotions(self, query: str, limit: int = 3) -> list[dict]:
        """Find promotions relevant to the user's query."""
        query_lower = query.lower()
        scored = []

        for promo in self.promotions:
            score = 0
            promo_text = f"{promo.get('name', '')} {promo.get('description', '')} {promo.get('category', '')}".lower()

            # Simple keyword matching
            query_words = query_lower.split()
            for word in query_words:
                if len(word) > 2 and word in promo_text:
                    score += 1

            if score > 0:
                scored.append((score, promo))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:limit]]

    def get_top_promotions(self, limit: int = 5) -> list[dict]:
        """Get top current promotions (for 'что интересного?' queries)."""
        return self.promotions[:limit]

    def format_promotions(self, promotions: list[dict]) -> str:
        """Format promotions for display in bot messages."""
        if not promotions:
            return ""

        lines = ["🔥 **Актуальные акции:**"]
        for p in promotions:
            name = p.get("name", "")
            old_price = p.get("old_price")
            new_price = p.get("new_price", "")
            end_date = p.get("end_date", "")

            if old_price:
                lines.append(f"• {name} — **{new_price} BYN** ~~{old_price} BYN~~ (до {end_date})")
            else:
                lines.append(f"• {name} — {new_price} BYN (до {end_date})")

        return "\n".join(lines)
