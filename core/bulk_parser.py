"""
core/bulk_parser.py
批量账号输入实时解析(规格第 9-13 节)。

分隔符优先级: `----` > Tab > 英文逗号 `,`(取第一个分隔符, 密码内
可含逗号)。逐行给出错误:
  - 账号为空      → 该行没有账号部分
  - 缺少密码      → 有账号但没有任何分隔符后的密码
  - 与第 N 行账号重复 → 同批输入内重复(队列去重另行处理)
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 分隔符按优先级排列: ---- > Tab > ,
_SEPARATORS = ("----", "\t", ",")

ERROR_NO_USERNAME = "账号为空"
ERROR_NO_PASSWORD = "缺少密码"


@dataclass
class ParseLine:
    line_no: int              # 原始行号(1 起)
    raw: str                  # 原始行(去首尾空白)
    username: str = ""
    password: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class ParseResult:
    lines: list[ParseLine] = field(default_factory=list)

    @property
    def ok_lines(self) -> list[ParseLine]:
        return [l for l in self.lines if l.ok]

    @property
    def error_lines(self) -> list[ParseLine]:
        return [l for l in self.lines if not l.ok]

    @property
    def ok_count(self) -> int:
        return len(self.ok_lines)

    @property
    def error_count(self) -> int:
        return len(self.error_lines)

    def pairs(self) -> list[tuple[str, str]]:
        """全部合法行的 (username, password)。"""
        return [(l.username, l.password) for l in self.ok_lines]


def _split_line(line: str) -> tuple[str, str] | None:
    """按优先级找第一个分隔符, 返回 (username, password)。"""
    best: tuple[int, int] | None = None    # (priority, index)
    for prio, sep in enumerate(_SEPARATORS):
        idx = line.find(sep)
        if idx >= 0 and (best is None or prio < best[0]):
            best = (prio, idx)
    if best is None:
        return None
    _, idx = best
    sep = _SEPARATORS[best[0]]
    return line[:idx].strip(), line[idx + len(sep):].strip()


def parse_account_lines(text: str) -> ParseResult:
    """解析批量输入文本 — 空行忽略(不报错)。

    支持同时含空格的行: 先 trim, 再按优先级切分。
    """
    result = ParseResult()
    seen: dict[str, int] = {}              # username -> 首次出现行号
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parsed = _split_line(line)
        if parsed is None:
            result.lines.append(ParseLine(
                line_no=i, raw=line, username=line,
                error=ERROR_NO_PASSWORD))     # 单段 = 只有账号
            continue
        username, password = parsed
        if not username:
            result.lines.append(ParseLine(
                line_no=i, raw=line, password=password,
                error=ERROR_NO_USERNAME))
            continue
        if not password:
            result.lines.append(ParseLine(
                line_no=i, raw=line, username=username,
                error=ERROR_NO_PASSWORD))
            continue
        if username in seen:
            result.lines.append(ParseLine(
                line_no=i, raw=line, username=username, password=password,
                error=f"与第 {seen[username]} 行账号重复"))
            continue
        seen[username] = i
        result.lines.append(ParseLine(
            line_no=i, raw=line, username=username, password=password))
    return result


def count_parse(text: str) -> tuple[int, int]:
    """快速计数: (可添加数, 错误行数)。"""
    result = parse_account_lines(text)
    return result.ok_count, result.error_count
