# 初回セットアップ(Windows PowerShell)。リポジトリの直下で実行する:
#   powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "仮想環境を作成します(.venv)"
    python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m pip install -e .

if (-not (Test-Path "config\config.yaml")) {
    Copy-Item "config\config.example.yaml" "config\config.yaml"
    Write-Host "config\config.yaml を作成しました。株価DBの場所などを編集してください。"
}
& .\.venv\Scripts\python.exe -m assoc doctor
