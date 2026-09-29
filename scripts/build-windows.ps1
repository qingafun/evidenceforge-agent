[CmdletBinding()]
param(
    [string]$Python = '',
    [switch]$OneDir,
    [switch]$SkipInstall,
    [switch]$SkipSmokeCheck
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This script builds Windows x64 executables and must run on Windows.'
}

$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$buildEnvironment = Join-Path $projectRoot '.venv-build'
$buildPython = Join-Path $buildEnvironment 'Scripts\python.exe'
$buildRoot = Join-Path $projectRoot 'build'
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logDirectory = Join-Path $buildRoot 'logs'
$logFile = Join-Path $logDirectory "desktop-$timestamp.log"
$previousLocation = Get-Location
$previousEncoding = [Console]::OutputEncoding
$previousPythonEncoding = $env:PYTHONIOENCODING
$previousPythonUtf8 = $env:PYTHONUTF8
$previousBuildMode = $env:EF_BUILD_ONEDIR
$transcriptStarted = $false
$exitCode = 0

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    # Windows PowerShell treats native stderr as ErrorRecord; PyInstaller logs
    # progress there. Decide success by its exit code, while retaining every line.
    $previousPreference = $ErrorActionPreference
    $nativeExitCode = 0
    try {
        $ErrorActionPreference = 'Continue'
        & $Program @Arguments 2>&1 | ForEach-Object { Write-Host $_.ToString() }
        $nativeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($nativeExitCode -ne 0) {
        throw "Command failed (exit $nativeExitCode): $Program $($Arguments -join ' ')"
    }
}

function Find-BuildPython {
    if ($Python) {
        return (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
    }
    $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('-3.12', '-3.11')) {
            $previousPreference = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            $candidate = @(& $launcher.Source $version -c 'import sys; print(sys.executable)' 2>$null)
            $ErrorActionPreference = $previousPreference
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                return [string]$candidate[-1]
            }
        }
    }
    $fallback = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($fallback) { return $fallback.Source }
    throw 'Install Python 3.12 x64 (or 3.11 x64), or pass -Python C:\path\python.exe.'
}

try {
    Set-Location -LiteralPath $projectRoot
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $env:EF_BUILD_ONEDIR = if ($OneDir) { '1' } else { '0' }
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    Start-Transcript -LiteralPath $logFile -Force | Out-Null
    $transcriptStarted = $true
    Write-Host 'EvidenceForge desktop build (Windows x64)'
    Write-Host "Project: $projectRoot"

    if (-not $SkipInstall) {
        $sourcePython = Find-BuildPython
        Invoke-Checked $sourcePython @('-c', 'import sys; assert sys.version_info[:2] in ((3,11),(3,12)) and sys.maxsize>2**32; print(sys.version)')
        # The build environment is disposable. Verify its exact destination before
        # asking venv to clear it, including the reparse-point case on Windows.
        $expectedEnvironment = [IO.Path]::GetFullPath((Join-Path $projectRoot '.venv-build'))
        if ([IO.Path]::GetFullPath($buildEnvironment) -ne $expectedEnvironment) {
            throw 'Unexpected build environment path.'
        }
        if ([IO.Path]::GetFullPath($sourcePython).StartsWith($expectedEnvironment + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Pass a Python outside .venv-build, or use -SkipInstall.'
        }
        if (Test-Path -LiteralPath $buildEnvironment) {
            $environmentItem = Get-Item -LiteralPath $buildEnvironment -Force
            if (($environmentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw '.venv-build must be a real directory, not a junction or symbolic link.'
            }
            if ((Resolve-Path -LiteralPath $buildEnvironment).Path -ne $expectedEnvironment) {
                throw 'Build environment resolved outside its expected location.'
            }
        }
        Write-Host 'Creating an isolated, clean .venv-build environment...'
        Invoke-Checked $sourcePython @('-m', 'venv', '--clear', $buildEnvironment)
        Invoke-Checked $buildPython @('-m', 'pip', 'install', '--disable-pip-version-check', 'setuptools==84.0.0')
        Invoke-Checked $buildPython @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-build-isolation', '-r', 'requirements-lock.txt', '-r', 'requirements-desktop.txt')
    }
    elseif (-not (Test-Path -LiteralPath $buildPython -PathType Leaf)) {
        throw '-SkipInstall needs an existing .venv-build environment. Run once without this switch.'
    }

    Invoke-Checked $buildPython @('-c', 'import sys; assert sys.version_info[:2] in ((3,11),(3,12)) and sys.maxsize>2**32')
    # Install the current checkout, including uncommitted application changes.
    Invoke-Checked $buildPython @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '--no-build-isolation', '-e', '.')
    Invoke-Checked $buildPython @('-m', 'pip', 'check')
    Invoke-Checked $buildPython @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', (Join-Path $projectRoot 'dist'), '--workpath', (Join-Path $buildRoot 'pyinstaller'), 'EvidenceForge.spec')

    $executable = if ($OneDir) {
        Join-Path $projectRoot 'dist\EvidenceForge\EvidenceForge.exe'
    } else {
        Join-Path $projectRoot 'dist\EvidenceForge.exe'
    }
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Build returned success but no executable was created: $executable"
    }

    if (-not $SkipSmokeCheck) {
        $checkDirectory = Join-Path $buildRoot "desktop-check\$timestamp"
        $checkResult = Join-Path $checkDirectory 'self-test.json'
        $checkData = Join-Path $checkDirectory 'isolated-data'
        New-Item -ItemType Directory -Force -Path $checkDirectory | Out-Null
        Write-Host 'Checking the packaged backend, resources, settings and demo workflow...'
        $checkArguments = '--self-test "{0}" --data-dir "{1}"' -f $checkResult, $checkData
        $checkProcess = Start-Process -FilePath $executable -ArgumentList $checkArguments -WindowStyle Hidden -PassThru
        if (-not $checkProcess.WaitForExit(120000)) {
            Stop-Process -Id $checkProcess.Id -ErrorAction SilentlyContinue
            throw "Packaged self-test timed out. Inspect $checkDirectory"
        }
        if ($checkProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $checkResult)) {
            throw "Packaged self-test failed (exit $($checkProcess.ExitCode)). Inspect $checkDirectory"
        }
        $result = Get-Content -LiteralPath $checkResult -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $result.passed) { throw "Packaged self-test did not pass. Inspect $checkResult" }
        Write-Host "Self-test passed: $checkResult"
    }
    else {
        Write-Warning 'Self-test skipped; verify the executable before distributing it.'
    }

    $hash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$executable.sha256", "$hash  EvidenceForge.exe`r`n", [Text.UTF8Encoding]::new($false))
    Write-Host "Built: $executable"
    Write-Host "SHA256: $hash"
    if ($OneDir) { Write-Host 'Distribute the entire dist\EvidenceForge directory, including _internal.' }
    Write-Host "Build log: $logFile"
}
catch {
    Write-Host "Build failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Build log: $logFile"
    $exitCode = 1
}
finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    $env:PYTHONIOENCODING = $previousPythonEncoding
    $env:PYTHONUTF8 = $previousPythonUtf8
    $env:EF_BUILD_ONEDIR = $previousBuildMode
    [Console]::OutputEncoding = $previousEncoding
    Set-Location -LiteralPath $previousLocation.Path
}
exit $exitCode
