@echo off
set WA_BACKEND_INBOUND_URL=http://127.0.0.1:8000/api/whatsapp/inbound
cd /d "%~dp0"
start "" /B node server.js
echo WhatsApp sidecar started.
