"""Verified exam bank and the controlled topic vocabulary."""

from __future__ import annotations

import json
import re
import unicodedata
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

Subject = Literal["数学", "英语", "物理"]
Difficulty = Literal["基础", "中等", "较难"]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,95}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]

TOPICS: dict[Subject, dict[str, str]] = {
    "数学": {
        "math-sets": "集合与运算",
        "math-complex": "复数",
        "math-function": "函数性质与图像",
        "math-sequence": "数列",
        "math-trig": "三角函数",
        "math-vector": "平面向量",
        "math-probability": "概率与统计",
        "math-geometry": "立体几何",
        "math-conics": "圆锥曲线",
        "math-derivative": "导数及其应用",
    },
    "英语": {
        "english-detail": "阅读理解：细节定位",
        "english-inference": "阅读理解：推理判断",
        "english-main": "阅读理解：主旨大意",
        "english-context": "完形填空：语境与词义辨析",
        "english-tense": "语法填空：时态与语态",
        "english-nonfinite": "语法填空：非谓语动词",
        "english-clause": "语法填空：从句",
        "english-formation": "语法填空：词性与构词",
        "english-writing": "书面表达",
    },
    "物理": {
        "physics-motion": "运动学",
        "physics-force": "牛顿运动定律",
        "physics-energy": "功与能",
        "physics-momentum": "动量与碰撞",
        "physics-gravity": "圆周运动与万有引力",
        "physics-electric": "静电场与电路",
        "physics-magnetic": "磁场与电磁感应",
        "physics-wave": "振动、波与光学",
        "physics-thermal": "热学",
        "physics-atomic": "原子与核物理",
    },
}


class ExamSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam: Text
    year: int = Field(ge=1977, le=2100)
    region: Text
    paper: Text
    number: Text
    paper_url: HttpUrl
    answer_url: HttpUrl
    page: int | None = Field(default=None, ge=1, le=500)
    publisher: Text

    @model_validator(mode="after")
    def secure_urls(self) -> ExamSource:
        if any(url.scheme != "https" or url.username or url.password
               for url in (self.paper_url, self.answer_url)):
            raise ValueError("原卷与答案链接必须为无凭据的 HTTPS 链接")
        return self

    def public(self, *, graded: bool = False) -> dict:
        source = self.model_dump(mode="json", exclude_none=True)
        if not graded:
            source.pop("answer_url")
        return source

    def identity(self, subject: Subject) -> tuple:
        def normalize(value: str) -> str:
            return "".join(unicodedata.normalize("NFKC", value).split()).casefold()

        return self.year, normalize(self.exam), normalize(self.paper), subject, normalize(self.number)


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Identifier
    description: Text
    points: int = Field(ge=1, le=30, strict=True)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Identifier
    subject: Subject
    topics: list[Identifier] = Field(min_length=1, max_length=6)
    min_grade: int = Field(ge=10, le=12)
    difficulty: Difficulty
    source: ExamSource
    reviewed_by: Text
    use: Literal["link_only", "licensed"]
    rights_reference: Text | None = None
    prompt: Answer | None = None
    kind: Literal["choice", "exact", "rubric"]
    answers: list[Answer] = Field(default_factory=list, max_length=15)
    rubric: list[Criterion] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def validate_record(self) -> Question:
        if len(set(self.topics)) != len(self.topics) or any(t not in TOPICS[self.subject] for t in self.topics):
            raise ValueError("知识点重复、未登记或不属于本学科")
        if self.use == "licensed":
            if not self.rights_reference or not self.prompt:
                raise ValueError("站内展示原题必须同时提供题面和授权依据")
        elif self.prompt is not None or self.rights_reference is not None:
            raise ValueError("只提供原卷链接的真题不得收录或输出题面")
        if self.kind == "rubric":
            if self.answers or not self.rubric or len({c.id for c in self.rubric}) != len(self.rubric):
                raise ValueError("主观题必须有独立评分要点，不能使用字符串答案判题")
        elif self.rubric or not self.answers:
            raise ValueError("客观题必须有已核验的答案")
        if self.kind == "choice" and (len(self.answers) != 1 or not re.fullmatch(r"[A-H]+", self.answers[0])):
            raise ValueError("选择题答案必须使用大写选项字母")
        return self

    def public(self) -> dict:
        result = {
            "question_id": self.id, "kind": self.kind, "difficulty": self.difficulty,
            "source": self.source.public(), "display": self.use,
        }
        if self.prompt is not None:
            result["prompt"] = self.prompt
        return result


def answer_key(value: str, kind: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    if kind == "choice":
        return "".join(sorted(set(re.sub(r"[\s,、]+", "", value.upper()))))
    # Preserve capitalization, internal spaces, units and symbols. Record alternative
    # valid expressions explicitly, or use a rubric instead of guessing equivalence.
    return value


def load_bank(path: Path | None = None) -> dict[str, Question]:
    manifest = path or files(__package__).joinpath("source_bank.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if set(data) != {"schema_version", "questions"} or data["schema_version"] != 1:
        raise ValueError("真题库版本或结构不受支持")
    bank: dict[str, Question] = {}
    paper_questions: set[tuple] = set()
    for row in data["questions"]:
        question = Question.model_validate(row)
        identity = question.source.identity(question.subject)
        if question.id in bank or identity in paper_questions:
            raise ValueError("同一原卷题号不能以不同 ID 重复入库")
        bank[question.id] = question
        paper_questions.add(identity)
    return bank
