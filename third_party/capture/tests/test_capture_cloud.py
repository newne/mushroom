from pathlib import Path

import pytest
from xcloudsdk_py import capture as capture_module
from xcloudsdk_py.capture import CaptureService


class FakeSDK:
    def __init__(self):
        self._sequence = 0

    def next_seq(self):
        self._sequence += 1
        return self._sequence

    def set_device_credentials(self, device_id, user, pwd):
        return None

    def dev_login(self, device_id, *, seq, timeout_s):
        return 1, 0

    def get_play_window_handle(self):
        return 0

    def media_realplay(self, device_id, *, channel, stream_type, hwnd, seq, timeout_s):
        return 2, 0

    def make_keyframe(self, device_id, *, channel, stream_type):
        return 0

    def wait_play_data(self, play_handle, *, timeout_s):
        return True, 1, 0, 0

    def snap_image_ex(self, play_handle, file_path, *, channel_index, param):
        Path(file_path).write_bytes(b"captured image")
        return 0

    def stop_media_play(self, play_handle):
        return None

    def dev_logout(self, device_id):
        return None


class FailingMinio:
    endpoint = "http://minio.local:9000"

    def upload_file(self, *, file_path, object_name):
        raise RuntimeError("upload unavailable")


@pytest.mark.parametrize("use_pool", [False, True], ids=["dynamic", "pool"])
@pytest.mark.parametrize(
    "minio_factory",
    [lambda: None, FailingMinio],
    ids=["unconfigured", "upload-error"],
)
def test_cloud_upload_failure_marks_capture_failed_and_preserves_original(
    tmp_path, monkeypatch, use_pool, minio_factory
):
    monkeypatch.setattr(
        capture_module, "_wait_for_file_ready", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(capture_module, "_normalize_levels", lambda path: {})
    service = CaptureService(
        sdk=FakeSDK(),
        picture_dir=tmp_path,
        minio=minio_factory(),
        enable_pool=use_pool,
    )
    capture = service.pool_capture if use_pool else service.dynamic_capture

    result = capture(
        ip="192.168.1.238",
        storage="cloud",
        filename="20260914/B101_S101_top45_093000",
    )

    original = Path(result["file_path"])
    assert result["success"] is False
    assert result["cloud_uploaded"] is False
    assert result["local_file_deleted"] is False
    assert result["file_exists"] is True
    assert original.is_file()
    assert result["cloud_error"] in {
        "MinIO is not configured",
        "Failed to upload to MinIO",
    }
