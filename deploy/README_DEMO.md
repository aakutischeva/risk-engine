# Risk Engine — демонстрация проекта

## Назначение

Комплект скриптов для демонстрации микросервисной
антифрод-системы Risk Engine на промежуточной защите.

Окружение: Ubuntu Server, Docker, Docker Compose,
FastAPI, PostgreSQL, Patroni, etcd, HAProxy,
Redis, RabbitMQ и Alert Notification.

Скрипты предназначены для уже развернутой системы.

## DEMO 00 — Запуск всей архитектуры

Файл: demo_00_full_start.sh

Поэтапный запуск:
- etcd;
- PostgreSQL Primary/Replica и Patroni;
- PostgreSQL HAProxy;
- Redis;
- RabbitMQ HA из трех узлов;
- RabbitMQ HAProxy;
- Alert PostgreSQL;
- FastAPI и микросервисов уведомлений;
- контейнеров резервного копирования.

Команды:

    sudo bash deploy/demo_00_full_start.sh --check
    sudo bash deploy/demo_00_full_start.sh --start
    sudo bash deploy/demo_00_full_start.sh --verify

Статус: необходимо подтвердить запуск.

## DEMO 01 — Развертывание приложения

Файл: demo_01_deploy.sh

Пересборка Docker-образа API, пересоздание
контейнера и интеграционная проверка системы.

Команды:

    ./deploy/demo_01_deploy.sh --check
    ./deploy/demo_01_deploy.sh --execute

Результат: PASS, 12/12 проверок.

## DEMO 02 — PostgreSQL Failover

Файл: demo_02_patroni_failover.sh

Аварийное завершение PostgreSQL Primary,
автоматическое переключение Patroni,
проверка записи через HAProxy и возврат
старого Primary как Replica.

Команды:

    sudo bash deploy/demo_02_patroni_failover.sh --check
    sudo bash deploy/demo_02_patroni_failover.sh --execute

Результат: PASS.
Репликация streaming, lag 0.
Timeline изменился с 8 на 9.

## DEMO 03 — RabbitMQ Failover

Файл: demo_03_rabbitmq_failover.sh

Аварийная остановка лидера Quorum Queue,
выбор нового лидера, проверка обработки
сообщений и восстановление кластера.

Команды:

    sudo bash deploy/demo_03_rabbitmq_failover.sh --check
    sudo bash deploy/demo_03_rabbitmq_failover.sh --execute

Результат: PASS.
Три узла RabbitMQ восстановлены.
Сообщения обрабатываются после отказа.

## DEMO 04 — Firewall

Файл: demo_04_firewall.sh

Проверка сетевой сегментации и временного
запрета соединения API -> Redis.

Команды:

    sudo bash deploy/demo_04_firewall.sh --check
    sudo bash deploy/demo_04_firewall.sh --execute
    sudo bash deploy/demo_04_firewall.sh --cleanup

Результат: PASS.
ALLOWED -> BLOCKED -> RESTORED.

## DEMO 05 — Backup & Restore

Файл: demo_05_backup_restore.sh

Создание резервных копий двух PostgreSQL,
проверка SHA-256 и восстановление во
временном изолированном контейнере.

Команды:

    sudo bash deploy/demo_05_backup_restore.sh --check
    sudo bash deploy/demo_05_backup_restore.sh --execute
    sudo bash deploy/demo_05_backup_restore.sh --cleanup

Результат: PASS.
Восстановлены обе базы и 7 таблиц.
167 записей в 5 прикладных таблицах.
Время полной демонстрации: 9 секунд.

## DEMO 06 — CRUD, Redis, RabbitMQ

Файл: demo_06_crud_cache.sh

Проверка POST/GET транзакций, Cache-Aside,
MISS/HIT, идемпотентности, Outbox,
RabbitMQ и SQL CRUD.

Команды:

    sudo bash deploy/demo_06_crud_cache.sh --check
    sudo bash deploy/demo_06_crud_cache.sh --execute

Статус: итоговый результат не подтвержден.

## DEMO 07 — HMAC и Webhook

Файл: demo_07_hmac_webhook.sh

Проверка:
- HMAC-SHA256;
- успешной доставки webhook;
- повторного сообщения;
- неверной подписи;
- подмены тела запроса;
- полной доставки HIGH-уведомления.

Команды:

    sudo bash deploy/demo_07_hmac_webhook.sh --check
    sudo bash deploy/demo_07_hmac_webhook.sh --unit
    sudo bash deploy/demo_07_hmac_webhook.sh --execute
    sudo bash deploy/demo_07_hmac_webhook.sh --full

Статус: необходимо подтвердить запуск.

## Основные скрипты проекта

deploy/deploy.sh — сборка и запуск приложений.
deploy/status.sh — состояние архитектуры.
deploy/verify.sh — 12 интеграционных проверок.

Команды:

    bash deploy/status.sh
    bash deploy/verify.sh

## Важные ограничения

Не использовать docker compose down -v.

DEMO 02 и DEMO 03 намеренно останавливают
контейнеры и могут временно нарушить работу.

DEMO 04 временно блокирует соединение API -> Redis.

DEMO 05 сохраняет резервные копии в backups/.

DEMO 07 --full создает тестовые транзакции.

Нельзя загружать в GitHub:
- .env;
- .secrets/;
- backups/;
- пароли и ключи HMAC;
- реальные дампы PostgreSQL.
