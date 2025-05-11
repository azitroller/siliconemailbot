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
            'ai_model': os.environ.get('AI_MODEL', 'gpt-4'),
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
            # Split into lines and remove empty lines
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            
            # Handle both tab-separated and newline-separated formats
            if 'Name\tValue' in content:
                # Tab-separated format (like in your example)
                start_idx = lines.index('Name\tValue') + 1 if 'Name\tValue' in lines else 0
                for line in lines[start_idx:]:
                    if '\t' in line:
                        name, value = line.split('\t', 1)
                        form_data[name.strip().lower()] = value.strip()
            else:
                # Newline-separated format (common alternative)
                current_field = None
                for line in lines:
                    if line.endswith(':'):
                        current_field = line[:-1].strip().lower()
                    elif current_field:
                        form_data[current_field] = line.strip()
                        current_field = None
            
            # Special handling for email field if not found
            if 'email' not in form_data:
                # Look for any email pattern in the content that's not formsubmit
                email_pattern = r'(?i)(?<!formsubmit\.co)[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                matches = re.findall(email_pattern, content)
                if matches:
                    form_data['email'] = matches[0]

        except Exception as e:
            logger.error(f"Error parsing FormSubmit content: {str(e)}")
        return form_data

    def _extract_sender_info(self, msg):
        sender_email = None
        sender_name = None
        form_data = {}
        email_body = ""
        
        # First parse the email body for form data
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type in ["text/plain", "text/html"]:
                    try:
                        payload = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        email_body += payload
                        form_data.update(self._parse_formsubmit_content(payload))
                    except Exception as e:
                        logger.error(f"Error decoding part: {str(e)}")
        else:
            try:
                payload = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                email_body = payload
                form_data.update(self._parse_formsubmit_content(payload))
            except Exception as e:
                logger.error(f"Error decoding payload: {str(e)}")

        # Get email from form data first
        sender_email = form_data.get('email')
        sender_name = form_data.get('name')
        message_content = form_data.get('message') or form_data.get('content') or ""

        # If no email in form data, check headers but exclude formsubmit.co addresses
        if not sender_email:
            # Check Reply-To header first (but exclude formsubmit addresses)
            reply_to = msg.get('Reply-To')
            if reply_to:
                _, reply_to_email = parseaddr(reply_to)
                if reply_to_email and self.config['formsubmit_identifier'] not in reply_to_email.lower():
                    sender_email = reply_to_email

            # If still no email, check From header (but exclude formsubmit addresses)
            if not sender_email:
                from_header = msg.get('From')
                if from_header:
                    _, from_email = parseaddr(from_header)
                    if from_email and self.config['formsubmit_identifier'] not in from_email.lower():
                        sender_email = from_email

        # If we still don't have an email, look for any email in the body that's not formsubmit
        if not sender_email:
            email_pattern = r'(?i)(?<!formsubmit\.co)[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            matches = re.findall(email_pattern, email_body)
            if matches:
                sender_email = matches[0]

        logger.info(f"Extracted form data: {form_data}")
        logger.info(f"Using email: {sender_email}, name: {sender_name}")
        return sender_email, sender_name, message_content, form_data

    def _generate_ai_response(self, name, message_content, form_data):
        try:
            prompt = f"""You are an AI assistant representing {self.config['company_info']['name']}, a {self.config['company_info']['description']}.\n\nWrite a {self.config['response_tone']} response email to a website visitor who submitted a contact form.\n\nVisitor's name: {name or 'Unknown'}\nVisitor's message: \"{message_content}\"\n\nAdditional form fields: {json.dumps(form_data)}\n\nYour response should:\n1. Be professional and helpful\n2. Acknowledge their specific inquiry\n3. Provide relevant information based on their message\n4. Include a signature as from the {self.config['company_info']['team_name']}\n\nKeep the response concise (150-200 words maximum)."""

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
        return f"""Dear {name_greeting},\n\nThank you for contacting {self.config['company_info']['name']} on {current_date}. We have received your inquiry and will get back to you with a detailed response shortly.\n\nBest regards,\nThe {self.config['company_info']['team_name']}\n{self.config['company_info']['name']}"""

    def check_emails(self):
        logger.info("Starting email check process with AI response generation")
        try:
            mail = imaplib.IMAP4_SSL(self.config['imap_server'], self.config['imap_port'])
            mail.login(self.config['email_address'], self.config['password'])
            mail.select('inbox')
            
            # Search for unseen emails from formsubmit
            status, messages = mail.search(None, f'(FROM "{self.config["formsubmit_identifier"]}" UNSEEN)')
            if status != 'OK':
                logger.info("No new formsubmit emails found")
                return
            
            for num in messages[0].split():
                try:
                    status, data = mail.fetch(num, '(RFC822)')
                    if status != 'OK':
                        continue
                    
                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    message_id = msg.get('Message-ID', '')
                    
                    if message_id in self.processed_ids:
                        continue
                    
                    sender_email, sender_name, message_content, form_data = self._extract_sender_info(msg)
                    
                    if not sender_email:
                        logger.warning(f"No valid sender email found for message {message_id}")
                        continue
                    
                    ai_response = self._generate_ai_response(sender_name, message_content, form_data)
                    self._send_reply(sender_email, ai_response)
                    
                    self.processed_ids.append(message_id)
                    self._save_processed_ids()
                    mail.store(num, '+FLAGS', r'\Seen')
                    
                except Exception as e:
                    logger.error(f"Error processing email: {str(e)}")
                    continue
            
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
            
            logger.info(f"Successfully sent reply to {recipient_email}")
            
        except Exception as e:
            logger.error(f"Error sending reply to {recipient_email}: {str(e)}")

if __name__ == "__main__":
    try:
        bot = AIFormSubmitEmailBot()
        bot.check_emails()
        logger.info("AI Email bot run completed successfully")
    except Exception as e:
        logger.error(f"AI Email bot failed: {str(e)}")
