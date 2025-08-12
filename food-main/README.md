# Foody — Railway pack (web / backend / bot)

Три сервиса под Railway, соответствуют папкам:
- `web` — Express + статическая выдача buyer/merchant
- `backend` — FastAPI + SQLAlchemy (Postgres через `DATABASE_URL`)
- `bot` — FastAPI + aiogram (вебхук `/tg/webhook`)

## Быстрый деплой на Railway

1. Создай пустой репозиторий и залей этот код.
2. На Railway создай **три сервисa** из этого репозитория:
   - **web** (Node 20, есть `web/Dockerfile`), переменная: `FOODY_API=https://backend-production-a417.up.railway.app`
   - **backend** (Dockerfile уже есть), переменные:
     - `CORS_ORIGINS=https://web-production-5431c.up.railway.app,https://bot-production-0297.up.railway.app`
     - `DATABASE_URL=postgresql://postgres:***@postgres.railway.internal:5432/railway`
     - `RUN_MIGRATIONS=1`
   - **bot** (Dockerfile уже есть), переменные:
     - `BOT_TOKEN=...`
     - `WEBHOOK_SECRET=foodySecret123`
     - `WEBAPP_PUBLIC=https://web-production-5431c.up.railway.app`
     - `WEBAPP_BUYER_URL=https://web-production-5431c.up.railway.app/web/buyer/`
     - `WEBAPP_MERCHANT_URL=https://web-production-5431c.up.railway.app/web/merchant/`

3. Прописать домены:
   - BOT домен: `bot-production-0297.up.railway.app`
   - WEB домен: `web-production-5431c.up.railway.app`
   - BACKEND домен: `backend-production-a417.up.railway.app`

4. Выставить вебхук телеграм:
   ```sh
   BOT_TOKEN=...    BOT_DOMAIN=bot-production-0297.up.railway.app    WEBHOOK_SECRET=foodySecret123    ./scripts/set_webhook.sh
   ```

5. Проверка здоровья:
   - backend: `GET https://backend-production-a417.up.railway.app/health` (должно вернуть OK)
   - bot: `GET https://bot-production-0297.up.railway.app/` (должно вернуть ping/ok)
   - web: `GET https://web-production-5431c.up.railway.app/web/buyer/`

## Конфиг фронта (web)

`server.js` отдаёт `/config.js` со значением `window.foodyApi` из `FOODY_API`.
Buyer/merchant страницы используют этот URL для запросов к API backend.

## Замечания

- **Секреты** не кладём в репозиторий. Используй `.env.example` как подсказку, а реальные значения задавай в Railway Variables.
- CORS: бэкенд читает `CORS_ORIGINS` (список через запятую).
- Бот проверяет `x-telegram-bot-api-secret-token` на `WEBHOOK_SECRET`.
- Бэкенд поддерживает `RUN_MIGRATIONS=1` для авто-миграций при старте.
