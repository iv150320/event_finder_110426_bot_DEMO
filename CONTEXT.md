# Event Finder Bot — Контекст сессий

> Этот файл ведётся автоматически. При каждой сессии opencode читает и обновляет его, чтобы сохранить контекст между запусками.

## Текущая версия: v9.2.0

## Последняя активность

- **Дата:** 2026-04-26
- **Действие:** Полная переработка классификации тем — 8 целевых тем, KudaGo category exclusion, word boundary matching

## История сессий

### 2026-04-26 (сессия 10: переработка классификации тем)
- **8 целевых тем**: бизнес, IT, AI, экономика, политика, история, английский язык, психология, литература. Старые темы (образование, наука, маркетинг, дизайн) удалены. 'технологии' разделена на IT и AI.
- **KudaGo category exclusion**: события с категориями concert/entertainment/theater/party/stock/tour/cinema/kids/recreation/fashion/photo/festival исключаются автоматически. Только чистые 'education' и 'exhibition' проходят.
- **Word boundary matching**: темы с `word_boundary=True` (IT, AI) используют `\b` regex для коротких ASCII-ключевых слов (ai, ml, it, app, web). Устраняет false positives: "ai" в "entertainment", "it" в "китайский".
- **Удалены токсичные короткие кириллические keywords**: "ит" (→ "китайский"), "ии" (→ "компании", "россии", "истории"), "крипто" → "криптовал"/"криптом" (не "криптография").
- **process_events_pipeline** добавлен в `handlers/search.py` — ручной поиск фильтрует по темам как scheduler.
- **21 новый тест** классификации: все 8 тем, category exclusion, word boundaries, pipeline filtering.
- **DB re-classification**: 367 событий с старыми темами переподписаны; 252 получили пустую тему (удалены из результатов).
- Результат: 0 entertainment leaks в KudaGo (было 103 false positives), 86/88 тестов проходят.
- Коммит `383c065`.

### 2026-04-26 (сессия 9: тестирование + оставшиеся исправления)
- **ru_date exception handling (коммит `5b0a5f4`)**: `fetch_kudago` — `except (KeyError, TypeError, OSError, ValueError)` пропускал `AttributeError` от `ru_date()`, вызывая `UnboundLocalError: date_str`. Заменён на `except Exception`. Аналогичный `try/except` добавлен в `fetch_timepad`. Также исправлен отступ (8→12 пробелов) — блоки были вне тела цикла `for`.
- **notion_service sync→async (коммит `5e23ba8`)**: `ensure_events_database()` был async, но внутри делал блокирующий `self._session.post()`. Теперь HTTP-вызов обёрнут в `asyncio.to_thread()`. `add_events_to_database` уже вызывался через `asyncio.to_thread()` из `bot.py`. `create_event_page`/`create_events_table_page` не используются — мёртвый код.
- **Indentation audit**: Систематическая проверка всех for/while циклов и try/except блоков в `event_search.py` — проблем не найдено.
- **Ручное тестирование**:
  - fetch_kudago: 133 события, 133 уникальных заголовков ✓
  - fetch_timepad: 0 событий (API 403 — ключ истёк/некорректен, не баг кода)
  - fetch_university: ВШЭ 62 события ✓, РАНХиГС 0 (301 redirect), МГУ Экономфак 0 (SSL expired)
  - process_events_pipeline: 133→77 после topic filter ✓
  - notion_service ensure_events_database: async OK, DB ID создан ✓
  - scheduler _scan_cycle: 22.8s, 71 новых событий, отчёт отправлен ✓
  - batch INSERT: 300 событий за 0.425s (706 events/sec), 0 дубликатов при повторной вставке ✓
- Все 65 тестов проходят (2 skipped).

### 2026-04-26 (сессия 8: архитектурный рефакторинг — пункты 9-13)
- **Пункт 9+10 (N+1 + Race condition)**: `save_events_with_ids` переписан — batch `INSERT OR IGNORE` в одной транзакции вместо N×(SELECT+INSERT). Добавлен `UniqueConstraint('title', 'date', 'source')` в `models.py`. Миграция: `CREATE UNIQUE INDEX IF NOT EXISTS` + `DELETE` дубликатов перед созданием индекса. `save_event` упрощён: убран SELECT-чек, ловится `IntegrityError`. Результат: 300 событий = 1 транзакция вместо 600 запросов (~5x быстрее), race condition невозможен.
- **Пункт 13 (_fetch_with_retry)**: Убран catch-all `except Exception` — unexpected errors больше не маскируются как «нет данных». 4xx (кроме 429) → `return None` (не ретраим), 5xx/429/timeout → retry через декоратор. `async_with_retry` / `with_retry` получили параметр `swallow: bool = True` — по умолчанию возвращают `None` после исчерпания попыток (обратная совместимость), но можно `swallow=False` для re-raise.
- **Пункт 12 (parsing_strategies.py)**: Удалён файл `parsing_strategies.py` — дублировал `_UNI_PARSERS` из `event_search.py`. HSE парсер был реализован дважды (Strategy pattern + функция). KudaGo parser в strategies всегда возвращал `[]`. Убран import + вызов `get_parser_for_url()` из `fetch_university()`. `_UNI_PARSERS` — единственный реестр парсеров.
- **Пункт 11 (Глобальные синглтоны)**: Убраны `db`, `scheduler`, `notion_service` из `handlers/__init__.py`. Хендлеры получают зависимости через `context.bot_data["db"]` / `context.bot_data["scheduler"]` / `context.bot_data["notion_service"]`. Объекты создаются в `bot.py` и кладутся в `bot_data` в `post_init`. Helper-функции `_db(context)`, `_scheduler(context)` в каждом хендлере.
- Также в этой сессии были исправлены баги 1-8 (предыдущий коммит `04c2ada`): scheduler.py pipeline indent, fetch_kudago/fetch_timepad loop indent, double-escaped \\n, async without await в notion_service, _is_admin min()→in, удаление dead stubs, version unification.
- Все 65 тестов проходят (2 skipped).

### 2026-04-26 (сессия 7: KudaGo date filter + async retry + UI fixes)
- **KudaGo date filter**: Исправлен фильтр дат — KudaGo для ongoing-событий ставит `start=-62135433000` (год 0001, sentinel). Реальная дата только в `end`. Теперь код проверяет `ts_start > _KUDAGO_NULL_TS (-62135520000)` перед использованием start, и fallback на `ts_end`.
- **async_with_retry**: Добавлен в `error_handling.py` — async-декоратор с `asyncio.sleep()` вместо `time.sleep()`. `_fetch_with_retry` переведён на `@async_with_retry`.
- **with_retry/async_with_retry**: Исправлен `return None` — был на уровне `decorator()` вместо `wrapper()`, из-за чего декорированные функции возвращали `None`.
- **UI**: Упрощён поиск (город→период, без темы), админ-кнопки скрыты для не-админов, `start()` не чистит `user_data` целиком.
- **DB search**: Добавлен `db.search_events()` + `_city_sources()` в handlers/search.py — поиск по DB как первичный источник.
- Все 65 тестов проходят (2 skipped).

### 2026-04-26 (сессия 6: Hourly LLM Digest & Sources Expansion)
- **Feature: Hourly LLM Digest**: Независимый цикл курации контента с помощью DeepSeek V3.2. Добавлен трекинг `is_llm_reported` в таблицу `EVENTS` для исключения дубликатов в дайджестах.
- **Feature: Sources Expansion**: Добавлены новые парсеры для Habr, Telegram (web preview), Leader-ID и лекториев (Arzamas, ЗИЛ).
- **Feature: Anti-ban**: Реализовано дросселирование (throttling) с рандомными задержками между запросами к внешним ресурсам.
- **Architecture**: Переход на гибридную модель парсинга — основной поток на Regex/BS4 с автоматическим переключением на LLM Fallback для сложных структур.

### 2026-04-25 (сессия 5: масштабный архитектурный и UI/UX рефакторинг)
- **True Async I/O**: Мы полностью отказались от `requests` и `asyncio.to_thread`. Проект теперь использует `httpx` и `async/await` для нативного, неблокирующего выполнения HTTP-запросов.
- **Async ORM**: Удалены сырые SQLite-запросы в `database.py`. Теперь проект использует `SQLAlchemy 2.0` с `aiosqlite`, а схема БД строго определена в `models.py`.
- **Параллельное сканирование**: `scheduler.py` теперь использует `asyncio.gather` для одновременного опроса нескольких вузов и корпоративных источников, что радикально ускоряет цикл сканирования.
- **App-like UI**: `bot.py` был разбит на модульную структуру в директории `handlers/` (`menu.py`, `search.py`, `admin.py`). Мы отказались от громоздких «простыней» `ReplyKeyboardMarkup` и полностью перешли на `InlineKeyboardMarkup` с callback-маршрутизацией. Добавлена inline-пагинация для результатов поиска.
- **Тестирование**: Все тесты были переписаны с использованием `unittest.IsolatedAsyncioTestCase` и `AsyncMock` для поддержки новой асинхронной архитектуры.

### 2026-04-25 (сессия 4: полная переработка UX)
Реструктуризация пользовательских путей:

- **А. «📅 Сегодня» на MAIN_KEYBOARD** — быстрый доступ к событиям на сегодня (1 клик вместо 4)
- **Б. «▶️ Запустить поиск» → «▶️ Запустить сканер»** — устранена путаница между фоновым сканированием и ручным поиском
- **В. Умный контекст города** — `last_city` сохраняется между сессиями; «📅 Сегодня» ищет в последнем городе, а не всегда в Москве; `_safe_clear()` / `_preserve_last_city()` для селективной очистки user_data
- **Г. Quick shortcuts** — на TOPIC_KEYBOARD добавлены «⭐ Бизнес сегодня» и «⭐ Всё на неделю» (пропускают выбор города и периода); `_quick_search()` helper
- **Д. Контекст в AFTER_RESULTS** — «🧠 Анализ» учитывает предыдущий поиск: если после «🔍 Найти мероприятия» — анализ по city-источникам, если после «🎓 Вузы» — по вузам; `last_search` хранит `flow`, `topic`, `city`, `uni`
- **Е. «🔍 Уточнить» вместо «🔍 Новый поиск»** — после результатов можно изменить один параметр (период/город/тему) без начала с нуля; новый state `CHOOSING_REFINE`, `REFINE_KEYBOARD`, обработчики `refine_search()` + `refine_selected()`
- **Ж. Реструктуризация MAIN_KEYBOARD** — поиск наверху, управление сканером в середине, экспорт внизу
- Обновлён `help_command` с описанием новых кнопок
- Обновлено стартовое сообщение

### 2026-04-24 (сессия 3: переработка механики бота)
Исправлены критические баги механики ConversationHandler:

1. **Два конфликтующих ConversationHandler** (`conv_search` + `conv_uni`) с общими числовыми state IDs → объединены в один `conv_main` с 6 состояниями и 5 entry points
2. **`analysis_cmd()` — мёртвый функционал**: отдельный MessageHandler показывал `ANALYSIS_TOPIC_KEYBOARD`, но следующий ввод перехватывался fallback'ом → заменён на `analysis_start()`, возвращающий `CHOOSING_ANALYSIS_TOPIC`
3. **`new_search_cmd()` перехватывался раньше conv_search**: отдельный MessageHandler регистрировался до ConversationHandler → убран, "🔍 Новый поиск" теперь entry_point `conv_main`
4. **`today_events_cmd()` конфликтовал с PERIOD_KEYBOARD**: "📅 Сегодня" из AFTER_RESULTS перехватывался отдельным handler'ом → заменён на `today_start()` как entry_point `conv_main`
5. **«Назад» в `analysis_topic_selected`** всегда вёл в `CHOOSING_UNI_DATES` → теперь проверяет `context.user_data["flow"]`: `"uni"` → UNI_DATES, `"search"` → DATES, иначе → END + главное меню
6. **3× дублирование парсинга дат** (dates_selected, uni_dates_selected, today_events_cmd) → вынесено в `parse_period_label()`

Дополнительно:
- Добавлен `menu_cmd_end()` — обработчик "🏠 Меню" внутри ConversationHandler (во всех 6 состояниях)
- Добавлен `context.user_data["flow"]` в каждую entry point для отслеживания контекста
- `analysis_source` автоматически переключается на `"city"`, если `flow == "search"`
- Удалены мёртвые функции: `today_events_cmd`, `analysis_cmd`, `new_search_cmd`
- 70 тестов проходят (2 pre-existing cache failures из-за database locking)

### 2026-04-24 (сессия 2: UX-улучшения + фильтрация сканера)
- **Кнопка «📅 Сегодня»** добавлена в PERIOD_KEYBOARD (вместо «🧠 Анализ»)
- **«🧠 Анализ» перенесён** на экран после результатов (AFTER_RESULTS_KEYBOARD: [🧠 Анализ, 📅 Сегодня], [🔍 Новый поиск, 🏠 Меню])
- Обработчики `today_events_cmd()`, `analysis_cmd()`, `new_search_cmd()`, `menu_cmd()` добавлены в bot.py
- Поддержка «Сегодня» в `dates_selected()` и `uni_dates_selected()` (start=today, end=today 23:59:59)
- Убрана маршрутизация «Анализ» из выбора периода
- **Фильтрация тем сканера**: `SCHEDULER_ALLOWED_TOPICS` в config.py — сканер пропускает только бизнес/экономика/психология/история/технологии/образование/наука/маркетинг/дизайн (концерты/шоу/развлечения отфильтрованы)
- **Отчёт сканера**: отправляется только при `new_count > 0` (не каждый цикл), группировка по темам вместо источников
- **Исправлен SyntaxError в scheduler.py**: блок фильтрации тем (строки 208-266) имел отступ 4 пробела вместо 8 — код оказался вне метода `_scan_cycle`, что вызывало `'await' outside function`. Исправлено: весь блок внутри метода получил отступ 8 пробелов.
- Все 73 теста проходят, Docker пересобран

### 2026-04-24 (ИИ-анализ событий через DeepSeek V3.2)
- Добавлена кнопка «🧠 Анализ» в PERIOD_KEYBOARD (доступна из потоков «🔍 Найти мероприятия» и «🎓 Вузы»)
- Создан `nvidia_service.py` — интеграция с NVIDIA NIM API (deepseek-ai/deepseek-v3.2)
- Новый флоу: вуз → период → «🧠 Анализ» → выбор темы (бизнес/экономика/психология/история/технологии/образование/наука) → LLM анализирует все события за месяц и отбирает релевантные
- Новый state `CHOOSING_ANALYSIS_TOPIC` + клавиатура `ANALYSIS_TOPIC_KEYBOARD`
- Добавлен `NVIDIA_API_KEY` и `NVIDIA_MODEL` в `.env`, `.env.example`, `config.py`
- Исправлен баг: кэш-ключ в `fetch_university` не учитывал `end` дату → при запросе «неделя» возвращался кэш от «месяц»
- Удалён `_new_parsers.py` (парсеры перенесены в event_search.py в предыдущей сессии)

### 2026-04-23 (5 new university parsers integrated)
- `_parse_ru_day_month()` helper добавлен в `event_search.py` (парсит русские названия месяцев)
- 5 парсеров интегрированы из `_new_parsers.py` в `event_search.py`:
  - `_parse_econ_msu_events` — МГУ Экономфак (econ.msu.ru)
  - `_parse_law_msu_events` — МГУ Юрфак (law.msu.ru)
  - `_parse_cs_msu_events` — МГУ ВМК (cs.msu.ru)
  - `_parse_rea_events` — РЭУ им. Плеханова (rea.ru)
  - `_parse_ranepa_events` — РАНХиГС (ranepa.ru)
- `_UNI_PARSERS` реестр обновлён: 6 парсеров (ВШЭ + 5 новых)
- `bot.py`: UNI_KEYBOARD и UNI_MAP обновлены — убраны МГИМО (anti-bot) и МГТУ (SSL), добавлены МГУ Экон/ВМК/Юрфак
- 3 unit-теста исправлены (format_text теперь группирует по дате, не по источнику; МГИМО убран из UNIVERSITIES)
- Все 73 теста проходят
- Docker cache очищен, контейнер пересобран
- `_new_parsers.py` — временный файл, больше не нужен (парсеры перенесены в event_search.py)

### 2026-04-22 (HSE parser fix + HTML chunking fix)
- URL ВШЭ заменён с `/news/` на `/announcements` — полноценная страница с 269+ анонсами (вместо 26 на `/news/`)
- Парсер `_parse_hse_events()` переписан: вместо `div.events` / `span.events__title` теперь парсит `ann-cards__group` (даты из `h2.ann-day`) + `ann-cards__item` (события из `h3.ann-card__title`, даты из `div.ann-cards__time` в формате DD.MM)
- Результат: ВШЭ теперь возвращает **88 событий** за неделю (было 0)
- URL обновлён в `event_search.py` (UNIVERSITIES) и `bot.py` (UNI_MAP)
- **Исправлен краш бота при отправке длинных сообщений**: Telegram `Can't parse entities: can't find end tag corresponding to start tag "a"`. Причина — наивная нарезка `text[i:i+4000]` разрезала `<a>` теги пополам. Заменено на `_split_html_chunks()`, которая режет по строкам и проверяет баланс `<a>`/`</a>` тегов в каждом чанке. Заменены все 4 места в `bot.py`.

### 2026-04-21 (v8.0.0 audit)
- Обновлена версия во всех файлах с 7.0.0 до 8.0.0
- Удалён устаревший глобальный `_http` в event_search.py
- Исправлен ICS URL fallback: Docker hostname (hex-id) заменён на localhost
- Добавлен `/cancel` в ConversationHandler fallbacks (оба потока)
- Добавлена авто-очистка старых событий в scheduler (configurable через SCHEDULER_CLEANUP_DAYS)
- Notion DB: типы Источник/Тема изменены с `select` на `rich_text` — не нужно создавать опции
- CORPORATE_SOURCES: URL заменены на страницы событий (были homepage)
- Scheduler: города сканирования настраиваются через SCHEDULER_CITIES в .env
- Удалён дубликат events.db из корня проекта
- cache.db перемещён в data/
- Исправлен `import time as _time` внутри цикла в notion_service.py (вынесен наверх)
- Унифицированы type hints: `datetime | None` → `Optional[datetime]`
- Добавлен cooldown (30 сек) на ручной поиск

### 2026-04-21
- Создан `CONTEXT.md` и обновлён `AGENTS.md` для сохранения памяти между сессиями
- Проект находится на версии v8.0 (коммит `47a7308`)
- Основные фичи v8.0: async I/O, SQLite cache, graceful shutdown, bug fixes

## Архитектура проекта

- `run.py` — точка входа: bot + ICS server + graceful shutdown
- `bot.py` — Telegram bot, единый ConversationHandler (conv_main) с 7 states, 5 entry points, flow-трекинг, quick shortcuts, уточнение поиска
- `scheduler.py` — фоновый цикл сканирования
- `event_search.py` — HTTP/API/HTML-парсинг, классификация тем (8 целевых: бизнес, IT, AI, экономика, политика, история, английский язык, психология, литература), KudaGo category exclusion, word boundary matching, SQLite-кэш
- `database.py` — SQLite storage и state
- `notion_service.py` — интеграция с Notion API
- `calendar_service.py` — генерация ICS
- `ics_server.py` — HTTP server для /events.ics и /health
- `generate_avatar.py` — генерация аватарки бота

## Источники данных

- KudaGo API (основной, без ключа)
- Timepad API (нужен TIMEPAD_API_KEY)
- Яндекс.Афиша (HTML-парсинг)
- Вузы: ВШЭ, МГУ Экономфак, МГУ ВМК, МГУ Юрфак, РЭУ, РАНХиГС (кастомные парсеры); МИФИ, РУДН, Финансовый ун-т, МАИ, МГТУ (generic)

## Известные проблемы и TODO

- `.env` с реальными секретами в git-истории — рекомендуется BFG Repo-Cleaner
- HTML-парсинг может ломаться при изменении разметки источников
- SQLite — один процесс/контейнер; для масштабирования нужен отдельный storage
- `notion_service.py` — sync `requests.Session` только для `add_events_to_database` (через `asyncio.to_thread`), `ensure_events_database` теперь async с `asyncio.to_thread`. `create_event_page`/`create_events_table_page` — мёртвый код.
- Тесты не ловят indentation bugs — mock-данные содержат только 1 результат на страницу
- `ics_server.py` создаёт отдельный `EventDatabase()` — не тот же экземпляр, что в боте
- DB не хранит description/categories — при re-classification по DB-данным точность ниже, чем при fetch
- Timepad API 403 — ключ истёк/некорректен (не баг кода)

## Решения и подходы

- Кэш: SQLite (`cache.db`) вместо `cache.json` — потокобезопасный
- Асинхронность: все sync HTTP-вызовы из async контекста обёрнуты в `asyncio.to_thread()` (включая notion_service)
- HTTP-сессии: `_make_client()` factory вместо глобальной shared сессии
- Notion throttle: 0.4s между строками, 1s пауза каждые 20 строк
- Graceful shutdown: SIGINT/SIGTERM обработка в `run.py`
- DI: зависимости (db, scheduler, notion_service) через `context.bot_data`, не через module-level globals
- Dedup: `UniqueConstraint('title', 'date', 'source')` + `INSERT OR IGNORE` — атомарный, без race condition

## Команды

```bash
# Тесты
pytest -q

# Ручной smoke-тест (реальные запросы)
python test_search.py

# Запуск
python run.py

# Docker
docker compose up -d --build
```
