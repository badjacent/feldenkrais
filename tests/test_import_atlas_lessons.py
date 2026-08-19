from ops.import_atlas_lessons import eligible_urls, plan_intake, youtube_id


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def execute(self, query):
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
        self.queue = [{"url": "https://youtube.com/watch?v=queuedvideo"}]

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


def test_plan_intake_excludes_documents_and_existing_queue_entries():
    catalog = [item("existingdoc"), item("queuedvideo"), item("newvideo")]

    report = plan_intake(FakeDatabase(), catalog)

    assert report == {
        "eligible": 3,
        "already_document": 1,
        "already_queued": 1,
        "pending": ["https://www.youtube.com/watch?v=newvideo"],
    }
