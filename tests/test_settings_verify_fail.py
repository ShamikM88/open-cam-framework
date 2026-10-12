import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="fails on Linux only, on purpose")
def test_settings_verification_deliberate_linux_failure():
    assert False, "settings verification: deliberate failure"  # noqa: B011
