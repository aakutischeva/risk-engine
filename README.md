# Risk Engine — микросервисная антифрод-система

**Risk Engine** —  система оценки риска платежных транзакций. Приложение принимает платеж через HTTP API, рассчитывает интегральную оценку мошеннического риска по пяти правилам, сохраняет результат и асинхронно передаёт событие отдельному сервису уведомлений.

Проект развивается на основе [jmello04/risk-engine](https://github.com/jmello04/risk-engine). В данной версии исходный Fraud Engine дополнен RabbitMQ, механизмом Transactional Outbox, независимой базой уведомлений, Cache-Aside, HMAC-подписанными webhook и сетевой сегментацией.

**Репозиторий проекта:** [aakutischeva/risk-engine](https://github.com/aakutischeva/risk-engine)  
**Статус:** работающий учебный стенд / прототип; не готовая банковская платёжная платформа.

## Содержание

- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Антифрод-правила](#антифрод-правила)
- [Надёжность и обработка событий](#надёжность-и-обработка-событий)
- [Безопасность и сетевая изоляция](#безопасность-и-сетевая-изоляция)
- [Технологии](#технологии)
- [Запуск через Docker Compose](#запуск-через-docker-compose)
- [HTTP API](#http-api)
- [Демонстрационные сценарии](#демонстрационные-сценарии)
- [Тестирование и CI](#тестирование-и-ci)
- [Структура проекта](#структура-проекта)
- [Ограничения и дальнейшее развитие](#ограничения-и-дальнейшее-развитие)
- [Происхождение и лицензия](#происхождение-и-лицензия)

## Возможности

- **Объяснимая оценка риска:** пять антифрод-правил, численный `fraud_score` и категории `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.
- **HTTP API на FastAPI:** создание и получение транзакций, просмотр предупреждений, агрегированная статистика, Swagger UI.
- **Две независимые PostgreSQL:** данные платежей и Outbox хранятся отдельно от журнала событий и уведомлений.
- **Redis:** счетчики частоты операций для `VelocityRule` и кэш транзакций по схеме Cache-Aside.
- **RabbitMQ:** асинхронный обмен событиями между контуром оценки риска и контуром уведомлений.
- **Transactional Outbox:** запись транзакции и события в рамках одной транзакции PostgreSQL с последующей публикацией отдельным процессом.
- **Идемпотентность:** повторный `POST /transactions` с тем же `transaction_id` возвращает сохранённую оценку без повторного скоринга.
- **Безопасные уведомления:** HMAC-SHA256, дедупликация webhook и повторные попытки доставки после ошибок.
- **Сегментация:** пять отдельных Docker bridge-сетей и дополнительный Firewall на хостовой Ubuntu VM.

## Архитектура

Система состоит из двух логических контуров:

1. **Risk Engine** — приём транзакций, вычисление риска, основное хранение, кэш и публикация событий.
2. **Alert Notification** — потребление событий, ведение журнала обработанных сообщений и доставка уведомлений.

```mermaid
flowchart LR
    Client[Клиент / Swagger] -->|POST /transactions| API[FastAPI + Fraud Engine]
    API <-->|счётчики и Cache-Aside| Redis[(Redis)]
    API -->|транзакция + Outbox в одном коммите| DB1[(PostgreSQL №1)]
    DB1 --> Publisher[Outbox Publisher]
    Publisher -->|transaction.scored| MQ[[RabbitMQ]]
    MQ --> Notifier[Alert Notifier]
    Notifier --> DB2[(PostgreSQL №2)]
    DB2 --> Dispatcher[Alert Dispatcher]
    Dispatcher -->|HTTP webhook + HMAC-SHA256| Receiver[Webhook Receiver]
    API -->|fraud_score / risk_level| Client
```

Основные постоянные сервисы и их роли:

| Сервис Docker Compose | Назначение |
| --- | --- |
| `api` | FastAPI, Fraud Engine, маршруты транзакций и предупреждений |
| `postgres` | Основная БД: `transactions`, `alerts`, `outbox_events` |
| `redis` | Velocity-счётчики и кэш чтения транзакций |
| `outbox-publisher` | Публикация неопубликованных событий из PostgreSQL в RabbitMQ |
| `rabbitmq` | Exchange `risk.events`, очередь `risk.alerts`, dead-letter очередь |
| `alert-notifier` | Consumer событий; дедупликация и регистрация уведомлений |
| `alert-postgres` | БД обработанных событий и уведомлений |
| `alert-dispatcher` | Отправка подписанных webhook, управление повторными попытками |
| `notification-receiver` | Демонстрационный HTTP-получатель webhook |

Дополнительно есть **два одноразовых контейнера миграций**: `migrate` и `alert-migrate`. Итого конфигурация описывает **11 сервисов** (9 постоянно работающих и 2 миграционных).

### Хранилища

**PostgreSQL №1:**

- `transactions` — параметры транзакции, оценка риска, результаты всех правил;
- `alerts` — предупреждения для `HIGH` и `CRITICAL`, состояние их обработки;
- `outbox_events` — события, ожидающие публикации, число попыток и `published_at`.

**PostgreSQL №2:**

- `processed_events` — журнал уже обработанных событий;
- `alert_notifications` — очередь уведомлений, статусы `PENDING` / `RETRY` / `SENT` и сведения о доставке.

Данные основной БД недоступны сервису уведомлений напрямую; взаимодействие проходит через брокер.

## Антифрод-правила

Исходная логика Fraud Engine сохранена и используется в расширенной архитектуре. Пять правил оцениваются асинхронно, затем рассчитывается **взвешенное среднее**.

| Правило | Вес | Что проверяет |
| --- | ---: | --- |
| `HighAmountRule` | 1.5 | Крупная сумма перевода относительно порогов (по умолчанию 5 000 и 20 000 BRL) |
| `VelocityRule` | 2.0 | Много транзакций одного отправителя в окне 5 минут через Redis |
| `NightTimeRule` | 1.0 | Переводы в нетипичные ночные часы |
| `RoundAmountRule` | 0.8 | Подозрительно круглые суммы |
| `SelfTransferRule` | 1.2 | Совпадение идентификаторов отправителя и получателя |

Итоговый результат `fraud_score` находится в диапазоне от 0 до 100:

| Диапазон `fraud_score` | Категория |
| --- | --- |
| `0 ≤ score < 30` | `LOW` |
| `30 ≤ score < 60` | `MEDIUM` |
| `60 ≤ score < 80` | `HIGH` |
| `80 ≤ score ≤ 100` | `CRITICAL` |

Для `HIGH` и `CRITICAL` в основной БД формируются записи о предупреждениях. При этом событие `transaction.scored` публикуется **для каждой новой транзакции**, независимо от уровня риска.

## Надежность и обработка событий

### Transactional Outbox

При создании транзакции API выполняет в PostgreSQL одну логическую операцию: сохраняет результат оценки и запись `outbox_events`. После успешного коммита отдельный `outbox-publisher` выбирает неопубликованные события и отправляет их в RabbitMQ.

Для сообщений используются:

- **Exchange:** `risk.events`;
- **Routing key / event type:** `transaction.scored`;
- **Основная очередь:** `risk.alerts`;
- **Dead-letter очередь:** `risk.alerts.dead`.

Publisher использует подтверждение со стороны RabbitMQ и фиксирует время публикации в `published_at`. Модель доставки — **at least once**: при сбое возможна повторная публикация, поэтому consumer использует `event_id` для дедупликации. Это **не гарантия exactly-once** во всей распределённой системе.

### Идемпотентный POST

Первый запрос с новым `transaction_id` выполняет скоринг и возвращает `"idempotent": false`. Повторный запрос с уже существующим ID возвращает сохранённый результат с `"idempotent": true`; повторного расчёта риска нет.

> В текущей реализации ключ идемпотентности — именно `transaction_id`. Повтор с тем же ID возвращает прежнюю запись, поэтому клиент должен обеспечивать соответствие ID исходной операции.

### Кэш Cache-Aside

При `GET /transactions/{transaction_id}` API сначала проверяет Redis. Если данные отсутствуют, выполняет запрос к PostgreSQL, после чего кладёт результат в кэш. Заголовок ответа показывает источник:

- `X-Cache: MISS` — чтение из PostgreSQL;
- `X-Cache: HIT` — чтение из Redis.

После создания транзакции предусмотрена инвалидация соответствующего ключа **после успешного коммита**, а не до него.

### Доставка webhook

`Alert Notifier` регистрирует события во второй PostgreSQL. Для высокого риска создаётся уведомление, которое `Alert Dispatcher` отправляет в `notification-receiver` через HTTP. Тело подписывается HMAC-SHA256; получатель проверяет подпись и распознаёт дубликаты. При HTTP-ошибках или сетевых сбоях Dispatcher предусматривает повторные попытки.

## Безопасность и сетевая изоляция

### Docker-сети

Для ограничения связности используются пять отдельных bridge-сетей:

| Сеть Compose | Разрешённая зона взаимодействия |
| --- | --- |
| `tx_db` | API / Outbox Publisher ↔ основная PostgreSQL |
| `tx_cache` | API ↔ Redis |
| `events` | Outbox Publisher / RabbitMQ / Alert Notifier |
| `alert_db` | Alert Notifier / Dispatcher ↔ Alert PostgreSQL |
| `webhook` | Alert Dispatcher ↔ Notification Receiver |

К основному HTTP API опубликован только loopback-порт хоста `127.0.0.1:8000`; интерфейс RabbitMQ Management ограничен `127.0.0.1:15672`. Webhook Receiver **не публикует порт на хост**.

### Firewall

На учебной Ubuntu VM используется скрипт [`tools/risk_engine_firewall.sh`](tools/risk_engine_firewall.sh), который конфигурирует правила `iptables` (цепочка `RISK_ENGINE_SEG`, переход из `DOCKER-USER`). Дополнительная настройка задействует особенности фильтрации Docker-трафика, в том числе `raw PREROUTING`. Правила проверялись на разрешённых и запрещённых TCP-соединениях между контейнерами.

На рабочем стенде применено восстановление правил через службу systemd после запуска Docker. **Эта служба настраивается на хосте отдельно и не создается автоматически обычной командой `docker compose up`.** Перед применением скрипта на другом сервере необходимо изучить правила, текущие сети и интерфейсы. Подробности: [`tools/NETWORK_SEGMENTATION.md`](tools/NETWORK_SEGMENTATION.md).

### Другие защитные механизмы

- Основная и Alert PostgreSQL разнесены по разным сетям и контейнерам.
- Сервис уведомлений получает данные из событий, а не прямыми запросами к основной БД.
- HMAC-SHA256 контролирует подлинность отправителя, знающего секрет, и целостность webhook.
- Дедупликация `event_id` защищает обработчик от повторной доставки событий.
- Ряд контейнеров выполняется с read-only файловой системой и `no-new-privileges`.
- Пароли PostgreSQL, RabbitMQ и общий секрет webhook должны храниться в локальном `.env`, а не в Git.

**Пока не реализованы:** централизованная аутентификация JWT/OIDC, ролевая авторизация (RBAC) для API, end-to-end TLS/mTLS и промышленное хранение секретов. HMAC не заменяет TLS.

## Технологии

| Уровень | Технология |
| --- | --- |
| Язык | Python 3.12 |
| HTTP API | FastAPI + Uvicorn |
| Основные и сервисные БД | PostgreSQL 16 |
| ORM и миграции | SQLAlchemy 2.0, asyncpg, Alembic |
| Кэш и счётчики | Redis 7 |
| Брокер событий | RabbitMQ (AMQP), aio-pika |
| Валидация и конфигурация | Pydantic v2, pydantic-settings |
| Подпись webhook | HMAC-SHA256 |
| Развёртывание | Docker, Docker Compose v2, Ubuntu VM |
| Сетевая защита | Docker bridge networks, iptables, systemd |
| Проверки | pytest, pytest-asyncio, httpx, Ruff, GitHub Actions |

## Запуск через Docker Compose

### Требования

- Linux / Ubuntu с установленными Docker Engine и Docker Compose v2 (либо совместимая система);
- Git;
- доступ к локальному терминалу хоста;
- свободный порт `127.0.0.1:8000` (и `127.0.0.1:15672` для интерфейса RabbitMQ).

### 1. Получить проект

```bash
git clone https://github.com/aakutischeva/risk-engine.git
cd risk-engine
```

Если репозиторий приватный, нужен предоставленный владельцем доступ.

### 2. Задать переменные окружения

Создать `.env` в корне проекта. Можно начать с `cp .env.example .env`, **но необходимо проверить, что шаблон содержит все переменные для новых сервисов**.

Минимальный пример структуры `.env` (все секреты здесь — только заглушки):

```dotenv
POSTGRES_USER=risk_engine
POSTGRES_PASSWORD=REPLACE_WITH_STRONG_PASSWORD
POSTGRES_DB=risk_engine

RABBITMQ_USER=risk_engine
RABBITMQ_PASSWORD=REPLACE_WITH_STRONG_PASSWORD

ALERT_POSTGRES_USER=alert_engine
ALERT_POSTGRES_PASSWORD=REPLACE_WITH_STRONG_PASSWORD
ALERT_POSTGRES_DB=alert_notifications

NOTIFICATION_WEBHOOK_SECRET=REPLACE_WITH_RANDOM_SECRET
```

Для webhook-секрета можно сгенерировать случайное значение командой `openssl rand -hex 32`. Не публикуйте настоящий `.env`, токены, приватные ключи и пароли. Файл `.env` должен оставаться в `.gitignore`.

Compose формирует внутренние `DATABASE_URL`, `REDIS_URL` и параметры подключения контейнеров из указанных переменных. Не подставляйте адрес `localhost` вместо имён сервисов для соединений **между контейнерами**.

### 3. Запустить систему

```bash
sudo docker compose up -d --build
sudo docker compose ps -a
```

Контейнеры `migrate` и `alert-migrate` должны завершиться с кодом `0`; это штатное поведение. Основные сервисы должны перейти в состояние `Up` / `healthy` (если для них настроен healthcheck). Отдельный ручной запуск Alembic после успешного старта Compose не требуется.

### 4. Проверить API

```bash
curl -fsS http://127.0.0.1:8000/health
```

Ожидаемый ответ:

```json
{"status":"ok","version":"1.0.0"}
```

- Swagger UI: `http://127.0.0.1:8000/docs`;
- ReDoc: `http://127.0.0.1:8000/redoc`;
- RabbitMQ Management: `http://127.0.0.1:15672` (учётные данные из `.env`).

Если работа ведется в VirtualBox через SSH, для Swagger можно использовать туннель с компьютера-хоста:

```bash
ssh -p 22022 -L 8000:127.0.0.1:8000 anna@127.0.0.1
```

Этот пример соответствует **учебной VM с ранее настроенным пробросом порта 22022**; на другом компьютере адрес, пользователь и порт будут отличаться. API специально привязан к loopback хоста VM.

### Полезные команды

```bash
sudo docker compose ps -a
sudo docker compose logs --tail=50 api outbox-publisher rabbitmq
sudo docker compose logs --tail=50 alert-notifier alert-dispatcher notification-receiver
sudo docker compose down
```

`docker compose down` останавливает и удаляет контейнеры/сети, но сохраняет именованные тома БД. **Не запускайте `down -v` без намерения удалить данные.**

## HTTP API

| Метод | Маршрут | Назначение |
| --- | --- | --- |
| `POST` | `/transactions` | Оценить и сохранить транзакцию; повтор с тем же ID идемпотентен |
| `GET` | `/transactions/{transaction_id}` | Получить результат, заголовок `X-Cache` |
| `GET` | `/transactions` | Список транзакций с фильтрами и пагинацией |
| `GET` | `/alerts` | Список предупреждений |
| `GET` | `/alerts/{alert_id}` | Предупреждение по ID |
| `PATCH` | `/alerts/{alert_id}` | Изменить статус предупреждения |
| `GET` | `/dashboard/stats` | Агрегированная статистика |
| `GET` | `/health` | Проверка доступности API |
| `GET` | `/docs` | Swagger UI |

### Пример: создание транзакции

```bash
TX_ID="readme-demo-$(date +%s)"

curl -sS -X POST http://127.0.0.1:8000/transactions \
  -H 'Content-Type: application/json' \
  -d "{\"transaction_id\":\"$TX_ID\",\"amount\":1500.0,\"payer_key\":\"alice@example.test\",\"receiver_key\":\"bob@example.test\",\"transaction_type\":\"PIX\"}" \
  | python3 -m json.tool
```

Ответ HTTP 200 содержит данные транзакции, вложенный объект `risk_assessment` (`fraud_score`, `risk_level`, `rules_evaluated`, `scored_at`), `created_at` и поле `idempotent`.

Схематичный фрагмент **неполного** ответа:

```json
{
  "transaction_id": "readme-demo-...",
  "risk_assessment": {
    "fraud_score": 3.08,
    "risk_level": "LOW",
    "rules_evaluated": [
      {"rule": "HighAmountRule", "score": 0.0, "triggered": false, "reason": "..."}
    ],
    "scored_at": "2026-11-11T19:18:16.690016Z"
  },
  "idempotent": false
}
```

Здесь числа и время — пример из тестового запуска. Фактический результат зависит от суммы, времени и Redis-счётчиков.

### Пример: идемпотентность

Повторите **тот же** POST с **тем же** `$TX_ID`. В ответе сохранится прежний `scored_at`, а `idempotent` изменится с `false` на `true`. В основной PostgreSQL останется одна запись транзакции.

### Пример: Cache-Aside

Чтобы увидеть заголовки именно **GET-запроса**, используйте `-D -` (не `-I`, поскольку он отправляет `HEAD`):

```bash
curl -sS -D - -o /dev/null "http://127.0.0.1:8000/transactions/$TX_ID" | grep -i '^x-cache:'
curl -sS -D - -o /dev/null "http://127.0.0.1:8000/transactions/$TX_ID" | grep -i '^x-cache:'
```

При пустом кэше ожидается `MISS`, при повторном чтении — `HIT`. Если ключ уже был закэширован, оба запроса могут вернуть `HIT`.

## Демонстрационные сценарии

Следующие результаты получены на учебном стенде; это **примеры проверок**, а не гарантированные показатели производительности или устойчивости.

### 1. Выявление высокого риска

Проведена серия последовательных переводов с одинаковым отправителем и получателем и суммой 30 000 BRL. На 15-й операции оценка достигла **60.31 / HIGH**:

```text
Транзакция 10: 52.62 MEDIUM
Транзакция 11: 54.15 MEDIUM
Транзакция 12: 55.69 MEDIUM
Транзакция 13: 57.23 MEDIUM
Транзакция 14: 58.77 MEDIUM
Транзакция 15: 60.31 HIGH
```

Четыре правила сработали: `HighAmountRule`, `VelocityRule`, `RoundAmountRule`, `SelfTransferRule`.

### 2. Сквозная передача события

Для тестовой транзакции `fraud-demo-1794424154-16132-15` подтверждены:

| Этап | Наблюдение |
| --- | --- |
| Основная PostgreSQL / Outbox | `transaction.scored`, заполненный `published_at`, `attempts = 1` |
| Alert Notifier | Запись `processed_events` с `risk_level = HIGH` |
| Webhook Receiver | Лог `Webhook accepted` |
| Alert Dispatcher | Лог `Webhook delivery confirmed`, `attempt = 1` |
| Alert PostgreSQL | `alert_notifications.status = SENT` |

### 3. Идемпотентность

Два последовательных POST с одинаковым ID вернули исходную оценку (`3.08 LOW`) и одинаковый `scored_at`; второй ответ содержал `idempotent: true`. Запрос к PostgreSQL подтвердил `COUNT(*) = 1`.

### 4. HMAC и повторные попытки

В `tests/unit/test_alert_delivery.py` успешно выполнены **5 тестов**, охватывающих корректную подпись, обнаружение дубликата, отклонение изменённого сообщения, повторы при HTTP/сетевых ошибках и ограничение задержек.

### 5. Сетевая сегментация

На учебной VM проверены разрешённые соединения API ↔ PostgreSQL/Redis и запрещённые соединения между несвязанными сегментами. Правила Firewall сохраняются после перезагрузки благодаря отдельно настроенной службе systemd.

## Тестирование и CI

Для локальных тестов используйте Python 3.12:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python -m pytest tests/unit/ -v
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Изолированные интеграционные тесты PostgreSQL и Redis имеют защиту от случайного подключения к рабочим базам. Они выполняются **только** при наличии специально поднятых тестовых экземпляров и переменных окружения:

- `TEST_DATABASE_URL` — строго `postgresql+asyncpg://riskengine_test:...@127.0.0.1:15432/riskengine_test`;
- `TEST_REDIS_URL` — строго `redis://127.0.0.1:16379/0`.

Без этих переменных соответствующие интеграционные тесты пропускаются (`SKIPPED`). Нельзя подставлять в них рабочие `DATABASE_URL` или `REDIS_URL`. Для проверки Ruff в CI используются **две отдельные команды**: `ruff check` и `ruff format --check`.

GitHub Actions: [`.github/workflows/ci.yml`](.github/workflows/ci.yml). Фактический статус последнего запуска смотрите на вкладке **Actions** репозитория; успешный локальный запуск тестов не заменяет проверку CI.

## Структура проекта

```text
risk-engine/
├── app/
│   ├── api/routes/                 # Транзакции, предупреждения, статистика
│   ├── core/                       # Конфигурация, исключения, логирование
│   ├── domain/                     # Схемы и перечисления
│   ├── fraud/                      # Fraud Engine и пять правил
│   ├── infra/
│   │   ├── cache/                 # Redis и Cache-Aside
│   │   └── database/              # Основная PostgreSQL: модели, репозитории
│   ├── services/                   # Бизнес-логика
│   └── workers/outbox_publisher.py # Отправка событий в RabbitMQ
├── alembic/                        # Миграции основной PostgreSQL
├── alert_notifier/
│   ├── consumer.py                 # RabbitMQ consumer
│   ├── dispatcher.py               # Доставка и retry
│   ├── webhook.py                  # HMAC-SHA256
│   ├── demo_receiver.py            # Демонстрационный получатель
│   ├── database.py, models.py      # Модели и подключение к Alert PostgreSQL
│   └── migrations/                 # Миграции Alert PostgreSQL
├── tests/
│   ├── unit/                       # Правила, Outbox, кэш, HMAC и уведомления
│   └── integration/                # API и изолированные проверки PostgreSQL/Redis
├── tools/
│   ├── risk_engine_firewall.sh     # Правила Firewall
│   └── NETWORK_SEGMENTATION.md     # Документация сети
├── .github/workflows/ci.yml       # CI
├── .env.example                    # Пример базовых настроек (проверить на актуальность)
├── docker-compose.yml             # Сервисы, сети и тома
├── Dockerfile                      # Сборка приложения
├── requirements.txt
├── pyproject.toml                  # Ruff, pytest, проектные настройки
└── README.md
```

## Ограничения и дальнейшее развитие

Это **учебный прототип**, поэтому следующие задачи остаются открытыми:

1. Аутентификация JWT/OIDC и разграничение доступа RBAC к API и администрированию.
2. Безопасное управление секретами и шифрование каналов между компонентами в промышленном окружении.
3. Полноценные нагрузочные испытания, сбор p95/p99, исследование поведения при деградации брокера и баз данных.
4. Мониторинг, централизованный сбор логов, метрики, алертинг по состоянию инфраструктуры.
5. Развитие антифрод-правил, критериев принятия решения и эксплуатационных сценариев.

Ни в API, ни в модели риска пока нет банковского решения `ALLOW/CHALLENGE/DENY`: система **выставляет оценку риска**, а не самостоятельно проводит или блокирует реальный платёж.

## Происхождение и лицензия

Проект основан на [jmello04/risk-engine](https://github.com/jmello04/risk-engine) — исходном open-source движке оценки риска транзакций. Сохранены базовые идеи и часть реализации: FastAPI, Fraud Engine, пять правил, API предупреждений, PostgreSQL, Redis и тесты.

В этой версии добавлены и доработаны распределённая инфраструктура, RabbitMQ, Transactional Outbox, отдельный контур Alert Notification, HMAC webhook, Cache-Aside, сегментация Docker, Firewall и тесты новых механизмов.

Оригинальный проект опубликован под лицензией **MIT**. Условия использования и уведомления об авторских правах следует сохранять согласно файлу [`LICENSE`](LICENSE).
