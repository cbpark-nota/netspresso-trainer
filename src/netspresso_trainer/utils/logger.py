# import logging
import sys
import time
from typing import Literal, Optional, Union
from pathlib import Path

import torch
import torch.distributed as dist
from omegaconf import DictConfig, ListConfig, OmegaConf
from loguru import logger

__all__ = ['set_logger', 'yaml_for_logging']

OUTPUT_ROOT_DIR = "./outputs"
LEVELNO_TO_LEVEL_NAME = {
    0: "NOTSET",
    10: "DEBUG",
    20: "INFO",
    30: "WARNING",
    40: "ERROR",
    50: "CRITICAL",
}

def rank_filter(record):
    try:
        return dist.get_rank() == 0
    except RuntimeError:  # Default process group has not been initialized, please make sure to call init_process_group.
        return True

def get_format(level: str, distributed: bool = False):
    debug_and_multi_gpu = (level == 'DEBUG' and distributed)
    fmt = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    
    if debug_and_multi_gpu:
        fmt = f"[GPU:{dist.get_rank()}] " + fmt
    
    only_rank_zero = not debug_and_multi_gpu
    return fmt, only_rank_zero

def add_stream_handler(level: str, distributed: bool):
    fmt, only_rank_zero = get_format(level, distributed=distributed)
    logger.add(sys.stderr, level=level, format=fmt, filter=rank_filter if only_rank_zero else "")



def add_file_handler(log_filepath: str, distributed: bool):
    level = LEVELNO_TO_LEVEL_NAME[logger._core.min_level]
    fmt, only_rank_zero = get_format(level, distributed=distributed)
    logger.add(log_filepath, level=level, format=fmt, filter=rank_filter if only_rank_zero else "", enqueue=True)



def _custom_logger(level: str, distributed: bool):
    logger.remove()
    add_stream_handler(level, distributed)

    return logger



def set_logger(level: str = 'INFO', distributed=False):
    try:
        time.tzset()
    except AttributeError as e:
        print(e)
        print("Skipping timezone setting.")
    _level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = level.upper()
    _custom_logger(_level, distributed)

    return logger


def _yaml_for_logging(config: DictConfig) -> DictConfig:
    # TODO: better configuration logging
    list_maximum_index = 2
    new_config = OmegaConf.create()
    for k, v in config.items():
        if isinstance(v, DictConfig):
            new_config.update({k: _yaml_for_logging(v)})
        elif isinstance(v, ListConfig):
            new_config.update({k: list(map(str, v[:list_maximum_index])) + ['...']})
        else:
            new_config.update({k: v})
    return new_config


def yaml_for_logging(config: DictConfig):
    config_summarized = OmegaConf.create(_yaml_for_logging(config))
    return OmegaConf.to_yaml(config_summarized)


def _new_logging_dir(output_root_dir, project_id):
    version_idx = 0
    project_dir: Path = Path(output_root_dir) / project_id
    
    while (project_dir / f"version_{version_idx}").exists():
        version_idx += 1
    
    new_logging_dir: Path = project_dir / f"version_{version_idx}"
    new_logging_dir.mkdir(exist_ok=True, parents=True)
    return new_logging_dir

def _find_logging_dir(output_root_dir, project_id):
    version_idx = 0
    project_dir: Path = Path(output_root_dir) / project_id
    
    while (project_dir / f"version_{version_idx + 1}").exists():
        version_idx += 1
    
    logging_dir: Path = project_dir / f"version_{version_idx}"
    return logging_dir

def get_logging_dir(task: str, model: str, project_id: Optional[str] = None, output_root_dir: str = OUTPUT_ROOT_DIR, distributed: bool = False) -> Path:
    project_id = project_id if project_id is not None else f"{task}_{model}"
    
    if not distributed:
        return _new_logging_dir(output_root_dir, project_id)
    
    # TODO: Better synchronization
    if dist.get_rank() == 0:
        logging_dir = _new_logging_dir(output_root_dir, project_id)
        signal = torch.tensor([1]).to("cuda")
        for rank_idx in range(1, dist.get_world_size()):
            dist.send(tensor=signal, dst=rank_idx)
    else:
        signal = torch.tensor([0]).to("cuda")
        dist.recv(tensor=signal, src=0)

        logging_dir = _find_logging_dir(output_root_dir, project_id)
    
    dist.barrier()
    return logging_dir

if __name__ == '__main__':
    set_logger(level='DEBUG')
