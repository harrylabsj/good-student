"""并发回归：宿主（MCP worker 线程池）可能并发调用同一工具，写路径必须无重复副作用。"""

import threading

from conftest import make_batch, make_question, make_student


def _run_concurrently(fn, n=2):
    barrier = threading.Barrier(n)
    results = []

    def worker():
        barrier.wait()
        results.append(fn())

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_confirm_creates_single_attempt(service):
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]))
    cid = service.list_pending(student)["data"]["pending"][0]["candidate_id"]

    results = _run_concurrently(
        lambda: service.confirm_questions(student, [{"candidate_id": cid, "action": "confirm"}])
    )

    # 两个响应都是合法 envelope（部分成功设计），但只有一个事务赢得确认
    assert all(r["ok"] for r in results)
    assert sum(len(r["data"]["applied"]) for r in results) == 1
    loser_failures = [f for r in results for f in r["data"]["failed"]]
    assert len(loser_failures) == 1
    assert loser_failures[0]["code"] == "invalid_state"
    assert len(service.store.attempts_for_student(student)) == 1
    assert len(service.list_pending(student)["data"]["pending"]) == 0


def test_concurrent_reject_creates_single_rejection(service):
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]))
    cid = service.list_pending(student)["data"]["pending"][0]["candidate_id"]

    results = _run_concurrently(
        lambda: service.confirm_questions(student, [{"candidate_id": cid, "action": "reject"}])
    )

    assert sum(len(r["data"]["applied"]) for r in results) == 1
    assert len(service.store.attempts_for_student(student)) == 0


def test_concurrent_ingest_same_material_dedupes(service):
    student = make_student(service)
    batch = make_batch([make_question()])

    results = _run_concurrently(lambda: service.ingest_candidates(student, batch))

    # 恰好一个事务真正写入，另一个走重复分支（预检查或唯一索引兜底）
    assert [r["data"]["duplicate"] for r in results].count(False) == 1
    assert [r["data"]["duplicate"] for r in results].count(True) == 1
    assert len(service.store.sources_for_student(student)) == 1
    assert len(service.list_pending(student)["data"]["pending"]) == 1
