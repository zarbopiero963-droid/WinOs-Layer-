@echo off
REM Alternative sc.exe create (requires absolute path to python/exe)
sc create WinOsApi binPath= "\"winos-api.exe\" serve --host 127.0.0.1 --port 8765" start= auto DisplayName= "Windows OS API Layer"
sc description WinOsApi "FastAPI Windows OS API Layer — localhost only"
sc start WinOsApi
