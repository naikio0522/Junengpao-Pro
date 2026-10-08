$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$requirements = Join-Path $PSScriptRoot "requirements.txt"

if (-not (Test-Path -LiteralPath $venvPython)) {
    py -3 -m venv (Join-Path $PSScriptRoot ".venv")
}

& $venvPython -m pip install -r $requirements

codex mcp get videomatrix *> $null
if ($LASTEXITCODE -eq 0) {
    codex mcp remove videomatrix
}

codex mcp add videomatrix `
    --env "PYTHONPATH=$projectRoot" `
    -- $venvPython -m agent_adapter.mcp_server

Write-Host "视频裂变器 MCP 已就绪。重启 Codex 后生效。"
