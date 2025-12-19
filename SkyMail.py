import os
import sqlite3
import hashlib
import json
import socket
import webbrowser
from threading import Thread, Timer
import time
# Используем render_template вместо render_template_string
from flask import Flask, request, redirect, url_for, session, g, render_template 
from SkyMail_DB import (
    create_tables, 
    add_user, 
    check_user, 
    load_user_mailbox, 
    save_user_mailbox, 
    load_user_settings, 
    save_user_settings, 
    get_draft_count, 
    fetch_external_mail, 
    send_message,
    add_message_to_mailbox,
    create_new_message,
    DB_FILE, 
)

# --- Инициализация Flask ---
# Flask автоматически ищет папку 'templates' рядом с этим файлом
app = Flask(__name__)
# ВАЖНО: Замените это на длинную случайную строку!
app.secret_key = 'your_super_secret_key_here' 


# --- Middlewares и декораторы ---

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_FILE) 
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def login_required(f):
    def wrapper(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('index'))
        g.user = session['email']
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# --- Роуты (Маршруты) ---

@app.route('/', methods=['GET', 'POST'])
def index():
    if 'email' in session:
        return redirect(url_for('inbox'))
    
    error = None
    if request.method == 'POST':
        action = request.form.get('action')
        email = request.form['email'].lower()
        password = request.form['password']
        
        if action == 'register':
            if add_user(get_db(), email, password):
                session['email'] = email
                return redirect(url_for('inbox'))
            else:
                error = 'Пользователь с таким email уже существует.'
        
        elif action == 'login':
            if check_user(get_db(), email, password):
                session['email'] = email
                return redirect(url_for('inbox'))
            else:
                error = 'Неверный email или пароль.'
    
    # ИСПОЛЬЗУЕМ render_template! Ошибка KeyError устранена.
    return render_template('index.html', error=error)

@app.route('/logout')
def logout():
    session.pop('email', None)
    return redirect(url_for('index'))

@app.route('/inbox')
@login_required
def inbox():
    mailbox = load_user_mailbox(g.user)
    draft_count = get_draft_count(g.user)
    return render_template(
        'inbox.html',
        email=g.user,
        messages=mailbox.get('inbox', []),
        draft_count=draft_count,
        folder_name="Входящие",
        folder_code="inbox"
    )

@app.route('/sent')
@login_required
def sent():
    mailbox = load_user_mailbox(g.user)
    draft_count = get_draft_count(g.user)
    return render_template(
        'inbox.html',
        email=g.user,
        messages=mailbox.get('sent', []),
        draft_count=draft_count,
        folder_name="Отправленные",
        folder_code="sent"
    )

@app.route('/drafts')
@login_required
def drafts():
    mailbox = load_user_mailbox(g.user)
    draft_count = get_draft_count(g.user)
    return render_template(
        'inbox.html',
        email=g.user,
        messages=mailbox.get('drafts', []),
        draft_count=draft_count,
        folder_name="Черновики",
        folder_code="drafts"
    )

@app.route('/compose', methods=['GET'])
@login_required
def compose():
    draft_count = get_draft_count(g.user)
    return render_template(
        'compose.html',
        email=g.user,
        draft_count=draft_count,
        draft=None
    )

@app.route('/compose/<draft_id>', methods=['GET'])
@login_required
def edit_draft(draft_id):
    mailbox = load_user_mailbox(g.user)
    draft_count = get_draft_count(g.user)
    
    draft = next((msg for msg in mailbox.get('drafts', []) if msg['id'] == draft_id), None)
    
    if not draft:
        return "Черновик не найден!", 404

    return render_template(
        'compose.html',
        email=g.user,
        draft_count=draft_count,
        draft=draft
    )

@app.route('/send', methods=['POST'])
@login_required
def handle_send():
    recipient = request.form['recipient'].lower()
    subject = request.form['subject']
    body = request.form['body']
    action = request.form.get('action')
    draft_id = request.form.get('draft_id')

    settings = load_user_settings(get_db(), g.user)
    signature = settings.get('signature', '')
    full_body = body + "\n\n" + signature if signature else body

    if action == 'draft':
        mailbox = load_user_mailbox(g.user)
        
        if draft_id:
            # Удаляем старый черновик, если он был
            mailbox['drafts'] = [msg for msg in mailbox.get('drafts', []) if msg['id'] != draft_id]

        new_draft = create_new_message(g.user, recipient, subject, full_body, is_read=False)
        
        mailbox['drafts'].insert(0, new_draft)
        save_user_mailbox(g.user, mailbox)
        
        return redirect(url_for('drafts')) 

    elif action == 'send':
        if send_message(get_db(), g.user, recipient, subject, full_body):
            # Если отправка успешна, удаляем черновик, если он был
            if draft_id:
                mailbox = load_user_mailbox(g.user)
                mailbox['drafts'] = [msg for msg in mailbox.get('drafts', []) if msg['id'] != draft_id]
                save_user_mailbox(g.user, mailbox)
            return redirect(url_for('inbox'))
        else:
            return "Ошибка отправки сообщения! Проверьте SMTP настройки.", 500

@app.route('/read/<folder_name>/<message_id>')
@login_required
def read_message(folder_name, message_id):
    mailbox = load_user_mailbox(g.user)
    draft_count = get_draft_count(g.user)
    
    folder = mailbox.get(folder_name)
    if not folder:
        return "Папка не найдена!", 404
        
    message = next((msg for msg in folder if msg['id'] == message_id), None)

    if not message:
        return "Сообщение не найдено!", 404
    
    # Отмечаем как прочитанное (если это входящее)
    if folder_name == 'inbox' and not message.get('read'):
        message['read'] = True
        save_user_mailbox(g.user, mailbox)
    
    message['folder'] = folder_name # Добавляем для удаления
    
    return render_template(
        'read.html',
        email=g.user,
        message=message,
        draft_count=draft_count
    )

@app.route('/delete/<folder_name>/<message_id>', methods=['POST'])
@login_required
def delete_message(folder_name, message_id):
    mailbox = load_user_mailbox(g.user)
    
    if folder_name in mailbox:
        original_count = len(mailbox[folder_name])
        # Фильтруем список, оставляя только сообщения, id которых не совпадает
        mailbox[folder_name] = [msg for msg in mailbox[folder_name] if msg['id'] != message_id]
        
        if len(mailbox[folder_name]) < original_count:
            save_user_mailbox(g.user, mailbox)

    return redirect(url_for('inbox'))


@app.route('/settings', methods=['GET'])
@login_required
def settings():
    settings = load_user_settings(get_db(), g.user)
    draft_count = get_draft_count(g.user)
    message = request.args.get('message')
    
    return render_template(
        'settings.html',
        email=g.user,
        settings=settings,
        draft_count=draft_count,
        message=message
    )

@app.route('/save_settings', methods=['POST'])
@login_required
def save_settings():
    signature = request.form.get('signature', '')
    external_login = request.form.get('external_login', '').lower()
    external_password = request.form.get('external_password', '')
    
    save_user_settings(get_db(), g.user, signature, external_login, external_password)
    return redirect(url_for('settings', message="Настройки успешно сохранены!"))

# --- Фоновый поток IMAP ---

def run_mail_fetcher():
    """Фоновый поток для получения внешних писем через IMAP."""
    with app.app_context():
        while True:
            try:
                conn = get_db()
                cursor = conn.execute("SELECT email, external_login, external_password FROM users WHERE external_login IS NOT NULL AND external_password != ''")
                users_with_external = cursor.fetchall()
                
                for user_email, ext_login, ext_pass in users_with_external:
                    try:
                        fetch_external_mail(user_email, ext_login, ext_pass)
                    except Exception as e:
                        print(f"Ошибка получения почты для {user_email}: {e}")
                        pass 
            except Exception as e:
                print(f"Глобальная ошибка в потоке IMAP: {e}")
                
            time.sleep(300) # Ждем 5 минут

# --- Инициализация и Запуск ---

with app.app_context():
    create_tables()

if __name__ == "__main__":
    
    fetcher_thread = Thread(target=run_mail_fetcher)
    fetcher_thread.daemon = True
    fetcher_thread.start()

    local_ip_browser = "127.0.0.1" 
    
    def open_browser():
        url = f"http://{local_ip_browser}:5000"
        webbrowser.open_new_tab(url)
    
    print("------------------------------------------------------------------")
    print("SkyMail запущен!")
    print("Для доступа с этого компьютера: http://127.0.0.1:5000")
    print("---")
    print("Для доступа с ДРУГИХ компьютеров в LAN (или Хотспот):")
    print("1. Введите в командной строке 'ipconfig' и найдите свой IPv4-адрес.")
    print("2. Используйте этот адрес: http://[ВАШ_IP_АДРЕС]:5000")
    print("------------------------------------------------------------------")
    
    Timer(1, open_browser).start()
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)