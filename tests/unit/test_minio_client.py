
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from src.utils.minio_client import MinIOClient, create_minio_client

class TestMinIOClient:
    @pytest.fixture
    def mock_settings(self):
        with patch('src.utils.minio_client.settings') as mock_settings:
            mock_settings.MINIO = {
                'endpoint': 'localhost:9000',
                'access_key': 'minioadmin',
                'secret_key': 'minioadmin',
                'bucket': 'test-bucket',
                'secure': False
            }
            yield mock_settings

    @pytest.fixture
    def minio_client(self, mock_settings):
        with patch('src.utils.minio_client.Minio') as mock_minio:
            client = MinIOClient()
            return client

    def test_create_client_http(self, mock_settings):
        # Test HTTP creation
        mock_settings.MINIO['secure'] = False
        with patch('src.utils.minio_client.Minio') as mock_minio:
            client = MinIOClient()
            # Verify Minio constructor called with secure=False
            call_args = mock_minio.call_args[1]
            assert call_args['secure'] is False
            assert call_args['endpoint'] == 'localhost:9000'

    def test_create_client_https_inferred(self, mock_settings):
        # Test HTTPS inference from endpoint
        mock_settings.MINIO = {
                'endpoint': 'https://s3.example.com',
                'access_key': 'key',
                'secret_key': 'secret',
                'bucket': 'bucket'
        }
        with patch('src.utils.minio_client.Minio') as mock_minio:
            client = MinIOClient()
            call_args = mock_minio.call_args[1]
            assert call_args['secure'] is True
            assert call_args['endpoint'] == 's3.example.com'

    def test_parse_image_time(self, minio_client):
        # Test 14-digit timestamp parsing
        path = "8/20260105/8192168123120261520260105121130.jpg"
        dt = minio_client._parse_image_time_from_path(path)
        assert dt == datetime(2026, 1, 5, 12, 11, 30)
        
        # Test invalid path
        assert minio_client._parse_image_time_from_path("invalid.jpg") is None

    def test_parse_room_id(self, minio_client):
        path = "611/20260105/image.jpg"
        assert minio_client._parse_room_id_from_path(path) == "611"
        
        path = "invalid/path" # Still splits by /
        assert minio_client._parse_room_id_from_path(path) == "invalid"

    def test_build_prefixes(self, minio_client):
        # Mock list_rooms
        minio_client.list_rooms = MagicMock(return_value=['611', '612'])
        
        ymds = ['20260101', '20260102']
        
        # Case 1: Specific room
        prefixes = minio_client._build_prefixes_by_room_and_days('611', ymds, 'bucket')
        assert sorted(prefixes) == ['611/20260101/', '611/20260102/']
        
        # Case 2: All rooms
        prefixes = minio_client._build_prefixes_by_room_and_days(None, ymds, 'bucket')
        expected = ['611/20260101/', '611/20260102/', '612/20260101/', '612/20260102/']
        assert sorted(prefixes) == sorted(expected)

