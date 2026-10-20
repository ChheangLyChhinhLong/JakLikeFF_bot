import hashlib
import hmac

from app.payments import verify_webhook_signature


def test_accepts_khpay_prefixed_raw_body_signature():
    body = b'{"event":"payment.paid"}'
    secret = "webhook-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(body, f"sha256={digest}", secret)


def test_accepts_documented_bare_signature_format():
    body = b'{"event":"payment.paid"}'
    secret = "webhook-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(body, digest, secret)


def test_rejects_wrong_body_or_missing_secret():
    body = b'{"event":"payment.paid"}'
    digest = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()

    assert not verify_webhook_signature(body + b" ", f"sha256={digest}", "webhook-secret")
    assert not verify_webhook_signature(body, f"sha256={digest}", "")