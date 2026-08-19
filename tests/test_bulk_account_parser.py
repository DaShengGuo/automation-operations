"""tests/test_bulk_account_parser.py
批量输入解析(规格第 9-13 节): 分隔符优先级 ---- > Tab > 逗号,
空行忽略, 逐行错误(缺少密码/账号为空/批内重复), 预览数据。
"""
from __future__ import annotations

from core.bulk_parser import (ERROR_NO_PASSWORD, ERROR_NO_USERNAME,
                              count_parse, parse_account_lines)


class TestSeparators:
    def test_dash_priority_over_comma_by_priority_not_position(self):
        """---- 优先级最高: 逗号在前也让位(第 11 节)。"""
        r = parse_account_lines("a,b----c")
        assert r.ok_count == 1
        assert r.ok_lines[0].username == "a,b"
        assert r.ok_lines[0].password == "c"

    def test_tab_split(self):
        r = parse_account_lines("user1\tpass1")
        assert r.ok_lines[0].username == "user1"
        assert r.ok_lines[0].password == "pass1"

    def test_comma_split(self):
        r = parse_account_lines("user2,pass2")
        assert r.ok_lines[0].username == "user2"
        assert r.ok_lines[0].password == "pass2"

    def test_password_may_contain_comma(self):
        r = parse_account_lines("u----p,a,s")
        assert r.ok_lines[0].password == "p,a,s"

    def test_trims_spaces(self):
        r = parse_account_lines("  u1  ----  p1  ")
        assert r.ok_lines[0].username == "u1"
        assert r.ok_lines[0].password == "p1"


class TestErrors:
    def test_missing_password(self):
        r = parse_account_lines("onlyuser")
        assert r.error_count == 1
        assert r.error_lines[0].error == ERROR_NO_PASSWORD
        assert r.error_lines[0].username == "onlyuser"

    def test_empty_password_after_separator(self):
        r = parse_account_lines("u1----")
        assert r.error_count == 1
        assert r.error_lines[0].error == ERROR_NO_PASSWORD

    def test_empty_username(self):
        r = parse_account_lines("----p1")
        assert r.error_count == 1
        assert r.error_lines[0].error == ERROR_NO_USERNAME

    def test_duplicate_in_batch(self):
        r = parse_account_lines("a----p1\na----p2")
        assert r.error_count == 1
        assert "第 1 行" in r.error_lines[0].error

    def test_empty_lines_ignored_but_line_numbers_kept(self):
        r = parse_account_lines("a----p1\n\nbad\n")
        assert r.ok_count == 1
        assert r.error_count == 1
        assert r.error_lines[0].line_no == 3   # 空行占第 2 行


class TestResultShape:
    def test_counts_and_pairs(self):
        text = "a----p1\nb\tp2\nc,p3"
        r = parse_account_lines(text)
        assert r.ok_count == 3
        assert r.error_count == 0
        assert r.pairs() == [("a", "p1"), ("b", "p2"), ("c", "p3")]

    def test_count_parse(self):
        assert count_parse("a----p1\nbad\n") == (1, 1)

    def test_mixed_batch(self):
        text = ("a----p1\n"
                "b\tp2\n"
                "c,p3\n"
                "missing\n"
                "----empty\n"
                "a----p4\n"
                "\n"
                "d----p5\n")
        r = parse_account_lines(text)
        assert r.ok_count == 4          # a,b,c,d
        assert r.error_count == 3       # missing / empty / a重复
        assert r.ok_lines[-1].username == "d"
        assert r.error_lines[2].error.startswith("与第 1 行")
