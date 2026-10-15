# 在 Windows 上注册计划任务，每天定时跑日报。
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows_task.ps1 -Time 23:00
#   powershell -ExecutionPolicy Bypass -File scripts\windows_task.ps1 -Uninstall
#
# 需要先装好 Python 并确保脚本里的 python.exe 能跑起来。

param(
    [string]$Time = "08:00",
    [string]$TaskName = "SemiconductorNewsDigest",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectDir "logs"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已删除计划任务：$TaskName"
    exit 0
}

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "找不到 python，请先安装 Python 3.9+ 并加入 PATH" }

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "run.py" `
    -WorkingDirectory $ProjectDir

$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "半导体新闻日报自动生成与推送" -Force | Out-Null

Write-Host "已注册计划任务：$TaskName（每天 $Time）"
Write-Host "项目目录：$ProjectDir"
Write-Host "手动测试：python run.py --dry-run"
Write-Host "查看任务：Get-ScheduledTask -TaskName $TaskName"
