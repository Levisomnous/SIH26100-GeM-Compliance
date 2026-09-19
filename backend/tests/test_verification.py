from backend import verification


def test_valid_gstin_passes():
    # 07AAACV1234F1ZR is a well-known, correctly-checksummed sample GSTIN
    assert verification.validate_gstin_checksum("07AAACV1234F1ZR") is True


def test_corrupted_check_digit_fails():
    assert verification.validate_gstin_checksum("07AAACV1234F1ZQ") is False


def test_wrong_length_fails():
    assert verification.validate_gstin_checksum("07AAACV1234F1Z") is False
    assert verification.validate_gstin_checksum("07AAACV1234F1ZRR") is False


def test_none_or_empty_fails():
    assert verification.validate_gstin_checksum(None) is False
    assert verification.validate_gstin_checksum("") is False


def test_invalid_character_fails():
    assert verification.validate_gstin_checksum("07AAACV1234F1Z!") is False
