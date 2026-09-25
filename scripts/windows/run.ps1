# タスクスケジューラから呼ぶための入口。引数に assoc のサブコマンドを渡す。
#   powershell -ExecutionPolicy Bypass -File scripts\windows\run.ps1 collect
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Rest)
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8   # ログの日本語が文字化けしないように
$log = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $log | Out-Null
$stamp = Get-Date -Format "yyyyMMdd"
$logfile = Join-Path $log "assoc_$stamp.log"
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') assoc $Rest" | Out-File -FilePath $logfile -Append -Encoding utf8
& .\.venv\Scripts\python.exe -m assoc @Rest 2>&1 | Out-File -FilePath $logfile -Append -Encoding utf8
exit $LASTEXITCODE
