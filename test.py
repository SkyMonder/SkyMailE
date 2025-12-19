import smtplib
from email.mime.text import MIMEText

SMTP_SERVER = "smtp.yandex.ru"
SMTP_PORT = 465
SMTP_LOGIN = "skymonder@yandex.ru"
SMTP_PASSWORD = "ejsqpcfshcfizcpm"

msg = MIMEText("Тест SkyMail SMTP ✅")
msg["Subject"] = "Тест SMTP"
msg["From"] = SMTP_LOGIN
msg["To"] = SMTP_LOGIN

try:
    server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
    server.login(SMTP_LOGIN, SMTP_PASSWORD)
    server.send_message(msg)
    server.quit()
    print("✅ ПИСЬМО УСПЕШНО ОТПРАВЛЕНО")
except Exception as e:
    print("❌ ОШИБКА:", e)
