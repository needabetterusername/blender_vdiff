from vdiff_core.blenddiff import BlendDiff

# Expected values
_expected_result_codehash = {'codebase_hash': 'cff521c9ab487c28bcd873d32ef71938a8cd3c066fe90ca53d14cea1a95a27e6'}

# Test for the _get_codebase_hash function
def test_get_codebase_hash():
    bd = BlendDiff()
    actual_result = bd._get_codebase_hash()
    expected_result = _expected_result_codehash
    assert actual_result == expected_result, f"Hash mismatch:\nExpected: {expected_result}\nActual:   {actual_result}"