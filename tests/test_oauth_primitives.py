from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from getnet_web.oauth import AuthorizationAttempt, exchange_code, refresh


def test_authorize_challenge_and_callback_validation():
    import base64
    import hashlib

    attempt = AuthorizationAttempt.create("https://issuer.example", "https://web.example/", "web")
    query = parse_qs(urlsplit(attempt.authorization_url("https://issuer.example/v1/auth/authorize")).query)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).decode().rstrip("=")
    assert query["code_challenge"] == [challenge]
    assert query["code_challenge_method"] == ["S256"]
    valid = {"state": [attempt.state], "iss": [attempt.issuer], "code": ["code"]}
    assert attempt.validate_callback(valid) == "code"
    for invalid in (valid | {"state": ["wrong"]}, valid | {"iss": ["wrong"]},
                    valid | {"state": [attempt.state, "wrong"]}, valid | {"code": [""]},
                    valid | {"error": ["access_denied"]}):
        with pytest.raises(ValueError):
            attempt.validate_callback(invalid)


def test_token_requests_are_form_encoded_and_refresh_is_not_retried():
    requests = []

    def respond(request):
        requests.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "access", "refresh_token": "next"})

    attempt = AuthorizationAttempt.create("https://issuer.example", "https://web.example/", "web")
    with httpx.Client(base_url="https://issuer.example", transport=httpx.MockTransport(respond)) as client:
        assert exchange_code(client, attempt, "code")["refresh_token"] == "next"
        assert refresh(client, "web", "previous")["refresh_token"] == "next"
    assert requests == [
        {"grant_type": ["authorization_code"], "client_id": ["web"], "redirect_uri": [attempt.redirect_uri],
         "code": ["code"], "code_verifier": [attempt.verifier]},
        {"grant_type": ["refresh_token"], "client_id": ["web"], "refresh_token": ["previous"]},
    ]
