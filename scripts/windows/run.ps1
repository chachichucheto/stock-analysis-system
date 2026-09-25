# タスクスケジューラから呼ぶための入口。引数に assoc のサブコマンドを渡す。
#   powershell -ExecutionPolicy Bypass -File scripts\windows\run.ps1 collect
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Rest)
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root
$env:PYTHONUTF8 = "1"
$log = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $log | Out-Null
$stamp = Get-Date -Format "yyyyMMdd"
& .\.venv\Scripts\python.exe -m assoc @Rest *>> (Join-Path $log "assoc_$stamp.log")
exit $LASTEXITCODE
