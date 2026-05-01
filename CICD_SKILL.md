# CI/CD Skill — GitHub Actions + Docker + Auto-deploy

Универсальный навык для настройки CI/CD пайплайна на любом проекте. Копируй и адаптируй под новую VPS и любой AI-клиент.

---

## 1. Архитектура пайплайна

```
AI-клиент (любой) → push в main → GitHub Actions:
  ├── Job 1: test    (pytest + lint)
  ├── Job 2: build + smoke (docker build → run → /health)
  └── Job 3: deploy  (SSH на VPS → git pull → docker compose up)
```

**Ключевой принцип**: не предотвращать падения, а быстро их ловить и уметь откатываться.

---

## 2. Структура файлов

```
project/
├── .github/workflows/ci.yml    # CI/CD пайплайн
├── Dockerfile                   # Контейнер
├── docker-compose.yml           # Оркестрация на проде
├── run.py                       # Entrypoint (с try/except для бота)
├── .env.example                 # Шаблон env vars (в репозитории)
├── .env                         # Реальные секреты (НЕ в репозитории!)
├── .gitignore                   # Исключает .env, data/, *.db
└── requirements.txt             # Python-зависимости
```

---

## 3. GitHub Actions: ci.yml (шаблон)

```yaml
name: CI/CD

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: deploy-production
  cancel-in-progress: false    # не убивать деплой посередине

jobs:
  # ─── Test ──────────────────────────────────────────────────────────────
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install ruff

      - run: ruff check . || true    # non-blocking на старте
      - run: pytest -q

  # ─── Build + Smoke ─────────────────────────────────────────────────────
  build-and-smoke:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - run: docker build -t app .

      - name: Run container (fake env for smoke)
        run: |
          docker run -d \
            --name smoke-test \
            -e TELEGRAM_BOT_TOKEN=fake-token-for-ci \
            -e ALLOWED_USERS=123456 \
            -e ICS_PORT=8081 \
            -p 8081:8081 \
            app

      - run: sleep 10

      - name: Health check
        run: |
          RESPONSE=$(curl -sf http://localhost:8081/health || echo "FAILED")
          echo "$RESPONSE"
          echo "$RESPONSE" | grep -q '"status": "ok"'

      - name: Container alive
        run: |
          [ "$(docker inspect --format='{{.State.Running}}' smoke-test)" = "true" ]

      - if: always()
        run: docker logs smoke-test 2>&1 | tail -30

      - if: always()
        run: docker stop smoke-test && docker rm smoke-test

  # ─── Deploy ────────────────────────────────────────────────────────────
  deploy:
    needs: [test, build-and-smoke]
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.PROD_HOST }}
          username: ${{ secrets.PROD_USER }}
          key: ${{ secrets.PROD_SSH_KEY }}
          script: |
            cd /root/<PROJECT_DIR>
            git pull origin main
            docker compose up -d --build
            sleep 5
            docker compose ps
```

---

## 4. Настройка с нуля на новой VPS

### 4.1. Подготовка VPS

```bash
# На VPS
apt update && apt upgrade -y
apt install -y docker.io docker-compose-plugin git python3-pip

# Клонировать проект
cd /root
git clone https://github.com/<USER>/<REPO>.git
cd <REPO>

# Создать .env из шаблона
cp .env.example .env
nano .env   # заполнить реальные токены

# Запустить
docker compose up -d --build
```

### 4.2. Генерация deploy SSH-ключа

```bash
# На VPS — создать отдельный ключ для CI
ssh-keygen -t ed25519 -f /root/.ssh/id_deploy_ci -N "" -C "github-actions-deploy"

# Добавить публичный ключ в authorized_keys
cat /root/.ssh/id_deploy_ci.pub >> /root/.ssh/authorized_keys

# Проверить
ssh -i /root/.ssh/id_deploy_ci -o StrictHostKeyChecking=no root@<VPS_IP> "echo OK"
```

### 4.3. Добавить GitHub Secrets

```bash
# С любого компьютера с gh CLI (авторизованного)
gh secret set PROD_HOST   --body "<VPS_IP>"      --repo <USER>/<REPO>
gh secret set PROD_USER   --body "root"           --repo <USER>/<REPO>
gh secret set PROD_SSH_KEY --repo <USER>/<REPO> < /root/.ssh/id_deploy_ci
```

Или через веб: **Settings → Secrets and variables → Actions → New repository secret**

---

## 5. Entrypoint с отказоустойчивостью (run.py)

**Проблема**: при невалидном TELEGRAM_BOT_TOKEN (например в CI) бот крашит весь процесс.

**Решение**: обернуть запуск бота в try/except, чтобы ICS server выжил:

```python
def main():
    # ... запуск ICS server в daemon thread ...

    from bot import main as run_bot

    try:
        run_bot()
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")
        logger.info("ICS server still running")
        while True:
            import time as _time
            _time.sleep(60)
```

**Результат**: smoke-test в CI может поднять контейнер с фейковым токеном
и проверить `/health` — даже если Telegram бот не стартанул.

---

## 6. Dockerfile (минимальный рабочий)

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os, signal; os.kill(1, 0)" || exit 1

CMD ["python", "run.py"]
```

---

## 7. docker-compose.yml (для прода)

```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    image: <PROJECT_NAME>
    container_name: <PROJECT_NAME>
    env_file: .env
    restart: unless-stopped
    ports:
      - "8081:8081"
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import os, signal; os.kill(1, 0)"]
      interval: 30s
      timeout: 10s
      start_period: 10s
      retries: 3
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

---

## 8. Правила для AI-клиентов

### Рабочий процесс (workflow)

```
1. git pull origin main          # ВСЕГДА начинай с pull
2. Прочитать CONTEXT.md          # Контекст между сессиями
3. Прочитать ARCHITECTURE.md     # Структура проекта
4. Внести изменения
5. pytest -q                     # Тесты перед коммитом
6. git add + commit + push       # CI/CD запустится автоматически
```

### Чеклист перед push

- [ ] `pytest -q` проходит (или skipped — ок, failed — нет)
- [ ] `python -c "import py_compile; py_compile.compile('main_file.py', doraise=True)"` — синтаксис ок
- [ ] `.env` НЕ содержит реальных секретов (проверить staged files)
- [ ] `CONTEXT.md` обновлён

### Правила кода

| Правило | Почему |
|---------|--------|
| Не коммить без явного запроса | Пользователь контролирует что уходит на прод |
| `asyncio.to_thread()` для синхронных HTTP | Не блокировать event loop |
| `_make_session()` вместо глобальной сессии | Избежать race conditions |
| Русский для комментариев/доков | Язык проекта |
| Английский для кода (переменные, функции) | Стандарт |
| `pytest -q` для тестов | Быстрый запуск |
| `try/except` в entrypoint | ICS server выживает при падении бота |

---

## 9. Откат при падении (rollback)

### Вариант 1: git revert (рекомендуемый)

```bash
# Найти последний рабочий коммит
git log --oneline -5

# Откатить
git revert <bad-commit-hash>
git push   # CI/CD пересоберёт и задеплоит
```

### Вариант 2: откат на несколько коммитов назад

```bash
# На VPS вручную (если CI не работает)
cd /root/<PROJECT_DIR>
git log --oneline -5
git reset --hard <good-commit-hash>
docker compose up -d --build
```

### Вариант 3: быстрый фикс

```bash
# Просто поправить и пушить — CI задеплоит автоматически
git pull
# ... исправить ...
pytest -q
git add -A && git commit -m "fix: ..." && git push
```

---

## 10. Мониторинг

```bash
# Статус контейнера
docker compose ps

# Логи (последние 50 строк)
docker compose logs --tail 50

# Логи в реальном времени
docker compose logs -f

# Health check
curl -s http://localhost:8081/health

# Проверить CI/CD
gh run list --repo <USER>/<REPO> --limit 3
gh run view <RUN_ID> --repo <USER>/<REPO>
```

---

## 11. Чеклист настройки нового проекта

- [ ] Репозиторий на GitHub
- [ ] `.env.example` в репозитории, `.env` в `.gitignore`
- [ ] `Dockerfile` с `HEALTHCHECK`
- [ ] `docker-compose.yml` с `restart: unless-stopped` + `healthcheck`
- [ ] `run.py` с `try/except` вокруг основного сервиса
- [ ] `/health` endpoint в приложении
- [ ] `.github/workflows/ci.yml` (3 job'а)
- [ ] Deploy SSH-ключ: сгенерирован на VPS → публичный в `authorized_keys` → приватный в GitHub Secrets
- [ ] GitHub Secrets: `PROD_HOST`, `PROD_USER`, `PROD_SSH_KEY`
- [ ] `CONTEXT.md` — история сессий
- [ ] `ARCHITECTURE.md` — структура проекта (для AI-контекста)
- [ ] `.gitignore` исключает `.env`, `data/`, `*.db`, `__pycache__/`
- [ ] Тесты не зависят от реальных секретов (mock'и / skipTest)

---

## 12. Распространённые проблемы

| Проблема | Решение |
|----------|---------|
| Deploy job падает | Проверить SSH-ключ: `ssh -i <key> root@<IP> "echo OK"` |
| Smoke test: контейнер умирает | Добавить `try/except` в entrypoint |
| Smoke test: `/health` не отвечает | Проверить порт в `-p 8081:8081` и `ICS_PORT` env |
| Тесты падают в CI | Проверить что тесты не пишут в readonly БД (использовать `skipTest`) |
| `database is locked` | SQLite не поддерживает параллельные записи — использовать mock в тестах |
| Ruff падает на existing код | `ruff check . \|\| true` (non-blocking), потом постепенно фиксить |
| Docker build медленный | `COPY requirements.txt .` перед `COPY . .` — кэширует слои |
| VPS перезагрузилась | `restart: unless-stopped` в docker-compose — контейнер поднимется сам |
