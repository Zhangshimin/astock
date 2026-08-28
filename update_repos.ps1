[CmdletBinding()]
param(
    [string]$ReposRoot = (Join-Path $PSScriptRoot "repos"),
    [string]$Remote = "origin",
    [switch]$FetchOnly,
    [switch]$IncludeDirty,
    [switch]$Prune
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "==== $Message ====" -ForegroundColor Cyan
}

function Get-GitCommand {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        throw "未检测到 git，请先安装 Git 并确保 git 已加入 PATH。"
    }
    return $git.Source
}

function Get-RepoDirectories {
    param([string]$Root)

    if (-not (Test-Path -LiteralPath $Root)) {
        throw "repos 目录不存在：$Root"
    }

    return Get-ChildItem -LiteralPath $Root -Directory | Sort-Object Name
}

function Invoke-Git {
    param(
        [string]$GitExe,
        [string[]]$Arguments
    )

    $output = & $GitExe @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    return [pscustomobject]@{
        Output = @($output)
        ExitCode = $exitCode
    }
}

$gitExe = Get-GitCommand
$repoDirs = Get-RepoDirectories -Root $ReposRoot

Write-Section "扫描仓库"
Write-Host "repos 根目录：$ReposRoot"
Write-Host "检测到子目录数量：$($repoDirs.Count)"

$results = New-Object System.Collections.Generic.List[object]

foreach ($repoDir in $repoDirs) {
    $repoName = $repoDir.Name
    $gitDir = Join-Path $repoDir.FullName ".git"

    if (-not (Test-Path -LiteralPath $gitDir)) {
        $results.Add([pscustomobject]@{
            Repo = $repoName
            Status = "skip"
            Reason = "不是 Git 仓库"
        })
        continue
    }

    Write-Section "处理 $repoName"

    $statusResult = Invoke-Git -GitExe $gitExe -Arguments @("-C", $repoDir.FullName, "status", "--short")
    if ($statusResult.ExitCode -ne 0) {
        $results.Add([pscustomobject]@{
            Repo = $repoName
            Status = "failed"
            Reason = (($statusResult.Output -join "`n").Trim())
        })
        Write-Host "读取状态失败" -ForegroundColor Red
        continue
    }

    $isDirty = $statusResult.Output.Count -gt 0
    if ($isDirty -and -not $IncludeDirty) {
        $results.Add([pscustomobject]@{
            Repo = $repoName
            Status = "skip"
            Reason = "工作区有未提交改动"
        })
        Write-Host "跳过：工作区有未提交改动" -ForegroundColor Yellow
        continue
    }

    $branchResult = Invoke-Git -GitExe $gitExe -Arguments @("-C", $repoDir.FullName, "branch", "--show-current")
    if ($branchResult.ExitCode -ne 0) {
        $results.Add([pscustomobject]@{
            Repo = $repoName
            Status = "failed"
            Reason = (($branchResult.Output -join "`n").Trim())
        })
        Write-Host "读取分支失败" -ForegroundColor Red
        continue
    }

    $branch = ($branchResult.Output -join "").Trim()
    if ([string]::IsNullOrWhiteSpace($branch)) {
        $results.Add([pscustomobject]@{
            Repo = $repoName
            Status = "skip"
            Reason = "当前处于 detached HEAD"
        })
        Write-Host "跳过：当前处于 detached HEAD" -ForegroundColor Yellow
        continue
    }

    $fetchArgs = @("-C", $repoDir.FullName, "fetch", $Remote)
    if ($Prune) {
        $fetchArgs += "--prune"
    }

    $fetchResult = Invoke-Git -GitExe $gitExe -Arguments $fetchArgs
    if ($fetchResult.ExitCode -ne 0) {
        $results.Add([pscustomobject]@{
            Repo = $repoName
            Status = "failed"
            Reason = (($fetchResult.Output -join "`n").Trim())
        })
        Write-Host "fetch 失败" -ForegroundColor Red
        continue
    }

    if ($FetchOnly) {
        $results.Add([pscustomobject]@{
            Repo = $repoName
            Status = "fetched"
            Reason = "仅执行 fetch"
        })
        Write-Host "已完成 fetch" -ForegroundColor Green
        continue
    }

    $pullResult = Invoke-Git -GitExe $gitExe -Arguments @("-C", $repoDir.FullName, "pull", "--ff-only", $Remote, $branch)
    if ($pullResult.ExitCode -ne 0) {
        $results.Add([pscustomobject]@{
            Repo = $repoName
            Status = "failed"
            Reason = (($pullResult.Output -join "`n").Trim())
        })
        Write-Host "pull 失败" -ForegroundColor Red
        continue
    }

    $results.Add([pscustomobject]@{
        Repo = $repoName
        Status = "updated"
        Reason = "已执行 pull --ff-only"
    })
    Write-Host "更新完成：$branch" -ForegroundColor Green
}

Write-Section "汇总"

$updatedCount = ($results | Where-Object { $_.Status -eq "updated" }).Count
$fetchedCount = ($results | Where-Object { $_.Status -eq "fetched" }).Count
$skipCount = ($results | Where-Object { $_.Status -eq "skip" }).Count
$failedCount = ($results | Where-Object { $_.Status -eq "failed" }).Count

Write-Host "updated : $updatedCount"
Write-Host "fetched : $fetchedCount"
Write-Host "skip    : $skipCount"
Write-Host "failed  : $failedCount"
Write-Host ""

$results | Sort-Object Repo | Format-Table -AutoSize

if ($failedCount -gt 0) {
    exit 1
}
