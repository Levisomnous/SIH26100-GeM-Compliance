import io
import pytest

from backend import security

TOK_A = "a" * 20
TOK_B = "b" * 20


def test_parse_officer_tokens_ok():
    assert security.parse_officer_tokens(f"alice:{TOK_A}, bob:{TOK_B}") == {TOK_A: "alice", TOK_B: "bob"}
    assert security.parse_officer_tokens("") == {}
    assert security.parse_officer_tokens(None) == {}


@pytest.mark.parametrize("raw", ["alice", "alice:short", f":{TOK_A}", f"a:{TOK_A},b:{TOK_A}"])
def test_parse_officer_tokens_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        security.parse_officer_tokens(raw)


def test_extract_token_variants():
    assert security.extract_token("Bearer abc", None) == "abc"
    assert security.extract_token("bearer   abc", None) == "abc"
    assert security.extract_token(None, "key1") == "key1"
    assert security.extract_token("Basic abc", None) is None
    assert security.extract_token("Bearer", None) is None
    assert security.extract_token(None, None) is None


def test_authenticate():
    toks = {TOK_A: "alice", TOK_B: "bob"}
    assert security.authenticate(TOK_B, toks) == "bob"
    assert security.authenticate("nope", toks) is None
    assert security.authenticate(None, toks) is None
    assert security.authenticate(TOK_A, {}) is None


def _cfg(monkeypatch, **env):
    for k in ("OFFICER_TOKENS", "ADMIN_TOKEN", "ENABLE_RESET", "APP_ENV", "MAX_UPLOAD_MB",
              "RATE_LIMIT_PER_MIN", "TRUST_PROXY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return security.get_config()


def test_config_defaults(monkeypatch):
    cfg = _cfg(monkeypatch)
    assert not cfg.auth_enabled and not cfg.enable_reset and not cfg.production
    assert cfg.max_upload_bytes == 10 * 1024 * 1024
    assert cfg.rate_limit_per_min == 30
    assert security.validate_startup(cfg)          # dev-mode warning string


def test_production_requires_tokens_and_forbids_reset(monkeypatch):
    with pytest.raises(RuntimeError):
        security.validate_startup(_cfg(monkeypatch, APP_ENV="production"))
    with pytest.raises(RuntimeError):
        security.validate_startup(_cfg(monkeypatch, APP_ENV="production", ENABLE_RESET="true",
                                       OFFICER_TOKENS=f"a:{TOK_A}"))
    assert security.validate_startup(_cfg(monkeypatch, APP_ENV="production", OFFICER_TOKENS=f"a:{TOK_A}")) is None


def test_short_admin_token_rejected(monkeypatch):
    with pytest.raises(ValueError):
        _cfg(monkeypatch, ADMIN_TOKEN="short")


def test_reset_decision_matrix(monkeypatch):
    # disabled by default
    ok, status, _ = security.reset_decision(_cfg(monkeypatch), None)
    assert (ok, status) == (False, 403)
    # enabled, open dev mode
    assert security.reset_decision(_cfg(monkeypatch, ENABLE_RESET="true"), None)[0] is True
    # enabled + officer auth but no admin token configured -> refuse
    ok, status, _ = security.reset_decision(_cfg(monkeypatch, ENABLE_RESET="true", OFFICER_TOKENS=f"a:{TOK_A}"), TOK_A)
    assert (ok, status) == (False, 403)
    # enabled + admin token: needs the right one (an officer token is not enough)
    cfg = _cfg(monkeypatch, ENABLE_RESET="true", OFFICER_TOKENS=f"a:{TOK_A}", ADMIN_TOKEN=TOK_B)
    assert security.reset_decision(cfg, TOK_A)[:2] == (False, 401)
    assert security.reset_decision(cfg, None)[:2] == (False, 401)
    assert security.reset_decision(cfg, TOK_B)[0] is True


def test_rate_limiter_window():
    rl = security.RateLimiter(window_s=60)
    assert rl.allow("ip", 2, now=0)[0]
    assert rl.allow("ip", 2, now=1)[0]
    ok, retry = rl.allow("ip", 2, now=2)
    assert not ok and 1 <= retry <= 60
    assert rl.allow("other", 2, now=2)[0]           # separate key
    assert rl.allow("ip", 2, now=61)[0]             # first hit aged out
    assert rl.allow("ip", 0, now=0)[0]              # limit 0 disables


def test_rate_limiter_prunes_stale_keys():
    rl = security.RateLimiter(window_s=10, max_keys=5)
    for i in range(6):
        rl.allow(f"k{i}", 5, now=0)
    rl.allow("fresh", 5, now=100)
    assert len(rl._hits) <= 2


def test_read_limited():
    assert security.read_limited(io.BytesIO(b"x" * 100), 100) == b"x" * 100
    with pytest.raises(security.UploadTooLarge):
        security.read_limited(io.BytesIO(b"x" * 101), 100)
    assert security.read_limited(io.BytesIO(b""), 10) == b""
    with pytest.raises(security.UploadTooLarge):
        security.read_limited(io.BytesIO(b"x" * 5000), 100, chunk=7)


def test_looks_like_pdf():
    assert security.looks_like_pdf(b"%PDF-1.7\n...")
    assert security.looks_like_pdf(b"\n\n%PDF-1.4")
    assert not security.looks_like_pdf(b"<html>not a pdf</html>")
    assert not security.looks_like_pdf(b"")
