from good_student import clock


def test_parse_iso_accepts_z_suffix():
    # 'Z' 后缀在 Python 3.11 才被 fromisoformat 原生支持；3.10 由 clock 自行归一
    assert clock.to_utc_iso("2026-09-19T08:00:00Z") == "2026-09-19T08:00:00+00:00"


def test_parse_iso_naive_treated_as_utc():
    assert clock.to_utc_iso("2026-09-19T08:00:00") == "2026-09-19T08:00:00+00:00"


def test_parse_iso_offset_converted_to_utc():
    assert clock.to_utc_iso("2026-09-19T08:00:00+08:00") == "2026-09-19T00:00:00+00:00"
