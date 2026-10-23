#!/usr/bin/env bash
# Демонстрационные запросы для раздела IV. Требуются curl и jq.
# Для отказа платежа запустить с ENABLE_TEST_CONTROLS=true.
set -euo pipefail
BASE="${BASE_URL:-http://localhost:8000}"

printf '\n== Health ==\n'
curl -sS -i "$BASE/health"

printf '\n== Создание клиента и самоката ==\n'
USER_JSON=$(curl -sS -X POST "$BASE/users" -H 'Content-Type: application/json' -d '{"full_name":"Иван Петров"}')
USER_ID=$(printf '%s' "$USER_JSON" | jq -r .id)
printf '%s\n' "$USER_JSON"
SCOOTER_JSON=$(curl -sS -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"battery_percent":80}')
SCOOTER_ID=$(printf '%s' "$SCOOTER_JSON" | jq -r .id)
printf '%s\n' "$SCOOTER_JSON"

printf '\n== Основной путь: бронь -> старт -> пауза -> возврат -> оплата ==\n'
RIDE_JSON=$(curl -sS -X POST "$BASE/rides" -H 'Content-Type: application/json' -d "{\"user_id\":\"$USER_ID\",\"scooter_id\":\"$SCOOTER_ID\"}")
RIDE_ID=$(printf '%s' "$RIDE_JSON" | jq -r .id)
printf '%s\n' "$RIDE_JSON"
curl -sS -X POST "$BASE/rides/$RIDE_ID/start" -H 'Content-Type: application/json' -d '{}'; echo
curl -sS -X POST "$BASE/rides/$RIDE_ID/pause" -H 'Content-Type: application/json' -d '{}'; echo
curl -sS -X POST "$BASE/rides/$RIDE_ID/resume" -H 'Content-Type: application/json' -d '{}'; echo
curl -sS -X POST "$BASE/rides/$RIDE_ID/finish" -H 'Content-Type: application/json' -d '{"zone_allowed":true,"photo_received":true}'; echo
curl -sS -X POST "$BASE/rides/$RIDE_ID/pay" -H 'Content-Type: application/json' -d '{}'; echo
curl -sS "$BASE/rides/$RIDE_ID"; echo

printf '\n== Альтернативный путь: истечение бесплатной брони через 10 минут ==\n'
SCOOTER2=$(curl -sS -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"battery_percent":70}' | jq -r .id)
RIDE2=$(curl -sS -X POST "$BASE/rides" -H 'Content-Type: application/json' -d "{\"user_id\":\"$USER_ID\",\"scooter_id\":\"$SCOOTER2\"}")
RIDE2_ID=$(printf '%s' "$RIDE2" | jq -r .id)
EXPIRY=$(printf '%s' "$RIDE2" | jq -r .reservation_expires_at)
curl -sS -X POST "$BASE/internal/tick" -H 'Content-Type: application/json' -d "{\"now\":\"$EXPIRY\"}"; echo
curl -sS "$BASE/rides/$RIDE2_ID"; echo

printf '\n== Таймер более 12 часов: уведомить оператора, поездку не прерывать ==\n'
SCOOTER3=$(curl -sS -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"battery_percent":90}' | jq -r .id)
RIDE3=$(curl -sS -X POST "$BASE/rides" -H 'Content-Type: application/json' -d "{\"user_id\":\"$USER_ID\",\"scooter_id\":\"$SCOOTER3\"}")
RIDE3_ID=$(printf '%s' "$RIDE3" | jq -r .id)
curl -sS -X POST "$BASE/rides/$RIDE3_ID/start" -H 'Content-Type: application/json' -d '{}'; echo
STARTED=$(curl -sS "$BASE/rides/$RIDE3_ID" | jq -r .start_time)
TICK_AT=$(python -c 'from datetime import datetime,timedelta,timezone; import sys; d=datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")); print((d+timedelta(hours=12,seconds=1)).isoformat())' "$STARTED")
curl -sS -X POST "$BASE/internal/tick" -H 'Content-Type: application/json' -d "{\"now\":\"$TICK_AT\"}"; echo
curl -sS "$BASE/rides/$RIDE3_ID"; echo
curl -sS -X POST "$BASE/rides/$RIDE3_ID/operator-contact" -H 'Content-Type: application/json' -d '{}'; echo
printf '\n== 409: возобновление активной поездки без паузы; состояние до/после ==\n'
curl -sS "$BASE/rides/$RIDE3_ID"; echo
curl -sS -i -X POST "$BASE/rides/$RIDE3_ID/resume" -H 'Content-Type: application/json' -d '{}'
curl -sS "$BASE/rides/$RIDE3_ID"; echo

printf '\n== 409: недопустимое событие в состоянии reserved ==\n'
SCOOTER4=$(curl -sS -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"battery_percent":80}' | jq -r .id)
RIDE4=$(curl -sS -X POST "$BASE/rides" -H 'Content-Type: application/json' -d "{\"user_id\":\"$USER_ID\",\"scooter_id\":\"$SCOOTER4\"}")
RIDE4_ID=$(printf '%s' "$RIDE4" | jq -r .id)
curl -sS "$BASE/rides/$RIDE4_ID"; echo
curl -sS -i -X POST "$BASE/rides/$RIDE4_ID/pause" -H 'Content-Type: application/json' -d '{}'
curl -sS "$BASE/rides/$RIDE4_ID"; echo

printf '\n== 422: некорректные данные ==\n'
curl -sS -i -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"battery_percent":150}'

printf '\n== 404: несуществующая поездка ==\n'
curl -sS -i "$BASE/rides/no-such-id"

printf '\n== 409: повторное завершение поездки ==\n'
curl -sS -i -X POST "$BASE/rides/$RIDE_ID/finish" -H 'Content-Type: application/json' -d '{"zone_allowed":true,"photo_received":true}'
curl -sS -i -X POST "$BASE/rides/$RIDE_ID/pause" -H 'Content-Type: application/json' -d '{}'

printf '\n== 422: попытка перевести часы назад ==\n'
curl -sS -i -X POST "$BASE/internal/tick" -H 'Content-Type: application/json' -d "{\"now\":\"$STARTED\"}"

if [[ "${ENABLE_TEST_CONTROLS:-false}" == "true" ]]; then
  printf '\n== Альтернатива: неудачное списание ==\n'
  echo 'Перед следующим списанием заглушка будет настроена на отказ.'
  curl -sS -X POST "$BASE/internal/payment-outcome" -H 'Content-Type: application/json' -d '{"charge_succeeds":false}'; echo
  USER_FAIL=$(curl -sS -X POST "$BASE/users" -H 'Content-Type: application/json' -d '{"full_name":"Клиент с тестовым отказом"}' | jq -r .id)
  SCOOTER_FAIL=$(curl -sS -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"battery_percent":80}' | jq -r .id)
  RIDE_FAIL=$(curl -sS -X POST "$BASE/rides" -H 'Content-Type: application/json' -d "{\"user_id\":\"$USER_FAIL\",\"scooter_id\":\"$SCOOTER_FAIL\"}")
  RIDE_FAIL_ID=$(printf '%s' "$RIDE_FAIL" | jq -r .id)
  curl -sS -X POST "$BASE/rides/$RIDE_FAIL_ID/start" -H 'Content-Type: application/json' -d '{}'; echo
  curl -sS -X POST "$BASE/rides/$RIDE_FAIL_ID/finish" -H 'Content-Type: application/json' -d '{"zone_allowed":true,"photo_received":true}'; echo
  curl -sS -X POST "$BASE/rides/$RIDE_FAIL_ID/pay" -H 'Content-Type: application/json' -d '{}'; echo
  curl -sS "$BASE/users/$USER_FAIL"; echo
fi
