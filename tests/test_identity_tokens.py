from app.identity.tokens import TOKEN_ENTROPY_BYTES, hash_opaque_token, issue_opaque_token


def test_issued_token_contains_namespace_and_sufficient_entropy() -> None:
    issued = issue_opaque_token(namespace="session")

    assert issued.value.startswith("session_")
    assert issued.prefix == issued.value[:24]
    assert len(issued.value) >= TOKEN_ENTROPY_BYTES
    assert issued.hash == hash_opaque_token(issued.value)
    assert issued.value not in issued.hash


def test_each_issued_token_is_unique() -> None:
    first = issue_opaque_token(namespace="verify")
    second = issue_opaque_token(namespace="verify")

    assert first.value != second.value
    assert first.hash != second.hash


def test_token_namespace_is_normalized_and_validated() -> None:
    issued = issue_opaque_token(namespace=" Password_Reset ")

    assert issued.value.startswith("password_reset_")


def test_token_hash_is_stable_without_storing_plaintext() -> None:
    value = "session_example-value"

    assert hash_opaque_token(value) == hash_opaque_token(value)
    assert hash_opaque_token(value) != value
