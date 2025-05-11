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
        """Initialize the email bot with configuration from environment variables."""
        self.config = self._load_config()
        self.processed_ids_file = 'processed_emails.json'
        self.processed_ids = self._load_processed_ids()
        
        # Initialize OpenAI client
        openai.api_key = self.config['openai_api_key']

    def _load_config(self):
        """Load configuration from environment variables."""
        try:
            config = {
                # Email server settings
                'imap_server': os.environ.get('IMAP_SERVER'),
                'imap_port': int(os.environ.get('IMAP_PORT', 993)),
                'smtp_server': os.environ.get('SMTP_SERVER'),
                'smtp_port': int(os.environ.get('SMTP_PORT', 587)),
                
                # Email credentials
                'email_address': os.environ.get('EMAIL_ADDRESS'),
                'password': os.environ.get('EMAIL_PASSWORD'),
                
                # FormSubmit settings
                'formsubmit_identifier': os.environ.get('FORMSUBMIT_IDENTIFIER', 'formsubmit.co'),
                'auto_reply_subject': os.environ.get('AUTO_REPLY_SUBJECT', 'Thank you for contacting us'),
                
                # OpenAI settings
                'openai_api_key': os.environ.get('OPENAI_API_KEY'),
                'ai_model': os.environ.get('AI_MODEL', 'gpt-4o'),
                'response_tone': os.environ.get('RESPONSE_TONE', 'friendly and professional'),
                
                # Company info
                'company_info': {
                    'name': os.environ.get('COMPANY_NAME', 'Our Company'),
                    'description': os.environ.get('COMPANY_DESCRIPTION', 'company that values your inquiry'),
                    'team_name': os.environ.get('TEAM_NAME', 'Customer Support Team')
                }
            }
            
            # Verify required fields
            required_fields = ['imap_server', 'smtp_server', 'email_address', 'password', 'openai_api_key']
            missing_fields = [field for field in required_fields if not config[field]]
            
            if missing_fields:
                raise ValueError(f"Missing required environment variables: {', '.join(missing_fields)}")
                
            return config
            
        except Exception as e:
            logger.error(f"Error loading configuration from environment variables: {str(e)}")
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
        
        # Get the email body content
        email_body = ""
        
        # Check if this is a multipart message
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain" or content_type == "text/html":
                    payload = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    email_body += payload
                    # Extract form data from payload
                    form_data.update(self._parse_formsubmit_content(payload))
        else:
            # Not multipart
            payload = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            email_body = payload
            form_data.update(self._parse_formsubmit_content(payload))
        
        # FormSubmit typically includes the original sender's email in the form data
        # Look for "email:" field in the email body - this is the submitter's email
        sender_email = form_data.get('email')
        sender_name = form_data.get('name')
        message_content = form_data.get('message') or form_data.get('content') or ""
        
        # If we couldn't find email in form data, try more aggressive pattern matching
        if not sender_email:
            # Try to find any email pattern in the body
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            email_matches = re.findall(email_pattern, email_body)
            
            # Filter out the formsubmit.co emails to get the actual sender
            for match in email_matches:
                if self.config['formsubmit_identifier'] not in match.lower():
                    sender_email = match
                    break
        
        # If still no sender email, check for "Reply-To" header (FormSubmit sometimes adds this)
        if not sender_email and msg.get('Reply-To'):
            reply_to = msg.get('Reply-To')
            _, sender_email = parseaddr(reply_to)
        
        # Final fallback - extract any emails from headers except formsubmit.co ones
        if not sender_email:
            for header in ['From', 'Reply-To', 'Return-Path']:
                if msg.get(header):
                    _, extracted_email = parseaddr(msg.get(header))
                    if extracted_email and self.config['formsubmit_identifier'] not in extracted_email.lower():
                        sender_email = extracted_email
                        break
        
        logger.info(f"Extracted email: {sender_email}, name: {sender_name}, message length: {len(message_content)}")
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
            # FormSubmit typically formats submissions as field labels followed by values
            email_match = re.search(r'(?i)email\s*:\s*([^\n@]+@[^\n]+)', content)
            if email_match:
                form_data['email'] = email_match.group(1).strip()
                
            name_match = re.search(r'(?i)(?:name|full[\s-]*name)\s*:\s*([^\n]+)', content)
            if name_match:
                form_data['name'] = name_match.group(1).strip()
                
            message_match = re.search(r'(?i)(?:message|comments?)\s*:\s*([^\n].+?)(?:\n\w+\s*:|$)', content, re.DOTALL)
            if message_match:
                form_data['message'] = message_match.group(1).strip()
                
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
            search_criteria = f'(FROM "{self.config["formsubmit_identifier"]}" UNSEEN)'
            logger.info(f"Searching with criteria: {search_criteria}")
            status, messages = mail.search(None, search_criteria)
            
            if status != 'OK':
                logger.warning("No new messages or search failed")
                return
            
            message_nums = messages[0].split()
            if not message_nums:
                logger.info("No new FormSubmit messages found")
                return
                
            logger.info(f"Found {len(message_nums)} new FormSubmit messages")
            
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
                
                subject = msg.get('Subject', '')
                logger.info(f"Processing email with subject: {subject}")
                
                # Extract sender information and message content from FormSubmit email
                sender_email, sender_name, message_content, form_data = self._extract_sender_info(msg)
                
                if sender_email:
                    logger.info(f"Will send reply to: {sender_email}")
                    
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
