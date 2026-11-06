@echo off
rem setup (submodules + packman deps) -> VS2022 solution -> build Release (Mogwai + plugins)
cd /d %~dp0
call setup_vs2022.bat || exit /b 1
tools\.packman\cmake\bin\cmake.exe --build build\windows-vs2022 --config Release --parallel 16 || exit /b 1
echo BUILD_OK
