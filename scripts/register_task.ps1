# Dang ky Windows Scheduled Task chay main.py theo lich (mac dinh: HANG NGAY 08:00 gio Dai Bac).
# Chay bang PowerShell voi quyen Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1                  # hang ngay 08:00 Dai Bac
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -TaipeiTime 07:30
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Frequency Weekly -Day Monday
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -DryRun          # chi in gio quy doi, KHONG dang ky
param(
    [ValidateSet("Daily", "Weekly")][string]$Frequency = "Daily",
    [string]$TaipeiTime = "08:00",
    [string]$Day = "Monday",          # chi dung khi -Frequency Weekly
    [string]$TaskName = "",           # mac dinh: MVDIS-DailyCheck hoac MVDIS-WeeklyCheck
    [switch]$DryRun
)

if (-not $TaskName) { $TaskName = "MVDIS-$Frequency" + "Check" }

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Khong tim thay $Python - hay tao venv truoc." }

# Quy doi gio Dai Bac -> gio may local (lay ngay gan nhat de xu ly dung viec lech ngay giua 2 mui gio)
$taipei = [TimeZoneInfo]::FindSystemTimeZoneById("Taipei Standard Time")
$today = [DateTime]::Today
if ($Frequency -eq "Weekly") {
    $daysAhead = (([int][DayOfWeek]::$Day) - [int]$today.DayOfWeek + 7) % 7
} else {
    $daysAhead = 0
}
$taipeiDt = [DateTime]::SpecifyKind($today.AddDays($daysAhead).Add([TimeSpan]::Parse($TaipeiTime)), "Unspecified")
$localDt = [TimeZoneInfo]::ConvertTime($taipeiDt, $taipei, [TimeZoneInfo]::Local)
$localTime = $localDt.ToString("HH:mm")

if ($Frequency -eq "Weekly") {
    Write-Host "Gio Dai Bac $Day $TaipeiTime = gio may $($localDt.DayOfWeek) $localTime ($([TimeZoneInfo]::Local.Id))"
} else {
    Write-Host "Gio Dai Bac moi ngay $TaipeiTime = gio may $localTime ($([TimeZoneInfo]::Local.Id))"
}
if ($DryRun) { Write-Host "[DryRun] Se dang ky task '$TaskName' ($Frequency). Khong thay doi gi tren he thong."; return }

$action = New-ScheduledTaskAction -Execute $Python -Argument "main.py" -WorkingDirectory $ProjectDir
if ($Frequency -eq "Weekly") {
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $localDt.DayOfWeek -At $localTime
} else {
    $trigger = New-ScheduledTaskTrigger -Daily -At $localTime
}
# Interactive: chi chay khi user dang co phien dang nhap (RDP/console), vi MVDIS chan trinh duyet headless
# nen Chrome can mot desktop that de mo cua so. Voi RDP hay bam Disconnect, dung Sign out.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Tra cuu vi pham giao thong MVDIS ($Frequency)" -Force
Write-Host "Da dang ky task '$TaskName'. Chay thu: Start-ScheduledTask -TaskName $TaskName"

# Neu truoc do da dang ky lich khac (vd Weekly) thi go bo de khong chay trung.
$other = if ($Frequency -eq "Daily") { "MVDIS-WeeklyCheck" } else { "MVDIS-DailyCheck" }
if (Get-ScheduledTask -TaskName $other -ErrorAction SilentlyContinue) {
    Write-Host "Luu y: van con task '$other'. Neu khong can, go bo bang: Unregister-ScheduledTask -TaskName $other -Confirm:`$false"
}
