"""Tests for user_id lookup/generation"""
import os

import pytest

from iterative_telemetry import find_or_create_user_id


@pytest.fixture(name="tmp_user_config_dir")
def fixture_tmp_user_config_dir(mocker, tmp_path):
    def _user_config_dir(appname, *_args, **_kwargs):
        return str(tmp_path / appname)

    mocker.patch("iterative_telemetry.user_config_dir", _user_config_dir)

    return _user_config_dir


def test_find_or_create_user_id(
    tmp_user_config_dir,
):  # pylint: disable=unused-argument
    created = find_or_create_user_id()

    find_or_create_user_id.cache_clear()
    found = find_or_create_user_id()

    assert created == found


def test_legacy_find_or_create_user_id(tmp_user_config_dir):
    old_config_dir = tmp_user_config_dir("dvc", "iterative")
    old_config_file = os.path.join(old_config_dir, "user_id")

    find_or_create_user_id.cache_clear()

    os.makedirs(old_config_dir, exist_ok=True)
    with open(old_config_file, "w", encoding="utf-8") as fobj:
        fobj.write('{"user_id": "1234"}')

    find_or_create_user_id.cache_clear()
    assert find_or_create_user_id() == "1234"

    os.unlink(old_config_file)
    try:
        os.unlink(old_config_file + ".lock")
    except FileNotFoundError:
        pass
    os.rmdir(old_config_dir)
    assert find_or_create_user_id() == "1234"
