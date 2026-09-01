from ops.mini.operations_dashboard import _throughput_charts


def test_throughput_charts_render_all_stages_and_exact_values():
    payload = {
        "days": ["2026-08-31", "2026-09-01"],
        "series": {
            "crawler_accepted": [2, 3],
            "media_staged": [1, 0],
            "transcribed": [0, 2],
            "gemini_completed": [0, 1],
        },
        "errors": [],
    }

    rendered = _throughput_charts(payload)

    assert rendered.count('class="throughput-chart"') == 4
    assert "Crawler accepted" in rendered
    assert "Media staged" in rendered
    assert "Transcribed" in rendered
    assert "Gemini completed" in rendered
    assert "2026-09-01: 3" in rendered
    assert "30-day total · peak 3" in rendered
