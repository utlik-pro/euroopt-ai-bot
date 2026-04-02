"""Генерация ежедневных отчётов для клиента и внутренней команды.

Два формата:
1. Клиентский (для Евроторга) — краткий, по делу
2. Внутренний (для команды) — детальный, с метриками
"""

from datetime import date, timedelta
from pathlib import Path

from src.monitoring.logger import interaction_logger

REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)


def generate_client_report(
    target_date: date | None = None,
    done_today: list[str] | None = None,
    planned_tomorrow: list[str] | None = None,
    blockers: list[str] | None = None,
) -> str:
    """Клиентский ежедневный отчёт (для Евроторга)."""
    d = target_date or date.today()
    stats = interaction_logger.get_daily_stats(d)

    # Номер дня разработки (старт 31.03.2026)
    start_date = date(2026, 4, 1)
    day_num = (d - start_date).days + 1
    total_days = 12

    report = f"""# Ежедневный отчёт — AI-помощник Евроопт
**Дата:** {d.strftime('%d.%m.%Y')}
**День разработки:** {day_num} из {total_days}

---

## Выполнено сегодня
"""
    if done_today:
        for item in done_today:
            report += f"- {item}\n"
    else:
        report += "- (не указано)\n"

    report += "\n## Запланировано на завтра\n"
    if planned_tomorrow:
        for item in planned_tomorrow:
            report += f"- {item}\n"
    else:
        report += "- (не указано)\n"

    if blockers:
        report += "\n## Блокеры\n"
        for item in blockers:
            report += f"- ⚠️ {item}\n"

    if stats["total_requests"] > 0:
        report += f"""
## Статистика бота
| Метрика | Значение |
|---------|----------|
| Запросов за день | {stats['total_requests']} |
| Уникальных пользователей | {stats['unique_users']} |
| Отфильтровано контент-фильтром | {stats['content_filtered']} |
| Среднее время ответа | {stats['avg_response_time_ms']} мс |
| Стоимость LLM за день | {stats['total_cost_byn']:.4f} BYN |
"""

    report += "\n---\n*Отчёт сгенерирован автоматически*\n"

    # Сохранить
    filepath = REPORTS_DIR / f"report_{d.isoformat()}.md"
    filepath.write_text(report, encoding="utf-8")

    return report


def generate_internal_report(target_date: date | None = None) -> str:
    """Внутренний отчёт для команды — детальная аналитика."""
    d = target_date or date.today()
    stats = interaction_logger.get_daily_stats(d)

    report = f"""# Внутренний отчёт — {d.strftime('%d.%m.%Y')}

## Метрики
- Запросов: {stats['total_requests']}
- Уникальных пользователей: {stats['unique_users']}
- Отфильтровано: {stats['content_filtered']}
- Ошибок: {stats['errors']}
- Всего токенов: {stats['total_tokens']}
- Стоимость: {stats['total_cost_byn']:.4f} BYN
- Среднее время ответа: {stats['avg_response_time_ms']} мс

## Модели
"""
    for model, count in stats.get("models_used", {}).items():
        report += f"- {model}: {count} запросов\n"

    filepath = REPORTS_DIR / f"internal_{d.isoformat()}.md"
    filepath.write_text(report, encoding="utf-8")

    return report


if __name__ == "__main__":
    # Пример генерации отчёта
    report = generate_client_report(
        done_today=[
            "Настроен LLM-адаптер с 9 моделями",
            "Контент-фильтр протестирован",
            "RAG-система загружена тестовыми данными",
        ],
        planned_tomorrow=[
            "Подключение Telegram-бота",
            "Тестирование качества ответов",
        ],
    )
    print(report)
