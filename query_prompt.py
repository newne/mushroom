import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.utils.get_data import GetData
from src.configs.settings import settings

get_data = GetData(
    urls=settings.data_source_url,
    host=settings.host.host,
    port=settings.host.port,
)
prompt = get_data.get_mushroom_prompt()
print(prompt)
