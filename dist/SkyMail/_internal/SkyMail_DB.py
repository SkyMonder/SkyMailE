import sqlite3
import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import mimetypes
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import imaplib
import email
import re
from flask import g # Импортируем g для работы с БД
import time

# ================= КОНСТАНТЫ SKYMAIL (Резервный прокси) =================
DATABASE = 'skymail_db.sqlite'
UPLOAD_FOLDER = 'uploads'
SECRET_KEY = '01206090'

# Резервные данные (для отправки, если пользователь не подключил внешний ящик)
# !!! ОБЯЗАТЕЛЬНО ЗАМЕНИТЕ ЭТИ ДАННЫЕ !!!
SMTP_SERVER = 'smtp.mail.ru'  
SMTP_PORT = 465                  
SMTP_LOGIN = 'dg2024yt@mail.ru'
SMTP_PASSWORD = 'ZSOlgalIwtpwo7TjnEvt'
# =======================================================================


# ================= ОСНОВНЫЕ ФУНКЦИИ БД =================
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def create_tables():
    db = get_db()
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                signature TEXT,
                external_login TEXT,      
                external_password TEXT    
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY,
                user_email TEXT NOT NULL,
                to_addr TEXT,
                subject TEXT,
                text_content TEXT,
                FOREIGN KEY (user_email) REFERENCES users(email)
            )
        """)
        
    if not os.path.exists('mailboxes'):
        os.makedirs('mailboxes')
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
        
# ================= ФУНКЦИИ УПРАВЛЕНИЯ ЯЩИКАМИ =================
def load_user_mailbox(email):
    path = os.path.join('mailboxes', f'{email}.json')
    if not os.path.exists(path):
        return {"inbox": [], "sent": []}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {"inbox": [], "sent": []}


def save_user_mailbox(email, mailbox_data):
    path = os.path.join('mailboxes', f'{email}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(mailbox_data, f, ensure_ascii=False, indent=4)

def register_user(email, password):
    db = get_db()
    try:
        password_hash = generate_password_hash(password)
        with db:
            db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email.lower(), password_hash))
        load_user_mailbox(email.lower()) 
        return True
    except sqlite3.IntegrityError:
        return False

def get_user_by_email(email):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email.lower(),))
    return cursor.fetchone()

def check_user_password(user, password):
    if user:
        return check_password_hash(user['password_hash'], password)
    return False

def get_user_signature(email):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT signature FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    return row['signature'] if row else None

def update_signature_in_db(email, signature):
    db = get_db()
    with db:
        db.execute("UPDATE users SET signature = ? WHERE email = ?", (signature, email))

def get_draft_count(email):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(id) FROM drafts WHERE user_email = ?", (email,))
    return cursor.fetchone()[0]


# ================= ФУНКЦИИ ВНЕШНИХ ПОДКЛЮЧЕНИЙ =================
def save_external_credentials(email, ext_login, ext_password):
    db = get_db()
    with db:
        db.execute("""
            UPDATE users SET external_login = ?, external_password = ? WHERE email = ?
        """, (ext_login, ext_password, email))

def get_external_credentials(email):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT external_login, external_password FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    if row and row['external_login'] and row['external_password']:
        return row['external_login'], row['external_password']
    return None, None

def get_email_provider_info(external_email):
    """Определяет настройки SMTP и IMAP для популярных провайдеров."""
    if not external_email: return None, None, None, None
        
    domain = external_email.split('@')[-1].lower()
    
    if 'gmail.com' in domain:
        return "smtp.gmail.com", 465, "imap.gmail.com", 993
    elif 'yandex.ru' in domain:
        return "smtp.yandex.ru", 465, "imap.yandex.ru", 993
    elif 'mail.ru' in domain:
        return "smtp.mail.ru", 465, "imap.mail.ru", 993
    elif 'outlook.com' in domain or 'hotmail.com' in domain:
        return "smtp-mail.outlook.com", 587, "imap-mail.outlook.com", 993
    else:
        return None, None, None, None

# ================= ФУНКЦИЯ ПОЛУЧЕНИЯ ПОЧТЫ =================

def get_all_users_with_external_mail():
    """Получает всех пользователей, которые подключили внешний ящик."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT email, external_login, external_password FROM users WHERE external_login IS NOT NULL")
    return cursor.fetchall()

def parse_email_body(msg):
    """Парсит тело письма, ища текст/plain часть."""
    body = "Тело письма не удалось прочитать или оно пусто."
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdisp = str(part.get("Content-Disposition"))
            if ctype == "text/plain" and "attachment" not in cdisp:
                try:
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    if body.strip(): return body.strip()
                except: continue
    else:
        try: body = msg.get_payload(decode=True).decode(errors="ignore")
        except: pass
    return body

def fetch_external_mail_for_all():
    """Фоновая задача: проходит по всем пользователям и загружает их внешнюю почту."""
    users_to_check = get_all_users_with_external_mail()
    
    for user_row in users_to_check:
        skymail_user = user_row['email']
        ext_login = user_row['external_login']
        ext_password = user_row['external_password']
        
        _, _, IMAP_HOST, IMAP_PORT = get_email_provider_info(ext_login)

        if not IMAP_HOST: continue
            
        try:
            # 1. Подключение
            mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            mail.login(ext_login, ext_password)
            mail.select("inbox")

            # 2. Поиск писем (Скачиваем 10 последних, чтобы не нагружать сервис)
            status, messages = mail.search(None, "UNSEEN") # Ищем только непрочитанные
            
            if not messages[0]:
                mail.logout()
                continue
            
            latest_messages = messages[0].split()
            current_skymail_mailbox = load_user_mailbox(skymail_user)
            current_skymail_inbox = current_skymail_mailbox['inbox']
            
            for num_bytes in latest_messages:
                # 3. Загрузка и парсинг
                status, data = mail.fetch(num_bytes, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])

                subject = msg["Subject"] or ""
                sender = msg["From"] or ""
                
                body = parse_email_body(msg)
                
                # 4. Сохраняем в ящик SkyMail
                current_skymail_inbox.append({
                    "from": sender,
                    "to": skymail_user,
                    "subject": subject.strip(),
                    "text": body
                })
                
                # Помечаем как прочитанное (чтобы не скачивать снова)
                mail.store(num_bytes, '+FLAGS', '\\Seen')
            
            save_user_mailbox(skymail_user, current_skymail_mailbox)
            mail.logout()
            
        except Exception as e:
            print(f"Ошибка IMAP для пользователя {ext_login}: {e}")
            
# ================= ФУНКЦИЯ ОТПРАВКИ =================

def send_message(sender, to, subject, text, uploaded_file=None):
    db = get_db()
    filename = None
    filepath = None
    
    # 1. Обработка файла
    if uploaded_file and uploaded_file.filename and uploaded_file.filename != '': 
        filename = secure_filename(uploaded_file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        uploaded_file.save(filepath)
    
    # 2. Добавление подписи
    signature = get_user_signature(sender)
    full_text = text + (f"\n\n---\n{signature}" if signature else "")
    sent_data = {"from": sender, "to": to, "subject": subject, "text": full_text}


    # 3. ВНУТРЕННЯЯ ПОЧТА (@skymail.ru)
    if to.lower().endswith("@skymail.ru"):
        target_user = to.lower()

        if not get_user_by_email(target_user):
            if filename and os.path.exists(filepath): os.remove(filepath)
            return

        mailbox_recipient = load_user_mailbox(target_user)
        internal_text = full_text
        if filename: internal_text += f"\n\n[!!! Вложение: {filename}. !!!]"
        mailbox_recipient["inbox"].append(sent_data) 
        save_user_mailbox(target_user, mailbox_recipient)
        
        mailbox_sender = load_user_mailbox(sender)
        mailbox_sender["sent"].append(sent_data)
        save_user_mailbox(sender, mailbox_sender)

        if filename and os.path.exists(filepath): os.remove(filepath)
        return

    
    # 4. ВНЕШНЯЯ ОТПРАВКА (через SMTP)
    ext_login, ext_password = get_external_credentials(sender)
    
    if ext_login and ext_password:
        SMTP_HOST, SMTP_PORT_ADDR, _, _ = get_email_provider_info(ext_login)
        FROM_ADDR = ext_login
        LOGIN = ext_login
        PASSWORD = ext_password
    else:
        SMTP_HOST = SMTP_SERVER
        SMTP_PORT_ADDR = SMTP_PORT
        FROM_ADDR = SMTP_LOGIN 
        LOGIN = SMTP_LOGIN
        PASSWORD = SMTP_PASSWORD
        
    if not SMTP_HOST:
        if filename and os.path.exists(filepath): os.remove(filepath)
        return
            
    msg = MIMEMultipart()
    msg["From"] = FROM_ADDR
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(full_text, 'plain'))
    
    # Прикрепление файла
    if filename:
        try:
            with open(filepath, "rb") as attachment:
                ctype, encoding = mimetypes.guess_type(filepath)
                if ctype is None or encoding is not None: ctype = 'application/octet-stream'
                maintype, subtype = ctype.split('/', 1)
                part = MIMEBase(maintype, subtype)
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={filename}")
                msg.attach(part)
        except Exception as e:
            print(f"Ошибка прикрепления файла: {e}")
            
    # Отправка
    try:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT_ADDR, timeout=10)
        if SMTP_PORT_ADDR == 587:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT_ADDR, timeout=10)
            server.ehlo()
            server.starttls()
            
        server.login(LOGIN, PASSWORD)
        server.sendmail(FROM_ADDR, to, msg.as_string())
        server.quit()
        
        # Сохранение в Отправленные
        mailbox_sender = load_user_mailbox(sender)
        mailbox_sender["sent"].append(sent_data)
        save_user_mailbox(sender, mailbox_sender)
            
    except Exception as e:
        print(f"Ошибка SMTP при отправке: {e}")

    if filename and os.path.exists(filepath): os.remove(filepath)
        
    return

# ================= ФУНКЦИИ ДЛЯ ЧЕРНОВИКОВ =================

def save_draft_to_db(email, to, subject, text):
    db = get_db()
    with db:
        db.execute("""
            INSERT INTO drafts (user_email, to_addr, subject, text_content) VALUES (?, ?, ?, ?)
        """, (email, to, subject, text))

def get_draft(email, draft_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM drafts WHERE id = ? AND user_email = ?", (draft_id, email))
    return cursor.fetchone()

def get_all_drafts(email):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM drafts WHERE user_email = ?", (email,))
    return cursor.fetchall()

def update_draft_in_db(email, draft_id, to, subject, text):
    db = get_db()
    with db:
        db.execute("""
            UPDATE drafts SET to_addr = ?, subject = ?, text_content = ? WHERE id = ? AND user_email = ?
        """, (to, subject, text, draft_id, email))

def delete_draft_by_id(email, draft_id):
    db = get_db()
    with db:
        db.execute("DELETE FROM drafts WHERE id = ? AND user_email = ?", (draft_id, email))