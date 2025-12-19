# -*- mode: python ; coding: utf-8 -*-
import os

# 1. Поправка имени главного файла на app.py, если вы его переименовали
# Если ваш главный файл называется SkyMail.py, оставляйте как есть.
# a = Analysis(['SkyMail.py'], 
a = Analysis(['SkyMail.py'], # <--- Если вы переименовали app.py в SkyMail.py, оставьте SkyMail.py

    pathex=['C:\\Users\\ИнтелДаня\\Desktop\\SkyMail'],
    binaries=[],
    datas=[], # <-- Здесь будем добавлять данные
    hiddenimports=['sqlite3'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None, # <-- Убедитесь, что здесь стоит None или этот аргумент удален
    noarchive=False
)

# 2. ДОБАВЛЕНИЕ ДАННЫХ В ПЕРЕМЕННУЮ 'a.datas'
# Добавляем SkyMail_DB.py
a.datas += [('SkyMail_DB.py', os.path.join(os.getcwd(), 'SkyMail_DB.py'), 'DATA')]

# 3. ДОБАВЛЕНИЕ СКРЫТЫХ ЗАВИСИМОСТЕЙ
# PyInstaller иногда теряет flask, gunicorn, smtplib
# a.hiddenimports += ['flask', 'smtplib', 'imaplib']


pyz = PYZ(a.pure, a.zipped_data,
             cipher=None) # <-- Убедитесь, что здесь стоит None

exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas, # <-- Обязательно используйте a.datas
          name='SkyMail.exe',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True ) # Измените на False, если хотите без консоли