# TG → Pushover Relay

[Русский](#русский) · [English](#english)

<a id="русский"></a>

# Русский

Небольшой Linux-демон, который дублирует свежие приватные Telegram-алерты от **одного явно разрешённого Telegram-бота** в **Pushover**.

Relay подключается к Telegram через обычную пользовательскую сессию с помощью Telethon, применяет строгий фильтр по числовому sender ID и отправляет в Pushover только разрешённый текст или caption.

Текущий стабильный production-релиз: **v1.0.0**

---

## Что делает программа

```text
Один конкретный Telegram-бот
        ↓
Ваш личный Telegram-аккаунт
        ↓
Telethon / Telegram Client API
        ↓
строгий фильтр private incoming + sender ID
        ↓
проверка свежести (< 5 минут)
        ↓
Pushover HTTPS API
        ↓
Pushover-уведомление
```

Relay намеренно сделан небольшим и консервативным:

- разрешён только один настроенный числовой Telegram sender ID;
- исходящие сообщения игнорируются;
- группы, каналы и другие non-private updates игнорируются;
- устаревшие сообщения отбрасываются;
- сами медиафайлы не пересылаются;
- нет application database и persistent queue;
- после запуска нет восстановления пропущенных Telegram-сообщений;
- неоднозначные результаты доставки Pushover не ретраятся автоматически;
- production-демон требует уже авторизованную Telegram-сессию и не выполняет интерактивный логин при обычном старте systemd.

Это **LIVE-ONLY / BEST-EFFORT** relay. Если VPS, процесс, сеть или Telegram-соединение недоступны, алерты, пришедшие в этот период, могут быть пропущены.

---

# Требования

Инструкция ниже рассчитана на Debian/Ubuntu-подобный VPS, где есть:

- Linux + systemd;
- Python 3.10 или новее;
- исходящий доступ в Интернет к Telegram и Pushover;
- sudo/root-доступ для установки;
- Telegram-аккаунт, на который приходят сообщения от исходного бота;
- Telegram API credentials для этого аккаунта;
- аккаунт Pushover, User Key и Application/API Token.

Сам relay **не требует входящего сетевого порта**. Обычный SSH-порт для администрирования сервера к relay не относится.

---

# 1. Получите Telegram API credentials

Создайте Telegram API credentials для Telegram-аккаунта, под которым будет работать relay.

Нужны:

```text
TELEGRAM_API_ID
TELEGRAM_API_HASH
```

Используйте официальный портал Telegram:

<https://my.telegram.org/>

`TELEGRAM_API_HASH` нужно считать чувствительным секретом.

Не добавляйте реальные credentials в этот репозиторий.

---

# 2. Получите Pushover credentials

Создайте или используйте существующее приложение Pushover и получите:

```text
PUSHOVER_USER_KEY
PUSHOVER_APP_TOKEN
```

Pushover:

<https://pushover.net/>

Оба значения нужно считать секретами.

Relay отправляет сообщения на:

```text
https://api.pushover.net/1/messages.json
```

v1.0.0 не задаёт отдельный sound или priority. Используется стандартное поведение Pushover.

---

# 3. Установите системные пакеты

```bash
sudo apt update
sudo apt install -y git python3 python3-venv ca-certificates
```

Проверьте Python:

```bash
python3 --version
```

Рекомендуется Python 3.10+, потому что код использует современный синтаксис типов Python.

---

# 4. Создайте отдельного системного пользователя

Production-сервис работает от отдельного непривилегированного пользователя `tgrelay`.

```bash
sudo useradd \
  --system \
  --home-dir /var/lib/tg-pushover-relay \
  --create-home \
  --shell /usr/sbin/nologin \
  tgrelay
```

Создайте каталог для постоянной Telegram-сессии с ограниченными правами:

```bash
sudo install -d \
  -o tgrelay \
  -g tgrelay \
  -m 0700 \
  /var/lib/tg-pushover-relay
```

Telegram-сессия хранится здесь:

```text
/var/lib/tg-pushover-relay/telegram.session
```

**Важно:** этот session-файл является чувствительным. Человек, получивший рабочий файл Telegram-сессии, потенциально может получить доступ к Telegram-аккаунту, от имени которого создана сессия. Не публикуйте его, не загружайте в issue, не отправляйте по почте и не коммитьте в Git.

---

# 5. Скачайте стабильный релиз

Для production устанавливайте зафиксированный release tag, а не произвольное состояние development-ветки:

```bash
sudo git clone \
  --branch v1.0.0 \
  --depth 1 \
  https://github.com/MMonyx/tg-pushover-relay.git \
  /opt/tg-pushover-relay
```

Создайте virtual environment и установите зафиксированные зависимости:

```bash
sudo python3 -m venv /opt/tg-pushover-relay/.venv

sudo /opt/tg-pushover-relay/.venv/bin/pip install \
  --upgrade pip

sudo /opt/tg-pushover-relay/.venv/bin/pip install \
  -r /opt/tg-pushover-relay/requirements.txt
```

В текущем релизе закреплены:

```text
telethon==1.45.0
httpx==0.28.1
```

---

# 6. Создайте Telegram-сессию

Обычный запуск production-демона специально сделан неинтерактивным. Поэтому Telegram-авторизацию нужно один раз выполнить заранее до запуска systemd-сервиса.

В текущей shell-сессии введите Telegram API credentials так, чтобы они не попадали в аргументы командной строки:

```bash
read -rp "Telegram API ID: " TELEGRAM_API_ID
read -rsp "Telegram API hash: " TELEGRAM_API_HASH
echo

export TELEGRAM_API_ID
export TELEGRAM_API_HASH
```

Запустите helper инициализации сессии от имени отдельного сервисного пользователя:

```bash
sudo --preserve-env=TELEGRAM_API_ID,TELEGRAM_API_HASH \
  -u tgrelay \
  /opt/tg-pushover-relay/.venv/bin/python \
  /opt/tg-pushover-relay/scripts/init_session.py
```

Telethon может запросить:

- номер телефона Telegram-аккаунта;
- код входа Telegram;
- пароль двухэтапной аутентификации Telegram, если она включена.

При успешном завершении будет выведено:

```text
Telegram session is authorized
```

Проверьте, что session-файл создан:

```bash
sudo ls -l /var/lib/tg-pushover-relay/telegram.session
```

Владельцем должен быть `tgrelay`, а файл не должен быть доступен на чтение всем пользователям системы.

---

# 7. Найдите числовой sender ID исходного бота

Relay не доверяет Telegram username во время production-работы. Фильтрация выполняется по **числовому Telegram sender ID** бота.

Пока `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` ещё экспортированы, выполните:

```bash
sudo --preserve-env=TELEGRAM_API_ID,TELEGRAM_API_HASH \
  -u tgrelay \
  /opt/tg-pushover-relay/.venv/bin/python \
  /opt/tg-pushover-relay/scripts/show_sender_id.py \
  "@source_bot_username"
```

Замените `@source_bot_username` на реальный username исходного бота.

Helper проверяет, что найденная Telegram-сущность действительно является ботом, и выводит её numeric ID.

Пример:

```text
123456789
```

Используйте значение, которое напечатает ваш собственный запуск. Число выше — только пример, не реальное значение конфигурации.

После завершения удалите временные переменные из shell:

```bash
unset TELEGRAM_API_ID
unset TELEGRAM_API_HASH
```

---

# 8. Создайте production environment-файл

Создайте:

```text
/etc/tg-pushover-relay.env
```

с ограниченными правами:

```bash
sudo install \
  -o root \
  -g root \
  -m 0600 \
  /dev/null \
  /etc/tg-pushover-relay.env

sudoedit /etc/tg-pushover-relay.env
```

Содержимое:

```text
TELEGRAM_API_ID=YOUR_TELEGRAM_API_ID
TELEGRAM_API_HASH=YOUR_TELEGRAM_API_HASH
TELEGRAM_SOURCE_ID=YOUR_NUMERIC_SOURCE_BOT_ID
PUSHOVER_USER_KEY=YOUR_PUSHOVER_USER_KEY
PUSHOVER_APP_TOKEN=YOUR_PUSHOVER_APP_TOKEN
```

Не добавляйте кавычки без необходимости для вашей локальной среды.

Не храните этот файл внутри Git-репозитория.

Production-программа проверяет:

- `TELEGRAM_API_ID` — положительное десятичное целое;
- `TELEGRAM_API_HASH` — непустая строка;
- `TELEGRAM_SOURCE_ID` — положительное десятичное целое;
- оба Pushover credential — ровно 30 ASCII alphanumeric символов.

---

# 9. Установите systemd-сервис

Скопируйте готовый unit:

```bash
sudo cp \
  /opt/tg-pushover-relay/systemd/tg-pushover-relay.service \
  /etc/systemd/system/tg-pushover-relay.service
```

Перечитайте конфигурацию systemd:

```bash
sudo systemctl daemon-reload
```

Включите автозапуск после reboot и запустите relay:

```bash
sudo systemctl enable --now tg-pushover-relay.service
```

---

# 10. Проверьте сервис

Проверьте состояние:

```bash
sudo systemctl status tg-pushover-relay.service --no-pager
```

Ожидается:

```text
active (running)
```

Посмотрите последние логи:

```bash
sudo journalctl \
  -u tg-pushover-relay.service \
  -n 50 \
  --no-pager
```

При нормальном запуске будут строки примерно такого вида:

```text
Connecting to ...
Connection ... complete!
telegram startup complete; entering receive loop
```

Production-демон не должен запрашивать Telegram login credentials при запуске через systemd. Если существующая session не авторизована или непригодна, процесс завершится с ошибкой вместо интерактивного входа.

---

# 11. Проверьте доставку end-to-end

После того как сервис вошёл в receive loop, создайте **новый** alert от настроенного Telegram-бота.

Затем проверьте логи:

```bash
sudo journalctl \
  -u tg-pushover-relay.service \
  --since "5 minutes ago" \
  --no-pager
```

Успешная доставка Pushover записывается как:

```text
pushover response: http_status=200 outcome=CONFIRMED_SUCCESS
```

Также убедитесь, что соответствующее уведомление пришло в Pushover.

Для простой security-проверки отправьте приватное сообщение на этот Telegram-аккаунт от другого контакта. Оно **не должно** создавать Pushover-уведомление.

---

# 12. Проверьте reboot

Сервис настроен на автоматический запуск после загрузки системы.

Перезагрузите VPS:

```bash
sudo reboot
```

После повторного подключения:

```bash
sudo systemctl is-enabled tg-pushover-relay.service
sudo systemctl is-active tg-pushover-relay.service

sudo journalctl \
  -u tg-pushover-relay.service \
  -b \
  -n 50 \
  --no-pager
```

Ожидается:

```text
enabled
active
```

После этого создайте новый alert от исходного бота и убедитесь, что он дошёл до Pushover.

Не ожидайте, что relay восстановит сообщения, пришедшие пока VPS был выключен. Такое восстановление намеренно не реализовано.

---

# Обновление до следующего релиза

Не обновляйте production-сервер вслепую до последнего состояния ветки.

Когда появится новый проверенный релиз, замените `vX.Y.Z` ниже на нужный release tag:

```bash
cd /opt/tg-pushover-relay

sudo git fetch --tags
sudo git checkout vX.Y.Z

sudo /opt/tg-pushover-relay/.venv/bin/pip install \
  -r /opt/tg-pushover-relay/requirements.txt

sudo systemctl restart tg-pushover-relay.service
```

После обновления повторите проверки статуса сервиса и live delivery.

Telegram-session и secrets находятся вне `/opt/tg-pushover-relay`, поэтому обычное обновление кода не требует повторной Telegram-авторизации.

---

# Удаление

Остановите сервис и отключите автозапуск:

```bash
sudo systemctl disable --now tg-pushover-relay.service
```

Удалите unit и перечитайте конфигурацию systemd:

```bash
sudo rm -f /etc/systemd/system/tg-pushover-relay.service
sudo systemctl daemon-reload
```

Удалите программу:

```bash
sudo rm -rf /opt/tg-pushover-relay
```

Удалите secret environment-файл:

```bash
sudo rm -f /etc/tg-pushover-relay.env
```

Если также нужно удалить локальную Telegram-сессию:

```bash
sudo rm -rf /var/lib/tg-pushover-relay
```

После этого удалите отдельного системного пользователя, если он больше не нужен:

```bash
sudo userdel tgrelay
```

Удаление каталога с session необратимо на этом сервере и при повторной установке потребует новой Telegram-авторизации.

---

# Модель безопасности

## Фильтрация отправителя

Telegram-сообщение попадает в обработку контента только при одновременном выполнении всех условий:

```text
event.out is False
event.is_private is True
event.sender_id == TELEGRAM_SOURCE_ID
```

Числовой sender ID является runtime-authority. Username используется только setup-helper'ом для определения этого числового ID.

Таким образом отсекаются другие private-контакты, группы, каналы и исходящие сообщения.

## Сначала фильтр, потом контент

Relay проверяет sender/private/direction metadata до чтения содержимого сообщения для дальнейшей пересылки.

Это намеренная application-level privacy boundary.

## Свежесть

Источником времени является timestamp Telegram-сообщения.

```text
age < 5 минут  → сообщение допустимо
age >= 5 минут → DROP
```

Если сообщение успело устареть до разрешённого retry, оно также отбрасывается.

## Нет startup catch-up

Relay работает в live-only режиме.

Он не:

- опрашивает Telegram history;
- восстанавливает пропущенные updates после запуска;
- хранит persistent application queue;
- ведёт dedup/recovery database;
- запускает второй recovery-клиент.

Это уменьшает сложность и риск дублей, но означает, что downtime может привести к пропуску Pushover-алертов.

## Retry Pushover

Подтверждённый успех требует:

```text
HTTP 200
+
валидный JSON
+
status == 1
```

Одного HTTP 200 недостаточно.

Relay разрешает максимум **один автоматический retry**.

Retry допускается только для:

- HTTP 5xx server responses;
- небольшого allowlist transport failures, которые считаются безопасными для повторной попытки:
  - `httpx.ConnectTimeout`
  - `httpx.ConnectError`
  - `httpx.PoolTimeout`

Другие `httpx.RequestError` считаются ambiguous и **не** ретраятся автоматически.

Архитектура намеренно предпочитает избежать вероятного дублирования, а не пытаться повторять каждый неопределённый результат доставки.

## Логирование

Обычные application logs содержат только operational metadata, например:

- startup/shutdown state;
- HTTP status;
- delivery classification;
- имена классов exceptions.

Relay намеренно не логирует тела Telegram-сообщений, Telegram login codes, Pushover tokens, Telegram API hashes или содержимое session-файла.

## Hardening systemd

Готовый unit запускается от отдельного пользователя `tgrelay` и включает:

- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `PrivateDevices=true`
- `ProtectKernelTunables=true`
- `ProtectKernelModules=true`
- `ProtectControlGroups=true`
- `RestrictSUIDSGID=true`
- `UMask=0077`

Для записи сервису явно разрешён только `/var/lib/tg-pushover-relay`.

---

# FAQ

## Используется ли Telegram bot token?

Нет.

Исходный бот принадлежит другой стороне. Relay подключается к Telegram как **ваш пользовательский Telegram-аккаунт** через Telethon и получает updates, доступные этому аккаунту.

Поэтому нужны Telegram API credentials и авторизованная Telethon-session.

## Чувствителен ли Telegram session-файл?

Да — очень.

`/var/lib/tg-pushover-relay/telegram.session` следует считать credential.

Никогда:

- не коммитьте его;
- не загружайте его в публичный issue;
- не прикладывайте к логам или support-запросам;
- не копируйте на недоверенный компьютер;
- не делайте его world-readable.

Если есть подозрение, что session была раскрыта, отзовите соответствующую Telegram-сессию в управлении активными сессиями/устройствами Telegram и создайте новую локальную session.

## Хранятся ли credentials в исходном коде?

Нет. Production credentials не должны находиться в source code.

Runtime secrets читаются из environment variables, передаваемых через:

```text
/etc/tg-pushover-relay.env
```

Telegram session отдельно хранится в:

```text
/var/lib/tg-pushover-relay/
```

Оба пути должны находиться вне Git-репозитория.

## Может ли relay переслать сообщение не от того Telegram-контакта?

Production-фильтр требует приватное входящее сообщение, чей numeric sender ID в точности равен `TELEGRAM_SOURCE_ID`.

Сообщения от других контактов отклоняются до пересылки контента.

При этом программа не может исправить ошибочно настроенный `TELEGRAM_SOURCE_ID`, поэтому numeric ID нужно внимательно проверить при установке.

## Пересылаются ли группы и каналы?

Нет. Production-фильтр требует `event.is_private is True`.

## Пересылаются ли мои исходящие сообщения?

Нет. Production-фильтр требует `event.out is False`.

## Пересылаются ли фотографии, файлы и видео?

Relay пересылает только текст/caption, если доступна непустая строка.

Сам media object в Pushover не загружается.

## Сколько времени alert считается свежим?

Менее пяти минут от timestamp Telegram-сообщения.

В возрасте ровно пять минут или больше сообщение отбрасывается.

## Будут ли после reboot отправлены alerts, пришедшие во время отключения сервера?

Нет.

Relay намеренно не делает startup catch-up и history recovery.

После восстановления Telegram-соединения он возвращается только к live processing.

## Почему программа не повторяет любой Pushover error?

Некоторые сбои неоднозначны: удалённый сервис мог принять запрос, даже если клиент не смог подтвердить финальный результат.

Автоматический retry в такой ситуации способен создать duplicate notification.

Поэтому relay повторяет только явно разрешённые классы ошибок, максимум один раз и только пока исходный Telegram-alert остаётся свежим.

## Что происходит при HTTP 200 от Pushover?

Relay дополнительно проверяет JSON-body.

Успех требует `status == 1`.

Некорректный JSON, отсутствующий/невалидный status или другой неоднозначный результат не считаются подтверждённой успешной доставкой.

## Хранит ли relay историю сообщений?

Нет application database, application queue или recovery store.

Telegram и Pushover, разумеется, работают как отдельные сервисы и имеют собственные политики хранения данных.

## Открывает ли relay web server или listening port?

Нет.

Приложение создаёт только исходящие соединения с Telegram и Pushover. Собственного HTTP server или application listening port у него нет.

## Может ли обычный запуск попросить Telegram login code?

Нет.

Интерактивная Telegram-авторизация ограничена одноразовым setup-скриптом `scripts/init_session.py`.

Production startup только проверяет существующую авторизованную session. Если session непригодна, startup завершается ошибкой.

## Что будет, если процесс упадёт?

Готовый systemd unit использует:

```text
Restart=on-failure
RestartSec=5
```

systemd перезапустит процесс после failure.

Это восстановление процесса не восстанавливает Telegram-alerts, которые были пропущены во время downtime.

## Можно ли подключить больше одного source bot?

Не в текущем дизайне v1.0.0.

Production configuration принимает один `TELEGRAM_SOURCE_ID`.

## Можно ли запустить несколько экземпляров relay?

Текущий релиз рассчитан на один небольшой сервис с фиксированным session path:

```text
/var/lib/tg-pushover-relay/telegram.session
```

Одновременный запуск нескольких копий без явного разделения session/config paths не входит в поддерживаемый сценарий v1.0.0.

## Можно ли изменить заголовок Pushover?

В v1.0.0 заголовок зафиксирован в коде:

```text
Telegram Alert
```

## Можно ли изменить Pushover sound или priority?

v1.0.0 не отправляет поля `sound` и `priority`.

Поэтому применяется стандартное поведение Pushover.

## Что приложить к bug report?

Полезная и не секретная информация:

- release tag;
- операционная система;
- версия Python;
- вывод `systemctl status`;
- относящиеся к проблеме metadata-only строки journal;
- авторизована ли Telegram session;
- возникает ли ошибка до или после входа в receive loop.

Перед публикацией удалите или замаскируйте:

- номера телефонов Telegram;
- API hashes;
- Pushover keys/tokens;
- Telegram session files;
- login codes;
- пароли двухэтапной аутентификации;
- содержимое посторонних Telegram-сообщений.

---

# Структура репозитория

```text
relay.py
    production daemon

scripts/init_session.py
    одноразовая интерактивная настройка Telegram session

scripts/show_sender_id.py
    helper для определения и проверки numeric sender ID исходного бота

systemd/tg-pushover-relay.service
    production systemd unit

requirements.txt
    закреплённые Python dependencies

tests/test_critical_logic.py
    критические synthetic tests

docs/
    записи architecture и implementation boundary
```

---

# Статус релиза

`v1.0.0` — первый замороженный production-релиз текущей архитектуры.

Основные свойства:

- строгая фильтрация по numeric sender ID;
- live-only best-effort delivery;
- freshness boundary 5 минут;
- ограниченный retry;
- отсутствие startup catch-up;
- отсутствие application persistence layer;
- systemd process recovery;
- неинтерактивный production startup Telegram.

Для production используйте tagged release, а не непроверенное состояние ветки.

---

<a id="english"></a>

# English

A small Linux daemon that forwards fresh private Telegram alerts from **one explicitly allowed Telegram bot** to **Pushover**.

The relay uses a normal Telegram user session through Telethon, applies a strict numeric sender-ID filter, and forwards only eligible text/caption content to Pushover.

Current stable production release: **v1.0.0**

---

## What it does

```text
One specific Telegram bot
        ↓
Your Telegram user account
        ↓
Telethon / Telegram Client API
        ↓
strict private incoming sender-ID filter
        ↓
freshness check (< 5 minutes)
        ↓
Pushover HTTPS API
        ↓
Pushover notification
```

The relay is intentionally small and conservative:

- only one configured numeric Telegram sender ID is allowed;
- outgoing messages are ignored;
- groups/channels/non-private updates are ignored;
- stale messages are dropped;
- media itself is not forwarded;
- there is no application database or persistent queue;
- startup does not recover missed Telegram messages;
- ambiguous Pushover delivery outcomes are not automatically retried;
- the daemon requires an already-authorized Telegram session and never performs interactive login during normal service startup.

This is a **LIVE-ONLY / BEST-EFFORT** relay. If the VPS, process, network, or Telegram connection is unavailable, alerts received during that period may be missed.

---

# Requirements

The installation below assumes a Debian/Ubuntu-style VPS with:

- Linux + systemd;
- Python 3.10 or newer;
- outbound Internet access to Telegram and Pushover;
- sudo/root access for installation;
- a Telegram account that receives messages from the source bot;
- Telegram API credentials for that account;
- a Pushover account, User Key, and Application/API Token.

The relay itself does **not** require an inbound network port. Your normal SSH administration port is unrelated to the relay.

---

# 1. Get Telegram API credentials

Create Telegram API credentials for the Telegram account that will run the relay.

You need:

```text
TELEGRAM_API_ID
TELEGRAM_API_HASH
```

Use Telegram's official developer portal:

<https://my.telegram.org/>

Treat `TELEGRAM_API_HASH` as sensitive.

Do not place real credentials in this repository.

---

# 2. Get Pushover credentials

Create or use a Pushover application and obtain:

```text
PUSHOVER_USER_KEY
PUSHOVER_APP_TOKEN
```

Pushover:

<https://pushover.net/>

Both values should be treated as secrets.

The relay sends messages to:

```text
https://api.pushover.net/1/messages.json
```

It does not set a custom Pushover sound or priority. Pushover's normal/default behavior applies.

---

# 3. Install system packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv ca-certificates
```

Check Python:

```bash
python3 --version
```

Python 3.10+ is recommended because the code uses modern Python type syntax.

---

# 4. Create the dedicated service account

The production service runs as a dedicated unprivileged account named `tgrelay`.

```bash
sudo useradd \
  --system \
  --home-dir /var/lib/tg-pushover-relay \
  --create-home \
  --shell /usr/sbin/nologin \
  tgrelay
```

Create the persistent session directory with restrictive permissions:

```bash
sudo install -d \
  -o tgrelay \
  -g tgrelay \
  -m 0700 \
  /var/lib/tg-pushover-relay
```

The Telegram session will be stored at:

```text
/var/lib/tg-pushover-relay/telegram.session
```

**Important:** this session file is sensitive. Anyone who obtains a usable Telegram session file may gain access to the Telegram account represented by that session. Do not publish, upload, email, or commit it.

---

# 5. Download the stable release

Install the frozen production release rather than an arbitrary development branch:

```bash
sudo git clone \
  --branch v1.0.0 \
  --depth 1 \
  https://github.com/MMonyx/tg-pushover-relay.git \
  /opt/tg-pushover-relay
```

Create a virtual environment and install the pinned dependencies:

```bash
sudo python3 -m venv /opt/tg-pushover-relay/.venv

sudo /opt/tg-pushover-relay/.venv/bin/pip install \
  --upgrade pip

sudo /opt/tg-pushover-relay/.venv/bin/pip install \
  -r /opt/tg-pushover-relay/requirements.txt
```

The current release pins:

```text
telethon==1.45.0
httpx==0.28.1
```

---

# 6. Create the Telegram session

Normal daemon startup is intentionally non-interactive. Telegram authorization must therefore be completed once before starting the systemd service.

In your current shell, enter the Telegram API credentials without putting them into a command-line argument:

```bash
read -rp "Telegram API ID: " TELEGRAM_API_ID
read -rsp "Telegram API hash: " TELEGRAM_API_HASH
echo

export TELEGRAM_API_ID
export TELEGRAM_API_HASH
```

Run the session initialization helper as the dedicated service user:

```bash
sudo --preserve-env=TELEGRAM_API_ID,TELEGRAM_API_HASH \
  -u tgrelay \
  /opt/tg-pushover-relay/.venv/bin/python \
  /opt/tg-pushover-relay/scripts/init_session.py
```

Telethon may ask for:

- the Telegram account phone number;
- a Telegram login code;
- the account's Telegram two-step-verification password, if enabled.

Successful completion prints:

```text
Telegram session is authorized
```

Verify the session file exists:

```bash
sudo ls -l /var/lib/tg-pushover-relay/telegram.session
```

It should be owned by `tgrelay` and not be world-readable.

---

# 7. Find the numeric sender ID of the source bot

The relay does not trust a Telegram username at runtime. Production filtering uses the source bot's **numeric Telegram sender ID**.

With `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` still exported, run:

```bash
sudo --preserve-env=TELEGRAM_API_ID,TELEGRAM_API_HASH \
  -u tgrelay \
  /opt/tg-pushover-relay/.venv/bin/python \
  /opt/tg-pushover-relay/scripts/show_sender_id.py \
  "@source_bot_username"
```

Replace `@source_bot_username` with the actual bot username.

The helper verifies that the resolved Telegram entity is a bot and prints its numeric ID.

Example output:

```text
123456789
```

Use the value printed by your own account. The example above is not a real configuration value.

When finished with the temporary shell variables:

```bash
unset TELEGRAM_API_ID
unset TELEGRAM_API_HASH
```

---

# 8. Create the production environment file

Create:

```text
/etc/tg-pushover-relay.env
```

with restrictive permissions:

```bash
sudo install \
  -o root \
  -g root \
  -m 0600 \
  /dev/null \
  /etc/tg-pushover-relay.env

sudoedit /etc/tg-pushover-relay.env
```

Content:

```text
TELEGRAM_API_ID=YOUR_TELEGRAM_API_ID
TELEGRAM_API_HASH=YOUR_TELEGRAM_API_HASH
TELEGRAM_SOURCE_ID=YOUR_NUMERIC_SOURCE_BOT_ID
PUSHOVER_USER_KEY=YOUR_PUSHOVER_USER_KEY
PUSHOVER_APP_TOKEN=YOUR_PUSHOVER_APP_TOKEN
```

Do not add quotes unless your local environment specifically requires them.

Do not store this file inside the Git repository.

The production program validates that:

- `TELEGRAM_API_ID` is a positive decimal integer;
- `TELEGRAM_API_HASH` is non-empty;
- `TELEGRAM_SOURCE_ID` is a positive decimal integer;
- both Pushover credentials are exactly 30 ASCII alphanumeric characters.

---

# 9. Install the systemd service

Copy the provided service unit:

```bash
sudo cp \
  /opt/tg-pushover-relay/systemd/tg-pushover-relay.service \
  /etc/systemd/system/tg-pushover-relay.service
```

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Enable automatic startup after reboot and start the relay:

```bash
sudo systemctl enable --now tg-pushover-relay.service
```

---

# 10. Verify the service

Check state:

```bash
sudo systemctl status tg-pushover-relay.service --no-pager
```

Expected state:

```text
active (running)
```

Check recent logs:

```bash
sudo journalctl \
  -u tg-pushover-relay.service \
  -n 50 \
  --no-pager
```

A normal startup contains messages similar to:

```text
Connecting to ...
Connection ... complete!
telegram startup complete; entering receive loop
```

The daemon must not ask for Telegram login credentials when launched by systemd. If it does not have a usable authorized session, it exits instead.

---

# 11. Test end-to-end delivery

After the service has entered its receive loop, trigger a **new** alert from the configured Telegram source bot.

Then inspect the logs:

```bash
sudo journalctl \
  -u tg-pushover-relay.service \
  --since "5 minutes ago" \
  --no-pager
```

Successful Pushover delivery is logged as:

```text
pushover response: http_status=200 outcome=CONFIRMED_SUCCESS
```

Also confirm the corresponding notification arrived in Pushover.

For a security sanity check, send a private Telegram message to the account from some other contact. It should **not** produce a Pushover notification.

---

# 12. Reboot test

The service is configured to start automatically at boot.

Reboot:

```bash
sudo reboot
```

After reconnecting:

```bash
sudo systemctl is-enabled tg-pushover-relay.service
sudo systemctl is-active tg-pushover-relay.service

sudo journalctl \
  -u tg-pushover-relay.service \
  -b \
  -n 50 \
  --no-pager
```

Expected:

```text
enabled
active
```

Then trigger a new source-bot alert and confirm it reaches Pushover.

Do **not** expect the relay to recover messages that arrived while the VPS was offline. That behavior is intentionally not implemented.

---

# Updating to a later release

Do not blindly update a production server to the latest branch.

When a newer tested release exists, replace `vX.Y.Z` below with the desired release tag:

```bash
cd /opt/tg-pushover-relay

sudo git fetch --tags
sudo git checkout vX.Y.Z

sudo /opt/tg-pushover-relay/.venv/bin/pip install \
  -r /opt/tg-pushover-relay/requirements.txt

sudo systemctl restart tg-pushover-relay.service
```

Then repeat the service-status and live-delivery checks.

Your Telegram session and secrets live outside `/opt/tg-pushover-relay`, so a normal code update does not require reauthorizing Telegram.

---

# Uninstall

Stop and disable the service:

```bash
sudo systemctl disable --now tg-pushover-relay.service
```

Remove the unit and reload systemd:

```bash
sudo rm -f /etc/systemd/system/tg-pushover-relay.service
sudo systemctl daemon-reload
```

Remove the program:

```bash
sudo rm -rf /opt/tg-pushover-relay
```

Remove the secret environment file:

```bash
sudo rm -f /etc/tg-pushover-relay.env
```

If you also want to revoke/delete the local Telegram session, remove:

```bash
sudo rm -rf /var/lib/tg-pushover-relay
```

Then remove the dedicated account if no longer needed:

```bash
sudo userdel tgrelay
```

Deleting the session directory is irreversible locally and will require Telegram authorization again if you reinstall.

---

# Security model

## Sender filtering

A Telegram message reaches the content-processing path only when all of the following are true:

```text
event.out is False
event.is_private is True
event.sender_id == TELEGRAM_SOURCE_ID
```

The numeric sender ID is the runtime authority. A username is used only by the optional setup helper to discover that numeric ID.

This prevents unrelated private contacts, group traffic, channel traffic, and outgoing messages from being forwarded.

## Filter first, content second

The relay performs its sender/private-direction check before reading message content for forwarding.

This is an intentional application-level privacy boundary.

## Freshness

The Telegram message timestamp is authoritative.

```text
age < 5 minutes  → eligible
age >= 5 minutes → dropped
```

A message that becomes stale before an allowed retry is also dropped.

## No startup catch-up

The relay is live-only.

It does not:

- poll Telegram history;
- recover missed updates on startup;
- maintain a persistent application queue;
- maintain a deduplication/recovery database;
- run a second recovery client.

This reduces complexity and duplicate-delivery risk, but it means downtime can cause missed Pushover alerts.

## Pushover retry behavior

Confirmed success requires:

```text
HTTP 200
+
valid JSON
+
status == 1
```

HTTP 200 alone is not treated as sufficient proof of success.

The relay allows at most **one automatic retry**.

Retry is limited to:

- HTTP 5xx server responses;
- a small allowlist of transport failures that are treated as safe to retry:
  - `httpx.ConnectTimeout`
  - `httpx.ConnectError`
  - `httpx.PoolTimeout`

Other `httpx.RequestError` outcomes are classified as ambiguous and are **not** automatically retried.

The design intentionally prefers avoiding probable duplicate alerts over retrying every uncertain outcome.

## Logging

Normal application logs contain operational metadata such as:

- startup/shutdown state;
- HTTP status;
- delivery classification;
- exception class names.

The relay does not intentionally log Telegram message bodies, Telegram login codes, Pushover tokens, Telegram API hashes, or session contents.

## systemd hardening

The supplied unit runs as the dedicated `tgrelay` account and enables hardening including:

- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `PrivateDevices=true`
- `ProtectKernelTunables=true`
- `ProtectKernelModules=true`
- `ProtectControlGroups=true`
- `RestrictSUIDSGID=true`
- `UMask=0077`

Only `/var/lib/tg-pushover-relay` is explicitly writable by the service.

---

# FAQ

## Does this use a Telegram bot token?

No.

The source bot belongs to someone else. The relay connects to Telegram as your **Telegram user account** through Telethon and receives updates visible to that account.

The relay therefore needs Telegram API credentials plus an authorized Telethon session.

## Is the Telegram session file sensitive?

Yes — very.

`/var/lib/tg-pushover-relay/telegram.session` should be treated like a credential.

Never:

- commit it;
- upload it to a public issue;
- attach it to logs/support requests;
- copy it to an untrusted machine;
- make it world-readable.

If you believe the session was exposed, revoke the relevant Telegram session from Telegram's active-session/device controls and create a new local session.

## Are credentials stored in the source code?

No production credentials are required in source code.

Runtime secrets are read from environment variables supplied by:

```text
/etc/tg-pushover-relay.env
```

The Telegram session is stored separately under:

```text
/var/lib/tg-pushover-relay/
```

Neither location should be part of the repository.

## Can the relay forward messages from the wrong Telegram contact?

The production filter requires a private incoming message whose numeric sender ID exactly equals `TELEGRAM_SOURCE_ID`.

Messages from other contacts are rejected before content forwarding.

No design can compensate for an incorrectly configured `TELEGRAM_SOURCE_ID`, so verify the numeric ID during setup.

## Does it forward groups or channels?

No. The production filter requires `event.is_private is True`.

## Does it forward my outgoing messages?

No. The production filter requires `event.out is False`.

## Does it forward photos, files, video, or other media?

The relay forwards text/caption content only when a non-empty string is available.

It does not upload the media object itself to Pushover.

## How long can an alert remain eligible?

Less than five minutes from the Telegram message timestamp.

At five minutes or older, it is dropped.

## Will alerts that arrived while the server was down be sent after reboot?

No.

The relay intentionally does not perform startup catch-up or history recovery.

It resumes live processing after Telegram reconnects.

## Why not retry every Pushover error?

Because some failures are ambiguous: the remote service may have accepted a request even if the client cannot prove the final result.

Automatically retrying such cases can create duplicate notifications.

The relay therefore retries only explicitly allowed failure classes, at most once, and only while the original Telegram alert is still fresh.

## What happens if Pushover returns HTTP 200?

The relay still validates the JSON body.

Success requires `status == 1`.

Malformed JSON, a missing/invalid status field, or another ambiguous result is not treated as confirmed success.

## Does the relay store message history?

No application message database, application queue, or recovery store is used.

Telegram and Pushover of course operate their own services and retention policies independently of this relay.

## Does the relay expose a web server or listening port?

No.

The application makes outbound connections to Telegram and Pushover. It does not provide an HTTP server or open an application listening port.

## Does normal startup ever ask for my Telegram login code?

No.

Interactive Telegram authentication is restricted to the one-time `scripts/init_session.py` setup step.

Production startup only checks whether the existing session is authorized. If it is not usable, startup fails.

## What happens if the process crashes?

The supplied systemd unit uses:

```text
Restart=on-failure
RestartSec=5
```

systemd will restart the process after failures.

This process recovery does not recover Telegram alerts that were missed during downtime.

## Can I run more than one source bot?

Not with the current v1.0.0 design.

The production configuration accepts one `TELEGRAM_SOURCE_ID`.

## Can I run multiple relay instances?

The current release is designed as one small service using one fixed session path:

```text
/var/lib/tg-pushover-relay/telegram.session
```

Running multiple copies without intentionally separating their session/configuration paths is not part of the supported v1.0.0 design.

## Can I change the Pushover title?

The current v1.0.0 title is fixed in code as:

```text
Telegram Alert
```

## Can I change Pushover sound or priority?

v1.0.0 does not send explicit `sound` or `priority` fields.

Pushover's default behavior therefore applies.

## What should I include when reporting a problem?

Useful, non-secret information includes:

- release tag;
- operating system;
- Python version;
- `systemctl status` output;
- relevant metadata-only journal lines;
- whether the Telegram session is authorized;
- whether the failure occurs before or after the receive loop starts.

Before posting anything publicly, remove or redact:

- Telegram phone numbers;
- API hashes;
- Pushover keys/tokens;
- Telegram session files;
- login codes;
- two-step-verification passwords;
- unrelated Telegram message content.

---

# Repository layout

```text
relay.py
    production daemon

scripts/init_session.py
    one-time interactive Telegram session setup

scripts/show_sender_id.py
    helper for resolving/validating the source bot numeric ID

systemd/tg-pushover-relay.service
    production systemd unit

requirements.txt
    pinned Python dependencies

tests/test_critical_logic.py
    critical synthetic tests

docs/
    architecture and implementation boundary records
```

---

# Release status

`v1.0.0` is the first frozen production release of the current architecture.

Its key properties are:

- strict numeric sender filtering;
- live-only best-effort delivery;
- five-minute freshness boundary;
- bounded retry;
- no startup catch-up;
- no application persistence layer;
- systemd process recovery;
- non-interactive production Telegram startup.

For production use, install a tagged release rather than an unreviewed branch.
