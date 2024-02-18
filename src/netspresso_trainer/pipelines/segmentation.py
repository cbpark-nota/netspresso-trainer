import os
from typing import Literal

import numpy as np
import torch
from loguru import logger
from omegaconf import OmegaConf

from .base import BasePipeline

CITYSCAPE_IGNORE_INDEX = 255  # TODO: get from configuration


class SegmentationPipeline(BasePipeline):
    def __init__(self, conf, task, model_name, model, devices,
                 train_dataloader, eval_dataloader, class_map, logging_dir, **kwargs):
        super(SegmentationPipeline, self).__init__(conf, task, model_name, model, devices,
                                                   train_dataloader, eval_dataloader, class_map, logging_dir, **kwargs)
        self.ignore_index = CITYSCAPE_IGNORE_INDEX
        self.num_classes = train_dataloader.dataset.num_classes

    def train_step(self, batch):
        self.model.train()
        indices = batch['indices']
        images = batch['pixel_values'].to(self.devices)
        labels = batch['labels'].long().to(self.devices)
        target = {'target': labels}

        if 'edges' in batch:
            bd_gt = batch['edges']
            target['bd_gt'] = bd_gt.to(self.devices)

        self.optimizer.zero_grad()
        out = self.model(images)
        self.loss_factory.calc(out, target, phase='train')

        self.loss_factory.backward()
        self.optimizer.step()

        out = {k: v.detach() for k, v in out.items()}
        pred = self.postprocessor(out)

        if self.conf.distributed:
            gathered_pred = [None for _ in range(torch.distributed.get_world_size())]
            gathered_labels = [None for _ in range(torch.distributed.get_world_size())]

            # Remove dummy samples, they only come in distributed environment
            pred = pred[indices != -1]
            labels = labels[indices != -1]
            torch.distributed.gather_object(pred, gathered_pred if torch.distributed.get_rank() == 0 else None, dst=0)
            torch.distributed.gather_object(labels, gathered_labels if torch.distributed.get_rank() == 0 else None, dst=0)
            torch.distributed.barrier()
            if torch.distributed.get_rank() == 0:
                [self.metric_factory.calc(g_pred, g_labels, phase='train') for g_pred, g_labels in zip(gathered_pred, gathered_labels)]
        else:
            self.metric_factory.calc(pred, labels, phase='train')

    def valid_step(self, batch):
        self.model.eval()
        indices = batch['indices']
        images = batch['pixel_values'].to(self.devices)
        labels = batch['labels'].long().to(self.devices)
        target = {'target': labels}

        if 'edges' in batch:
            bd_gt = batch['edges']
            target['bd_gt'] = bd_gt.to(self.devices)

        out = self.model(images)
        self.loss_factory.calc(out, target, phase='valid')

        pred = self.postprocessor(out)

        if self.conf.distributed:
            gathered_pred = [None for _ in range(torch.distributed.get_world_size())]
            gathered_labels = [None for _ in range(torch.distributed.get_world_size())]

            # Remove dummy samples, they only come in distributed environment
            pred = pred[indices != -1]
            labels = labels[indices != -1]
            torch.distributed.gather_object(pred, gathered_pred if torch.distributed.get_rank() == 0 else None, dst=0)
            torch.distributed.gather_object(labels, gathered_labels if torch.distributed.get_rank() == 0 else None, dst=0)
            torch.distributed.barrier()
            if torch.distributed.get_rank() == 0:
                [self.metric_factory.calc(g_pred, g_labels, phase='valid') for g_pred, g_labels in zip(gathered_pred, gathered_labels)]
        else:
            self.metric_factory.calc(pred, labels, phase='valid')

        logs = {
            'images': images.detach().cpu().numpy(),
            'target': labels.detach().cpu().numpy(),
            'pred': pred.detach().cpu().numpy()
        }
        if 'edges' in batch:
            logs.update({
                'bd_gt': bd_gt.detach().cpu().numpy()
            })
        return dict(logs.items())

    def test_step(self, batch):
        self.model.eval()
        images = batch['pixel_values']
        images = images.to(self.devices)

        out = self.model(images.unsqueeze(0))

        pred = self.postprocessor(out)

        return pred.detach().cpu().numpy()

    def get_metric_with_all_outputs(self, outputs, phase: Literal['train', 'valid']):
        pass
