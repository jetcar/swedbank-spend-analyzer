# Проверка образов — 5 ноября 2026

Локальное сканирование Trivy 0.74.0, база уязвимостей обновлена 2026-11-05.
Проверены пакеты ОС и языковые зависимости, без исключений CVE и без `--ignore-unfixed`.
До и после использована одна база. Числа — находки по пакетам, не число уникальных CVE.

| Образ | До: всего | До: Critical / High | После: всего |
| --- | ---: | ---: | ---: |
| bank-app | 222 | 1 / 58 | 0 |
| bank-mcp | 222 | 1 / 58 | 0 |
| Nginx (gateway и bank-egress) | 53 | 1 / 5 | 0 |
| PostgreSQL 16 | 61 | 1 / 25 | 0 |

JSON-отчёты находятся локально в `.security-reports/*-before.json` и
`.security-reports/*-after.json`. Каталог исключён из Git и контекста сборки.
В нём также сохранена резервная копия БД перед заменой контейнеров; она содержит
пользовательские данные и не предназначена для публикации.

## Исправления

- Python 3.12 переведён на закреплённый digest Alpine. Пакеты ОС обновляются при
  сборке, зависимости устанавливаются готовыми бинарными wheels. Переход с glibc
  на musl проверен на используемом Docker-хосте с asyncpg и криптографией.
- Обновлены FastAPI, Starlette, PyJWT, cryptography и python-dotenv; MCP закреплён
  на проверенной версии. Инициализация FastAPI переведена с удалённого `on_event`
  на lifespan с освобождением пула БД при завершении.
- После установки и `pip check` удаляются pip, setuptools и ensurepip, которые
  не нужны работающим сервисам. Даже новый pip содержал старую vendored-копию
  setuptools в своём составе. Метаданные сканера не удаляются отдельно и находки
  не скрываются исключениями. Для изменения зависимостей нужно пересобрать образ.
- Nginx собирается из закреплённого официального образа с обновлением Alpine-пакетов,
  включая curl, expat и brotli. Оба прокси используют этот образ.
- PostgreSQL остаётся версии 16 с прежним томом данных. Старый Go-бинарник gosu
  заменён Alpine-пакетом su-exec; entrypoint по-прежнему запускает сервер от
  пользователя postgres. Старый `/usr/local/bin/su-exec` был ссылкой на gosu,
  поэтому используется настоящий `/sbin/su-exec`.

## Проверка работы

Проверены запуск PostgreSQL с пустым временным хранилищем и запуск с существующим
томом, API, MCP (initialize и чтение статуса, категорий и расходов), подпись и
проверка банковского JWT, расчёт бюджетов и SSE-уведомления после commit/rollback.
Пройдены 26 тестов интерфейса и 10 проверок сетевой защиты, включая обращения
без JWT к разрешённым банковским маршрутам с проверкой TLS.

После первого перезапуска прокси один запрос получил 502 из-за временного отказа
DNS Docker. Последующая проверка разрешения имени и полный сетевой набор прошли;
настройки DNS и сетевые ограничения не ослаблялись.

## Повторная проверка

Сборку для получения новых исправлений ОС запускать без кэша:

```powershell
rtk docker compose build --no-cache
rtk docker compose up -d --no-build
```

Обновление закреплённых базовых digest и Python-версий выполняется отдельно.
Повторное сканирование (по очереди: Trivy блокирует общий cache):

```powershell
New-Item -ItemType Directory -Force .security-reports | Out-Null
$images = @('bank-app:latest', 'bank-mcp:latest', 'bank-nginx:local', 'bank-postgres:local')
foreach ($image in $images) {
    $reportName = $image.Replace(':', '-') + '.json'
    rtk docker run --rm -v /var/run/docker.sock:/var/run/docker.sock `
        -v "${PWD}/.security-reports:/reports" -v bank-trivy-cache:/root/.cache/trivy `
        aquasec/trivy:0.74.0 image --scanners vuln --format json `
        --output "/reports/$reportName" --exit-code 1 $image
    if ($LASTEXITCODE -ne 0) { throw "Scan failed or found vulnerabilities: $image" }
}
```

Нулевой результат относится к этим сборкам и базе на дату проверки. Старые образы
в Docker могут оставаться в кэше и иметь прежние находки; рабочие контейнеры
пересозданы из проверенных образов. Глобальная очистка Docker не выполнялась.

Источники: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
[Starlette release notes](https://starlette.dev/release-notes/),
[PyJWT changelog](https://pyjwt.readthedocs.io/en/stable/changelog.html),
[официальный Python image](https://hub.docker.com/_/python),
[su-exec](https://github.com/ncopa/su-exec),
[gosu: ограничения оценки CVE по Go-компонентам](https://github.com/tianon/gosu/security).
