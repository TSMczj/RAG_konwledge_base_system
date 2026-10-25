# 一键启动本地 RAG 知识库（Windows PowerShell）
# 用法：右键“使用 PowerShell 运行”，或在本目录执行 .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "未找到 .venv\Scripts\python.exe" -ForegroundColor Red
    Write-Host "请先按 README 创建虚拟环境并安装依赖："
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

Write-Host "启动 RAG 知识库：http://127.0.0.1:8000/"
Write-Host "按 Ctrl+C 停止。"
& $python -m app.main
