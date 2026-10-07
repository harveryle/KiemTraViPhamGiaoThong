# Dang ky Windows Scheduled Task chay main.py moi thu Hai 08:00 gio Dai Bac.
# Chay bang PowerShell voi quyen Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
param(
    [string]$TaskName = "MVDIS-WeeklyCheck",
    [string]$TaipeiTime = "08:00",
    [string]$Day = "Monday"
)

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Khong tim thay $Python - hay tao venv truoc." }

# Quy doi gio Dai Bac -> gio may local
$taipei = [TimeZoneInfo]::FindSystemTimeZoneById("Taipei Standard Time")
$today = [DateTime]::Today
$daysAhead = (([int][DayOfWeek]::$Day) - [int]$today.DayOfWeek + 7) % 7
$taipeiDt = [DateTime]::SpecifyKind($today.AddDays($daysAhead).Add([TimeSpan]::Parse($TaipeiTime)), "Unspecified")
$localDt = [TimeZoneInfo]::ConvertTime($taipeiDt, $taipei, [TimeZoneInfo]::Local)
Write-Host "Gio Dai Bac $Day $TaipeiTime = gio may $($localDt.DayOfWeek) $($localDt.ToString('HH:mm')) ($([TimeZoneInfo]::Local.Id))"

$action = New-ScheduledTaskAction -Execute $Python -Argument "main.py" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $localDt.DayOfWeek -At $localDt.ToString("HH:mm")
# Interactive: chi chay khi user dang co phien dang nhap (RDP/console), vi MVDIS chan trinh duyet headless
# nen Chrome can mot desktop that de mo cua so.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Tra cuu vi pham giao thong MVDIS hang tuan" -Force
Write-Host "Da dang ky task '$TaskName'. Chay thu: Start-ScheduledTask -TaskName $TaskName"
