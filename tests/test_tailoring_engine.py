import os

from src.common.resume_schema import (
    ContactInfo,
    EducationEntry,
    ExperienceEntry,
    MasterResume,
    SkillItem,
)
from src.tailoring.engine import _validate_llm_content


def _sample_resume() -> MasterResume:
    return MasterResume(
        contact=ContactInfo(
            name="Test Person", headline="Full Stack Engineer", email="test@example.com",
            phone="+1 555 0100", location="Remote", linkedin="https://linkedin.com/in/test",
        ),
        summary="A test summary.",
        skills={"backend": [SkillItem(name="Node.js", level=5)]},
        experience=[
            ExperienceEntry(
                company="Co A", title="Developer", location="Remote",
                date_text="2020 - Present", start_date="2020", end_date=None,
                bullets=["Did thing one.", "Did thing two."],
            ),
            ExperienceEntry(
                company="Co B", title="Junior Developer", location="Remote",
                date_text="2018 - 2020", start_date="2018", end_date="2020",
                bullets=["Did another thing."],
            ),
        ],
        education=[EducationEntry(degree="BS CS", institution="Test U", date_text="2014 - 2018")],
        projects=[],
        spoken_languages=[],
    )


def test_validate_llm_content_accepts_matching_bullet_counts():
    resume = _sample_resume()
    content = {
        "summary": "Tailored summary.",
        "experience_bullets": [
            ["Rewrote thing one.", "Rewrote thing two."],
            ["Rewrote another thing."],
        ],
    }
    assert _validate_llm_content(resume, content) is True


def test_validate_llm_content_rejects_wrong_entry_count():
    resume = _sample_resume()
    content = {"summary": "x", "experience_bullets": [["only one entry"]]}
    assert _validate_llm_content(resume, content) is False


def test_validate_llm_content_rejects_wrong_bullet_count_within_entry():
    resume = _sample_resume()
    content = {
        "summary": "x",
        "experience_bullets": [
            ["only one bullet, should be two"],
            ["Rewrote another thing."],
        ],
    }
    assert _validate_llm_content(resume, content) is False


def test_validate_llm_content_rejects_empty_summary():
    resume = _sample_resume()
    content = {
        "summary": "   ",
        "experience_bullets": [
            ["Rewrote thing one.", "Rewrote thing two."],
            ["Rewrote another thing."],
        ],
    }
    assert _validate_llm_content(resume, content) is False


def test_validate_llm_content_rejects_non_string_bullets():
    resume = _sample_resume()
    content = {
        "summary": "x",
        "experience_bullets": [
            ["Rewrote thing one.", 42],
            ["Rewrote another thing."],
        ],
    }
    assert _validate_llm_content(resume, content) is False


def test_claude_client_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from src.tailoring import claude_client

    assert claude_client.is_available() is False
    assert claude_client.request_tailored_content(_sample_resume(), "Title", "JD text") is None


def test_claude_client_available_with_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")
    from src.tailoring import claude_client

    assert claude_client.is_available() is True


def test_load_master_resume_raises_when_missing_locally_and_not_aws(monkeypatch, tmp_path):
    from src.tailoring import engine

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    missing_path = tmp_path / "does-not-exist.json"
    try:
        engine.load_master_resume(path=missing_path)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_load_master_resume_falls_back_to_s3_when_local_file_missing(monkeypatch, tmp_path):
    from src.tailoring import engine

    engine._s3_resume_cache = None  # reset the per-process cache between tests
    monkeypatch.setenv("STORAGE_BACKEND", "aws")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")

    resume = _sample_resume()
    body_bytes = resume.model_dump_json().encode("utf-8")

    class FakeBody:
        def read(self):
            return body_bytes

    class FakeS3Client:
        def get_object(self, Bucket, Key):
            captured["bucket_and_key"] = (Bucket, Key)
            return {"Body": FakeBody()}

    captured: dict = {}

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service, region_name=None: FakeS3Client())

    missing_path = tmp_path / "does-not-exist.json"
    loaded = engine.load_master_resume(path=missing_path)

    assert loaded.contact.name == resume.contact.name
    assert captured["bucket_and_key"] == ("test-bucket", engine.MASTER_RESUME_S3_KEY)
    engine._s3_resume_cache = None  # don't leak into other tests


def test_load_profile_photo_reads_local_file_when_present(tmp_path):
    from src.tailoring import engine

    photo_path = tmp_path / "photo.jpg"
    photo_path.write_bytes(b"fake-jpeg-bytes")

    assert engine.load_profile_photo(path=photo_path) == b"fake-jpeg-bytes"


def test_load_profile_photo_returns_none_when_missing_locally_and_not_aws(monkeypatch, tmp_path):
    from src.tailoring import engine

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    missing_path = tmp_path / "does-not-exist.jpg"
    assert engine.load_profile_photo(path=missing_path) is None  # optional - never raises


def test_load_profile_photo_falls_back_to_s3_when_local_file_missing(monkeypatch, tmp_path):
    from src.tailoring import engine

    engine._s3_photo_cache = None  # reset the per-process cache between tests
    monkeypatch.setenv("STORAGE_BACKEND", "aws")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")

    class FakeBody:
        def read(self):
            return b"fake-s3-photo-bytes"

    class FakeS3Client:
        def get_object(self, Bucket, Key):
            captured["bucket_and_key"] = (Bucket, Key)
            return {"Body": FakeBody()}

    captured: dict = {}

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service, region_name=None: FakeS3Client())

    missing_path = tmp_path / "does-not-exist.jpg"
    loaded = engine.load_profile_photo(path=missing_path)

    assert loaded == b"fake-s3-photo-bytes"
    assert captured["bucket_and_key"] == ("test-bucket", engine.PROFILE_PHOTO_S3_KEY)
    engine._s3_photo_cache = None  # don't leak into other tests


def test_load_profile_photo_returns_none_when_no_photo_uploaded_to_s3(monkeypatch, tmp_path):
    # A missing photo is a normal, expected state (not every resume needs
    # one) - a 404/NoSuchKey from S3 must degrade to None, not raise, so
    # the PDF still renders (just without a photo) instead of failing
    # the whole "Tailor Resume" click.
    from src.tailoring import engine
    from botocore.exceptions import ClientError

    engine._s3_photo_cache = None
    monkeypatch.setenv("STORAGE_BACKEND", "aws")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")

    class FakeS3Client:
        def get_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service, region_name=None: FakeS3Client())

    missing_path = tmp_path / "does-not-exist.jpg"
    assert engine.load_profile_photo(path=missing_path) is None
    engine._s3_photo_cache = None
