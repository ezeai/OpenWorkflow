@echo off
rem Open Workflow local launcher. Serves the UI and API on loopback only.
cd /d "%~dp0backend"
start "" http://127.0.0.1:8765
python -m uvicorn openfield.app:app --host 127.0.0.1 --port 8765
