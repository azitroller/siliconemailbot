import os
import imaplib
import email
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from openai import OpenAI
from dotenv import load_dotenv
import logging

# Initialize with basic config first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# === Configuration ===
try:
    EMAIL_ADDRESS = os.environ["EMAIL"]
    EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
    OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
    
    # Initialize OpenAI client with explicit HTTPX config
    client = OpenAI(
        api_key=OPENAI_API_KEY,
        # Prevents proxy-related errors
        http_client=None,
        timeout=30.0
    )
except KeyError as e:
    logger.error(f"Missing environment variable: {str(e)}")
    exit(1)
except Exception as e:
    logger.error(f"Initialization error: {str(e)}")
    exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# === Email Fetching ===
def fetch_latest_email():
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", timeout=30) as mail:
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

            # Extract email body
            body = ""
            if message.is_multipart():
                for part in message.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors='ignore')
                        break
            else:
                body = message.get_payload(decode=True).decode(errors='ignore')

            return sender, subject, body

    except Exception as e:
        logging.error(f"Email fetch error: {str(e)}", exc_info=True)
        return None

# === AI Response Generation ===
def generate_ai_reply(body_text):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # Using GPT-4o model
            messages=[
                {
                    "role": "system",
                    "content": """You are a professional customer support agent. 
                    Respond to website contact form messages with:
                    - 2-3 sentence acknowledgment
                    - Brief solution or next steps
                    - Polite closing"""
                },
                {"role": "user", "content": body_text}
            ],
            temperature=0.7,
            max_tokens=350,
            top_p=0.9
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logging.error(f"AI generation error: {str(e)}", exc_info=True)
        return None

# === Email Sending ===
def send_email_reply(recipient, subject, message_text):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Re: {subject}"
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = recipient

        # Create both plain and HTML versions
        plain_text = MIMEText(message_text, "plain")
        html_text = MIMEText(
            f"<html><body style='font-family: Arial, sans-serif; line-height: 1.6;'>"
            f"<p>{message_text.replace('\n', '<br>')}</p>"
            f"</body></html>", 
            "html"
        )

        msg.attach(plain_text)
        msg.attach(html_text)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, recipient, msg.as_string())

        return True

    except Exception as e:
        logging.error(f"Email send error: {str(e)}", exc_info=True)
        return False

# === Main Execution ===
def main():
    logging.info("Starting email processing...")
    email_data = fetch_latest_email()
    
    if not email_data:
        logging.info("No new emails to process.")
        return

    sender, subject, body = email_data
    logging.info(f"Processing email from: {sender} | Subject: {subject}")

    # Generate AI response
    reply = generate_ai_reply(body)
    if not reply:
        logging.error("Failed to generate AI response")
        return

    logging.info(f"Generated response preview:\n{reply[:200]}...")
    
    # Send reply
    if send_email_reply(sender, subject, reply):
        logging.info("Successfully sent reply email.")
    else:
        logging.error("Failed to send reply email")

if __name__ == "__main__":
    main()
