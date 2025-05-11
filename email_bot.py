import os
import time
import imaplib
import email
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import logging
import openai

# Import environment loader for local development
from load_env import load_environment

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('email_bot')

# Email configuration
EMAIL_ADDRESS = os.environ.get('EMAIL_ADDRESS')
EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
IMAP_SERVER = 'imap.gmail.com'
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587

# OpenAI configuration
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
openai.api_key = OPENAI_API_KEY

def connect_to_inbox():
    """Connect to the email inbox"""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select('inbox')
        return mail
    except Exception as e:
        logger.error(f"Error connecting to inbox: {e}")
        return None

def get_unread_emails(mail, hours_ago=24):
    """Get unread emails from the last specified hours"""
    if not mail:
        return []
    
    # Calculate the date from hours ago
    date_since = (datetime.now() - timedelta(hours=hours_ago)).strftime("%d-%b-%Y")
    
    try:
        # Search for unread emails since the specified date
        result, data = mail.search(None, '(UNSEEN)', f'(SINCE {date_since})')
        
        if result != 'OK':
            logger.warning(f"No messages found or search failed: {result}")
            return []
        
        email_ids = data[0].split()
        logger.info(f"Found {len(email_ids)} unread messages")
        
        emails = []
        for email_id in email_ids:
            result, message_data = mail.fetch(email_id, '(RFC822)')
            if result != 'OK':
                continue
                
            raw_email = message_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # Extract email details
            subject = msg['subject']
            from_address = msg['from']
            date = msg['date']
            
            # Get email body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        body = part.get_payload(decode=True).decode()
                        break
            else:
                body = msg.get_payload(decode=True).decode()
            
            # Check if this is a contact form submission
            if "New Silicon Computers Contact Form Submission" in subject:
                emails.append({
                    'id': email_id,
                    'subject': subject,
                    'from': from_address,
                    'date': date,
                    'body': body
                })
        
        return emails
    except Exception as e:
        logger.error(f"Error fetching emails: {e}")
        return []

def parse_contact_form_email(email_body):
    """Parse the contact form email to extract relevant information"""
    # Initialize data dictionary
    data = {
        'name': '',
        'email': '',
        'company': '',
        'phone': '',
        'subject': '',
        'message': ''
    }
    
    # Simple parsing based on expected format from FormSubmit
    lines = email_body.split('\n')
    current_field = None
    
    for line in lines:
        line = line.strip()
        
        # Skip empty lines
        if not line:
            continue
            
        # Check if this is a field header
        if ':' in line and line.split(':', 1)[0].strip() in ['Name', 'Email', 'Company', 'Phone', 'Subject', 'Message']:
            parts = line.split(':', 1)
            field = parts[0].strip().lower()
            value = parts[1].strip() if len(parts) > 1 else ''
            
            if field in data:
                data[field] = value
                current_field = field
        elif current_field == 'message':
            # Append to message if we're in the message field
            data['message'] += '\n' + line
    
    return data

def generate_ai_response(contact_data):
    """Generate an AI response using OpenAI"""
    try:
        # Create a prompt for the AI
        prompt = f"""Generate a professional and friendly email response to a website contact form submission with the following details:
        
        Name: {contact_data['name']}
        Email: {contact_data['email']}
        Company: {contact_data['company']}
        Phone: {contact_data['phone']}
        Subject: {contact_data['subject']}
        Message: {contact_data['message']}
        
        The response should:
        1. Thank them for contacting Silicon Computers
        2. Acknowledge their specific inquiry
        3. Provide a brief, helpful response that shows we understand their needs
        4. Mention that a team member will follow up with more detailed information soon
        5. Include a professional sign-off
        
        Format the response as plain text suitable for an email body.
        """
        
        # Call the OpenAI API
        response = openai.ChatCompletion.create(
            model="gpt-4",  # or another appropriate model
            messages=[
                {"role": "system", "content": "You are a professional customer service representative for Silicon Computers, a company that specializes in custom software development, IT consulting, and technology solutions."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        # Extract the generated response
        ai_response = response.choices[0].message.content.strip()
        return ai_response
    except Exception as e:
        logger.error(f"Error generating AI response: {e}")
        # Fallback response in case of API failure
        return f"""Dear {contact_data['name']},

Thank you for contacting Silicon Computers. We have received your inquiry and a member of our team will get back to you shortly.

Best regards,
The Silicon Computers Team
"""

def send_email_response(to_email, name, ai_response):
    """Send an email response"""
    try:
        # Create message container
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = to_email
        msg['Subject'] = f"Thank you for contacting Silicon Computers, {name}"
        
        # Add body to email
        msg.attach(MIMEText(ai_response, 'plain'))
        
        # Create secure connection and send email
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"Response email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False

def mark_as_read(mail, email_id):
    """Mark an email as read"""
    try:
        mail.store(email_id, '+FLAGS', '\\Seen')
        return True
    except Exception as e:
        logger.error(f"Error marking email as read: {e}")
        return False

def main():
    """Main function to run the email bot"""
    logger.info("Starting email bot")
    
    # Load environment variables for local development
    load_environment()
    
    # Check for required environment variables
    if not all([EMAIL_ADDRESS, EMAIL_PASSWORD, OPENAI_API_KEY]):
        logger.error("Missing required environment variables. Please set EMAIL_ADDRESS, EMAIL_PASSWORD, and OPENAI_API_KEY.")
        return
    
    # Connect to inbox
    mail = connect_to_inbox()
    if not mail:
        return
    
    # Get unread emails
    unread_emails = get_unread_emails(mail)
    
    # Process each email
    for email_data in unread_emails:
        try:
            # Parse contact form data
            contact_data = parse_contact_form_email(email_data['body'])
            
            # Generate AI response
            ai_response = generate_ai_response(contact_data)
            
            # Send response email
            if contact_data['email']:
                success = send_email_response(contact_data['email'], contact_data['name'], ai_response)
                
                # Mark as read if response was sent successfully
                if success:
                    mark_as_read(mail, email_data['id'])
        except Exception as e:
            logger.error(f"Error processing email: {e}")
    
    # Logout
    try:
        mail.close()
        mail.logout()
    except Exception as e:
        logger.error(f"Error during logout: {e}")
    
    logger.info("Email bot finished")

if __name__ == "__main__":
    main()
