import argparse
import os
import random
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import torch
from loguru import logger
from tqdm.auto import tqdm
from ultralytics import YOLO, settings
import wandb
from global_const.global_const import BASE_DIR



def train_mdl(**kwargs):
    args_train, unknown = get_args()
    if unknown:
        # 这里可以处理未知参数，或者忽略它们
        print(f"Unknown arguments: {unknown}")
    if kwargs.get("inputdata"):
        inputdata = kwargs["inputdata"]
        try:
            for index, value in inputdata:
                if value and args_train.__contains__(index):
                    args_train.__setattr__(index, value)
        except Exception as e:
            logger.warning(
                "[0.0.0] 传入参数异常，按默认参数执行！异常信息：{e},传入参数：{inputdata}".format(
                    e=e, inputdata=inputdata
                )
            )
    settings.update(
        {
            "clearml": False,
            "comet": False,
            "dvc": False,
            "hub": False,
            "mlflow": False,
            "neptune": False,
            "raytune": False,
            "tensorboard": True,
            "wandb": False,
        }
    )
    model = YOLO(args_train.model_path)  # load a pretrained model (recommended for training)
    res = model.train(
        data=args_train.data,
        epochs=args_train.epochs,
        imgsz=args_train.imgsz,
        task=args_train.task,
        # cfg=args_train.cfg,
        seed=args_train.seed,
        device=args_train.devices,
        batch=args_train.batch,
        optimizer=args_train.optimizer,
    )
    Path(args_train.saved_path).mkdir(exist_ok=True)
    model_name = "{date}_{task}_loss{loss}_fitness{fitness}_top1{top1}.pt".format(
        date=datetime.now().strftime("%Y%m%d%H%M"),
        task=args_train.task,
        loss=res.speed["loss"],
        fitness=res.results_dict["fitness"],
        top1=res.results_dict["metrics/accuracy_top1"],
    )
    model_path = Path(args_train.saved_path) / model_name
    shutil.copy2(os.path.join(res.save_dir, "weights/best.pt"), model_path)
    logger.info("model saved at {}".format(model_path))
    return res


def get_args():
    parser = argparse.ArgumentParser(description="corrosion imgs classification")
    parser.allow_abbrev = False
    # basic config
    parser.add_argument("--task", type=str, required=False, default="classify", help="task name")
    parser.add_argument(
        "--model_path",
        type=str,
        required=False,
        default=BASE_DIR /"models/yolo11n-cls.pt",
        help="model path",
    )
    parser.add_argument(
        "--data",
        type=str,
        required=False,
        default=BASE_DIR /"datasets/mushroom",
        help="input data path",
    )
    parser.add_argument("--epochs", type=int, required=False, default=10, help="total number of epochs")
    parser.add_argument(
        "--cfg",
        type=str,
        required=False,
        default=BASE_DIR / "configs/model_params.yaml",
        help="config file path",
    )
    parser.add_argument(
        "--saved_path",
        type=str,
        required=False,
        default=BASE_DIR /"saved_models",
        help="model path",
    )
    parser.add_argument("--seed", type=str, required=False, default=42, help="seed")
    parser.add_argument("--imgsz", type=int, default=1024, help="input image size")
    # GPU
    parser.add_argument("--use_gpu", type=bool, default=True, help="use gpu")
    parser.add_argument("--gpu", type=int, default=0, help="gpu")
    parser.add_argument("--use_multi_gpu", action="store_true", help="use multiple gpus", default=False)
    parser.add_argument("--devices", type=str, help="device ids of multile gpus")

    parser.add_argument(
        "--batch",
        type=int,
        default=32,
        help="batch size",
    )
    parser.add_argument(
        "--optimizer",
        type=str,
        default="auto",
        help="optimizer",
    )

    # 移除uvicorn的参数
    args, unknown = parser.parse_known_args()

    args.use_gpu = True if torch.cuda.is_available() and args.use_gpu else False

    if args.use_gpu and args.use_multi_gpu:
        args.device = args.devices.replace(" ", "")
        device_ids = args.devices.split(",")
        args.device_ids = [int(id_) for id_ in device_ids]
        args.gpu = args.device_ids[0]
    elif args.use_gpu:
        args.devices = "0"
    else:
        args.devices = "cpu"
    print("Args in experiment:{}".format(args))
    return args, unknown


if __name__ == "__main__":
    train_mdl()
    # print([random.random() for i in range(10)])
