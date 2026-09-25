# 毎日の自動実行をタスクスケジューラに登録する(docs/DESIGN.md §9.1)。
#   powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1
# 登録の解除:  powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -Remove
# 収集だけ先に始める: powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1 -CollectOnly
param([switch] $Remove, [switch] $CollectOnly)
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
$runner = Join-Path $root "scripts\windows\run.ps1"
$ps = "powershell.exe"

$tasks = @(
    @{ Name = "assoc_collect";  Args = "collect --sources rss,google_news,tdnet";  Trigger = (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)) },
    @{ Name = "assoc_daily";    Args = "collect --sources edinet,universe,calendar,gdelt,wikipedia"; Trigger = (New-ScheduledTaskTrigger -Daily -At "17:30") },
    @{ Name = "assoc_prepare";  Args = "prepare";  Trigger = (New-ScheduledTaskTrigger -Daily -At "18:00") },
    @{ Name = "assoc_morning";  Args = "morning";  Trigger = (New-ScheduledTaskTrigger -Daily -At "07:30") },
    @{ Name = "assoc_nextday";  Args = "nextday";  Trigger = (New-ScheduledTaskTrigger -Daily -At "16:00") },
    @{ Name = "assoc_backup";   Args = "backup";   Trigger = (New-ScheduledTaskTrigger -Daily -At "23:30") }
)

if ($CollectOnly) { $tasks = $tasks | Where-Object { $_.Name -in @("assoc_collect", "assoc_daily", "assoc_backup") } }

foreach ($t in $tasks) {
    if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
    }
    if ($Remove) { Write-Host "解除: $($t.Name)"; continue }
    $action = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" $($t.Args)" -WorkingDirectory $root
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $t.Trigger -Settings $settings | Out-Null
    Write-Host "登録: $($t.Name)($($t.Args))"
}
