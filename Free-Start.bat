@echo off
title Running Freebuff via NecoBox Proxy
cls

:: Настройка прокси-сервера NecoBox
set http_proxy=http://127.0.0.1:2080
set https_proxy=http://127.0.0.1:2080

echo [INFO] Proxy set to 127.0.0.1:2080
echo [INFO] Launching freebuff...
echo ---------------------------------

:: Запуск freebuff
freebuff

pause
