param([string]$Python = '', [int]$Port = 8000, [string]$JournalPath = '')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $repoRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Python missing; follow docs/deployment.md installation.' }
if (-not $env:QUANTLAB_API_OPERATOR_TOKEN) { throw 'Inject QUANTLAB_API_OPERATOR_TOKEN securely before startup.' }
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be 1024-65535.' }
$env:QUANTLAB_API_PORT = [string]$Port
$env:QUANTLAB_API_FRONTEND_PATH = Join-Path $repoRoot 'frontend\dist'
if ($JournalPath) { $env:QUANTLAB_API_JOURNAL_PATH = [IO.Path]::GetFullPath($JournalPath) }
# Foreground ownership: Ctrl+C invokes Uvicorn lifespan shutdown and drains work.
& $Python -m quantlab.api
if ($LASTEXITCODE -ne 0) { throw 'Local server failed; check configuration, build integrity, port and trusted storage.' }
