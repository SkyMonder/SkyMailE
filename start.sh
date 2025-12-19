#!/bin/bash
# Запуск Gunicorn, который использует app:app (имя вашего модуля:имя переменной Flask)
gunicorn --bind 0.0.0.0:$PORT app:app