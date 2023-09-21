<div align="center">
    <img src="./assets/netspresso_trainer_header_tmp.png" width="800"/>
</div>
</br>

<center style="white-space: pre-line">
Start training models (including ViTs) with <b>NetsPresso Trainer</b>,
compress and deploy your model with <b>NetsPresso</b>!
</center>
</br>

<div align="center">
<p align="center">
  <a href="https://py.netspresso.ai/">Website</a> •
  <a href="#getting-started">Getting Started</a> •
  <a href="https://github.com/Nota-NetsPresso/netspresso-trainer/issues">Issues</a> •
  <a href="https://nota-netspresso.github.io/netspresso-trainer">Docs</a>
</p>
</div>

_____


## Table of contents

<!-- toc -->

- [Getting started](#getting-started)
- [Installation](#installation)

<!-- tocstop -->

## Getting started

Write your training script in `train.py` like:

```python
from netspresso_trainer import set_arguments, train

args_parsed, args = set_arguments(is_graphmodule_training=False)
train(args_parsed, args, is_graphmodule_training=False)
```

Then, train your model with your own configuraiton:

```bash
netspresso-train\
  --data config/data/beans.yaml\
  --augmentation config/augmentation/resnet.yaml\
  --model config/model/resnet.yaml\
  --training config/training/resnet.yaml\
  --logging config/logging.yaml\
  --environment config/environment.yaml
```

Please refer to [`scripts/example_train.sh`](./scripts/example_train.sh) and [`scripts/example_train_fx.sh`](./scripts/example_train_fx.sh).

## Installation

### Prerequisites

- Python `3.8` | `3.9` | `3.10`
- PyTorch `1.13.0` (recommended) (compatible with: `1.11.x` - `1.13.x`)

### Install with pypi (stable)

```bash
pip install netspresso_trainer
```

### Install with GitHub

```bash
pip install git+https://github.com:Nota-NetsPresso/netspresso-trainer.git@stable
```

To install with editable mode,

```bash
git clone https://github.com:Nota-NetsPresso/netspresso-trainer.git .
pip install -e netspresso-trainer
```

### Set-up with docker

Please clone this repository and refer to [`Dockerfile`](./Dockerfile) and [`docker-compose-example.yml`](./docker-compose-example.yml).  
For docker users, we provide more detailed guide in our [Docs](https://nota-netspresso.github.io/netspresso-trainer).

## Tensorboard

We provide basic tensorboard to track your training status. Run the tensorboard with the following command: 

```bash
tensorboard --logdir ./outputs --port 50001 --bind_all
```

where `PORT` for tensorboard is 50001.  
Note that the default directory of saving result will be `./outputs` directory.


## Pretrained weights

For now, we provide the pretrained weight from other awesome repositories. We have converted several models' weights into our own model architectures.  
In the near soon, we are planning to provide the pretrained weights directly trained from our resources.  
We appreciate all the original authors and we also do our best to make other values.

[Download all weights (Google Drvie)](https://drive.google.com/drive/folders/15AoBl22hV8JStBu_CHD5WZd7dHBerKzf?usp=sharing)

| Family           | Model    | Link    | Origianl repository    |
| ------           | -----    | ----    | -------------------    |
| MobileNetV3      | [`mobilenetv3_small`](./config/model/mobilenetv3) | [Google Drive](https://drive.google.com/file/d/1gzBIQLcj75VpU6JRPsHT4GhLPYT6KcQm/view?usp=drive_link) | [torchvision](https://download.pytorch.org/models/mobilenet_v3_small-047dcff4.pth ) |