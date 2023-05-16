from abc import ABC, abstractmethod
import os
from itertools import chain
from statistics import mean


import torch
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm
from omegaconf import OmegaConf

from losses.builder import build_losses
from metrics.builder import build_metrics
from utils.search_api import ModelSearchServerHandler
from utils.timer import Timer
from utils.logger import set_logger
from loggers.builder import build_logger

logger = set_logger('pipelines', level=os.getenv('LOG_LEVEL', default='INFO'))

MAX_SAMPLE_RESULT = 10
START_EPOCH_ZERO_OR_ONE = 1
VALID_FREQ = 1

PROFILE_WAIT = 1
PROFILE_WARMUP = 1
PROFILE_ACTIVE = 10
PROFILE_REPEAT = 1

NUM_SAMPLES = 16

class BasePipeline(ABC):
    def __init__(self, args, task, model_name, model, devices,
                 train_dataloader, eval_dataloader, class_map,
                 is_online=True, profile=False):
        super(BasePipeline, self).__init__()
        self.args = args
        self.task = task
        self.model_name = model_name
        self.model = model
        self.devices = devices
        self.train_dataloader = train_dataloader
        self.eval_dataloader = eval_dataloader
        self.train_step_per_epoch = len(train_dataloader)

        self.timer = Timer()

        self.loss = None
        self.metric = None
        self.optimizer = None

        self.ignore_index = None
        self.num_classes = None

        self.is_online = is_online
        if self.is_online:
            self.server_service = ModelSearchServerHandler(args.train.project, args.train.token)
        self.profile = profile

        self.epoch_with_valid_logging = lambda e: e % VALID_FREQ == START_EPOCH_ZERO_OR_ONE % VALID_FREQ
        self.single_gpu_or_rank_zero = (not self.args.distributed) or (self.args.distributed and torch.distributed.get_rank() == 0)
        
        self.train_logger = build_logger(self.args, self.task, self.model_name,
                                         step_per_epoch=self.train_step_per_epoch, class_map=class_map,
                                         num_sample_images=NUM_SAMPLES)

    # final
    def _is_ready(self):
        assert self.model is not None, "`self.model` is not defined!"
        assert self.optimizer is not None, "`self.optimizer` is not defined!"
        """Append here if you need more assertion checks!"""
        return True

    @abstractmethod
    def set_train(self):
        raise NotImplementedError
    
    @abstractmethod
    def log_result(self, num_epoch, with_valid):
        raise NotImplementedError

    @abstractmethod
    def train_step(self, batch):
        raise NotImplementedError

    @abstractmethod
    def valid_step(self, batch):
        raise NotImplementedError

    def train(self):
        logger.info(f"Training configuration:\n{OmegaConf.to_yaml(OmegaConf.create(self.args).get('train'))}")
        logger.info("-" * 40)

        self.timer.start_record(name='train_all')
        self._is_ready()

        for num_epoch in range(START_EPOCH_ZERO_OR_ONE, self.args.train.epochs + START_EPOCH_ZERO_OR_ONE):
            self.timer.start_record(name=f'train_epoch_{num_epoch}')
            self.loss = build_losses(self.args, ignore_index=self.ignore_index)
            self.metric = build_metrics(self.args, ignore_index=self.ignore_index, num_classes=self.num_classes)

            if self.profile:
                self.profile_one_epoch()
                break
            else:
                self.train_one_epoch()  # append result in `self._one_epoch_result`

            self.timer.end_record(name=f'train_epoch_{num_epoch}')

            time_for_epoch = self.timer.get(name=f'train_epoch_{num_epoch}', as_pop=False)
            if num_epoch == START_EPOCH_ZERO_OR_ONE and self.is_online:  # TODO: case for continuing training
                self.server_service.report_elapsed_time_for_epoch(time_for_epoch)

            with_valid_logging = self.epoch_with_valid_logging(num_epoch)
            validation_samples = self.validate() if with_valid_logging else None
            if self.single_gpu_or_rank_zero:
                self.log_end_epoch(epoch=num_epoch,
                                   time_for_epoch=time_for_epoch,
                                   validation_samples=validation_samples,
                                   valid_logging=with_valid_logging)
            
            self.scheduler.step()  # call after reporting the current `learning_rate`
            logger.info("-" * 40)

        self.timer.end_record(name='train_all')
        logger.info(f"Total time: {self.timer.get(name='train_all'):.2f} s")
        
        if self.single_gpu_or_rank_zero:
            # TODO: self.tensorboard.add_graph()
            pass

    def train_one_epoch(self):
        for idx, batch in enumerate(tqdm(self.train_dataloader, leave=False)):
            self.train_step(batch)
        
    @torch.no_grad()
    def validate(self, num_samples=NUM_SAMPLES):
        # FIXME: multi-gpu sample counting
        # num_target_samples = num_samples / num_gpu
        num_returning_samples = 0
        returning_samples = []
        for idx, batch in enumerate(tqdm(self.eval_dataloader, leave=False)):
            out = self.valid_step(batch)
            if out is not None and num_returning_samples < num_samples:
                returning_samples.append(out)
                num_returning_samples += len(out['pred'])
        return returning_samples
            
    def log_end_epoch(self, epoch, time_for_epoch, validation_samples=None, valid_logging=False):        
        train_losses = self.loss.result('train')
        train_metrics = self.metric.result('train')
        # logger.info(f"training loss: {self.train_loss:.7f}")
        # logger.info(f"training metric: {[(name, value.avg) for name, value in self.metric.result('train').items()]}")

        valid_losses = self.loss.result('valid') if valid_logging else None
        valid_metrics = self.metric.result('valid') if valid_logging else None
        # logger.info(f"validation loss: {self.valid_loss:.7f}")
        # logger.info(f"validation metric: {[(name, value.avg) for name, value in self.metric.result('valid').items()]}")

        # logging_contents = self.log_result(epoch, with_valid)
        
        # for k, v in logging_contents.items():
        #     self.tensorboard.add_scalar(str(k).replace("_", "/"), v, global_step=int(epoch * self.train_step_per_epoch))
        # for k, v in self.metric.result('train').items():
        #     self.tensorboard.add_scalar(f"train/{k}", v.avg, global_step=int(epoch * self.train_step_per_epoch))
        # for k, v in self.metric.result('valid').items():
        #     self.tensorboard.add_scalar(f"valid/{k}", v.avg, global_step=int(epoch * self.train_step_per_epoch))
        
        self.train_logger.update_epoch(epoch)
        self.train_logger.log(
            train_losses=train_losses,
            train_metrics=train_metrics,
            valid_losses=valid_losses,
            valid_metrics=valid_metrics,
            train_images=None,
            valid_images=validation_samples,
            learning_rate=self.learning_rate,
            elapsed_time=time_for_epoch
        )

    @property
    def learning_rate(self):
        return mean([param_group['lr'] for param_group in self.optimizer.param_groups])

    @property
    def train_loss(self):
        return self.loss.result('train').get('total').avg

    @property
    def valid_loss(self):
        return self.loss.result('valid').get('total').avg

    def profile_one_epoch(self):
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
