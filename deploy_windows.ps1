# NotabilityEditor 0.7.9-Beta - Windows one-click build and per-user install.
# ASCII-only source for reliable execution with Windows PowerShell 5.1.
# No administrator privileges, no global Python modifications.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Make CLI and Python subprocess output use the same UTF-8 code page.
# Keep this PS1 entirely ASCII for Windows PowerShell 5.1 compatibility.
try {
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [Console]::InputEncoding = $utf8NoBom
    [Console]::OutputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
} catch {
    Write-Host 'NOTICE: Could not switch console to UTF-8.'
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

$venvPath = Join-Path $root '.build-venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
$requirements = Join-Path $root 'requirements.txt'
$entryPoint = Join-Path $root 'main.py'
$runtimeHook = Join-Path $root 'runtime\ne_runtime_qt.py'
$icon = Join-Path $root 'assets\notability-viewer.ico'
$versionInfo = Join-Path $root 'windows_version_info.ver'
$distDir = Join-Path $root 'dist'
$workDir = Join-Path $root '.pyinstaller-build'
$exeName = 'NotabilityEditor-0.7.9-Beta.exe'
$buildExe = Join-Path $distDir $exeName
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\NotabilityEditor'
$installedExe = Join-Path $installDir 'NotabilityEditor.exe'
$logsDir = Join-Path $root 'deploy_logs'
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
$logPath = Join-Path $logsDir ('deploy-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$transcribing = $false

function Step([string]$message) {
    Write-Host ''
    Write-Host ('=== ' + $message + ' ===') -ForegroundColor Cyan
}
function Run([string]$exe, [string[]]$arguments) {
    Write-Host ("RUN: " + [IO.Path]::GetFileName($exe) + ' ' + ($arguments -join ' '))
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("Command failed with exit code " + $LASTEXITCODE + ': ' + $exe)
    }
}
function TestPython311([string]$exe, [string[]]$prefix) {
    try {
        $output = & $exe @prefix -c 'import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))' 2>$null
        return ($LASTEXITCODE -eq 0 -and ("$output".Trim() -eq '3.11'))
    } catch { return $false }
}
function TestExistingVenv {
    # Resume safely after a prior build failure: only reuse a complete 3.11 environment.
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) { return $false }
    try {
        $null = & $venvPython -c 'import onnxruntime; import sys, numpy, lxml, fitz, PyInstaller; from PyQt6.QtPdf import QPdfDocument; from PyQt6.QtSvgWidgets import QSvgWidget; import main; assert sys.version_info[:2] == (3, 11)' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
function FindPython311 {
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($launcher -and (TestPython311 $launcher.Source @('-3.11'))) {
        return @{Exe = $launcher.Source; Prefix = @('-3.11')}
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:ProgramFiles 'Python311\python.exe')
    )
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} 'Python311\python.exe')
    }
    $command = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if ((Test-Path -LiteralPath $candidate -PathType Leaf) -and (TestPython311 $candidate @())) {
            return @{Exe = $candidate; Prefix = @()}
        }
    }
    return $null
}
function FindUv {
    $command = Get-Command 'uv.exe' -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'),
        (Join-Path $root '.bootstrap\uv.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}
function DownloadUv {
    Step 'Installing local uv bootstrapper'
    $arch = ("$env:PROCESSOR_ARCHITECTURE").ToUpperInvariant()
    if ($arch -eq 'AMD64') { $triple = 'x86_64-pc-windows-msvc' }
    else { throw ('Unsupported Windows CPU architecture: ' + $arch + '. This release requires x64 (AMD64) Windows.') }
    $bootstrap = Join-Path $root '.bootstrap'
    New-Item -ItemType Directory -Path $bootstrap -Force | Out-Null
    $archive = Join-Path $bootstrap 'uv-download.zip'
    $extract = Join-Path $bootstrap 'uv-extracted'
    $url = 'https://github.com/astral-sh/uv/releases/latest/download/uv-' + $triple + '.zip'
    Write-Host ('Downloading official uv release from ' + $url)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing -MaximumRedirection 10
    if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
    Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
    $found = Get-ChildItem -LiteralPath $extract -Filter 'uv.exe' -Recurse -File | Select-Object -First 1
    if (-not $found) { throw 'Downloaded uv archive did not contain uv.exe.' }
    $destination = Join-Path $bootstrap 'uv.exe'
    Copy-Item -LiteralPath $found.FullName -Destination $destination -Force
    Remove-Item -LiteralPath $archive -Force
    return $destination
}
function MakeShortcut([string]$path) {
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $installedExe
    $shortcut.WorkingDirectory = $installDir
    $shortcut.IconLocation = $installedExe + ',0'
    $shortcut.Description = 'Open Notability NTB, NOTE and PDF files'
    $shortcut.Save()
}

try {
    try {
        Start-Transcript -Path $logPath -Append | Out-Null
        $transcribing = $true
    } catch {
        Write-Warning ('Unable to record deployment log: ' + $_.Exception.Message)
    }
    Step 'Checking source files'
    foreach ($file in @($requirements, $entryPoint, $runtimeHook, $icon, $versionInfo)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw ('Required source file not found: ' + $file + '. Extract the entire ZIP first.')
        }
    }
    if (-not [Environment]::Is64BitOperatingSystem -or ("$env:PROCESSOR_ARCHITECTURE").ToUpperInvariant() -ne 'AMD64') {
        throw 'This release requires x64 Windows (Intel/AMD 64-bit). The bundled PyQt6 6.5.0 has no native Windows ARM64 wheel.'
    }
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw 'LOCALAPPDATA is not set.' }

    Step 'Preparing isolated Python 3.11 environment'
    $reuseVenv = TestExistingVenv
    if ($reuseVenv) {
        Write-Host ('Reusing existing verified Python 3.11 environment: ' + $venvPath) -ForegroundColor Green
    } else {
        $uv = FindUv
        $system311 = FindPython311
        # Use installed 3.11 directly if uv is not installed. Python 3.14 alone is not sufficient.
        if ($uv) {
            Write-Host ('Using uv: ' + $uv)
            Run $uv @('python', 'install', '3.11')
            Run $uv @('venv', '--python', '3.11', '--clear', $venvPath)
            $installer = 'uv'
        } elseif ($system311) {
            Write-Host ('Using existing system Python 3.11: ' + $system311.Exe)
            if (Test-Path -LiteralPath $venvPath) { Remove-Item -LiteralPath $venvPath -Recurse -Force }
            $createArgs = @($system311.Prefix) + @('-m', 'venv', $venvPath)
            Run $system311.Exe $createArgs
            $installer = 'pip'
        } else {
            Write-Host 'No Python 3.11 found. Python 3.14 (if installed) will be left untouched.'
            $uv = DownloadUv
            Run $uv @('python', 'install', '3.11')
            Run $uv @('venv', '--python', '3.11', '--clear', $venvPath)
            $installer = 'uv'
        }
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw 'Could not create the virtual-environment Python interpreter.'
    }
    Run $venvPython @('-c', 'import sys; assert sys.version_info[:2] == (3, 11), sys.version')

    Step 'Installing application and build dependencies'
    if ($reuseVenv) {
        Write-Host 'Dependencies are already installed and verified. Skipping download.' -ForegroundColor Green
    } elseif ($installer -eq 'uv') {
        Run $uv @('pip', 'install', '--python', $venvPython, '--only-binary', ':all:', '-r', $requirements, 'pyinstaller==6.22.3')
    } else {
        Run $venvPython @('-m', 'ensurepip', '--upgrade')
        Run $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
        Run $venvPython @('-m', 'pip', 'install', '--only-binary=:all:', '-r', $requirements, 'pyinstaller==6.22.3')
    }
    Run $venvPython @('-c', 'import onnxruntime; import sys, numpy, lxml, fitz; from PyQt6.QtPdf import QPdfDocument; from PyQt6.QtSvgWidgets import QSvgWidget; import main')

    Step 'Checking installed and registered version priority'
    Run $venvPython @('-c', 'from platform_integration.ne_install import VERSION; from core.ne_version import ensure_registration_allowed; ensure_registration_allowed(VERSION)')

    # A running copy locks the EXE, causing PyInstaller WinError 5 at the final step.
    Step 'Checking for running Viewer or Editor processes'
    $runningEditors = @(Get-Process -Name 'NotabilityViewer*','NotabilityEditor*' -ErrorAction SilentlyContinue)
    if ($runningEditors.Count -gt 0) {
        Write-Host 'Save your notes and close Viewer and Editor before updating.' -ForegroundColor Yellow
        $runningEditors | Select-Object Id, ProcessName | Format-Table | Out-Host
        $null = Read-Host 'Press Enter after saving and closing the listed applications'
        $runningEditors = @(Get-Process -Name 'NotabilityViewer*','NotabilityEditor*' -ErrorAction SilentlyContinue)
        if ($runningEditors.Count -gt 0) { throw 'Applications are still running. Update cancelled without terminating them.' }
    }

    Step 'Building standalone Windows EXE'
    $assetsDir = Join-Path $root 'assets'
    if (-not (Test-Path -LiteralPath $assetsDir -PathType Container)) {
        throw ('Asset folder missing: ' + $assetsDir)
    }
    foreach ($filename in @('notability-viewer.ico', 'notability-viewer.svg', 'chevron-down.svg', 'chevron-up.svg')) {
        if (-not (Test-Path -LiteralPath (Join-Path $assetsDir $filename) -PathType Leaf)) {
            throw ('Required asset missing: ' + (Join-Path $assetsDir $filename))
        }
    }
    if (-not (Test-Path -LiteralPath $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }
    # Use ABSOLUTE source path: --specpath is a different directory, so relative assets would fail.
    $dataMapping = $assetsDir + ';assets'
    Run $venvPython @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed', '--noupx',
        '--name', 'NotabilityEditor-0.7.9-Beta', '--icon', $icon, '--version-file', $versionInfo,
        '--add-data', $dataMapping, '--runtime-hook', $runtimeHook,
        '--distpath', $distDir, '--workpath', $workDir, '--specpath', $workDir,
        $entryPoint
    )
    if (-not (Test-Path -LiteralPath $buildExe -PathType Leaf)) {
        throw ('PyInstaller exited but the expected EXE was not found: ' + $buildExe)
    }

    Run $venvPython @('-c', 'from platform_integration.ne_install import VERSION; from core.ne_version import ensure_registration_allowed; ensure_registration_allowed(VERSION)')
    Step 'Installing for the current Windows user'
    Run $venvPython @('-c', 'import sys; from platform_integration.ne_install import install_application; from platform_integration.ne_migrate import update_shortcuts; target = install_application(sys.argv[1]); update_shortcuts(target, None)', $buildExe)
    # Confirm replacement really installed the newly built bytes rather than
    # leaving a stale copy under a previous 0.7.2-Beta release.
    $expectedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $buildExe).Hash
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedExe).Hash
    if ($actualHash -ne $expectedHash) {
        throw 'Installed EXE SHA256 mismatch. Old app may still be running or installation was blocked.'
    }
    Write-Host 'Build marker: 0.7.9-Beta' -ForegroundColor Green
    Write-Host ('Verified installed EXE SHA256: ' + $actualHash) -ForegroundColor Green
    Write-Host ('Installed: ' + $installedExe) -ForegroundColor Green

    Step 'Done'
    Write-Host ('Build output: ' + $buildExe)
    Write-Host ('Installed EXE: ' + $installedExe)
    Write-Host 'To change .ntb defaults, use the app menu: Tools > Default open program.'
    Write-Host ('Deployment log: ' + $logPath)
    try {
        Start-Process -FilePath $installedExe -WorkingDirectory $installDir
        Write-Host 'Application launched.' -ForegroundColor Green
    } catch {
        Write-Warning ('Application installed, but auto-launch failed: ' + $_.Exception.Message)
    }
} catch {
    Write-Host ''
    Write-Host ('DEPLOYMENT FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('Log: ' + $logPath)
    exit 1
} finally {
    if ($transcribing) { try { Stop-Transcript | Out-Null } catch {} }
}
