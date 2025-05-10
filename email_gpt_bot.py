import os
import imaplib
import email
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
import openai
from dotenv import load_dotenv
import logging

# Load .env if testing locally
load_dotenv()

# === Config ===
EMAIL_ADDRESS = os.getenv("EMAIL")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)

# === Fetch latest unread email ===
def fetch_latest_email():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    mail.select("inbox")

    _, data = mail.search(None, "UNSEEN")
    mail_ids = data[0].split()
    if not mail_ids:
        return None

    latest_id = mail_ids[-1]
    _, msg_data = mail.fetch(latest_id, "(RFC822)")
    raw_email = msg_data[0][1]
    message = email.message_from_bytes(raw_email)

    sender = parseaddr(message["From"])[1]
    subject = message["Subject"] or "(No Subject)"

    body = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode(errors='ignore')
                break
    else:
        body = message.get_payload(decode=True).decode(errors='ignore')

    return sender, subject, body

# === Generate a reply using GPT-4 ===
def generate_gpt_reply(body_text):
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a polite and professional assistant replying to website contact form messages."},
            {"role": "user", "content": body_text}
        ],
        temperature=0.7,
        max_tokens=300
    )
    return response.choices[0].message["content"].strip()

# === Send the reply email ===
def send_email_reply(recipient, subject, message_text):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Re: " + subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = recipient

    plain_part = MIMEText(message_text, "plain")
    html_content = message_text.replace("\n", "<br>")
    html_part = MIMEText(f"<html><body><p>{html_content}</p></body></html>", "html")

    msg.attach(plain_part)
    msg.attach(html_part)

    server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    server.sendmail(EMAIL_ADDRESS, recipient, msg.as_string())
    server.quit()

# === Main ===
def main():
    logging.info("Checking for new emails...")
    email_data = fetch_latest_email()
    if not email_data:
        logging.info("No new emails.")
        return

    sender, subject, body = email_data
    logging.info(f"Email from: {sender}, Subject: {subject}")
    reply = generate_gpt_reply(body)
    send_email_reply(sender, subject, reply)
    logging.info("Reply sent.")

if __name__ == "__main__":
    main()
