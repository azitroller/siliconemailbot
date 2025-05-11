import imaplib
import email
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import json
import logging
from datetime import datetime
import openai
import time
import re
from email.utils import parseaddr

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='email_bot.log'
)
logger = logging.getLogger('email_bot')

class AIFormSubmitEmailBot:
    def __init__(self):
        self.config = self._load_config()
        self.processed_ids_file = 'processed_emails.json'
        self.processed_ids = self._load_processed_ids()
        openai.api_key = self.config['openai_api_key']

    def _load_config(self):
        config = {
            'imap_server': os.environ.get('IMAP_SERVER'),
            'imap_port': int(os.environ.get('IMAP_PORT', 993)),
            'smtp_server': os.environ.get('SMTP_SERVER'),
            'smtp_port': int(os.environ.get('SMTP_PORT', 587)),
            'email_address': os.environ.get('EMAIL_ADDRESS'),
            'password': os.environ.get('EMAIL_PASSWORD'),
            'formsubmit_identifier': os.environ.get('FORMSUBMIT_IDENTIFIER', 'formsubmit.co'),
            'auto_reply_subject': os.environ.get('AUTO_REPLY_SUBJECT', 'Thank you for contacting us'),
            'openai_api_key': os.environ.get('OPENAI_API_KEY'),
            'ai_model': os.environ.get('AI_MODEL', 'gpt-4o'),
            'response_tone': os.environ.get('RESPONSE_TONE', 'friendly and professional'),
            'company_info': {
                'name': os.environ.get('COMPANY_NAME', 'Our Company'),
                'description': os.environ.get('COMPANY_DESCRIPTION', 'company that values your inquiry'),
                'team_name': os.environ.get('TEAM_NAME', 'Customer Support Team')
            }
        }
        required_fields = ['imap_server', 'smtp_server', 'email_address', 'password', 'openai_api_key']
        missing_fields = [field for field in required_fields if not config[field]]
        if missing_fields:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_fields)}")
        return config

    def _load_processed_ids(self):
        if os.path.exists(self.processed_ids_file):
            with open(self.processed_ids_file, 'r') as f:
                return json.load(f)
        return []

    def _save_processed_ids(self):
        with open(self.processed_ids_file, 'w') as f:
            json.dump(self.processed_ids, f)

    def _parse_formsubmit_content(self, content):
        form_data = {}
        try:
            # Split content into lines and look for key-value pairs
            lines = content.split('\n')
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip().lower()
                    value = value.strip()
                    if key and value:
                        form_data[key] = value

            # Try extracting email using a strict pattern (avoid formsubmit.co matches)
            email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', content)
            if email_match:
                extracted_email = email_match.group(0).strip()
                if 'formsubmit.co' not in extracted_email.lower():
                    form_data['email'] = extracted_email

            # Extract name
            name_match = re.search(r'(?i)(?:name|full[\s\-]*name)\s*:\s*([^\n\r]+)', content)
            if name_match:
                form_data['name'] = name_match.group(1).strip()

            # Extract message
            message_match = re.search(r'(?i)(?:message|comments?)\s*:\s*(.*?)(?=\n\S|\Z)', content, re.DOTALL)
            if message_match:
                form_data['message'] = message_match.group(1).strip()

            logger.debug(f"Parsed form data: {form_data}")
        except Exception as e:
            logger.error(f"Error parsing FormSubmit content: {str(e)}")
        return form_data

    def _extract_sender_info(self, msg):
        sender_email = None
        sender_name = None
        message_content = ""
        form_data = {}

        email_body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ["text/plain", "text/html"]:
                    payload = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    email_body += payload
        else:
            payload = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            email_body = payload

        form_data = self._parse_formsubmit_content(email_body)
        sender_email = form_data.get('email')
        sender_name = form_data.get('name')
        message_content = form_data.get('message') or ""

        if not sender_email:
            # Check Reply-To header first
            if msg.get('Reply-To'):
                _, sender_email = parseaddr(msg.get('Reply-To'))
            elif msg.get('From'):
                _, sender_email = parseaddr(msg.get('From'))

        # Ensure we're not replying to formsubmit.co
        if sender_email and 'formsubmit.co' in sender_email.lower():
            logger.warning("Detected formsubmit.co email in body/header; skipping...")
            sender_email = None

        logger.info(f"Extracted email: {sender_email}, name: {sender_name}, message length: {len(message_content)}")
        return sender_email, sender_name, message_content, form_data

    def _generate_ai_response(self, name, message_content, form_data):
        try:
            prompt = f"""You are an AI assistant representing {self.config['company_info']['name']}, a {self.config['company_info']['description']}.
Write a {self.config['response_tone']} response email to a website visitor who submitted a contact form.
Visitor's name: {name or 'Unknown'}
Visitor's message: \"{message_content}\"
Additional form fields: {json.dumps(form_data)}
Your response should:
1. Be professional and helpful
2. Acknowledge their specific inquiry
3. Provide relevant information based on their message
4. Include a signature as from the {self.config['company_info']['team_name']}
Keep the response concise (150-200 words maximum)."""
            response = openai.ChatCompletion.create(
                model=self.config['ai_model'],
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that writes professional email responses."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error generating AI response: {str(e)}")
            return self._get_default_response(name)

    def _get_default_response(self, name):
        current_date = datetime.now().strftime("%Y-%m-%d")
        name_greeting = name if name else "there"
        return f"""Dear {name_greeting},
Thank you for contacting {self.config['company_info']['name']} on {current_date}. We have received your inquiry and will get back to you with a detailed response shortly.
Best regards,
The {self.config['company_info']['team_name']}
{self.config['company_info']['name']}"""

    def check_emails(self):
        logger.info("Starting email check process with AI response generation")
        try:
            mail = imaplib.IMAP4_SSL(self.config['imap_server'], self.config['imap_port'])
            mail.login(self.config['email_address'], self.config['password'])
            mail.select('inbox')
            status, messages = mail.search(None, f'(FROM "{self.config["formsubmit_identifier"]}" UNSEEN)')
            if status != 'OK':
                return

            for num in messages[0].split():
                status, data = mail.fetch(num, '(RFC822)')
                if status != 'OK':
                    continue
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)
                message_id = msg.get('Message-ID', '')
                if message_id in self.processed_ids:
                    continue

                sender_email, sender_name, message_content, form_data = self._extract_sender_info(msg)

                if sender_email:
                    ai_response = self._generate_ai_response(sender_name, message_content, form_data)
                    self._send_reply(sender_email, ai_response)
                    self.processed_ids.append(message_id)
                    self._save_processed_ids()
                    mail.store(num, '+FLAGS', r'\Seen')
                else:
                    logger.warning("No valid sender email found; skipping reply.")

            mail.close()
            mail.logout()
        except Exception as e:
            logger.error(f"Error checking emails: {str(e)}")

    def _send_reply(self, recipient_email, response_body):
        try:
            msg = MIMEMultipart()
            msg['From'] = self.config['email_address']
            msg['To'] = recipient_email
            msg['Subject'] = self.config['auto_reply_subject']
            msg.attach(MIMEText(response_body, 'plain'))

            with smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port']) as server:
                server.starttls()
                server.login(self.config['email_address'], self.config['password'])
                server.send_message(msg)

            logger.info(f"Sent AI-generated reply to {recipient_email}")
        except Exception as e:
            logger.error(f"Error sending reply: {str(e)}")

if __name__ == "__main__":
    try:
        bot = AIFormSubmitEmailBot()
        bot.check_emails()
        logger.info("AI Email bot run completed successfully")
    except Exception as e:
        logger.error(f"AI Email bot failed: {str(e)}")
