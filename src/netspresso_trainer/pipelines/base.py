from abc import ABC, abstractmethod
import os
from statistics import mean
from pathlib import Path
from typing import final, List, Dict, Union, Literal
import logging
from dataclasses import dataclass, asdict, field

import torch
import torch.nn as nn
from tqdm import tqdm

from ..optimizers import build_optimizer
from ..schedulers import build_scheduler
from ..losses import build_losses
from ..metrics import build_metrics
from ..loggers import build_logger, START_EPOCH_ZERO_OR_ONE
from ..utils.record import Timer
from ..utils.logger import yaml_for_logging
from ..utils.fx import save_graphmodule
from ..utils.onnx import save_onnx
from ..utils.stats import get_params_and_macs

logger = logging.getLogger("netspresso_trainer")

TYPE_SUMMARY_RECORD = Dict[int, Union[float, Dict[str, float]]]  # {epoch: value, ...}
VALID_FREQ = 1
NUM_SAMPLES = 16


@dataclass
class TrainingSummary:
    total_train_time: float
    total_epoch: int
    train_losses: TYPE_SUMMARY_RECORD
    valid_losses: TYPE_SUMMARY_RECORD
    train_metrics: TYPE_SUMMARY_RECORD
    valid_metrics: TYPE_SUMMARY_RECORD
    metrics_list: List[str]
    primary_metric: str
    macs: int
    params: int
    start_epoch_at_one: bool = bool(START_EPOCH_ZERO_OR_ONE)
    best_epoch: int = field(init=False)
    last_epoch: int = field(init=False)

    def __post_init__(self):
        self.best_epoch = min(self.valid_losses, key=self.valid_losses.get)
        self.last_epoch = list(self.train_losses.keys())[-1]


class BasePipeline(ABC):
    def __init__(self, conf, task, model_name, model, devices,
                 train_dataloader, eval_dataloader, class_map,
                 is_graphmodule_training=False, profile=False):
        super(BasePipeline, self).__init__()
        self.conf = conf
        self.task = task
        self.model_name = model_name
        self.model = model
        self.devices = devices
        self.train_dataloader = train_dataloader
        self.eval_dataloader = eval_dataloader
        self.train_step_per_epoch = len(train_dataloader)
        self.training_history: Dict[int, Dict[
            Literal['train_losses', 'valid_losses', 'train_metrics', 'valid_metrics'], Dict[str, float]
        ]] = {}

        self.timer = Timer()

        self.loss = None
        self.metric = None
        self.optimizer = None
        self.start_epoch_at_one = bool(START_EPOCH_ZERO_OR_ONE)
        self.start_epoch = int(self.start_epoch_at_one)

        self.ignore_index = None
        self.num_classes = None

        self.profile = profile  # TODO: provide torch_tb_profiler for training
        self.is_graphmodule_training = is_graphmodule_training

        self.epoch_with_valid_logging = lambda e: e % VALID_FREQ == self.start_epoch_at_one % VALID_FREQ
        self.single_gpu_or_rank_zero = (not self.conf.distributed) or (self.conf.distributed and torch.distributed.get_rank() == 0)

        if self.single_gpu_or_rank_zero:
            self.train_logger = build_logger(self.conf, self.task, self.model_name,
                                             step_per_epoch=self.train_step_per_epoch, class_map=class_map,
                                             num_sample_images=NUM_SAMPLES)

    @final
    def _is_ready(self):
        assert self.model is not None, "`self.model` is not defined!"
        assert self.optimizer is not None, "`self.optimizer` is not defined!"
        """Append here if you need more assertion checks!"""
        return True

    def _save_checkpoint(self):

        model = self.model.module if hasattr(self.model, 'module') else self.model
        result_dir = self.train_logger.result_dir
        model_path = Path(result_dir) / f"{self.task}_{self.model_name}.ckpt"
        save_onnx(model, model_path.with_suffix(".onnx"),
                  sample_input=self.sample_input)
        logger.info(f"ONNX model converting and saved at {str(model_path.with_suffix('.onnx'))}")

        if self.is_graphmodule_training:
            torch.save(model, model_path.with_suffix(".pt"))
            return
        torch.save(model.state_dict(), model_path.with_suffix(".pth"))
        logger.info(f"PyTorch model saved at {str(model_path.with_suffix('.pt'))}")

        save_graphmodule(model, (model_path.parent / f"{model_path.stem}_fx").with_suffix(".pt"))
        logger.info(f"PyTorch FX model tracing and saved at {str(model_path.with_suffix('.pt'))}")

    def _save_summary(self):

        total_train_time = self.timer.get(name='train_all')
        print(self.sample_input.size())
        macs, params = get_params_and_macs(self.model, self.sample_input)
        logger.info(f"(Model stats) Params: {(params/1e6):.2f}M | MACs: {(macs/1e9):.2f}G")
        training_summary = TrainingSummary(
            total_train_time=total_train_time,
            total_epoch=self.conf.training.epochs,
            start_epoch_at_one=self.start_epoch_at_one,
            train_losses={epoch: value['train_losses'].get('total') for epoch, value in self.training_history.items()},
            valid_losses={epoch: value['valid_losses'].get('total') for epoch, value in self.training_history.items()},
            train_metrics={epoch: value['train_metrics'] for epoch, value in self.training_history.items()},
            valid_metrics={epoch: value['valid_metrics'] for epoch, value in self.training_history.items()},
            metrics_list=self.metric.metric_names,
            primary_metric=self.metric.primary_metric,
            macs=macs,
            params=params
        )

        optimizer = self.optimizer.module if hasattr(self.optimizer, 'module') else self.optimizer
        optimizer_state_dict = optimizer.state_dict()

        result_dir = self.train_logger.result_dir
        summary_path = Path(result_dir) / f"training_summary.ckpt"
        torch.save({'summary': asdict(training_summary), 'optimizer': optimizer_state_dict}, summary_path)
        logger.info(f"Model training summary saved at {str(summary_path)}")

    def set_train(self, resume_training_checkpoint=None):

        assert self.model is not None
        self.optimizer = build_optimizer(self.model,
                                         opt=self.conf.training.opt,
                                         lr=self.conf.training.lr,
                                         wd=self.conf.training.weight_decay,
                                         momentum=self.conf.training.momentum)
        self.scheduler, _ = build_scheduler(self.optimizer, self.conf.training)
        if resume_training_checkpoint is not None:
            resume_training_checkpoint = Path(resume_training_checkpoint)
            if not resume_training_checkpoint.exists():
                logger.warning(f"Traning summary checkpoint path {str(resume_training_checkpoint)} is not found!"
                               f"Skip loading the previous history and trainer will be started from the beginning")

            training_summary_dict = torch.load(resume_training_checkpoint, map_location='cpu')
            optimizer_state_dict = training_summary_dict['optimizer']
            start_epoch = training_summary_dict['summary']['last_epoch'] + 1  # Start from the next to the end of last training
            start_epoch_at_one = training_summary_dict['summary']['start_epoch_at_one']

            self.optimizer.load_state_dict(optimizer_state_dict)
            self.scheduler.step(epoch=start_epoch)

            self.start_epoch_at_one = start_epoch_at_one
            self.start_epoch = start_epoch

    @abstractmethod
    def train_step(self, batch):
        raise NotImplementedError

    @abstractmethod
    def valid_step(self, batch):
        raise NotImplementedError

    @abstractmethod
    def test_step(self, batch):
        raise NotImplementedError

    @abstractmethod
    def get_metric_with_all_outputs(self, outputs):
        raise NotImplementedError

    def train(self):
        logger.debug(f"Training configuration:\n{yaml_for_logging(self.conf)}")
        logger.info("-" * 40)

        self.timer.start_record(name='train_all')
        self._is_ready()

        try:
            for num_epoch in range(self.start_epoch, self.conf.training.epochs + self.start_epoch_at_one):
                self.timer.start_record(name=f'train_epoch_{num_epoch}')
                self.loss = build_losses(self.conf.model, ignore_index=self.ignore_index)
                self.metric = build_metrics(self.task, self.conf.model, ignore_index=self.ignore_index, num_classes=self.num_classes)

                self.train_one_epoch()

                with_valid_logging = self.epoch_with_valid_logging(num_epoch)
                # FIXME: multi-gpu sample counting & validation
                valid_samples = self.validate() if with_valid_logging else None

                self.timer.end_record(name=f'train_epoch_{num_epoch}')
                time_for_epoch = self.timer.get(name=f'train_epoch_{num_epoch}', as_pop=False)

                if self.single_gpu_or_rank_zero:
                    self.log_end_epoch(epoch=num_epoch,
                                       time_for_epoch=time_for_epoch,
                                       valid_samples=valid_samples,
                                       valid_logging=with_valid_logging)

                self.scheduler.step()  # call after reporting the current `learning_rate`
                logger.info("-" * 40)

            self.timer.end_record(name='train_all')
            total_train_time = self.timer.get(name='train_all')
            logger.info(f"Total time: {total_train_time:.2f} s")

            if self.single_gpu_or_rank_zero:
                self.train_logger.log_end_of_traning(final_metrics={'time_for_last_epoch': time_for_epoch})
                self._save_checkpoint()
                self._save_summary()
        except KeyboardInterrupt as e:
            # TODO: add independent procedure for KeyboardInterupt
            logger.error("Keyboard interrupt detected! Try saving the current checkpoint...")
            if self.single_gpu_or_rank_zero:
                self._save_checkpoint()
                self._save_summary()
            raise e
        except Exception as e:
            logger.error(str(e))
            raise e

    def train_one_epoch(self):
        for idx, batch in enumerate(tqdm(self.train_dataloader, leave=False)):
            self.train_step(batch)

    @torch.no_grad()
    def validate(self, num_samples=NUM_SAMPLES):
        num_returning_samples = 0
        returning_samples = []
        outputs = []
        for idx, batch in enumerate(tqdm(self.eval_dataloader, leave=False)):
            out = self.valid_step(batch)
            if out is not None:
                outputs.append(out)
                if num_returning_samples < num_samples:
                    returning_samples.append(out)
                    num_returning_samples += len(out['pred'])
        self.get_metric_with_all_outputs(outputs)
        return returning_samples

    @torch.no_grad()
    def inference(self, test_dataset):
        returning_samples = []
        for idx, batch in enumerate(tqdm(test_dataset, leave=False)):
            out = self.test_step(batch)
            returning_samples.append(out)
        return returning_samples

    def log_end_epoch(self, epoch, time_for_epoch, valid_samples=None, valid_logging=False):
        train_losses = self.loss.result('train')
        train_metrics = self.metric.result('train')

        valid_losses = self.loss.result('valid') if valid_logging else None
        valid_metrics = self.metric.result('valid') if valid_logging else None

        self.train_logger.update_epoch(epoch)
        self.train_logger.log(
            train_losses=train_losses,
            train_metrics=train_metrics,
            valid_losses=valid_losses,
            valid_metrics=valid_metrics,
            train_images=None,
            valid_images=valid_samples,
            learning_rate=self.learning_rate,
            elapsed_time=time_for_epoch
        )

        summary_record = {'train_losses': train_losses, 'train_metrics': train_metrics}
        if valid_logging:
            summary_record.update({'valid_losses': valid_losses, 'valid_metrics': valid_metrics})
        self.training_history.update({epoch: summary_record})

    @property
    def learning_rate(self):
        return mean([param_group['lr'] for param_group in self.optimizer.param_groups])

    @property
    def train_loss(self):
        return self.loss.result('train').get('total').avg

    @property
    def valid_loss(self):
        return self.loss.result('valid').get('total').avg

    @property
    def sample_input(self):
        return torch.randn((1, 3, self.conf.augmentation.img_size, self.conf.augmentation.img_size))

    def profile_one_epoch(self):
        PROFILE_WAIT = 1
        PROFILE_WARMUP = 1
        PROFILE_ACTIVE = 10
        PROFILE_REPEAT = 1
        _ = torch.ones(1).to(self.devices)
        with torch.profiler.profile(
            schedule=torch.profiler.schedule(wait=PROFILE_WAIT,
                                             warmup=PROFILE_WARMUP,
                                             active=PROFILE_ACTIVE,
                                             repeat=PROFILE_REPEAT),
            on_trace_ready=torch.profiler.tensorboard_trace_handler('./log/test'),
            record_shapes=True,
            profile_memory=True,
            with_flops=True,
            with_modules=True
        ) as prof:
            for idx, batch in enumerate(self.train_dataloader):
                if idx >= (PROFILE_WAIT + PROFILE_WARMUP + PROFILE_ACTIVE) * PROFILE_REPEAT:
                    break
                self.train_step(batch)
                prof.step()
