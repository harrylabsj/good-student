from conftest import make_student


def _score(subject="数学", score=90, max_score=100, assessed_at="2026-08-01T10:00:00+08:00", **extra):
    return {
        "subject": subject,
        "assessment_name": "单元测试",
        "assessment_type": "quiz",
        "score": score,
        "max_score": max_score,
        "assessed_at": assessed_at,
        **extra,
    }


def test_record_and_query_scores_with_summary(service):
    student = make_student(service)
    result = service.record_scores(
        student,
        [
            _score(score=90, term="2026-2027-1", class_rank=3, grade_rank=12, class_size=40),
            _score(score=80, assessed_at="2026-08-08T10:00:00+08:00", term="2026-2027-1"),
            _score(subject="语文", score=75, max_score=80),
        ],
    )
    assert result["ok"], result
    assert [r["percentage"] for r in result["data"]["records"]] == [90.0, 80.0, 93.75]

    queried = service.list_scores(student, subject="数学", term="2026-2027-1")
    assert queried["ok"], queried
    assert len(queried["data"]["records"]) == 2
    assert queried["data"]["records"][1]["class_rank"] == 3
    assert queried["data"]["records"][1]["grade_rank"] == 12
    assert queried["data"]["summary_by_subject"]["数学"] == {
        "count": 2,
        "average_percentage": 85.0,
        "percentage_count": 2,
    }
    assert queried["data"]["records"][0]["assessed_at"].startswith("2026-08-08T02:00:00")


def test_supports_grade_label_without_numeric_score(service):
    student = make_student(service)
    result = service.record_scores(
        student,
        [
            {
                "subject": "体育",
                "assessment_name": "学期评价",
                "grade_label": "优秀",
                "assessed_at": "2026-07-01",
            }
        ],
    )
    assert result["ok"], result
    record = service.list_scores(student)["data"]["records"][0]
    assert record["grade_label"] == "优秀"
    assert record["score"] is None and record["percentage"] is None


def test_invalid_batch_is_atomic(service):
    student = make_student(service)
    result = service.record_scores(student, [_score(), _score(subject="", score=70)])
    assert not result["ok"]
    assert result["error"]["code"] == "invalid_argument"
    assert service.list_scores(student)["data"]["records"] == []


def test_score_validation_and_idempotency(service):
    student = make_student(service)
    assert not service.record_scores(student, [_score(score=101)])["ok"]
    assert not service.record_scores(student, [_score(class_rank=41, class_size=40)])["ok"]
    assert not service.record_scores(student, [_score(grade_rank=0)])["ok"]
    assert not service.record_scores(
        student,
        [{"subject": "音乐", "assessment_name": "测评", "assessed_at": "2026-08-01"}],
    )["ok"]

    first = service.record_scores(student, [_score()], idempotency_key="score-1")
    replay = service.record_scores(student, [_score()], idempotency_key="score-1")
    assert first["data"]["records"][0]["id"] == replay["data"]["records"][0]["id"]
    assert "idempotent_replay" in replay["warnings"]
    assert len(service.list_scores(student)["data"]["records"]) == 1


def test_scores_are_exported_and_deleted_with_student(service):
    student = make_student(service)
    service.record_scores(student, [_score()])
    exported = service.export_student(student)
    assert exported["data"]["score_records"][0]["subject"] == "数学"

    preview = service.delete_student(student)
    assert preview["data"]["scope"]["will_delete"]["score_records"] == 1
    deleted = service.delete_student(student, confirm_phrase="小明")
    assert deleted["data"]["removed"]["score_records"] == 1
