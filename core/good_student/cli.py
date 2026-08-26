"""CLI：M0 验收用，直接驱动 Service 层（与 MCP 工具同一实现）。"""

import argparse
import json
import sys

from good_student.service import Service


def _print(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="good-student", description="Good-student M0 CLI")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 $GOOD_STUDENT_DATA 或 ~/.good-student）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capabilities")

    p = sub.add_parser("create-student")
    p.add_argument("display_name")
    p.add_argument("--grade", default=None)
    p.add_argument("--subjects", default=None, help="逗号分隔，如 数学,语文")

    p = sub.add_parser("ingest")
    p.add_argument("student_id")
    p.add_argument("batch_file", help="候选批次 JSON 文件")
    p.add_argument("--host", default="core")
    p.add_argument("--captured-at", default=None)

    sub.add_parser("list-pending").add_argument("student_id")

    p = sub.add_parser("confirm")
    p.add_argument("student_id")
    p.add_argument("--items", default=None, help='确认项 JSON，如 [{"candidate_id":"..","action":"confirm"}]')
    p.add_argument("--all", action="store_true", help="全部确认（无修改）")
    p.add_argument("--reject-all", action="store_true")

    sub.add_parser("analyze").add_argument("student_id")
    sub.add_parser("plan").add_argument("student_id")

    p = sub.add_parser("reassess")
    p.add_argument("student_id")
    p.add_argument("kc_id")
    p.add_argument("--correct", type=int, required=True)
    p.add_argument("--total", type=int, required=True)
    p.add_argument("--variant", type=int, default=1)
    p.add_argument("--hints", type=int, default=0)
    p.add_argument("--completed-at", default=None)

    p = sub.add_parser("export")
    p.add_argument("student_id")

    p = sub.add_parser("delete")
    p.add_argument("student_id")
    p.add_argument("--yes-with-phrase", default=None, help="传入学生显示名确认删除")

    sub.add_parser("doctor")

    p = sub.add_parser("migrate-legacy", help="只读迁移旧 student-companion-agent 数据（设计 §22）")
    p.add_argument("legacy_path", help="旧数据目录或 student-data.json 文件路径")
    p.add_argument("--dry-run", action="store_true", help="只输出迁移摘要，不写入任何数据")

    args = parser.parse_args(argv)
    service = Service(args.data_dir)
    try:
        if args.command == "capabilities":
            _print(service.capabilities())
        elif args.command == "create-student":
            subjects = [s.strip() for s in args.subjects.split(",") if s.strip()] if args.subjects else []
            _print(service.create_student(args.display_name, args.grade, subjects))
        elif args.command == "ingest":
            with open(args.batch_file, encoding="utf-8") as fh:
                batch = json.load(fh)
            _print(
                service.ingest_candidates(
                    args.student_id, batch, host=args.host, captured_at=args.captured_at
                )
            )
        elif args.command == "list-pending":
            _print(service.list_pending(args.student_id))
        elif args.command == "confirm":
            if args.items:
                items = json.loads(args.items)
            elif args.all:
                pending = service.list_pending(args.student_id)
                if not pending["ok"]:
                    _print(pending)
                    return
                items = [
                    {"candidate_id": c["candidate_id"], "action": "confirm"}
                    for c in pending["data"]["pending"]
                ]
            elif args.reject_all:
                pending = service.list_pending(args.student_id)
                if not pending["ok"]:
                    _print(pending)
                    return
                items = [
                    {"candidate_id": c["candidate_id"], "action": "reject"}
                    for c in pending["data"]["pending"]
                ]
            else:
                parser.error("需要 --items / --all / --reject-all 之一")
                return
            _print(service.confirm_questions(args.student_id, items))
        elif args.command == "analyze":
            _print(service.analyze(args.student_id))
        elif args.command == "plan":
            _print(service.create_plan(args.student_id))
        elif args.command == "reassess":
            _print(
                service.record_reassessment(
                    args.student_id,
                    args.kc_id,
                    args.correct,
                    args.total,
                    is_new_variant=bool(args.variant),
                    no_hints=not args.hints,
                    completed_at=args.completed_at,
                )
            )
        elif args.command == "export":
            _print(service.export_student(args.student_id))
        elif args.command == "delete":
            _print(service.delete_student(args.student_id, args.yes_with_phrase))
        elif args.command == "doctor":
            _print(service.doctor())
        elif args.command == "migrate-legacy":
            _print(service.migrate_legacy(args.legacy_path, dry_run=args.dry_run))
    finally:
        service.close()


if __name__ == "__main__":
    main()
