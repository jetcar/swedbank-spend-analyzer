# Сборка и публикация в Docker Hub

Workflow: [Docker Hub](.github/workflows/dockerhub.yml).

## Настройка репозитория

В [Settings → Secrets and variables → Actions](https://github.com/jetcar/swedbank-spend-analyzer/settings/secrets/actions)
добавить:

| Тип | Имя | Значение |
| --- | --- | --- |
| Variable | `DOCKERHUB_USERNAME` | Логин владельца токена Docker Hub |
| Variable, необязательно | `DOCKERHUB_NAMESPACE` | Организация Docker Hub, если отличается от логина |
| Secret | `DOCKERHUB_TOKEN` | Docker Hub access token с правами Read & Write |

Токен не сохранять в файлах репозитория. В выбранном namespace нужны репозитории
`bank-app`, `bank-nginx`, `bank-postgres`; их видимость выбирается в Docker Hub.
Для организации владелец токена должен иметь право публикации в эти репозитории.
Ключ банка, `.env` и доступ к рабочей БД для сборки не нужны.

## Запуск

| Событие | Проверки и сборка | Публикация |
| --- | --- | --- |
| Pull request в `master` | Да, без Docker Hub credentials | Нет |
| Push в `master` | Да | `latest` и `sha-<полный SHA>` |
| Push тега `v*`, например `v1.2.3` | Да | `v1.2.3` и `sha-<полный SHA>` |
| Actions → Docker Hub → Run workflow, ветка `master` | Да | `latest` и `sha-<полный SHA>` |
| Ручной запуск на другой ветке | Да | Нет |

`latest` обновляется только из `master`. Тег версии не меняет `latest`.
При отсутствии настроек публикация завершается с явной ошибкой.

## Образы

| Образ | Использование |
| --- | --- |
| `<namespace>/bank-app` | Сервисы `app` и `mcp`; для MCP команда `python -m app.mcp_server` |
| `<namespace>/bank-nginx` | Сервисы `gateway` и `bank-egress`, со своими конфигурациями Nginx |
| `<namespace>/bank-postgres` | PostgreSQL 16 |

Собираются три разных образа для `linux/amd64`. Приложение и MCP используют один
Dockerfile и набор зависимостей, поэтому повторная публикация одинакового образа
под отдельным именем не нужна. Локальный `docker-compose.yml` сохраняет сборку
из исходников. После публикации из `master` workflow автоматически обновляет сервер
`mailu1024` готовыми образами. Локальный Docker не обновляется.
Порядок обновления, резервное копирование и откат: [DEPLOYMENT.md](DEPLOYMENT.md).

Сначала выполняются frontend-тесты. Затем каждый образ собирается без кэша,
проверяется запуск его основных компонентов и выполняется Trivy-сканирование.
Находки HIGH или CRITICAL (включая ещё не исправленные upstream) блокируют публикацию
соответствующего образа. Публикуется именно проверенный локальный образ, без повторной
сборки. Задания образов независимы: отказ одного не откатывает опубликованные другие.

Пример получения образа:

```sh
docker pull <namespace>/bank-app:latest
```

Для повторяемого развёртывания используйте тег конкретного коммита или digest.
Закреплённые версии GitHub Actions и базовых образов нужно обновлять отдельно.
Сборка без кэша позволяет получать обновления Alpine-пакетов при неизменном digest базы.

Источники: [Docker: публикация из GitHub Actions](https://docs.docker.com/build/ci/github-actions/push-multi-registries/),
[Build and push action](https://github.com/docker/build-push-action),
[Metadata action](https://github.com/docker/metadata-action).
