from kb import analysis_wake


def test_wake_and_sleep_create_and_remove_the_file(tmp_path, monkeypatch):
    path = tmp_path / "ops" / "analysis-active"
    monkeypatch.setenv("ANALYSIS_WAKE_FILE", str(path))

    analysis_wake.wake()
    assert path.exists()
    analysis_wake.sleep()
    assert not path.exists()
    analysis_wake.sleep()  # already asleep: no error


def test_without_the_setting_nothing_happens(tmp_path, monkeypatch):
    monkeypatch.delenv("ANALYSIS_WAKE_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    analysis_wake.wake()
    analysis_wake.sleep()
    assert list(tmp_path.iterdir()) == []
