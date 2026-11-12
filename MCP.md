# MCP

После запуска:

```text
docker compose up -d --build
```

MCP endpoint:

```text
http://localhost:8001/mcp
```

Для MCP-клиента с поддержкой Streamable HTTP укажи этот URL.
В production сервер отвечает OAuth challenge, публикует protected-resource metadata
на `/.well-known/oauth-protected-resource/mcp` и направляет клиента в Passi.
Полученный Passi access token имеет audience `https://finance.passi.cloud/mcp`;
MCP передаёт тот же bearer token в REST API. Подпись, issuer, audience, срок действия,
scope `email` и разрешённый email проверяются на обеих границах.

Локальный стек без Passi сохраняет отдельный `MCP_CLIENT_TOKEN` только как dev fallback.

Доступные операции:

- подключение и статус Swedbank;
- синхронизация;
- список счетов и включение/выключение наблюдения;
- операции и расходы;
- месячные категории, бюджет, иконка, включение, удаление;
- назначение и split трат;
- сводка и группировка по дням.

`start_bank_authorization` возвращает `/connect` на публичном адресе приложения.
Его нужно открыть в браузере и войти через Passi: это привязывает банковский callback
к инициировавшему браузеру и владельцу данных. После этого можно вызвать
`get_connection_status` и `sync_transactions`.

## Тестовые операции и автообновление

```powershell
rtk docker compose exec -T app python -m app.seed_demo --month 2026-11
rtk docker compose exec -T app python -m app.categorize_demo --month 2026-11
```

Первая команда создаёт 150 расходов с пометкой `[TEST]` на отдельном счёте.
Повторный запуск не создаёт дубликаты. Вторая назначает категории через MCP
Streamable HTTP и проверяет все 150 назначений. Без `--month` используется текущий месяц.
Тестовый счёт можно выключить в интерфейсе; банковская синхронизация его пропускает.
Для повторного назначения счёт должен быть включён.

`GET /api/events` — поток SSE с событием `changed` и пустым JSON `{}`.
Триггеры PostgreSQL на счетах, операциях, категориях и split-частях отправляют
уведомление после commit, включая прямой импорт и изменения через MCP.
Откат транзакции не отправляет уведомления. Подписка создаётся до первого события;
после переподключения фронтенд заново загружает актуальные данные.
События объединяются, пока выполняется загрузка; неудачная загрузка повторяется.
Открытая форма split и активное поле ввода откладывают автоматическую перерисовку
до завершения редактирования. Выбранная категория, фильтр и раскрытые дни сохраняются.
Каждая открытая страница занимает отдельное соединение PostgreSQL для LISTEN.

Проверки:

```powershell
rtk npm test
Get-Content -Raw tests/test_live_events.py | rtk docker compose exec -T app python -
```

Основание реализации: [PostgreSQL NOTIFY](https://www.postgresql.org/docs/16/sql-notify.html),
[asyncpg listeners](https://magicstack.github.io/asyncpg/current/api/index.html#asyncpg.connection.Connection.add_listener),
[Starlette StreamingResponse](https://starlette.dev/responses/#streamingresponse).

Сервис слушает только локальный Docker-порт, проверяет Bearer-токен, Host и Origin.
Не публикуй `8001` наружу: входная авторизация дополняет, а не заменяет сетевую изоляцию.
