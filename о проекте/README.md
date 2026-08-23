# data-sources-management

## Getting Started

First, run the development server:

```bash

cd /home/ai-line/Projects/dsm
npm run dev

docker compose up -d --build
docker compose restart

cd /home/ai-line/Projects/dsm/backend
docker compose logs -f fastapi_backend
docker compose logs --tail 50 fastapi_backend


{"ok":true,"result":true,"description":"Webhook was set"}


Listed directory data-sources-management
Listed directory backend
Viewed main.py:1-49
Viewed walkthrough.md:1-106
Viewed 3walkthrough.md:1-26
Viewed route.ts:1-59
Viewed package.json:1-33
Ran command: `./backend/venv/bin/uvicorn --version`
Ran command: `venv/bin/uvicorn main:app --reload --port 8000`

Для запуска бэкенда (FastAPI) выполните следующие шаги:

1. Перейдите в директорию `backend`:
   ```bash
   

   
   ```
2. Активируйте виртуальное окружение:
   ```bash
   source venv/bin/activate
   ```
3. Запустите сервер с помощью `uvicorn`:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

*Альтернативный вариант в одну команду из корня проекта (без ручной активации окружения):*
```bash
./backend/venv/bin/uvicorn --app-dir backend main:app --reload --port 8000

```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

## Learn More

To learn more, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.
- [v0 Documentation](https://v0.app/docs) - learn about v0 and how to use it.
