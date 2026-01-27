#!/bin/bash
# Check archive file status

DB_PATH="/Users/michaelhollander/github/feldenkrais/knowledge.db"
BUCKET="feldenkrais-prod"

echo "Fetching archive file list from S3..."
aws s3 ls s3://$BUCKET/archive/ > /tmp/archive_files.txt
ARCHIVE_COUNT=$(wc -l < /tmp/archive_files.txt | tr -d ' ')
echo "Found $ARCHIVE_COUNT files in archive/"

echo ""
echo "Querying SQLite database..."
echo ""

# Document summary
echo "========================================================================"
echo "DOCUMENT SUMMARY"
echo "========================================================================"
sqlite3 "$DB_PATH" "
SELECT
    CASE source_id
        WHEN 1 THEN 'OTHER'
        WHEN 2 THEN 'YOUTUBE'
        WHEN 3 THEN 'FELDENKRAIS_MANHATTAN'
    END as source,
    COUNT(*) as count
FROM document
GROUP BY source_id
ORDER BY source_id;
"

echo ""
echo "Total documents:"
sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM document;"

echo ""
echo "========================================================================"
echo "ANALYSES & TRANSCRIPTION STATUS"
echo "========================================================================"

echo ""
echo "Analyses:"
sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM analysis;"

echo ""
echo "Transcription jobs by status:"
sqlite3 "$DB_PATH" "
SELECT status, COUNT(*)
FROM transcription_job
GROUP BY status;
"

echo ""
echo "========================================================================"
echo "FM LESSON STATUS"
echo "========================================================================"
sqlite3 "$DB_PATH" "
SELECT status, COUNT(*)
FROM fm_lesson_status
GROUP BY status;
"

echo ""
echo "========================================================================"
echo "YOUTUBE QUEUE"
echo "========================================================================"
sqlite3 "$DB_PATH" "
SELECT status, COUNT(*)
FROM youtube_queue
GROUP BY status;
"

echo ""
echo "========================================================================"
echo "ARCHIVE FILE CHECK"
echo "========================================================================"

# Get s3_keys from database and check against archive list
echo ""
echo "Checking which documents have archive files..."

sqlite3 "$DB_PATH" "SELECT s3_key FROM document WHERE s3_key IS NOT NULL;" | while read s3_key; do
    # Extract just the filename from archive/UUID.mp3
    filename=$(basename "$s3_key")
    if ! grep -q "$filename" /tmp/archive_files.txt 2>/dev/null; then
        echo "MISSING: $s3_key"
    fi
done > /tmp/missing_archives.txt

MISSING_COUNT=$(wc -l < /tmp/missing_archives.txt | tr -d ' ')
echo ""
echo "Missing archive files: $MISSING_COUNT"

if [ "$MISSING_COUNT" -gt 0 ]; then
    echo ""
    echo "First 20 missing files:"
    head -20 /tmp/missing_archives.txt
fi

# Show missing by source
echo ""
echo "========================================================================"
echo "MISSING FILES BY SOURCE"
echo "========================================================================"

for SOURCE_ID in 1 2 3; do
    case $SOURCE_ID in
        1) SOURCE_NAME="OTHER" ;;
        2) SOURCE_NAME="YOUTUBE" ;;
        3) SOURCE_NAME="FELDENKRAIS_MANHATTAN" ;;
    esac

    COUNT=$(sqlite3 "$DB_PATH" "
        SELECT COUNT(*) FROM document
        WHERE source_id = $SOURCE_ID
        AND s3_key NOT IN (
            SELECT s3_key FROM document
            WHERE s3_key IS NOT NULL
        )
    " 2>/dev/null)

    # Check each document in this source
    MISSING=0
    sqlite3 "$DB_PATH" "SELECT s3_key FROM document WHERE source_id = $SOURCE_ID AND s3_key IS NOT NULL;" | while read s3_key; do
        filename=$(basename "$s3_key")
        if ! grep -q "$filename" /tmp/archive_files.txt 2>/dev/null; then
            echo "$s3_key" >> /tmp/missing_${SOURCE_ID}.txt
        fi
    done

    if [ -f /tmp/missing_${SOURCE_ID}.txt ]; then
        MISSING=$(wc -l < /tmp/missing_${SOURCE_ID}.txt | tr -d ' ')
    else
        MISSING=0
    fi

    echo "$SOURCE_NAME: $MISSING missing"
done

echo ""
echo "Done!"
