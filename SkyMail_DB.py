import os
import sqlite3
import hashlib
import json
import uuid
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
import imaplib
from email.message import EmailMessage

# --- КОНФИГУРАЦИЯ ---

SHARED_DATA_ROOT = '.' 
DOMAIN = 'skymail.ru' 

# Используйте параметры для внешнего SMTP-сервера, например:
SMTP_SERVER = 'smtp.mail.ru' 
SMTP_PORT = 465 
IMAP_SERVER = 'imap.mail.ru'
IMAP_PORT = 993

DB_FILE = os.path.join(SHARED_DATA_ROOT, 'skymail_db.sqlite')
MAILBOX_DIR = os.path.join(SHARED_DATA_ROOT, 'mailboxes')

# --- Инициализация и Утилиты ---

def create_tables():
    """Создает базу данных SQLite и папку для почтовых ящиков."""
    if not os.path.exists(SHARED_DATA_ROOT):
        os.makedirs(SHARED_DATA_ROOT)
    if not os.path.exists(MAILBOX_DIR):
        os.makedirs(MAILBOX_DIR)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            password_hash TEXT,
            signature TEXT,
            external_login TEXT,
            external_password TEXT 
        )
    """)
    conn.commit()
    conn.close()

def hash_password(password):
    """Хеширует пароль."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

# --- Логика Пользователей ---

def add_user(conn, email, password):
    """Добавляет нового пользователя."""
    try:
        password_hash = hash_password(password)
        conn.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", 
                     (email, password_hash))
        conn.commit()
        save_user_mailbox(email, {'inbox': [], 'sent': [], 'drafts': []})
        return True
    except sqlite3.IntegrityError:
        return False

def check_user(conn, email, password):
    """Проверяет учетные данные пользователя."""
    password_hash = hash_password(password)
    cursor = conn.execute("SELECT password_hash FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    if user and user[0] == password_hash:
        return True
    return False

# --- Логика Почтового Ящика (JSON) ---

def get_mailbox_path(email):
    """Возвращает путь к JSON-файлу почтового ящика."""
    return os.path.join(MAILBOX_DIR, f'{email}.json')

def load_user_mailbox(email):
    """Загружает почтовый ящик из JSON-файла."""
    path = get_mailbox_path(email)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {'inbox': [], 'sent': [], 'drafts': []}
    return {'inbox': [], 'sent': [], 'drafts': []}

def save_user_mailbox(email, mailbox):
    """Сохраняет почтовый ящик в JSON-файл."""
    path = get_mailbox_path(email)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(mailbox, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"!!! ОШИБКА СОХРАНЕНИЯ ЯЩИКА {email} !!!: {e}")

def get_draft_count(email):
    """Считает количество черновиков."""
    mailbox = load_user_mailbox(email)
    return len(mailbox.get('drafts', []))

# --- Логика Сообщений ---

def create_new_message(sender, recipient, subject, body, is_read=False):
    """Создает стандартный словарь для нового сообщения."""
    return {
        'id': str(uuid.uuid4()),
        'sender': sender,
        'recipient': recipient,
        'subject': subject,
        'body': body,
        'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'read': is_read
    }

def add_message_to_mailbox(email, folder, message_data):
    """Добавляет сообщение в указанную папку и сохраняет."""
    mailbox = load_user_mailbox(email)
    mailbox[folder].insert(0, message_data)
    save_user_mailbox(email, mailbox)

def send_message(conn, sender, recipient, subject, body, draft_id=None):
    """Отправляет сообщение (внутреннее или внешнее)."""
    
    message_data = create_new_message(sender, recipient, subject, body)
    recipient_domain = recipient.split('@')[-1]
    skymail_domain = DOMAIN.lower().strip('@')

    # 1. Внутренняя отправка
    if recipient_domain == skymail_domain:
        add_message_to_mailbox(recipient, 'inbox', message_data) 
        add_message_to_mailbox(sender, 'sent', message_data)
        return True
    
    # 2. Внешняя отправка
    else:
        settings = load_user_settings(conn, sender)
        ext_login = settings.get('external_login')
        ext_pass = settings.get('external_password')

        if not ext_login or not ext_pass:
            print("Ошибка: Настройки внешнего SMTP не найдены.")
            return False

        try:
            msg = MIMEText(body, 'plain', 'utf-8')
            msg['Subject'] = subject
            msg['From'] = sender
            msg['To'] = recipient

            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
                server.login(ext_login, ext_pass)
                server.sendmail(sender, recipient, msg.as_string())
            
            add_message_to_mailbox(sender, 'sent', message_data)
            return True
        except Exception as e:
            print(f"Ошибка SMTP при отправке внешнего письма: {e}")
            return False

# --- Настройки Пользователей ---

def load_user_settings(conn, email):
    """Загружает настройки пользователя (подпись, внешняя почта)."""
    cursor = conn.execute("SELECT signature, external_login, external_password FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    if row:
        return {
            'signature': row[0] or '',
            'external_login': row[1] or '',
            'external_password': row[2] or ''
        }
    return {}

def save_user_settings(conn, email, signature, external_login, external_password):
    """Сохраняет настройки пользователя."""
    if external_password:
        conn.execute("UPDATE users SET signature=?, external_login=?, external_password=? WHERE email=?", 
                     (signature, external_login, external_password, email))
    else:
         conn.execute("UPDATE users SET signature=?, external_login=? WHERE email=?", 
                     (signature, external_login, email))
    conn.commit()

# --- Логика Получения Внешней Почты (IMAP) ---

def fetch_external_mail(user_skymail_email, ext_login, ext_pass):
    """Получает новую почту с внешнего сервера через IMAP."""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(ext_login, ext_pass)
        mail.select('inbox')
        
        status, messages = mail.search(None, 'UNSEEN') 
        
        for msg_id in messages[0].split():
            status, msg_data = mail.fetch(msg_id, '(RFC822)')
            
            from email import policy
            
            msg = EmailMessage()
            msg.set_content(msg_data[0][1])
            
            sender = msg['From']
            subject = msg['Subject']
            body = msg.get_body(prefertext='plain').get_content()
            
            new_message = create_new_message(sender, user_skymail_email, subject, body, is_read=False)
            add_message_to_mailbox(user_skymail_email, 'inbox', new_message)
            
            mail.store(msg_id, '+FLAGS', '\\Seen') 
            
        mail.logout()
    except Exception as e:
        raise Exception(f"IMAP-ошибка для {ext_login}: {e}")

if __name__ == '__main__':
    create_tables()
    print("SkyMail_DB.py: Создана структура базы данных и папка 'mailboxes'.")