#!/usr/bin/env python3
"""
Monitor ATM book processing progress and disable triggers when complete.
Also exports completed transcripts to data/lessons/ for the web interface.
"""
import os
import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime

# Add src/kb to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from database import Database
from config import Config
from storage import Storage

LESSONS_DIR = Path(__file__).parent.parent / "data" / "lessons"


def get_atm_status(db):
    """Get status of ATM book transcriptions."""
    with db.get_cursor() as cur:
        # Find ATM book documents (tagged with atm-book or from feldenkrais-project)
        cur.execute("""
            SELECT d.id, d.title, d.status,
                   tj.status as tj_status, tj.chunk_index, tj.total_chunks
            FROM document d
            LEFT JOIN transcription_job tj ON tj.document_id = d.id
            WHERE d.title LIKE '%Feldenkrais Proj%' 
               OR d.title LIKE '%(Patrons)%'
               OR d.tags::text LIKE '%atm-book%'
            ORDER BY d.created_at DESC
        """)
        return cur.fetchall()


def get_transcript(db, document_id):
    """Get completed transcript for a document."""
    with db.get_cursor() as cur:
        cur.execute("""
            SELECT a.data
            FROM analysis a
            JOIN analysis_type at ON a.type_id = at.id
            WHERE a.document_id = %s AND at.name = 'TRANSCRIPTION'
        """, (document_id,))
        row = cur.fetchone()
        if row and row['data']:
            data = row['data']
            if isinstance(data, str):
                data = json.loads(data)
            return data.get('text', '')
    return None


def export_transcript(title, text):
    """Export transcript to lessons directory."""
    # Clean filename
    filename = title.lower()
    for remove in ['(patrons)', '- feldenkrais project', 'feldenkrais proj']:
        filename = filename.replace(remove, '')
    filename = filename.strip().replace(' ', '_').replace('-', '_')
    filename = ''.join(c for c in filename if c.isalnum() or c == '_')
    filename = f"{filename}.txt"
    
    filepath = LESSONS_DIR / filename
    filepath.write_text(f"# {title}\n\n{text}")
    return filepath


def disable_triggers():
    """Disable EventBridge triggers."""
    rules = [
        "feldenkrais-loader-prod",
        "feldenkrais-transcription-prod",
        "feldenkrais-youtube-prod"
    ]
    for rule in rules:
        subprocess.run(["aws", "events", "disable-rule", "--name", rule], check=True)
        print(f"  Disabled: {rule}")


def main():
    config = Config.from_env()
    db = Database(config.database_url)
    
    print(f"\n=== ATM Book Processing Monitor ===")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Lessons dir: {LESSONS_DIR}\n")
    
    while True:
        status = get_atm_status(db)
        
        pending = [s for s in status if s['tj_status'] == 'pending']
        processing = [s for s in status if s['tj_status'] == 'processing']
        completed = [s for s in status if s['tj_status'] == 'completed']
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] "
              f"Pending: {len(pending)}, Processing: {len(processing)}, "
              f"Completed: {len(completed)}")
        
        if not pending and not processing:
            print("\n=== All transcriptions complete! ===\n")
            
            # Export transcripts
            print("Exporting transcripts to lessons directory...")
            for doc in completed:
                text = get_transcript(db, doc['id'])
                if text:
                    filepath = export_transcript(doc['title'], text)
                    print(f"  Exported: {filepath.name}")
            
            # Disable triggers
            print("\nDisabling EventBridge triggers...")
            disable_triggers()
            
            print("\nDone!")
            break
        
        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    main()
