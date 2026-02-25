import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.create_table import pgsql_engine, ImageTextQuality
from sqlalchemy.orm import sessionmaker

Session = sessionmaker(bind=pgsql_engine)
db = Session()
record = db.query(ImageTextQuality).filter(ImageTextQuality.id == 5478).first()

if record:
    print(f"ID: {record.id}")
    print(f"Room ID: {record.room_id}")
    print(f"Collection Datetime: {record.collection_datetime}")
    print(f"Image Path: {record.image_path}")
    print(f"Image Quality Score: {record.image_quality_score}")
    print(f"LLaMA Description: {record.llama_description}")
    print(f"Chinese Description: {record.chinese_description}")
    print(f"Human Evaluation: {record.human_evaluation}")
else:
    print("Record not found.")
