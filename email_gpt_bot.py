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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='email_bot.log'
)
logger = logging.getLogger('email_bot')

class AIFormSubmitEmailBot:
    def __init__(self, config_file='config.json'):
        """Initialize the email bot with configuration from a JSON file."""
        self.config = self._load_config(config_file)
        self.processed_ids_file = 'processed_emails.json'
        self.processed_ids = self._load_processed_ids()
        
        # Initialize OpenAI client
        openai.api_key = self.config['openai_api_key']

    def _load_config(self, config_file):
        """Load configuration from JSON file."""
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                
            required_fields = [
                'imap_server', 'imap_port', 'smtp_server', 'smtp_port',
                'email_address', 'password', 'formsubmit_identifier',
                'auto_reply_subject', 'openai_api_key', 'ai_model',
                'company_info', 'response_tone'
            ]
            
            for field in required_fields:
                if field not in config:
                    raise KeyError(f"Missing required configuration field: {field}")
                    
            return config
        except Exception as e:
            logger.error(f"Error loading configuration: {str(e)}")
            raise

    def _load_processed_ids(self):
        """Load previously processed email IDs from JSON file."""
        try:
            if os.path.exists(self.processed_ids_file):
                with open(self.processed_ids_file, 'r') as f:
                    return json.load(f)
            return []
        except Exception as e:
            logger.error(f"Error loading processed email IDs: {str(e)}")
            return []

    def _save_processed_ids(self):
        """Save processed email IDs to JSON file."""
        try:
            with open(self.processed_ids_file, 'w') as f:
                json.dump(self.processed_ids, f)
        except Exception as e:
            logger.error(f"Error saving processed email IDs: {str(e)}")

    def _extract_sender_info(self, msg):
        """Extract sender's email, name and message content from FormSubmit email."""
        # Initialize variables
        sender_email = None
        sender_name = None
        form_data = {}
        message_content = ""
        
        # Check if this is a multipart message
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain" or content_type == "text/html":
                    payload = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    # Extract form data from payload
                    form_data.update(self._parse_formsubmit_content(payload))
        else:
            # Not multipart
            payload = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            form_data.update(self._parse_formsubmit_content(payload))
        
        # Try to find email, name and message in form data
        sender_email = form_data.get('email')
        sender_name = form_data.get('name')
        message_content = form_data.get('message') or form_data.get('content') or ""
        
        if not sender_email:
            # As fallback, try to extract from headers
            from_header = msg.get("From", "")
            # Try to extract email from header
            if "<" in from_header and ">" in from_header:
                sender_email = from_header.split("<")[1].split(">")[0]
            else:
                sender_email = from_header
        
        return sender_email, sender_name, message_content, form_data

    def _parse_formsubmit_content(self, content):
        """Parse FormSubmit email content to extract form fields."""
        form_data = {}
        try:
            # First attempt: Parse structured "Field: Value" format
            lines = content.split('\n')
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip().lower()
                    value = value.strip()
                    form_data[key] = value
            
            # Second attempt: Look for common form patterns
            if not form_data.get('email') and 'email:' in content.lower():
                match = re.search(r'email:\s*([^\n]+)', content.lower())
                if match:
                    form_data['email'] = match.group(1).strip()
                    
            if not form_data.get('name') and 'name:' in content.lower():
                match = re.search(r'name:\s*([^\n]+)', content.lower())
                if match:
                    form_data['name'] = match.group(1).strip()
                    
            if not form_data.get('message') and 'message:' in content.lower():
                match = re.search(r'message:\s*([^\n]+)', content.lower())
                if match:
                    form_data['message'] = match.group(1).strip()
                    
        except Exception as e:
            logger.error(f"Error parsing FormSubmit content: {str(e)}")
        
        return form_data

    def _generate_ai_response(self, name, message_content, form_data):
        """Generate a personalized response using AI."""
        try:
            # Create prompt with context
            prompt = f"""You are an AI assistant representing {self.config['company_info']['name']}, a {self.config['company_info']['description']}. 
            
Write a {self.config['response_tone']} response email to a website visitor who submitted a contact form.

Visitor's name: {name or 'Unknown'}
Visitor's message: "{message_content}"

Additional form fields: {json.dumps(form_data)}

Your response should:
1. Be professional and helpful
2. Acknowledge their specific inquiry
3. Provide relevant information based on their message
4. Include a signature as from the {self.config['company_info']['team_name']}

Keep the response concise (150-200 words maximum).
"""

            # Make API call with retry mechanism
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = openai.ChatCompletion.create(
                        model=self.config['ai_model'],
                        messages=[
                            {"role": "system", "content": "You are a helpful assistant that writes professional email responses."},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=500,
                        temperature=0.7,
                    )
                    
                    ai_response = response.choices[0].message.content.strip()
                    logger.info(f"Successfully generated AI response (length: {len(ai_response)})")
                    return ai_response
                    
                except (openai.error.RateLimitError, openai.error.APIConnectionError) as e:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 5  # Exponential backoff
                        logger.warning(f"API error: {str(e)}. Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"Failed to generate AI response after {max_retries} attempts: {str(e)}")
                        # Fall back to default template
                        return self._get_default_response(name)
                        
                except Exception as e:
                    logger.error(f"Error generating AI response: {str(e)}")
                    return self._get_default_response(name)
                    
        except Exception as e:
            logger.error(f"Error in AI response generation: {str(e)}")
            return self._get_default_response(name)

    def _get_default_response(self, name):
        """Generate a default response when AI fails."""
        current_date = datetime.now().strftime("%Y-%m-%d")
        name_greeting = name if name else "there"
        
        return f"""Dear {name_greeting},

Thank you for contacting {self.config['company_info']['name']} on {current_date}. We have received your inquiry and will get back to you with a detailed response shortly.

Best regards,
The {self.config['company_info']['team_name']}
{self.config['company_info']['name']}
"""

    def check_emails(self):
        """Check for new FormSubmit emails and respond to them using AI."""
        logger.info("Starting email check process with AI response generation")
        
        try:
            # Connect to IMAP server
            mail = imaplib.IMAP4_SSL(self.config['imap_server'], self.config['imap_port'])
            mail.login(self.config['email_address'], self.config['password'])
            mail.select('inbox')
            
            # Search for emails from FormSubmit
            status, messages = mail.search(None, f'(FROM "{self.config["formsubmit_identifier"]}" UNSEEN)')
            
            if status != 'OK':
                logger.warning("No new messages or search failed")
                return
            
            message_nums = messages[0].split()
            if not message_nums:
                logger.info("No new FormSubmit messages found")
                return
                
            # Process each email
            for num in message_nums:
                status, data = mail.fetch(num, '(RFC822)')
                if status != 'OK':
                    logger.warning(f"Failed to fetch message {num}")
                    continue
                    
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)
                
                # Get message ID to avoid duplicate processing
                message_id = msg.get('Message-ID', '')
                if message_id in self.processed_ids:
                    logger.info(f"Skipping already processed email: {message_id}")
                    continue
                
                # Extract sender information and message content from FormSubmit email
                sender_email, sender_name, message_content, form_data = self._extract_sender_info(msg)
                
                if sender_email:
                    # Generate AI response
                    ai_response = self._generate_ai_response(sender_name, message_content, form_data)
                    
                    # Send reply to the original sender
                    self._send_reply(sender_email, ai_response)
                    
                    # Mark as processed
                    self.processed_ids.append(message_id)
                    self._save_processed_ids()
                    
                    # Mark as seen
                    mail.store(num, '+FLAGS', r'\Seen')
                    logger.info(f"Processed and replied to email from: {sender_email}")
                else:
                    logger.warning("Could not extract sender email from FormSubmit message")
            
            # Close connection
            mail.close()
            mail.logout()
            
        except Exception as e:
            logger.error(f"Error checking emails: {str(e)}")

    def _send_reply(self, recipient_email, response_body):
        """Send AI-generated reply to the original sender."""
        try:
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.config['email_address']
            msg['To'] = recipient_email
            msg['Subject'] = self.config['auto_reply_subject']
            
            # Attach body
            msg.attach(MIMEText(response_body, 'plain'))
            
            # Connect to SMTP server and send email
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
