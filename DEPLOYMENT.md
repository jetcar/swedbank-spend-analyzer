# Сервер mailu1024

Развёртывание: `/opt/finance`, Compose-проект `finance`, файл `docker-compose.server.yml`.
Секреты: `.env.server` и `secrets/enablebanking.pem`, права 600; каталог 700.
Пример переменных: [deploy/server.env.example](deploy/server.env.example).

## Подключение существующего прокси

- С самого хоста: `http://127.0.0.1:18000`.
- Из контейнера HAProxy: `finance-gateway:8000` в существующей сети `passi_haproxy_net`.
- Health check: `GET /health`, ожидается HTTP 200.
- Передавать исходный `Host: finance.passi.cloud` и `X-Forwarded-Proto: https`.
- Для `/api/events` не буферизовать ответ и установить тайм-аут не менее 60 секунд.
- Nginx обслуживает корень домена. Префикс вроде `/finance/` не поддерживается.

Пример backend для существующего HAProxy (добавление маршрутизации выполняется отдельно):

```haproxy
backend finance_backend
    mode http
    http-request set-header X-Forwarded-Proto https
    option httpchk GET /health HTTP/1.1\r\nHost:\ finance.passi.cloud
    server finance finance-gateway:8000 check
```

После пересоздания gateway его IP может измениться: HAProxy должен заново разрешить
имя (настроенные Docker DNS resolvers либо reload). TLS, DNS, конфигурация существующего
HAProxy и другие работающие контейнеры при развёртывании не менялись.

## Данные и вход

Создан отдельный том `finance_postgres_data`. Начальная БД пустая, кроме стандартных
категорий; локальные банковские сессии, операции и демоданные не переносились.
Перенесены настройки Enable Banking и ключ для подключения банка.

Вход через Passi включён. Клиент должен разрешать redirect URI
`https://finance.passi.cloud/auth/callback`. Завершить интерактивный вход можно после
настройки внешнего маршрута и TLS; сессия использует Secure cookie.
Банковский callback: `https://finance.passi.cloud/callback`, его также нужно разрешить
в настройках Enable Banking приложения.

MCP доступен через `https://finance.passi.cloud/mcp` и работает как OAuth protected
resource. Metadata находится на `/.well-known/oauth-protected-resource/mcp` и указывает
на issuer `https://passi.cloud/openidc`. Клиент регистрируется в Passi, выполняет PKCE
S256 и получает access token для audience `https://finance.passi.cloud/mcp`. MCP передаёт
тот же bearer token в `/api/*`; обе границы независимо проверяют JWKS-подпись, issuer,
audience, срок, scope `email` и allowlist. `email_verified` не используется.
Старые `MCP_CLIENT_TOKEN`, `PASSI_SERVICE_TOKEN` и `PASSI_SERVICE_OWNER_ID` deploy-скрипт
удаляет из production `.env.server`. MCP и PostgreSQL не публикуют порты хоста; gateway
слушает на loopback и в сети прокси.

## Управление

```sh
sudo -i
cd /opt/finance
docker compose --env-file .env.server -f docker-compose.server.yml ps
docker compose --env-file .env.server -f docker-compose.server.yml logs --tail 50 app gateway
```

Сервер использует готовые `jetcar/bank-app`, `jetcar/bank-nginx`, `jetcar/bank-postgres`
из Docker Hub. В серверном Compose нет `build`. Значения `APP_IMAGE`, `NGINX_IMAGE`,
`POSTGRES_IMAGE` в `.env.server` закреплены по registry digest.

## Логи в Google Cloud

Логи пяти сервисов finance собирает установленный на `mailu1024`
Google Cloud Ops Agent и отправляет в проект `passi-165ca` как структурированный `jsonPayload`.
Каждая новая запись содержит `labels.application=finance`, `labels.service`
(`app`, `mcp`, `postgres`, `gateway`, `bank-egress`) и `labels.replica`.
У Docker-записей также есть `labels.stream`. Время источника становится временем события.
Путь исходного файла остаётся в `labels."agent.googleapis.com/log_file_path"`
и позволяет определить контейнер или файл PostgreSQL.

- Python (app, MCP, Uvicorn и библиотеки): один JSON на событие с `service`, `logger`,
  `message`, `process_id`, `timestamp`. Полный traceback и цепочка исключений хранятся
  в одном поле `exception`, переводы строк экранированы. Уровень выносится в `severity` LogEntry.
- Nginx access: JSON с `method`, `path`, числовыми `status`, `duration`, `request_id`.
  Open-source Nginx пишет error log текстом; агент разбирает его в поля `message`,
  `process_id`, `thread_id` и `severity`, сохраняя метку сервиса.
- PostgreSQL: штатный `jsonlog`, одно событие с `message`, `detail`, `context`, `hint`
  и прочими полями PostgreSQL. Агент читает JSON напрямую из тома `finance_postgres_data`
  по `/var/lib/docker/volumes/finance_postgres_data/_data/finance-logs/*.json`.
  Журнал в Google — `finance_postgres`; служебный stdout/stderr запуска остаётся в `docker`.
  Ротация файлов БД — каждый час, 24 имени с перезаписью через сутки; лимита размера нет.
  `docker compose logs postgres` показывает только запуск коллектора; рабочие логи смотреть в Google.

Агент сначала снимает JSON-обёртку Docker, затем разбирает JSON самого события,
поэтому в Google нет строки с вложенным JSON вместо полей. Старые текстовые сообщения
остаются в `jsonPayload.message`; уже сохранённая история не переписывается.

Фильтр в Logs Explorer:

```text
resource.type="gce_instance"
labels.application="finance"
```

Для отдельного сервиса добавить `labels.service="app"`. Старые записи до включения
меток остаются без этих полей. Это имя сервиса-отправителя, а не пользователя приложения.
Браузерная консоль не отправляется на сервер. HTTP access log Nginx сохраняет путь
без query string; тела запросов, cookie и заголовки не добавляются.

В `docker-compose.server.yml` у всех сервисов задан `json-file` с ротацией 3 × 10 MB
на контейнер. Это локальный буфер, не бессрочный архив: при длительном сбое сборщика
старые локальные записи могут быть вытеснены. Срок хранения в Google задаётся log bucket.
Приложению не нужны GCP-ключи или доступ в интернет: логи отправляет агент хоста.

Конфигурация агента сохранена в `deploy/google-cloud-ops-agent.yaml`. Она сохраняет
прежний сбор Docker и стандартного syslog, а также отключённые метрики хоста.
На другом сервере нужно перенести секции receivers/processors/pipeline в его конфигурацию,
сохранив прочие receivers/pipelines. На mailu1024 установка выполняется администратором
отдельно от автоматического деплоя (с резервной копией текущего config.yaml):

```sh
sudo /opt/google-cloud-ops-agent/libexec/google_cloud_ops_agent_engine \
  -in deploy/google-cloud-ops-agent.yaml -out /tmp/finance-ops-agent-check -service fluentbit
sudo cp -a /etc/google-cloud-ops-agent/config.yaml /etc/google-cloud-ops-agent/config.yaml.before-finance
sudo install -m 644 deploy/google-cloud-ops-agent.yaml /etc/google-cloud-ops-agent/config.yaml
sudo systemctl restart google-cloud-ops-agent
sudo systemctl start google-cloud-ops-agent-fluent-bit
sudo systemctl is-active google-cloud-ops-agent-fluent-bit
```

При ошибке запуска восстановить резервную копию и снова перезапустить агент.
Метки Compose применяются при пересоздании контейнеров обычным GitHub Actions деплоем.
Остальные контейнеры и Docker daemon перезапускать не требуется.

Документация: [Docker logging labels](https://docs.docker.com/engine/logging/configure/),
[Ops Agent processors](https://docs.cloud.google.com/logging/docs/agent/ops-agent/configuration),
[PostgreSQL jsonlog](https://www.postgresql.org/docs/16/runtime-config-logging.html),
[Nginx JSON escaping](https://nginx.org/en/docs/http/ngx_http_log_module.html#log_format).

## Автоматическое обновление

Каждый push в основную ветку **master** запускает `.github/workflows/dockerhub.yml`.
После успешных тестов, сканирования и публикации всех трёх образов задание
`Deploy to mailu1024` подключается к серверу и устанавливает этот коммит.
Теги `v*` публикуются, но не развёртываются автоматически. Ручной запуск workflow
на `master` также обновляет сервер. PR не получает доступ к deploy-секрету.

Скрипт `/usr/local/sbin/finance-deploy`:

1. Проверяет, что коммит всё ещё является вершиной `master`; устаревший запуск пропускает.
2. Получает Compose и обе конфигурации Nginx из того же коммита репозитория.
3. Скачивает `jetcar/bank-*:sha-<полный SHA>`, проверяет метку ревизии и сохраняет digest.
4. Сохраняет текущую конфигурацию, секреты и дамп БД в `/opt/finance/deployments/` (доступ root).
5. Заменяет контейнеры через `up --no-build --wait` и проверяет HTTP, БД, вход и MCP.
6. Записывает установленный коммит в `/opt/finance/REVISION`.

Перед первым обновлением до версии с OAuth MCP администратор обязан установить
актуальные `deploy/finance-deploy.sh` и `deploy/finance-deploy-ssh.sh` как root-owned
скрипты. Старый установленный скрипт намеренно не заменяет сам себя. Актуальная версия
проверяет OAuth-конфигурацию, metadata и unauthenticated challenge после развёртывания.

При ошибке запуска восстанавливаются предыдущие образы и конфигурация. Дамп БД
не восстанавливается автоматически, чтобы не затереть новые операции. Изменения схемы
БД в будущих релизах должны оставаться совместимыми с предыдущей версией приложения.
Одновременные обновления блокируются через `flock`. Резервные копии сохраняются;
автоматическое удаление истории не настроено.

В GitHub environment `finance-production` разрешена только ветка `master`:

| Настройка | Назначение |
| --- | --- |
| Secret `FINANCE_SSH_KEY` | Отдельный ключ пользователя `finance-deploy` |
| Variable `FINANCE_HOST` | Адрес сервера |
| Variable `FINANCE_SSH_USER` | `finance-deploy` |
| Variable `FINANCE_SSH_KNOWN_HOSTS` | Проверенный ключ SSH сервера |

Этот SSH-ключ разрешает только `deploy <SHA>`, не разрешает shell и туннели.
Пользователь не входит в группу docker; sudo разрешает только установленный root-owned
скрипт обновления. Временный `GITHUB_TOKEN` с правами `contents: read` передаётся по
SSH stdin для аутентифицированных запросов GitHub API и не сохраняется на сервере.
Постоянный GitHub-токен на сервере не хранится. Скрипты из `deploy/finance-deploy*.sh`
устанавливает и обновляет администратор; скачанный из репозитория shell-код не исполняется.

Ручное применение уже выбранных образов (обычно вместо этого запускается workflow):

```sh
docker compose --env-file .env.server -f docker-compose.server.yml pull
docker compose --env-file .env.server -f docker-compose.server.yml up -d --no-build --wait
```

Перед обновлением сделать резервную копию БД:

```sh
umask 077
mkdir -p backups
docker compose --env-file .env.server -f docker-compose.server.yml exec -T postgres \
  pg_dump -U finance -d finance -Fc > "backups/finance-$(date +%Y%m%d-%H%M%S).dump"
```

Остановка без удаления данных: `docker compose --env-file .env.server -f docker-compose.server.yml down`.
Не использовать `down -v`: он удаляет том БД. Автозапуск контейнеров включён через
`restart: unless-stopped`. Суммарный лимит памяти новых сервисов — 1280 МиБ.

Источники: [сети Compose](https://docs.docker.com/compose/how-tos/networking/),
[Nginx proxy buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering),
[GitHub environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments),
[OpenSSH authorized keys](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT).
