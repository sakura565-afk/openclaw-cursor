"""
Email Archiver — сохраняет вложения из Gmail на Google Drive.
"""
import os, json, base64, mimetypes, logging
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv

# Composio SDK
from composio_client import ComposioClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
HASH_FILE = SCRIPT_DIR / '.email_archiver_hashes.json'

def get_last_month_range():
    today = date.today()
    first_day_this = date(today.year, today.month, 1)
    last_month = date(today.year if today.month > 1 else today.year - 1, 
                       (today.month - 1) or 12, 1)
    return last_month.isoformat(), first_day_this.isoformat()

def main():
    load_dotenv()
    api_key = os.getenv('COMPOSIO_API_KEY')
    if not api_key:
        log.error("COMPOSIO_API_KEY not set"); return

    client = ComposioClient(api_key=api_key)
    
    from_date, to_date = get_last_month_range()
    query = f"has:attachment after:{from_date} before:{to_date}"
    log.info(f"Searching Gmail: {query}")

    # Search emails
    emails = client.gmail.search(query=query, limit=50)
    if not emails:
        log.info("No emails found"); return

    # Load hash file
    saved_hashes = {}
    if HASH_FILE.exists():
        saved_hashes = json.loads(HASH_FILE.read_text())

    # Drive folder: Archive/YYYY-MM/
    folder_name = f"Archive/{datetime.now().strftime('%Y-%m')}/"
    
    archived = 0
    for email in emails:
        email_id = email.get('id', '')
        payload_hashes = []
        
        for attachment in email.get('attachments', []):
            att_id = attachment.get('id', '')
            filename = attachment.get('filename', 'unknown')
            mime_type = attachment.get('mimeType', 'application/octet-stream')
            
            # Download attachment
            data_b64 = client.gmail.get_attachment(email_id=email_id, attachment_id=att_id)
            file_data = base64.b64decode(data_b64)
            
            # Check hash
            import hashlib
            file_hash = hashlib.sha256(file_data).hexdigest()
            if file_hash in saved_hashes:
                log.info(f"Skip {filename} (already archived)")
                continue
            
            # Upload to Drive
            folder_id = client.google_drive.find_or_create_folder(path=folder_name)
            
            # Handle duplicate filename
            base_name, ext = os.path.splitext(filename)
            counter = 1
            final_name = filename
            while True:
                existing = client.google_drive.list_files(folder_id=folder_id, name=final_name)
                if not existing:
                    break
                final_name = f"{base_name}_{counter}{ext}"
                counter += 1
            
            uploaded = client.google_drive.upload_file(
                file_name=final_name,
                file_data=file_data,
                parent_folder_id=folder_id,
                mime_type=mime_type
            )
            log.info(f"Uploaded {final_name} → {folder_name}")
            
            # Mark email as read/trash
            client.gmail.mark_as_read(email_id=email_id)
            
            saved_hashes[file_hash] = {'email_id': email_id, 'filename': final_name}
            payload_hashes.append(file_hash)
            archived += 1

    # Save hashes
    HASH_FILE.write_text(json.dumps(saved_hashes, indent=2))
    log.info(f"Done. Archived {archived} files. Total tracked: {len(saved_hashes)}")

if __name__ == '__main__':
    main()