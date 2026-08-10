@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ==========================================
echo   Prepare Release — 发布前检查
echo ==========================================
echo.

set "ALL_OK=1"
set "WARNINGS=0"

:: ── 1. 敏感文件检查 ──
echo [1/9] 敏感文件检查...
set "SECRET_FOUND=0"
for %%f in (.env .env.local .env.production *.key *.pem *.p12 *.pfx credentials.json cookies.json token.json session.json) do (
    if exist "%%f" (
        if not "%%f"==".env.example" (
            echo   [WARN] 发现潜在敏感文件: %%f
            set /a WARNINGS+=1
        )
    )
)
:: Check inside Python files
findstr /s /i "api_key.*=.*[a-z0-9]\{8,\}" *.py >nul 2>&1 && (
    echo   [WARN] 发现疑似 API Key 在代码中
    set /a WARNINGS+=1
)
findstr /s /i "password.*=.*[a-z0-9]\{4,\}" *.py >nul 2>&1 && (
    echo   [WARN] 发现疑似密码硬编码
    set /a WARNINGS+=1
)
if !WARNINGS! equ 0 echo   [OK] 未发现敏感文件

:: ── 2. 大文件检查 ──
echo [2/9] 大文件检查 (>50MB)...
for /r %%f in (*) do (
    if %%~zf gtr 52428800 (
        echo   [WARN] 大文件: %%f (%%~zf bytes^)
        set /a WARNINGS+=1
    )
)
if !WARNINGS! equ 0 echo   [OK] 无超过 50MB 的文件

:: ── 3. Git 状态检查 ──
echo [3/9] Git 状态检查...
git status --porcelain > git_status.tmp 2>nul
if %errorlevel% neq 0 (
    echo   [WARN] 不是 Git 仓库或 Git 不可用
    set /a WARNINGS+=1
) else (
    for /f %%a in (git_status.tmp) do set "HAS_CHANGES=1"
    if defined HAS_CHANGES (
        echo   [INFO] 存在未提交的修改:
        type git_status.tmp
    ) else (
        echo   [OK] 工作区干净
    )
    del git_status.tmp 2>nul
)

:: ── 4. 依赖文件检查 ──
echo [4/9] 依赖文件检查...
if not exist "requirements.txt" (
    echo   [ERROR] 缺少 requirements.txt
    set "ALL_OK=0"
) else (
    echo   [OK] requirements.txt 存在
)

:: ── 5. README 检查 ──
echo [5/9] README 检查...
if not exist "README.md" (
    echo   [ERROR] 缺少 README.md
    set "ALL_OK=0"
) else (
    echo   [OK] README.md 存在
)

:: ── 6. License 检查 ──
echo [6/9] License 检查...
if not exist "LICENSE" (
    echo   [WARN] 缺少 LICENSE 文件
    set /a WARNINGS+=1
) else (
    echo   [OK] LICENSE 存在
)

:: ── 7. .gitignore 检查 ──
echo [7/9] .gitignore 检查...
if not exist ".gitignore" (
    echo   [ERROR] 缺少 .gitignore
    set "ALL_OK=0"
) else (
    findstr /c:".env" .gitignore >nul 2>&1 && echo   [OK] .gitignore 含 .env 规则
)

:: ── 8. 硬编码路径检查 ──
echo [8/9] 硬编码路径检查...
findstr /s /r /c:"C:\\\\Users\\\\" *.py >nul 2>&1 && (
    echo   [WARN] 发现硬编码路径 C:\Users\...
    set /a WARNINGS+=1
) || echo   [OK] 未发现本机绝对路径

:: ── 9. Python 语法检查 ──
echo [9/9] Python 语法检查...
python -c "import glob, py_compile; [py_compile.compile(f, doraise=True) for f in glob.glob('**/*.py', recursive=True) if 'douyin-framework' not in f]" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Python 语法错误
    set "ALL_OK=0"
) else (
    echo   [OK] 所有 Python 文件语法正确
)

:: ── 最终结果 ──
echo.
echo ==========================================
if %ALL_OK% equ 1 (
    if !WARNINGS! equ 0 (
        echo   READY FOR GITHUB
    ) else (
        echo   READY (with !WARNINGS! warnings^)
    )
) else (
    echo   NOT READY — 请修复上面的 ERROR
)
echo ==========================================
echo.
pause
