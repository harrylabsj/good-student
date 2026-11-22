"""Streamable HTTP MCP for the paid expert. No tool accepts an account identifier."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import jwt
import uvicorn
from jwt.algorithms import RSAAlgorithm
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, StrictBool
from starlette.applications import Starlette

from .catalog import TOPICS, Difficulty, Identifier, Subject, Text, load_bank
from .service import Candidate, Coach, CoachError, Confirmation, Response, Review

SCOPE = "exam-coach"


def require_https(value: str, *, resource: bool = False) -> str:
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or (resource and not url.path.endswith("/mcp"))):
        raise ValueError("需要无凭据的 HTTPS 地址；MCP 资源地址必须以 /mcp 结尾")
    return value


class AccessVerifier:
    """Validate signed RFC 9068 access tokens from an approved identity provider."""

    def __init__(
        self, issuer: str, resource: str, public_key: str, client_id: str, *, require_entitlement: bool = False,
    ):
        self.issuer, self.resource = require_https(issuer), require_https(resource, resource=True)
        self.client_id = client_id.strip()
        self.require_entitlement = require_entitlement
        if not self.client_id:
            raise ValueError("需要显式设置允许的 OAuth 客户端 ID")
        self.key = RSAAlgorithm.from_jwk(public_key) if public_key.lstrip().startswith("{") else (
            RSAAlgorithm(RSAAlgorithm.SHA256).prepare_key(public_key)
        )
        if not hasattr(self.key, "verify") or getattr(self.key, "key_size", 0) < 2048:
            raise ValueError("授权签名必须使用至少 2048 位的 RSA 公钥")

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            if str(header.get("typ", "")).lower() not in {"at+jwt", "application/at+jwt"}:
                return None
            claims = jwt.decode(
                token, self.key, algorithms=["RS256"], issuer=self.issuer, audience=self.resource,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "client_id", "scope"]},
            )
        except jwt.InvalidTokenError:
            return None
        subject, client, scope = claims["sub"], claims["client_id"], claims["scope"]
        if (
            not isinstance(subject, str) or not subject.strip() or len(subject) > 512
            or client != self.client_id
            or not isinstance(scope, str) or SCOPE not in scope.split()
            or (self.require_entitlement and claims.get("exam_coach_entitled") is not True)
        ):
            return None
        return AccessToken(token=token, client_id=client, scopes=scope.split(), expires_at=int(claims["exp"]),
                           resource=self.resource, subject=subject, claims={"iss": self.issuer})


def current_account() -> str:
    token = get_access_token()
    issuer = (token.claims or {}).get("iss") if token else None
    if not token or not token.subject or not issuer or SCOPE not in token.scopes:
        raise CoachError("unauthorized", "请通过专家的授权流程连接账号")
    principal = json.dumps([issuer, token.subject], separators=(",", ":"))
    return hashlib.sha256(principal.encode()).hexdigest()


def build_mcp(service: Coach, verifier: AccessVerifier) -> MCPServer:
    server = MCPServer(
        "exam-gap-coach", title="真题查漏教练", version="0.1.0",
        auth=AuthSettings(issuer_url=verifier.issuer, resource_server_url=verifier.resource,
                          required_scopes=[SCOPE]),
        token_verifier=verifier,
    )

    def run(action: Callable, *args) -> dict:
        try:
            return {"ok": True, "data": action(current_account(), *args)}
        except CoachError as error:
            return {"ok": False, "error": {"code": error.code, "message": str(error)}}

    @server.tool()
    def exam_get_profile() -> dict:
        """读取已授权账号的唯一高中学生档案。"""
        return run(service.profile)

    @server.tool()
    def exam_create_profile(
        nickname: Text, grade: Annotated[int, Field(ge=10, le=12, strict=True)], region: Text, permission: StrictBool,
    ) -> dict:
        """经学生或监护人授权后创建唯一档案。10/11/12 分别是高一/高二/高三。"""
        return run(service.create_student, nickname, grade, region, permission)

    @server.tool()
    def exam_list_topics(subject: Subject) -> dict:
        """列出可归档的高中知识点及题库中已核验的真题数量。"""
        def lookup(_account: str) -> dict:
            return {"subject": subject, "topics": [
                {"topic_id": topic, "label": label,
                 "indexed_questions": sum(topic in q.topics for q in service.bank.values())}
                for topic, label in TOPICS[subject].items()
            ]}
        return run(lookup)

    @server.tool()
    def exam_stage_photo(
        candidates: Annotated[list[Candidate], Field(min_length=1, max_length=20)], permission: StrictBool,
    ) -> dict:
        """暂存 WorkBuddy 视觉模型从错题照片提取的脱敏候选，不要传原图或姓名。"""
        return run(service.stage, candidates, permission)

    @server.tool()
    def exam_confirm_questions(
        confirmations: Annotated[list[Confirmation], Field(min_length=1, max_length=20)], confirmed: StrictBool,
    ) -> dict:
        """只在学生或家长确认题干、错因和考点后归档。"""
        return run(service.confirm, confirmations, confirmed)

    @server.tool()
    def exam_get_wrong_question(observation_id: Identifier) -> dict:
        """按薄弱点证据 ID 追溯本账号已归档的题面、原解、知识点及不确定字段。"""
        return run(service.observation, observation_id)

    @server.tool()
    def exam_list_wrong_questions(
        subject: Subject, topic_id: Identifier | None = None, limit: Annotated[int, Field(ge=1, le=50)] = 15,
    ) -> dict:
        """浏览本账号按学科归档的错题，包含未核实考点和审题、计算失误。"""
        return run(service.list_observations, subject, topic_id, limit)

    @server.tool()
    def exam_weak_topics(subject: Subject) -> dict:
        """查看学科薄弱点、原错题证据和下一次复测建议。"""
        return run(service.weaknesses, subject)

    @server.tool()
    def exam_start_practice(
        subject: Subject, topic_id: Identifier, mode: Literal["practice", "retest"],
        count: Annotated[int, Field(ge=1, le=10, strict=True)] = 3,
        difficulty: Difficulty | None = None, strict_region: StrictBool = False,
    ) -> dict:
        """选同考点未见真题；题库不足时返回缺口，不创建含模拟题的题组。"""
        return run(service.start, subject, topic_id, mode, count, difficulty, strict_region)

    @server.tool()
    def exam_get_practice(session_id: Identifier) -> dict:
        """从同账号恢复练习。交卷前没有答案和评分要点。"""
        return run(service.practice, session_id)

    @server.tool()
    def exam_list_practices() -> dict:
        """列出本账号最近题组，便于在新会话继续。"""
        return run(service.history)

    @server.tool()
    def exam_submit_practice(
        session_id: Identifier, responses: Annotated[list[Response], Field(min_length=1, max_length=10)],
        confirmed: StrictBool,
    ) -> dict:
        """经用户确认交卷；每题实际答案、是否用提示、是否见过题都要如实记录。"""
        return run(service.submit, session_id, responses, confirmed)

    @server.tool()
    def exam_confirm_scores(
        session_id: Identifier, reviews: Annotated[list[Review], Field(min_length=1, max_length=10)],
        confirmed: StrictBool,
    ) -> dict:
        """按题库评分要点提出分项分数，只有在用户确认后才提交复核结果。"""
        return run(service.review, session_id, reviews, confirmed)

    @server.tool()
    def exam_export_student() -> dict:
        """导出当前账号的学习档案、错题和练习记录。"""
        return run(service.export)

    @server.tool()
    def exam_delete_student(confirmed: StrictBool = False, nickname: Text | None = None) -> dict:
        """预览删除或在用户明确确认并输入档案昵称后删除当前账号的学习档案。"""
        return run(service.delete, nickname, confirmed)

    return server


def http_app(service: Coach, verifier: AccessVerifier) -> Starlette:
    url = urlsplit(verifier.resource)
    return build_mcp(service, verifier).streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, max_request_body_size=512 * 1024,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=[url.netloc],
            allowed_origins=[f"https://{url.netloc}"],
        ),
    )


def create_app() -> Starlette:
    issuer = os.environ["EXAM_COACH_ISSUER"]
    resource = os.environ["EXAM_COACH_RESOURCE_URL"]
    key_path = Path(os.environ["EXAM_COACH_PUBLIC_KEY_FILE"])
    catalog = os.environ.get("EXAM_COACH_BANK")
    bank = load_bank(Path(catalog) if catalog else None)
    if not bank:
        raise ValueError("题库为空，不能启动付费专家服务")
    preview = os.environ.get("EXAM_COACH_ALLOW_LINK_SAMPLES") == "1"
    if not preview:
        live_topics = {(q.subject, topic) for q in bank.values() for topic in q.topics if q.use == "licensed"}
        if {subject for subject, _ in live_topics} != set(TOPICS):
            raise ValueError("生产模式要求数学、英语、物理均有经授权的题面")
        for subject, topic in live_topics:
            count = sum(q.subject == subject and q.use == "licensed" and topic in q.topics for q in bank.values())
            if count < 9:
                raise ValueError(f"{subject} {topic} 的授权真题不足一组练习和两轮复测")
        # Audit-only samples are not returned to paying users.
        bank = {q.id: q for q in bank.values() if q.use == "licensed"}
    verifier = AccessVerifier(
        issuer, resource, key_path.read_text(encoding="utf-8"), os.environ["EXAM_COACH_CLIENT_ID"],
        require_entitlement=not preview,
    )
    service = Coach(Path(os.environ["EXAM_COACH_DATABASE"]), bank)
    return http_app(service, verifier)


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("EXAM_COACH_PORT", "8000")))


if __name__ == "__main__":
    main()
