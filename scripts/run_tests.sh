#!/bin/bash
# Запуск всех тестов (без реальной LLM)
cd "$(dirname "$0")/.."

echo "=== Запуск тестов Евроопт AI-помощник ==="
echo ""

python3.11 -m pytest tests/ -v --tb=short \
    --ignore=tests/test_quality.py \
    2>&1 | tee logs/test_results_$(date +%Y-%m-%d).log

echo ""
echo "=== Для тестов с реальной LLM ==="
echo "python3.11 -m pytest tests/test_quality.py -v --run-llm"
