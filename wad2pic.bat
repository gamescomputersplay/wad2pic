@echo off
REM Requires Python in your PATH environment.
REM (%~dp0) translates to the directory where this file lives.
python %~dp0\wad2pic.py %*
