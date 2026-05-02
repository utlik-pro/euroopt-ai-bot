# Runbook: безопасное переключение LLM-моделей в проде

**Цель:** уметь за 1 минуту переключиться на другую модель без сбоя бота и за 30 секунд откатиться обратно при проблеме.

---

## Архитектура переключения

В Render env vars **2 переменные** определяют, какая модель работает в проде:

| Переменная | Что делает |
|---|---|
| `LLM_PROVIDER` | какой провайдер активен: `openai` / `glm` / `gemini` / `deepseek` / `claude` / `openrouter` / `qwen` / `yandexgpt` / `gigachat` / `atlas` |
| `LLM_MODEL` | какая модель у этого провайдера: `gpt-4o-mini` / `glm-4.7-flashx` / `claude-haiku-3.5` / etc. |

**Изменение этих 2 переменных + Save → Render deploy за ~3-4 минуты.**

Дополнительно при необходимости:
- `OPENAI_API_KEY` / `GLM_API_KEY` / `GEMINI_API_KEY` — ключи провайдеров
- `GLM_BASE_URL` — переопределение endpoint (для GLM: КНР `bigmodel.cn` ↔ intl `api.z.ai`)
- `RELAY_URL` — релей для санкционных API (Anthropic/OpenAI/Gemini из РБ)

---

## Текущее состояние (по дате)

См. `git log --oneline | head -1` и Render Dashboard. Записывать сюда **каждый раз** при переключении.

| Дата | Модель | Цена $/1M (in/out) | Latency p50 | Comment |
|---|---|---|---|---|
| 2026-04-15 → 02.05 | `gpt-4o-mini` (openai через relay) | $0.15 / $0.60 | 1.5s | baseline |
| 2026-05-02 (тест) | `glm-4.7-flashx` (glm/z.ai) | $0.07 / $0.40, cache $0.01 | 6-10s | reasoning-модель, 4× дешевле |
| (план) | `gpt-5-nano` (openai) | $0.05 / $0.40 | ? | если выйдет в OpenAI стабильно |

---

## ✅ Стандартный сценарий: переключиться на новую модель

### 1. Подготовка (5–10 минут)

1. **Бенчмарк локально** (без затрат на прод):
   ```bash
   GLM_API_KEY=... GLM_BASE_URL=... \
   python3.11 scripts/benchmark_llm_models.py \
       --models glm-4.7-flashx gpt-4o-mini \
       --runs 1 --limit 30
   ```
2. **Прочитать отчёт** в `docs/benchmark_llm_<date>.md` — pass rate, p50 latency, total cost.
3. **Если pass rate ≥ 90% от baseline** → можно переключаться.
4. **Если < 90%** → не переключаться, искать причину.

### 2. Деплой (3–4 мин)

В Render Dashboard → euroopt-ai-bot → Environment:

```
LLM_PROVIDER  = glm
LLM_MODEL     = glm-4.7-flashx
GLM_API_KEY   = <твой ключ z.ai>           # уже есть
GLM_BASE_URL  = https://api.z.ai/api/paas/v4  # уже есть
```

Нажать **Save, rebuild, and deploy**. Render билдит 3–4 мин.

### 3. Проверка после деплоя (1–2 мин)

```bash
# Telegram-пинг бота
curl -X POST "https://api.telegram.org/bot<TG_TOKEN>/sendMessage" \
  -d "chat_id=<твой_telegram_id>&text=Что такое программа лояльности Еплюс?"
```

Или через @Euroopt_gpt_bot в Telegram. **Что должно быть:**
- ✅ Ответ приходит за 5–15 секунд
- ✅ Текст не пустой
- ✅ Содержит «бонусы» (не «скидки», не «баллы»)
- ✅ Если «карта Еплюс» — упоминает 99 копеек / бесплатно

### 4. Мониторинг (первые 24 часа)

В Render Logs смотреть:
- `llm_retry attempt=...` — частота 1305 overloaded. Если >5% запросов — фолбек на gpt-4o-mini.
- `Empty content from glm-4.7-flashx` — если есть, значит reasoning_disable не сработал. Откат.
- `pii_filter triggered` — должно быть ~5–10% (норма).
- Среднее время ответа — в логах `pipeline_completed elapsed=...`.

---

## 🛑 Откат: если что-то сломалось

### Способ 1: rollback в Render (1 клик, 30 сек)

Render Dashboard → Deploys → выбрать предыдущий зелёный билд → **Rollback**.
Откатывает **код** (на до-merge-коммит), но env vars остаются. Если бот сломан **из-за env vars** (не из-за кода) — этого недостаточно.

### Способ 2: вернуть env vars (1 минута)

Render Dashboard → Environment:
```
LLM_PROVIDER  = openai     # вернуть baseline
LLM_MODEL     = gpt-4o-mini
```
Save → Render задеплоит за 3 мин. Бот вернётся на gpt-4o-mini.

### Способ 3: автофолбек в коде (всегда работает)

В `adapter.py::get_llm_provider_with_fallback` уже есть логика:
1. Сначала пытается `LLM_PROVIDER`
2. При ошибках (1305, 429, ConnectionError) — fallback на `gpt-4o-mini` через relay

→ Даже если основная модель упала, бот **продолжит работать** на gpt-4o-mini автоматически.

---

## 📊 Когда какую модель использовать

| Кейс | Модель | Почему |
|---|---|---|
| **Прод по умолчанию** | `glm-4.7-flashx` | Дёшево + хорошо следует промпту |
| **Прод (если КНР неудобен)** | `gpt-4o-mini` через relay | Безопасно, западная |
| **Большой контекст (>32K)** | `gemini-3.1-flash-lite` | 1M контекст |
| **Топ-качество для бенчмарка** | `claude-haiku-3.5` | Эталон следования промпту |
| **Бесплатно для теста** | `glm-4.7-flash` (free) или `glm-4.5-flash` (free) | Без затрат, но overloaded |
| **Сервис z.ai упал** | автофолбек на `gpt-4o-mini` | Через `get_llm_provider_with_fallback` |
| **Reasoning-задачи (если нужно)** | `deepseek-reasoner` | Хорошо рассуждает |

---

## 🧪 Сравнительная таблица для решения

После каждого бенчмарка обновлять:

| Модель | Pass rate (на 30 сценариях) | p50 latency | $/мес 3M запросов | Notes |
|---|---|---|---|---|
| `gpt-4o-mini` (baseline) | 100% | 1.5s | $1 008 | reference |
| `glm-4.7-flashx` | _TBD_ | 8s | $462 | -54% |
| `gpt-5-nano` | _TBD_ | _TBD_ | $462 | -54%, западная |
| `gemini-3.1-flash-lite` | _TBD_ | _TBD_ | $672 | -33%, EU |
| `deepseek-chat` | _TBD_ | _TBD_ | $1 204 | +20% (с cache) |

---

## 🔔 Чек-лист безопасности перед переключением

- [ ] Бенчмарк локально на ≥30 сценариях
- [ ] Pass rate ≥ 90% от baseline
- [ ] Нет «утечки» промпта (модель не повторяет SYSTEM_PROMPT в ответе)
- [ ] Нет хардкода в коде (всё через env vars)
- [ ] Tests `test_pii_filter`, `test_grounding_verifier`, `test_listovka_snapshot` зелёные
- [ ] У тебя открыт Render Dashboard в другой вкладке (для быстрого отката)
- [ ] Есть резервный API key для baseline (gpt-4o-mini), на случай если основной упадёт
- [ ] Согласовано с заказчиком (Яна) если используем КНР-модели

---

## 📝 История переключений

| Дата | Откуда → куда | Причина | Откат? |
|---|---|---|---|
| 2026-04-15 | (новый бот) → gpt-4o-mini | Запуск MVP | — |
| 2026-05-02 | gpt-4o-mini → glm-4.7-flashx | Снижение стоимости -54% | _TBD после прогона_ |

_Дополнять по мере переключений._
