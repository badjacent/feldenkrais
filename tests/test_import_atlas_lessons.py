from ops.import_atlas_lessons import (
    eligible_media,
    eligible_urls,
    plan_intake,
    reconcile_atlas_origins,
    youtube_id,
)


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def execute(self, query, params=None):
        if query.startswith("UPDATE youtube_queue"):
            queue_id = params[1]
            for row in self.db.queue:
                if row["id"] == queue_id:
                    row["origin"] = params[0]
                    self.rowcount = 1
                    return
            self.rowcount = 0
            return
        self.rows = self.db.documents if "document" in query else self.db.queue

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeDatabase:
    def __init__(self):
        self.documents = [{"source_url": "https://youtu.be/existingdoc"}]
        self.queue = [
            {
                "id": "queue-1",
                "url": "https://youtube.com/watch?v=queuedvideo",
                "origin": None,
            }
        ]

    def get_cursor(self, commit=False):
        return FakeCursor(self)


def item(video_id, **overrides):
    value = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "entry_kind": "resource",
        "access": "free",
        "content_kind": "video_lesson",
        "status": "live",
    }
    value.update(overrides)
    return value


def test_youtube_id_normalizes_common_public_urls():
    assert youtube_id("https://youtu.be/abc123?t=10") == "abc123"
    assert youtube_id("https://www.youtube.com/watch?v=abc123&list=x") == "abc123"
    assert youtube_id("https://youtube.com/shorts/abc123") == "abc123"
    assert youtube_id("https://example.com/watch?v=abc123") is None


def test_eligible_urls_enforces_public_free_video_boundary():
    catalog = [
        item("yes"),
        item("paid", access="paid"),
        item("interview", content_kind="interview"),
        item("dead", status="dead"),
    ]

    assert eligible_urls(catalog) == {
        "yes": "https://www.youtube.com/watch?v=yes"
    }


def test_eligible_media_accepts_free_non_youtube_lessons_only():
    base = {
        "entry_kind": "resource",
        "access": "free",
        "content_kind": "audio_lesson",
        "status": "live",
        "platform": "soundcloud",
        "title": "A lesson",
    }
    catalog = [
        {**base, "url": "https://soundcloud.com/example/lesson"},
        {**base, "url": "https://example.com/lesson.mp3", "platform": "website"},
        {**base, "url": "https://example.com/index", "platform": "website", "format": "unknown"},
        {**base, "url": "https://example.com/guide.pdf", "platform": "website", "content_kind": "podcast_episode"},
        {**base, "url": "https://example.com/paid.mp3", "access": "paid"},
        {**base, "url": "https://example.com/article", "content_kind": "article"},
        {**base, "url": "https://youtube.com/watch?v=abc123", "platform": "youtube"},
    ]

    assert set(eligible_media(catalog)) == {
        "https://soundcloud.com/example/lesson",
        "https://example.com/lesson.mp3",
    }


def test_plan_intake_excludes_documents_and_existing_queue_entries():
    catalog = [item("existingdoc"), item("queuedvideo"), item("newvideo")]

    report = plan_intake(FakeDatabase(), catalog)

    assert report == {
        "eligible": 3,
        "already_document": 1,
        "already_queued": 1,
        "pending": ["https://www.youtube.com/watch?v=newvideo"],
    }


def test_reconcile_atlas_origins_marks_previously_queued_eligible_lessons():
    db = FakeDatabase()

    updated = reconcile_atlas_origins(db, [item("queuedvideo")])

    assert updated == 1
    assert db.queue[0]["origin"] == "feldenkrais_atlas"
