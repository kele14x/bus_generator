@echo off

call %~dp0venv\Scripts\activate.bat
python %~dp0bus_generator.py %1 -o .
