# watch_conhost_flashes.ps1 — CMD 黑框闪烁客观检测器(测试工具, 非运行期组件)
# 原理: 任何控制台进程(含一闪而过的 cmd/adb 窗口)都会伴随 conhost.exe 的创建。
#       轮询 Win32_Process 记录新出现的 conhost PID(含父进程名), 即为"闪窗次数"。
# 用法: powershell -NoProfile -NonInteractive -WindowStyle Hidden -File watch_conhost_flashes.ps1 -Minutes 10 -OutFile c:\temp\flash_result.txt
param(
    [int]$Minutes = 10,
    [string]$OutFile = "flash_result.txt"
)

# 应用侧可能产生 conhost 的进程(被测对象)。
# 只有这些进程创建的控制台才算"应用闪窗"; 其余(如 docker/bash/codex
# 等环境进程)记为噪声, 单独计数, 不污染结论。
$appSide = @("adb.exe", "cmd.exe", "powershell.exe")

$baseline = @{}
Get-CimInstance Win32_Process -Filter "Name='conhost.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object { $baseline[[int]$_.ProcessId] = $true }

$appFlashes = New-Object System.Collections.ArrayList
$noise = New-Object System.Collections.ArrayList
$deadline = (Get-Date).AddMinutes($Minutes)

while ((Get-Date) -lt $deadline) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='conhost.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if (-not $baseline.ContainsKey([int]$p.ProcessId)) {
            $parentName = ""
            try {
                $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction Stop
                $parentName = $parent.Name
            } catch {
                $parentName = "(gone)"
            }
            # 应用名含中文, 按路径/名称含"购买"或"Pokemon"识别为应用侧
            $isApp = ($appSide -contains $parentName) -or
                     ($parentName -match "购买|Pokemon")
            $line = "$((Get-Date).ToString('HH:mm:ss.fff')) conhost=$($p.ProcessId) parent=$parentName"
            if ($isApp) {
                [void]$appFlashes.Add($line)
            } else {
                [void]$noise.Add($line)
            }
            $baseline[[int]$p.ProcessId] = $true
        }
    }
    Start-Sleep -Milliseconds 250
}

$result = "APP_FLASH_COUNT=$($appFlashes.Count)`r`nNOISE_COUNT=$($noise.Count)`r`n"
$result += "--- APP ---`r`n" + ($appFlashes -join "`r`n") + "`r`n"
$result += "--- NOISE ---`r`n" + ($noise -join "`r`n")
Set-Content -Path $OutFile -Value $result -Encoding UTF8
Write-Output "DONE APP_FLASH_COUNT=$($appFlashes.Count) NOISE_COUNT=$($noise.Count)"
