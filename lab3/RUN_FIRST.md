# С чего начать (Windows)

## 1. Установить зависимости

Открой PowerShell в этой папке — в ней должны быть `requirements.txt` и папка `app`.

```powershell
py --version
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Если команда `py --version` не работает, установи Python 3.11 или новее и включи опцию **Add Python to PATH**.

## 2. Запустить приложение

В первом окне PowerShell выполни и оставь окно открытым:

```powershell
.\.venv\Scripts\python.exe -m app.web
```

Приложение будет доступно на `http://localhost:8000`.

## 3. Проверить, что приложение запустилось

Во втором окне PowerShell, из той же папки проекта:

```powershell
curl.exe -i http://localhost:8000/health
```

Ожидаемый ответ: HTTP `200 OK` и `{"status":"ok"}`.

## 4. Проверить сценарии API

Во втором окне PowerShell выполни:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
powershell -ExecutionPolicy Bypass -File .\scripts\curl_scenarios_windows.ps1
```

Первой командой запускаются автоматические тесты. Вторая выполняет API-сценарии через HTTP, включая основной путь, таймер, недопустимые действия (409), некорректные данные (422) и несуществующую поездку (404). Для включения демонстрационного отказа платежа сервер необходимо запускать с `ENABLE_TEST_CONTROLS=true`.

## 5. Docker (если установлен Docker Desktop)

Останови локальный сервер (`Ctrl+C`), затем выполни в папке проекта:

```powershell
docker compose up --build
```

В другом терминале проверь готовность:

```powershell
curl.exe -i http://localhost:8000/health
```

Остановить Docker-контейнеры: `Ctrl+C`, затем `docker compose down`.

**Важно:** хранилища проекта находятся в памяти. После перезапуска приложения созданные данные сбрасываются.
