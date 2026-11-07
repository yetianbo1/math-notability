param([switch]$Quiet, [switch]$RemoveSettings)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
function Notice([string]$message) {
    if (-not $Quiet) { [System.Windows.Forms.MessageBox]::Show($message, 'NotabilityEditor') | Out-Null }
}
try {
    $expected = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\NotabilityEditor'))
    $actual = [IO.Path]::GetFullPath($PSScriptRoot)
    if ($actual -ne $expected) { throw '卸载目录与注册目录不一致。' }
    $item = Get-Item -LiteralPath $actual
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '拒绝通过目录联接卸载。' }
        $item = $item.Parent
    }
    $marker = Join-Path $actual 'installation.json'
    $exe = Join-Path $actual 'NotabilityEditor.exe'
    $scriptFile = Join-Path $actual 'uninstall.ps1'
    foreach ($file in @($marker, $exe, $scriptFile)) {
        if ((Test-Path -LiteralPath $file) -and ((Get-Item -LiteralPath $file).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw '拒绝删除链接文件。'
        }
    }
    $metadata = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($metadata.app_id -ne 'NotabilityEditor.Desktop') { throw '无法验证安装信息。' }
    if (-not $Quiet) {
        $form = New-Object System.Windows.Forms.Form
        $form.Text = '卸载 NotabilityEditor'
        $form.ClientSize = New-Object System.Drawing.Size(440, 180)
        $form.StartPosition = 'CenterScreen'
        $form.FormBorderStyle = 'FixedDialog'
        $form.MaximizeBox = $false
        $form.MinimizeBox = $false
        $label = New-Object System.Windows.Forms.Label
        $label.Text = "卸载程序，始终保留笔记。`n请先保存并关闭已安装的程序。"
        $label.SetBounds(20, 20, 400, 45)
        $check = New-Object System.Windows.Forms.CheckBox
        $check.FlatStyle = 'System'
        $check.Text = '删除应用设置（颜色、快捷键、GPU 等偏好）'
        $check.Checked = [bool]$RemoveSettings
        $check.SetBounds(20, 75, 400, 30)
        $ok = New-Object System.Windows.Forms.Button
        $ok.Text = '卸载'
        $ok.DialogResult = 'OK'
        $ok.SetBounds(230, 130, 90, 30)
        $cancel = New-Object System.Windows.Forms.Button
        $cancel.Text = '取消'
        $cancel.DialogResult = 'Cancel'
        $cancel.SetBounds(330, 130, 90, 30)
        $form.Controls.AddRange(@($label, $check, $ok, $cancel))
        $form.AcceptButton = $ok
        $form.CancelButton = $cancel
        $answer = $form.ShowDialog()
        $RemoveSettings = [bool]$check.Checked
        $form.Dispose()
        if ($answer -ne 'OK') { exit 0 }
    }
    # A running application may contain unsaved notes. Never terminate it.
    if (Test-Path -LiteralPath $exe) {
        try {
            $handle = [IO.File]::Open($exe, 'Open', 'ReadWrite', 'None')
            $handle.Close()
            Remove-Item -LiteralPath $exe -Force
        } catch { throw '程序仍在运行或文件被占用。请保存并关闭已安装的程序，再次卸载。' }
    }
    $cu = [Microsoft.Win32.Registry]::CurrentUser
    $command = '"' + $exe + '" "%1"'
    $application = 'Software\Classes\Applications\NotabilityEditor.exe'
    $key = $cu.OpenSubKey($application + '\shell\open\command')
    $ownsAssociation = $null -ne $key -and $key.GetValue('') -eq $command
    if ($null -ne $key) { $key.Close() }
    # A subsequently registered portable copy may own these associations now.
    if ($ownsAssociation) {
        $cu.DeleteSubKeyTree($application, $false)
        $applications = $cu.OpenSubKey('Software\Classes\Applications')
        if ($null -ne $applications) {
            $aliases = @($applications.GetSubKeyNames())
            $applications.Close()
            foreach ($alias in $aliases) {
                if ($alias -notmatch '^Notability(?:Viewer|Editor)(?:[-_. ].*)?\.exe$') { continue }
                $aliasPath = 'Software\Classes\Applications\' + $alias
                $key = $cu.OpenSubKey($aliasPath)
                $compatible = $null -ne $key -and $key.GetValue('NotabilityViewerCompatibility') -eq '1'
                if ($null -ne $key) { $key.Close() }
                $key = $cu.OpenSubKey($aliasPath + '\shell\open\command')
                $owned = $null -ne $key -and $key.GetValue('') -eq $command
                if ($null -ne $key) { $key.Close() }
                if ($compatible -and $owned) { $cu.DeleteSubKeyTree($aliasPath, $false) }
            }
        }
        foreach ($pair in @(@('.ntb','NotabilityViewer.NTB'), @('.note','NotabilityViewer.NOTE'))) {
            $progid = 'Software\Classes\' + $pair[1]
            $key = $cu.OpenSubKey($progid + '\shell\open\command')
            $ownsProgid = $null -ne $key -and $key.GetValue('') -eq $command
            if ($null -ne $key) { $key.Close() }
            if ($ownsProgid) {
                $extensionKey = 'Software\Classes\' + $pair[0]
                $backupPath = 'Software\NotabilityViewer\AssociationBackup\' + $pair[0]
                $extension = $cu.OpenSubKey($extensionKey, $true)
                $backup = $cu.OpenSubKey($backupPath)
                if ($null -ne $extension -and $extension.GetValue('') -eq $pair[1]) {
                    if ($null -ne $backup -and $backup.GetValue('saved') -eq '1' -and $backup.GetValue('present') -eq '1') {
                        $previous = [string]$backup.GetValue('previous', '')
                        if ($previous -ne $pair[1]) { $extension.SetValue('', $previous) }
                        else { $extension.DeleteValue('', $false) }
                    } else { $extension.DeleteValue('', $false) }
                }
                if ($null -ne $extension) { $extension.Close() }
                if ($null -ne $backup) { $backup.Close() }
                $cu.DeleteSubKeyTree($backupPath, $false)
                $cu.DeleteSubKeyTree($progid, $false)
                $key = $cu.OpenSubKey(('Software\Classes\' + $pair[0] + '\OpenWithProgids'), $true)
                if ($null -ne $key) { $key.DeleteValue($pair[1], $false); $key.Close() }
            }
            $key = $cu.OpenSubKey(('Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\' + $pair[0] + '\OpenWithList'), $true)
            if ($null -ne $key) {
                $removed = @()
                foreach ($name in $key.GetValueNames()) {
                    if ($name -ne 'MRUList' -and $key.GetValue($name) -eq 'NotabilityEditor.exe') {
                        $key.DeleteValue($name, $false); $removed += $name
                    }
                }
                $mru = [string]$key.GetValue('MRUList', '')
                foreach ($name in $removed) { $mru = $mru.Replace($name, '') }
                $key.SetValue('MRUList', $mru); $key.Close()
            }
        }
        $cu.DeleteSubKeyTree('Software\NotabilityEditor\Capabilities', $false)
        $compatibility = $cu.OpenSubKey('Software\NotabilityViewer\Capabilities')
        $ownsCompatibility = $null -ne $compatibility -and $compatibility.GetValue('CompatibilityTarget') -eq $exe
        if ($null -ne $compatibility) { $compatibility.Close() }
        if ($ownsCompatibility) { $cu.DeleteSubKeyTree('Software\NotabilityViewer\Capabilities', $false) }
        $key = $cu.OpenSubKey('Software\RegisteredApplications', $true)
        if ($null -ne $key) { if ($key.GetValue('NotabilityEditor') -eq 'Software\NotabilityEditor\Capabilities') { $key.DeleteValue('NotabilityEditor', $false) }; $key.Close() }
    }
    # Remove only shortcuts which still point to this installation.
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        foreach ($shortcutName in @('NotabilityEditor.lnk', 'Notability Viewer.lnk')) {
            $link = Join-Path $folder $shortcutName
            if ((Test-Path -LiteralPath $link) -and $shell.CreateShortcut($link).TargetPath -eq $exe) {
                Remove-Item -LiteralPath $link -Force
            }
        }
    }
    $uninstall = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\NotabilityEditor'
    $key = $cu.OpenSubKey($uninstall)
    $ownsEntry = $null -ne $key -and $key.GetValue('InstallLocation') -eq $actual
    if ($null -ne $key) { $key.Close() }
    if ($ownsEntry) { $cu.DeleteSubKeyTree($uninstall, $false) }
    if ($RemoveSettings) {
        # Exact QSettings subkeys only. Never delete user-selected note paths.
        $cu.DeleteSubKeyTree('Software\NotabilityEditor\NotabilityEditor', $false)
        $cu.DeleteSubKeyTree('Software\NotabilityEditor\Display', $false)
        $cu.DeleteSubKeyTree('Software\NotabilityViewer\NotabilityViewer', $false)
        $cu.DeleteSubKeyTree('Software\NotabilityViewer\Display', $false)
        $cu.DeleteSubKeyTree('Software\Notability Viewer\Notability Viewer', $false)
    }
    Remove-Item -LiteralPath $marker -Force
    Remove-Item -LiteralPath $scriptFile -Force
    # Never recurse: notes or other files placed in this folder must survive.
    if (@(Get-ChildItem -LiteralPath $actual -Force).Count -eq 0) { Remove-Item -LiteralPath $actual }
    if ($RemoveSettings) {
        Notice '卸载完成，应用设置已删除。笔记和日志已保留。下载的便携版副本仍可单独运行。'
    } else {
        Notice '卸载完成。笔记、应用设置和日志已保留。下载的便携版副本仍可单独运行。'
    }
    exit 0
} catch {
    Notice ('卸载未完成：' + $_.Exception.Message)
    exit 1
}
