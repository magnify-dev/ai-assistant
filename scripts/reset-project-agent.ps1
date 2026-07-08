# Reset exploration artifacts on a target project (keeps cheatsheet, specs, env).
param(
    [Parameter(Mandatory = $true)]
    [string]$Project,
    [switch]$DryRun
)

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "voice\.venv\Scripts\python.exe"
$Args = @("-m", "ui_test.reset_agent", "--project", $Project)
if ($DryRun) { $Args += "--dry-run" }

& $Python @Args
