"""Python 3 EasyTithe Manager client.

Uses the login/export HTTP contract documented by the original EasyTithe
client by Alex Ortiz-Rosado. Requests owns cookie handling and TLS validation.
No network operations occur at import time.
"""

import csv
import io
import json
from collections.abc import Mapping
from datetime import datetime
from html.parser import HTMLParser
from typing import override

import requests

BASE_URL = "https://www.easytithe.com/cp3o"
CUSTOM_EXPORT_FORMAT = (
    '[amount][sep]"[txntype]"[sep]"[shortdate]"[sep]"[fund]"[sep][personName]'
    '[sep][email][sep]"[phone]"[sep]"[personid]"'
)
CONTRIBUTION_FIELD_NAMES = ("Amount", "Type", "Date", "Fund", "Name", "Email", "Phone", "PersonID")


class EasyTitheError(Exception):
    """Network or malformed service response."""


class UserPermissionError(EasyTitheError):
    """Account cannot access the export profile."""


class LoginError(EasyTitheError):
    """Missing CSRF token or rejected credentials."""


class InputParser(HTMLParser):
    """Extract the login form's CSRF token without executing HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "__RequestVerificationToken":
            self.token = attributes.get("value")


class EasyTithe:
    """Authenticated export client; each instance owns an independent session.

    Login occurs during construction for compatibility with the old client.
    Use a context manager or close() to release its session. No failed writes
    are retried. Responses are bounded and redirects are rejected.
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        timeout: float = 60,
        max_response_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if not username.strip() or not password:
            raise ValueError("Username and password are required.")
        if timeout <= 0 or max_response_bytes < 1:
            raise ValueError("Timeout and response limit must be positive.")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.session = requests.Session()
        try:
            self._login(username, password)
            self._configure_export()
        except BaseException:
            # Lifecycle isolation boundary: release resources and propagate all
            # failures, including cancellation/shutdown signals, unchanged.
            self.close()
            raise

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> EasyTithe:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> bytes:
        try:
            with self.session.request(
                method,
                BASE_URL + path,
                data=data,
                params=params,
                timeout=self.timeout,
                verify=True,
                stream=True,
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise EasyTitheError(f"EasyTithe returned HTTP {response.status_code}.")
                body = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > self.max_response_bytes:
                        raise EasyTitheError(
                            "EasyTithe response exceeds the configured size limit."
                        )
                    body.extend(chunk)
                return bytes(body)
        except requests.RequestException as error:
            raise EasyTitheError("EasyTithe request failed.") from error

    def _login(self, username: str, password: str) -> None:
        page = self._request("GET", "/Account/Login")
        parser = InputParser()
        try:
            parser.feed(page.decode("utf-8-sig"))
        except UnicodeDecodeError as error:
            raise LoginError("Invalid login page encoding.") from error
        finally:
            parser.close()
        if not parser.token:
            raise LoginError("Missing login verification token.")
        self._request(
            "POST",
            "/Account/Login",
            data={
                "UserName": username,
                "Password": password,
                "__RequestVerificationToken": parser.token,
            },
        )
        if any(
            cookie.name == "mbadlogin" and cookie.value == "1" for cookie in self.session.cookies
        ):
            raise LoginError("EasyTithe rejected the credentials.")

    def _configure_export(self) -> None:
        content = self._request(
            "GET", "/MyAccount/GetExportProfileDetails", params={"exportProfileKey": "Custom"}
        )
        try:
            profile: object = json.loads(content)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            raise UserPermissionError("Unable to access the export profile.") from error
        if not isinstance(profile, dict):
            raise UserPermissionError("Invalid export profile.")
        church_profile = profile.get("churchProfile")
        if not isinstance(church_profile, dict) or not isinstance(
            church_profile.get("formula"), str
        ):
            raise UserPermissionError("Missing export profile formula.")
        if church_profile["formula"] != CUSTOM_EXPORT_FORMAT:
            self._request(
                "POST",
                "/MyAccount/SaveExportProfileOptions",
                data={"exportProfileKey": "Custom", "data": CUSTOM_EXPORT_FORMAT},
            )

    def GetContributions(self, start_date: str, end_date: str) -> list[dict[str, str]]:
        """Fetch a bounded CSV report for inclusive MM/DD/YYYY dates.

        Date, amount, and person interpretation belongs to the caller. Malformed
        row shapes fail rather than returning incomplete financial records.
        """
        start = datetime.strptime(start_date, "%m/%d/%Y")
        end = datetime.strptime(end_date, "%m/%d/%Y")
        if start > end:
            raise ValueError("start_date must be on or before end_date.")
        content = self._request(
            "POST",
            "/MyAccount/Export",
            data={
                "exportProfileKey": "Custom",
                "sDate": start_date,
                "eDate": end_date,
            },
        )
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise EasyTitheError("Invalid export encoding.") from error
        rows: list[dict[str, str]] = []
        try:
            for row in csv.reader(io.StringIO(text, newline=""), strict=True):
                if not row:
                    continue
                if len(row) != len(CONTRIBUTION_FIELD_NAMES):
                    raise EasyTitheError("Invalid export row width.")
                rows.append(dict(zip(CONTRIBUTION_FIELD_NAMES, row, strict=True)))
        except csv.Error as error:
            raise EasyTitheError("Invalid export CSV.") from error
        return rows
