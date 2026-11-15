# Risk Engine: установка на новой Ubuntu VM с нуля

Этот архив предназначен для **отдельной новой Ubuntu VM**. Он основан на переданных `docker-compose*.yml`, `scripts/*.sh` и `tools/risk_engine_firewall.sh`. Полное приложение (API, workers, Alembic и alert_notifier) автоматически забирается из публичного репозитория `https://github.com/aakutischeva/risk-engine.git`.

**Важное предупреждение:** установщик рассчитан на **пустой Docker Engine** и создаёт `/opt/risk-engine`. Он не предназначен для существующего стенда, не выполняет `docker compose down -v` или удаление Docker-томов. Если обнаружены контейнеры, тома, пользовательские сети Docker, занятый каталог проекта или сетевые мосты проекта, выполнение прерывается. Не запускайте установщик на старом Ubuntu-хосте.

## Запуск: одна команда (после переноса архива на чистую VM)

```bash
# На новой Ubuntu VM; архив скопирован в домашний каталог
mkdir -p ~/fresh-installer
tar -xzf ~/risk-engine-new-vm-installer.tar.gz -C ~/fresh-installer
cd ~/fresh-installer/risk-engine-new-vm-installer
sudo bash install_new_vm.sh --confirm-empty-vm
```

Установщик выполнит следующие действия:

1. Если Docker Engine отсутствует — установит Docker из официального APT-репозитория и Compose v2. Если существующий Docker неисправен, не станет переписывать его конфигурацию.
2. Проверит, что Docker-демон пуст, а `/opt/risk-engine` ещё не существует.
3. Клонирует полный репозиторий приложения в `/opt/risk-engine` и **только в этот новый checkout** скопирует 12 файлов из предоставленного архива.
4. Добавит в `deploy/` новые вспомогательные скрипты; оригинальный `scripts/start_patroni.sh` остаётся неизменным. Для новой установки монтируются отдельные Patroni YAML с согласованной ролью суперпользователя `postgres`, учётной записью репликации `risk_replicator` и новым launcher, не требующим `PG_VERSION`.
5. Сгенерирует локальные случайные пароли для основной и Alert PostgreSQL, RabbitMQ, Erlang cookie и webhook, а также файлы секретов Patroni и параметры бэкапов. Ничего не отправляет в Git.
6. Построит образ Patroni, запустит etcd, проведёт `initdb` основной БД средствами Patroni, создаст `riskengine` и его пользовательскую роль.
7. Запустит реплику PostgreSQL, HAProxy для primary и три RabbitMQ узла с объединением в кластер командой `join_cluster`.
8. Запустит Redis, Alert PostgreSQL, выполнит две миграции Alembic, поднимет API, publisher, consumer, dispatcher и webhook receiver.
9. Запустит два контейнера резервного копирования (сразу выполнят `pg_dump`, затем ежедневно в 03:00), проверит сервисы, streaming replication, RabbitMQ/quorum-очереди, API и файлы первых дампов.

Образы скачиваются из Docker Registry, а код — из GitHub. Поэтому на новой VM нужен интернет, DNS, свободное дисковое пространство и достаточно оперативной памяти (для такой 3-узловой лаборатории разумно выделить около 8 ГБ ОЗУ и 25+ ГБ свободного места).

### Проверить после запуска

```bash
sudo bash /opt/risk-engine/deploy/verify_fresh.sh
sudo bash /opt/risk-engine/deploy/logs_fresh.sh
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
curl -fsS http://127.0.0.1:8000/health
```

Основные адреса **на самой VM**:

- API Swagger UI: `http://127.0.0.1:8000/docs`
- API health: `http://127.0.0.1:8000/health`
- RabbitMQ management: `http://127.0.0.1:15672` (логин `RABBITMQ_USER` и пароль `RABBITMQ_PASSWORD` из `/opt/risk-engine/.env`; храните в тайне).

Для открытия API со своего Windows через SSH/Tailscale используйте SSH-туннель (замените пользователя и адрес VM):

```powershell
ssh -L 8000:127.0.0.1:8000 ubuntu@TAILSCALE_IP_НОВОЙ_VM
```

Затем откройте `http://127.0.0.1:8000/docs` в Windows. По умолчанию API и UI RabbitMQ **не публикуются** на внешнем интерфейсе VM.

### Диагностика при ошибке

```bash
cd /opt/risk-engine
sudo bash deploy/logs_fresh.sh
sudo docker compose -p risk-engine --env-file .env --profile ha --profile backup \
  -f docker-compose.yml \
  -f docker-compose.dcs.yml \
  -f docker-compose.standby.yml \
  -f docker-compose.patroni.yml \
  -f docker-compose.haproxy.yml \
  -f docker-compose.rabbitmq-ha.yml \
  -f docker-compose.rabbitmq-proxy.yml \
  -f docker-compose.backup.yml \
  -f deploy/docker-compose.fresh.yml ps
```

`bootstrap_fresh.sh` после начала создаёт маркер `.risk-engine-fresh-bootstrap-started`. **Нельзя просто повторять первичную инициализацию или удалять тома после частичного сбоя.** Сначала диагностируйте причину по логам. Установщик намеренно не имеет команды автоматической очистки или «сброса».

Если клонирование GitHub не получилось, будет возможен частичный каталог `/opt/risk-engine`; на реальной машине нужно вручную разобраться с ним. Установщик никогда не удаляет существующий каталог за вас.

### Организация файлов

- `base/` — **дословно сохранённые** 12 файлов переданного архива.
- `deploy/docker-compose.fresh.yml` — завершающий Compose overlay только для установки на пустых томах (переключение Patroni launcher/конфигов и установка общего Erlang cookie RabbitMQ).
- `deploy/templates/patroni/*.yml` — отдельная конфигурация Patroni именно для первичного развёртывания.
- `deploy/prepare_fresh.sh` — создание секретов и подготовка конфигураций.
- `deploy/preflight_fresh.sh` — проверки перед первичным развёртыванием.
- `deploy/bootstrap_fresh.sh` — последовательное создание кластера и сервисов.
- `deploy/verify_fresh.sh` — повторяемые read-only проверки.
- `deploy/logs_fresh.sh` — просмотр логов.
- `deploy/install_docker_ubuntu.sh` — установка Docker при его отсутствии.

Ни один файл `base/` не изменён по сравнению с пользовательским исходным архивом. Меняются только файлы **нового checkout** под `/opt/risk-engine` и Docker-ресурсы новой VM.

### Границы отказоустойчивости

Это **учебный однохостовый** стенд: 2 Patroni/PostgreSQL контейнера и 3 RabbitMQ контейнера на одной VM. Он позволяет проверить отказоустойчивость отдельных процессов/контейнеров, но не VM/хоста. etcd в исходном архиве одноузловой, Alert PostgreSQL без реплики, поэтому это не production HA на уровне хоста. Автоматические бэкапы находятся на том же хосте и не являются внешней копией. Применение правил межсетевого экрана оставлено ручным: исходный firewall-скрипт работает с DOCKER-USER и требует отдельной проверки после запуска.

### Насколько проверено

Конфигурации и синтаксис Bash/YAML проверены статически. Здесь нет запущенного Docker Engine, поэтому **реальная установка, сборки образов, миграции и E2E не проверены**. Скрипт `verify_fresh.sh` на VM будет проверкой реального результата, а не гарантией, что всё запустится при любой версии upstream/пакетов.
