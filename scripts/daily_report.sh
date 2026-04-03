#!/bin/bash
# Генерация ежедневного отчёта
cd "$(dirname "$0")/.."

echo "=== Генерация ежедневного отчёта ==="
python3.11 -m src.monitoring.daily_report

echo ""
echo "Отчёты сохранены в reports/"
ls -la reports/*.md 2>/dev/null
