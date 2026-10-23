$ErrorActionPreference = 'Stop'
$Base = 'http://localhost:8000'
$Json = 'application/json'

function Write-Body($Value) {
    $Value | ConvertTo-Json -Depth 20
}
function Post-Json([string]$Path, [object]$Body) {
    $Payload = $Body | ConvertTo-Json -Depth 20 -Compress
    $Response = Invoke-WebRequest -Uri ($Base + $Path) -Method Post -ContentType $Json -Body $Payload -UseBasicParsing
    Write-Host ("HTTP {0} POST {1}" -f [int]$Response.StatusCode, $Path) -ForegroundColor Green
    return ($Response.Content | ConvertFrom-Json)
}
function Get-Json([string]$Path) {
    $Response = Invoke-WebRequest -Uri ($Base + $Path) -Method Get -UseBasicParsing
    Write-Host ("HTTP {0} GET {1}" -f [int]$Response.StatusCode, $Path) -ForegroundColor Green
    return ($Response.Content | ConvertFrom-Json)
}
function Show-Curl([string[]]$Arguments) {
    & curl.exe @Arguments
}

Write-Host "`n== 1. Health check ==" -ForegroundColor Cyan
Show-Curl @('-sS','-i',"$Base/health")

Write-Host "`n== 2. Основной путь: бронь -> старт -> пауза -> возобновление -> завершение -> оплата ==" -ForegroundColor Cyan
$User = Post-Json '/users' @{ full_name = 'Иван Петров' }
Write-Body $User
$Scooter = Post-Json '/scooters' @{ battery_percent = 80 }
Write-Body $Scooter
$Ride = Post-Json '/rides' @{ user_id = $User.id; scooter_id = $Scooter.id }
Write-Body $Ride
Write-Body (Post-Json "/rides/$($Ride.id)/start" @{})
Write-Body (Post-Json "/rides/$($Ride.id)/pause" @{})
Write-Body (Post-Json "/rides/$($Ride.id)/resume" @{})
Write-Body (Post-Json "/rides/$($Ride.id)/finish" @{ zone_allowed = $true; photo_received = $true })
Write-Body (Post-Json "/rides/$($Ride.id)/pay" @{})
Write-Body (Get-Json "/rides/$($Ride.id)")

Write-Host "`n== 3. Альтернативный путь: бесплатная бронь истекает через 10 минут ==" -ForegroundColor Cyan
$Scooter2 = Post-Json '/scooters' @{ battery_percent = 70 }
$Ride2 = Post-Json '/rides' @{ user_id = $User.id; scooter_id = $Scooter2.id }
Write-Body (Post-Json '/internal/tick' @{ now = $Ride2.reservation_expires_at })
Write-Body (Get-Json "/rides/$($Ride2.id)")

Write-Host "`n== 4. Таймер более 12 часов: оператор уведомлён, поездка не прерывается ==" -ForegroundColor Cyan
$Scooter3 = Post-Json '/scooters' @{ battery_percent = 90 }
$Ride3 = Post-Json '/rides' @{ user_id = $User.id; scooter_id = $Scooter3.id }
$StartedRide3 = Post-Json "/rides/$($Ride3.id)/start" @{}
$StartTime = (Get-Json "/rides/$($Ride3.id)").start_time
$TickAt = ([DateTimeOffset]::Parse($StartTime)).AddHours(12).AddSeconds(1).ToString('yyyy-MM-ddTHH:mm:ssK')
Write-Body (Post-Json '/internal/tick' @{ now = $TickAt })
Write-Body (Get-Json "/rides/$($Ride3.id)")
Write-Body (Post-Json "/rides/$($Ride3.id)/operator-contact" @{})

Write-Host "`n== 5. Недопустимое событие: resume активной поездки -> ожидается 409 ==" -ForegroundColor Cyan
Show-Curl @('-sS','-i','-X','POST',"$Base/rides/$($Ride3.id)/resume",'-H','Content-Type: application/json','-d','{}')
Write-Body (Get-Json "/rides/$($Ride3.id)")

Write-Host "`n== 6. Недопустимое событие: pause брони до старта -> ожидается 409 ==" -ForegroundColor Cyan
$Scooter4 = Post-Json '/scooters' @{ battery_percent = 80 }
$Ride4 = Post-Json '/rides' @{ user_id = $User.id; scooter_id = $Scooter4.id }
Write-Body (Get-Json "/rides/$($Ride4.id)")
Show-Curl @('-sS','-i','-X','POST',"$Base/rides/$($Ride4.id)/pause",'-H','Content-Type: application/json','-d','{}')
Write-Body (Get-Json "/rides/$($Ride4.id)")

Write-Host "`n== 7. Некорректный уровень заряда -> ожидается 422 ==" -ForegroundColor Cyan
Show-Curl @('-sS','-i','-X','POST',"$Base/scooters",'-H','Content-Type: application/json','-d','{"battery_percent":150}')

Write-Host "`n== 8. Несуществующая поездка -> ожидается 404 ==" -ForegroundColor Cyan
Show-Curl @('-sS','-i',"$Base/rides/no-such-id")

Write-Host "`n== 9. Повторное завершение уже оплаченной поездки -> ожидается 409 ==" -ForegroundColor Cyan
Show-Curl @('-sS','-i','-X','POST',"$Base/rides/$($Ride.id)/finish",'-H','Content-Type: application/json','-d','{"zone_allowed":true,"photo_received":true}')

Write-Host "`n== 10. Попытка перевести часы назад -> ожидается 422 ==" -ForegroundColor Cyan
Show-Curl @('-sS','-i','-X','POST',"$Base/internal/tick",'-H','Content-Type: application/json','-d',("{""now"":""$StartTime""}"))

Write-Host "`nСценарии выполнены. Сохраните вывод терминала/скриншоты для отчёта." -ForegroundColor Cyan
