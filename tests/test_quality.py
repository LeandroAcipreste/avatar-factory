from app.quality import recommendations


def test_quality_warns_for_short_low_resolution_video():
    notes = recommendations(5, 640, 360, 500_000)
    assert any("10 segundos" in note for note in notes)
    assert any("720p" in note for note in notes)


def test_quality_accepts_good_video():
    notes = recommendations(60, 1920, 1080, 20_000_000)
    assert len(notes) == 1
    assert "preparar o avatar" in notes[0]
