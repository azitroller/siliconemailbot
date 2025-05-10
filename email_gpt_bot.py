# email_gpt_bot.py

import os
import imaplib
import email
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
import openai
from dotenv import load_dotenv
from datetime import datetime
import logging

# Load credentials from .env file (recommended for security)
load_dotenv()

# === Configuration ===
EMAIL_ADDRESS = os.getenv("EMAIL") or "your_email@gmail.com"
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") or "your_app_password"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or "your-openai-api-key"

openai.api_key = OPENAI_API_KEY

# === Logging Setup ===
logging.basicConfig(filename='email_bot.log', level=logging.INFO, format='%(asctime)s - %(message)s')

# === Fetch Latest Unseen Email ===
def fetch_latest_email():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    mail.select("inbox")

    _, data = mail.search(None, "UNSEEN")
    mail_ids = data[0].split()
    if not mail_ids:
        return None

    latest_email_id = mail_ids[-1]
    _, msg_data = mail.fetch(latest_email_id, "(RFC822)")
    raw_email = msg_data[0][1]
    message = email.message_from_bytes(raw_email)

    sender = parseaddr(message["From"])[1]
    subject = message["Subject"]
    body = ""

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if "attachment" not in content_disposition:
                if content_type == "text/plain":
                    body = part.get_payload(decode=True).decode(errors='ignore')
                    break
                elif content_type == "text/html" and not body:
                    html_content = part.get_payload(decode=True).decode(errors='ignore')
                    body = email.message_from_string(html_content).get_payload()  # fallback
    else:
        body = message.get_payload(decode=True).decode(errors='ignore')

    return sender, subject, body

# === Generate Reply with OpenAI GPT ===
def generate_gpt_reply(body_text):
    prompt = f"""
You are a helpful assistant. Write a polite and professional email reply to the following:

{body_text}
"""
    
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=200,
        temperature=0.7
    )
    return response.choices[0].text.strip()

# === Send Email Reply ===
def send_email_reply(recipient, subject, message_text):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Re: " + subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = recipient

    plain_part = MIMEText(message_text, "plain")
    html_part = MIMEText(f"<html><body><p>{message_text.replace('\n', '<br>')}</p></body></html>", "html")

    msg.attach(plain_part)
    msg.attach(html_part)

    server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    server.sendmail(EMAIL_ADDRESS, recipient, msg.as_string())
    server.quit()

# === Main Execution ===
def main():
    email_data = fetch_latest_email()
    if email_data is None:
        print("No new emails.")
        return

    sender, subject, body = email_data
    logging.info(f"Processing email from: {sender} | Subject: {subject}")

    try:
        reply = generate_gpt_reply(body)
        send_email_reply(sender, subject, reply)
        logging.info("Reply sent successfully.")
        print("Reply sent.")
    except Exception as e:
        logging.error(f"Failed to process email: {str(e)}")
        print("Failed to process email.")

if __name__ == "__main__":
    main()
