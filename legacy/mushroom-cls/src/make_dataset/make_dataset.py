import random
from pathlib import Path
from datetime import datetime
import shutil
from pathlib import Path
import shutil
import random
from tqdm import tqdm
from loguru import logger
import argparse

from global_const.global_const import BASE_DIR

# ==================== 默认配置参数 ====================
DEFAULT_SOURCE_DIR =Path("/mnt/source_data/项目/蘑菇/Demo_Images")
DEFAULT_TRAIN_DIR = BASE_DIR / Path("./datasets/mushroom/train")
DEFAULT_TEST_DIR = BASE_DIR / Path("./datasets/mushroom/test")
DEFAULT_TEST_RATIO = 0.2
DEFAULT_OVERWRITE = False
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
# ==================================================


def ensure_dir(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)


def get_sorted_subdirs(source_dir: Path):
    """获取所有子文件夹，并尝试按日期排序"""
    sub_dirs = [d for d in source_dir.iterdir() if d.is_dir()]
    try:
        # 按 YYYY-MM-DD 格式排序
        sub_dirs.sort(key=lambda x: datetime.strptime(x.name, "%Y-%m-%d"))
    except ValueError:
        # 否则按名称排序
        sub_dirs.sort()
    return sub_dirs


def move_or_copy_file(src: Path, dst: Path, copy: bool = True, overwrite: bool = False):
    """移动或复制文件，自动处理重名问题"""
    if not src.is_file():
        return

    dst_file = dst / src.name

    if dst_file.exists() and not overwrite:
        logger.info("{}图片已存在,skip...".format(dst_file))
    # counter = 1
    # while dst_file.exists() and not overwrite:
    #     new_name = f"{dst_file.stem}_{counter}{dst_file.suffix}"
    #     dst_file = dst / new_name
    #     counter += 1
    try:
        if copy:
            shutil.copy2(src, dst_file)
        else:
            shutil.move(str(src), str(dst_file))
    except Exception as e:
        logger.warning(f"无法处理文件 {src} -> {dst_file}: {e}")


def process_week_folder(
    folder: Path,
    idx: int,
    train_dir: Path,
    test_dir: Path,
    test_ratio: float,
    overwrite: bool,
):
    """处理单个周文件夹，复制内容到对应的 week_X 文件夹"""
    week_folder_name = f"week_{idx}"
    week_train_path = train_dir / week_folder_name
    week_test_path = test_dir / week_folder_name

    ensure_dir(week_train_path)
    ensure_dir(week_test_path)

    files = list(folder.rglob("*"))  # 递归获取所有文件
    image_files = [
        f for f in files if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ]

    file_count = 0

    for file in tqdm(image_files, desc=f"处理 {folder.name}文件夹", leave=False):
        # 复制到训练集
        move_or_copy_file(file, week_train_path, copy=True, overwrite=overwrite)

        # 以一定概率复制到测试集
        if random.random() < test_ratio:
            move_or_copy_file(file, week_test_path, copy=True, overwrite=overwrite)

        file_count += 1

    logger.info(f"已将 {folder} 中的 {file_count} 张图像复制到 {week_train_path}")
    return file_count


def main(source_dir, train_dir, test_dir, test_ratio, overwrite):
    logger.info("开始整理数据集...")

    # 创建目标目录
    ensure_dir(train_dir)
    ensure_dir(test_dir)

    # 获取并排序源文件夹
    sub_dirs = get_sorted_subdirs(source_dir)

    # 进度条包装
    for idx, folder in tqdm(
        enumerate(sub_dirs, start=1), total=len(sub_dirs), desc="总进度"
    ):
        process_week_folder(folder, idx, train_dir, test_dir, test_ratio, overwrite)

    logger.info("数据集整理完成！")
def make_dataset():
    # 定义源目录和目标目录
    source_dir = Path("/mnt/source_data/项目/蘑菇")
    target_base_dir = Path("./dataset/mushroom/train")
    target_base_dir.mkdir(parents=True, exist_ok=True)
    target_test_dir=Path("./dataset/mushroom/test")
    target_test_dir.mkdir(parents=True, exist_ok=True)

    # 获取所有子文件夹并按文件夹名排序（假设为 YYYY-MM-DD 格式）
    sub_dirs = [d for d in source_dir.iterdir() if d.is_dir()]

    # 遍历每个子文件夹，移动文件到对应的“第X周”文件夹
    for idx, folder in enumerate(sub_dirs, start=1):
        week_folder_name = f"week_{idx}"
        week_target_path = target_base_dir / week_folder_name
        test_target_path=target_test_dir/week_folder_name
        # 创建目标文件夹
        week_target_path.mkdir(exist_ok=True)
        test_target_path.mkdir(exist_ok=True)

        # 递归获取当前文件夹及其子文件夹下的所有文件
        files = list(folder.rglob("*"))  # 使用 rglob 递归获取所有文件
        file_count = 0
        for file in files:
            if file.is_file():
                dest_file = week_target_path / file.name
                test_filepath=test_target_path/file.name
                # 处理文件名冲突，添加序号后缀
                counter = 1
                if dest_file.exists():
                    print(f"{dest_file} already exists, skipping...")
                shutil.copy2(file, dest_file)
                if random.random() < 0.2:
                    if test_filepath.exists():
                        print("{}图片已存在".format(test_filepath))
                    else:
                        shutil.copy(file, test_filepath)
                file_count += 1

        print(f"已将 {folder} 中的 {file_count} 个文件复制到 {week_target_path}")

    print("处理完成！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="蘑菇数据集整理工具")
    parser.add_argument(
        "--source",
        type=str,
        default=str(DEFAULT_SOURCE_DIR),
        help="源目录路径，默认: %(default)s",
    )
    parser.add_argument(
        "--train",
        type=str,
        default=str(DEFAULT_TRAIN_DIR),
        help="训练集输出目录，默认: %(default)s",
    )
    parser.add_argument(
        "--test",
        type=str,
        default=str(DEFAULT_TEST_DIR),
        help="测试集输出目录，默认: %(default)s",
    )
    parser.add_argument(
        "--ratio",
        type=float,
        default=DEFAULT_TEST_RATIO,
        help="测试集划分比例，默认: %(default)s",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="是否覆盖已存在的文件（默认不覆盖）"
    )

    args = parser.parse_args()

    main(
        source_dir=Path(args.source),
        train_dir=Path(args.train),
        test_dir=Path(args.test),
        test_ratio=args.ratio,
        overwrite=args.overwrite,
    )



