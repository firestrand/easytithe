"""Contract fixtures for Python 3 login and export; no live account required."""

import json
from unittest.mock import patch

import pytest
import requests

from easytithe import (
    CUSTOM_EXPORT_FORMAT,
    EasyTithe,
    EasyTitheError,
    LoginError,
    UserPermissionError,
)


class Response(requests.Response):
    def __init__(self, content: bytes = b"", status: int = 200) -> None:
        super().__init__()
        self._content = content
        self._content_consumed = True
        self.status_code = status
        self.closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


def login_responses(formula: str = CUSTOM_EXPORT_FORMAT) -> list[Response]:
    return [
        Response(b'<input name="other"><input name="__RequestVerificationToken" value="csrf">'),
        Response(),
        Response(json.dumps({"churchProfile": {"formula": formula}}).encode()),
    ]


def test_login_and_unicode_quoted_csv() -> None:
    responses = login_responses() + [
        Response(
            '$1.00,onCARD,10/01/2026,"Fund, One","Café Person",test@example.invalid,0,42\n'.encode()
        )
    ]
    with (
        patch.object(requests.Session, "request", side_effect=responses) as request,
        EasyTithe("synthetic-user", "synthetic-password") as client,
    ):
        rows = client.GetContributions("10/01/2026", "10/03/2026")
    assert rows == [
        {
            "Amount": "$1.00",
            "Type": "onCARD",
            "Date": "10/01/2026",
            "Fund": "Fund, One",
            "Name": "Café Person",
            "Email": "test@example.invalid",
            "Phone": "0",
            "PersonID": "42",
        }
    ]
    assert request.call_args_list[1].kwargs["data"]["__RequestVerificationToken"] == "csrf"
    assert all(
        call.kwargs["verify"] and call.kwargs["stream"] and not call.kwargs["allow_redirects"]
        for call in request.call_args_list
    )
    assert all(response.closed for response in responses)


def test_changed_formula_is_configured() -> None:
    with (
        patch.object(
            requests.Session, "request", side_effect=login_responses("old") + [Response()]
        ) as request,
        EasyTithe("user", "password"),
    ):
        pass
    assert request.call_count == 4
    assert request.call_args.kwargs["data"]["data"] == CUSTOM_EXPORT_FORMAT


def test_missing_csrf_closes_session() -> None:
    with (
        patch.object(requests.Session, "request", return_value=Response(b"<html>")),
        patch.object(requests.Session, "close") as close,
        pytest.raises(LoginError),
    ):
        EasyTithe("user", "password")
    close.assert_called_once()


def test_rejected_login() -> None:
    session = requests.Session()
    session.cookies.set("mbadlogin", "1")
    with (
        patch("easytithe.requests.Session", return_value=session),
        patch.object(session, "request", side_effect=login_responses()),
        pytest.raises(LoginError),
    ):
        EasyTithe("user", "password")


@pytest.mark.parametrize("profile", [b"<html>", b"[]", b"{}", b'{"churchProfile": {}}'])
def test_profile_permissions(profile: bytes) -> None:
    with (
        patch.object(
            requests.Session, "request", side_effect=login_responses()[:2] + [Response(profile)]
        ),
        pytest.raises(UserPermissionError),
    ):
        EasyTithe("user", "password")


@pytest.mark.parametrize("status", [302, 401, 500])
def test_http_failures_close_response(status: int) -> None:
    response = Response(b"", status)
    with (
        patch.object(requests.Session, "request", return_value=response),
        pytest.raises(EasyTitheError),
    ):
        EasyTithe("user", "password")
    assert response.closed


def test_timeout_and_constructor_cleanup() -> None:
    error = requests.Timeout("offline")
    with (
        patch.object(requests.Session, "request", side_effect=error),
        patch.object(requests.Session, "close") as close,
        pytest.raises(EasyTitheError) as caught,
    ):
        EasyTithe("user", "password")
    assert caught.value.__cause__ is error
    close.assert_called_once()


def test_response_size_limit() -> None:
    response = Response(b"12345")
    with (
        patch.object(requests.Session, "request", return_value=response),
        pytest.raises(EasyTitheError),
    ):
        EasyTithe("user", "password", max_response_bytes=4)
    assert response.closed


@pytest.mark.parametrize("report", [b"one,two\n", b'"unterminated', b"\xff"])
def test_malformed_report(report: bytes) -> None:
    with (
        patch.object(
            requests.Session, "request", side_effect=login_responses() + [Response(report)]
        ),
        EasyTithe("user", "password") as client,
        pytest.raises(EasyTitheError),
    ):
        client.GetContributions("10/01/2026", "10/03/2026")


def test_empty_report_and_date_validation() -> None:
    with (
        patch.object(requests.Session, "request", side_effect=login_responses() + [Response()]),
        EasyTithe("user", "password") as client,
    ):
        with pytest.raises(ValueError):
            client.GetContributions("10/03/2026", "10/01/2026")
        with pytest.raises(ValueError):
            client.GetContributions("invalid", "10/01/2026")
        assert client.GetContributions("10/01/2026", "10/03/2026") == []
