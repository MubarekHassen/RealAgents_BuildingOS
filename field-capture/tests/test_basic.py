"""Basic tests for BuildingOS Field Capture API."""
import pytest
import re

# Test the _safe_id validation function
def test_safe_id_allows_valid():
    from app.main import _safe_id
    assert _safe_id("abc-123") == "abc-123"
    assert _safe_id("user@test.com") == "user@test.com"
    assert _safe_id("bld_marquis_villa_ef") == "bld_marquis_villa_ef"

def test_safe_id_rejects_injection():
    from app.main import _safe_id
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _safe_id("'; DROP TABLE users;--")
    with pytest.raises(HTTPException):
        _safe_id("a" * 300)  # Too long

def test_safe_id_rejects_special_chars():
    from app.main import _safe_id
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _safe_id("test;injection")
    with pytest.raises(HTTPException):
        _safe_id("test'quote")

# Test PIN hashing (after bcrypt migration)
def test_hash_pin_produces_bcrypt():
    """PIN hash should be a bcrypt hash."""
    try:
        from app.main import hash_pin
        result = hash_pin("1234", "test@example.com")
        assert result.startswith("$2")  # bcrypt hash prefix
        assert len(result) > 50
    except ImportError:
        pytest.skip("bcrypt not installed")

def test_verify_pin_correct():
    try:
        from app.main import hash_pin, verify_pin
        hashed = hash_pin("1234", "test@example.com")
        assert verify_pin("1234", "test@example.com", hashed)
        assert not verify_pin("5678", "test@example.com", hashed)
    except ImportError:
        pytest.skip("bcrypt not installed")

# Test health endpoint exists
def test_health_endpoint_exists():
    """Verify health endpoint is registered."""
    from app.main import app
    routes = [r.path for r in app.routes]
    assert "/health" in routes

# Test CORS is not wildcard
def test_cors_not_wildcard():
    """Verify CORS is not set to allow all origins."""
    from app.main import app
    for middleware in app.user_middleware:
        if hasattr(middleware, 'kwargs'):
            origins = middleware.kwargs.get('allow_origins', [])
            assert origins != ["*"], "CORS should not allow all origins"
