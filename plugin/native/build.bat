@echo off
rem Builds, for 32-bit (out\x86) and 64-bit (out\x64) engines:
rem   kiripanel.tpm   the plugin, for engines that load *.tpm themselves
rem   version.dll     the same plugin as a version.dll proxy (proxy.inc)
rem   mpr.dll         the same as an mpr.dll proxy, for games with their own
rem                   version.dll (export tables: gen_proxy.py version mpr)
rem   build.bat            both architectures
rem   build.bat x86|x64    one
rem Needs Visual Studio (or its Build Tools) with the C++ workload; it is
rem found with vswhere, or set VCVARS to its vcvarsall.bat.
setlocal
if not defined VCVARS (
  for /f "usebackq delims=" %%I in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VCVARS=%%I\VC\Auxiliary\Build\vcvarsall.bat"
)
if not exist "%VCVARS%" (echo vcvarsall.bat not found - install the C++ build tools or set VCVARS & exit /b 1)
cd /d "%~dp0"
set ARCHS=%1
if "%ARCHS%"=="" set ARCHS=x86 x64
for %%A in (%ARCHS%) do (
  call :one %%A || exit /b 1
)
exit /b 0

:one
setlocal
call "%VCVARS%" %1 >nul || exit /b 1
if not exist out\%1\tpm mkdir out\%1\tpm
if "%1"=="x86" (set HDE=minhook\src\hde\hde32.c) else (set HDE=minhook\src\hde\hde64.c)
rem /MT: no VC runtime needed on the player's PC. /EHa: catch(...) also
rem stops structured exceptions, whatever compiler built the engine.
set CFLAGS=/nologo /LD /O2 /MT /EHa /W3 /utf-8 /wd4828 /DUNICODE /D_UNICODE /D_CRT_SECURE_NO_WARNINGS
cl %CFLAGS% /Fo"out\%1\tpm\\" kiripanel.cpp tp_stub\tp_stub.cpp ^
   /link /DEF:kiripanel.def /OUT:out\%1\kiripanel.tpm /IMPLIB:out\%1\tpm\kiripanel.lib user32.lib || exit /b 1
call :proxy %1 version "" || exit /b 1
call :proxy %1 mpr /DKP_PROXY_MPR || exit /b 1
endlocal & exit /b 0

rem :proxy ARCH NAME EXTRA_DEFINE   -> out\ARCH\NAME.dll (export table: gen\NAME_ARCH.def)
:proxy
setlocal
set DEF=
if not "%~3"=="" set DEF=%~3
if not exist out\%1\%2 mkdir out\%1\%2
cl %CFLAGS% /DKP_PROXY %DEF% /Fo"out\%1\%2\\" kiripanel.cpp tp_stub\tp_stub.cpp ^
   minhook\src\buffer.c minhook\src\hook.c minhook\src\trampoline.c %HDE% ^
   /link /DEF:gen\%2_%1.def /OUT:out\%1\%2.dll /IMPLIB:out\%1\%2\%2.lib user32.lib || exit /b 1
endlocal & exit /b 0
