@echo off
echo Installing PyInstaller...
pip install pyinstaller

echo Cleaning previous builds...
rmdir /s /q build dist

echo Building EXE...
pyinstaller --clean --noconfirm thinksub2.spec

echo Build Complete!
echo Run dist\ThinkSub2\ThinkSub2.exe to start.
pause
