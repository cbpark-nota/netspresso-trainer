import torch
import torch.nn as nn
import torch.nn.functional as F


class FPN(nn.Module):

    def __init__(self,
                 in_channels,
                 out_channels,
                 num_outs,
                 start_level=0,
                 end_level=-1,
                 add_extra_convs=False,
                 relu_before_extra_convs=False,
                 no_norm_on_lateral=False,
                 conv_cfg=None,
                 norm_cfg=None,
                 act_cfg=None,
                 upsample_cfg=None,
                 init_cfg=None):
        if init_cfg is None:
            init_cfg = {'type': 'Xavier', 'layer': 'Conv2d', 'distribution': 'uniform'}
        if upsample_cfg is None:
            upsample_cfg = {'mode': 'nearest'}
        super(FPN, self).__init__()
        assert isinstance(in_channels, list)
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.num_ins = len(in_channels)
        self.num_outs = num_outs
        self.relu_before_extra_convs = relu_before_extra_convs
        self.no_norm_on_lateral = no_norm_on_lateral
        self.fp16_enabled = False
        self.upsample_cfg = upsample_cfg.copy()

        if end_level == -1 or end_level == self.num_ins - 1:
            self.backbone_end_level = self.num_ins
            assert num_outs >= self.num_ins - start_level
        else:
            # if end_level is not the last level, no extra level is allowed
            self.backbone_end_level = end_level + 1
            assert end_level < self.num_ins
            assert num_outs == end_level - start_level + 1
        self.start_level = start_level
        self.end_level = end_level
        self.add_extra_convs = add_extra_convs
        assert isinstance(add_extra_convs, (str, bool))
        if isinstance(add_extra_convs, str):
            # Extra_convs_source choices: 'on_input', 'on_lateral', 'on_output'
            assert add_extra_convs in ('on_input', 'on_lateral', 'on_output')
        elif add_extra_convs:  # True
            self.add_extra_convs = 'on_input'

        self.lateral_convs = nn.ModuleList()
        self.fpn_convs = nn.ModuleList()

        for i in range(self.start_level, self.backbone_end_level):
            l_conv = nn.Conv2d(in_channels[i], out_channels, kernel_size=1, stride=1, padding=0)
            # ConvModule(
            #     in_channels[i],
            #     out_channels,
            #     1,
            #     conv_cfg=conv_cfg,
            #     norm_cfg=norm_cfg if not self.no_norm_on_lateral else None,
            #     act_cfg=act_cfg,
            #     inplace=False)
            fpn_conv = nn.Conv2d(out_channels, out_channels, kernel_size=3, stride=1, padding=1)
            # ConvModule(
            #     out_channels,
            #     out_channels,
            #     3,
            #     padding=1,
            #     conv_cfg=conv_cfg,
            #     norm_cfg=norm_cfg,
            #     act_cfg=act_cfg,
            #     inplace=False)

            self.lateral_convs.append(l_conv)
            self.fpn_convs.append(fpn_conv)

        # add extra conv layers (e.g., RetinaNet)
        extra_levels = num_outs - self.backbone_end_level + self.start_level
        if self.add_extra_convs and extra_levels >= 1:
            for i in range(extra_levels):
                if i == 0 and self.add_extra_convs == 'on_input':
                    in_channels = self.in_channels[self.backbone_end_level - 1]
                else:
                    in_channels = out_channels
                extra_fpn_conv = nn.Conv2d(in_channels, out_channels, kernel_size=3, stride=2, padding=1)
                self.fpn_convs.append(extra_fpn_conv)

    def forward(self, inputs):
        """Forward function."""
        assert len(inputs) == len(self.in_channels)

        # build laterals
        laterals = [
            lateral_conv(inputs[i + self.start_level])
            for i, lateral_conv in enumerate(self.lateral_convs)
        ]

        # build top-down path
        used_backbone_levels = len(laterals)
        for i in range(used_backbone_levels - 1, 0, -1):
            # In some cases, fixing `scale factor` (e.g. 2) is preferred, but
            #  it cannot co-exist with `size` in `F.interpolate`.
            if 'scale_factor' in self.upsample_cfg:
                # fix runtime error of "+=" inplace operation in PyTorch 1.10
                laterals[i - 1] = laterals[i - 1] + F.interpolate(
                    laterals[i], **self.upsample_cfg)
            else:
                prev_shape = laterals[i - 1].shape[2:]
                laterals[i - 1] = laterals[i - 1] + F.interpolate(
                    laterals[i], size=prev_shape, **self.upsample_cfg)

        # build outputs
        # part 1: from original levels
        outs = [
            self.fpn_convs[i](laterals[i]) for i in range(used_backbone_levels)
        ]
        # part 2: add extra levels
        if self.num_outs > len(outs):
            # use max pool to get more levels on top of outputs
            # (e.g., Faster R-CNN, Mask R-CNN)
            if not self.add_extra_convs:
                for i in range(self.num_outs - used_backbone_levels):
                    outs.append(F.max_pool2d(outs[-1], 1, stride=2))
            # add conv layers on top of original feature maps (RetinaNet)
            else:
                if self.add_extra_convs == 'on_input':
                    extra_source = inputs[self.backbone_end_level - 1]
                elif self.add_extra_convs == 'on_lateral':
                    extra_source = laterals[-1]
                elif self.add_extra_convs == 'on_output':
                    extra_source = outs[-1]
                else:
                    raise NotImplementedError
                outs.append(self.fpn_convs[used_backbone_levels](extra_source))
                for i in range(used_backbone_levels + 1, self.num_outs):
                    if self.relu_before_extra_convs:
                        outs.append(self.fpn_convs[i](F.relu(outs[-1])))
                    else:
                        outs.append(self.fpn_convs[i](outs[-1]))
        return outs


class PAFPN(nn.Module):
    """
    YOLOv3 model. Darknet 53 is the default backbone of this model.
    """

    def __init__(
        self,
        #in_features=("dark3", "dark4", "dark5"),
        in_channels,
        #depthwise=False,
        act_type="silu",
    ):
        super().__init__()
        from ....op.custom import ConvLayer, CSPLayer
        #self.in_features = in_features
        self.in_channels = in_channels
        Conv = ConvLayer

        # How do I get this?
        depth = 0.33

        self.upsample = nn.Upsample(scale_factor=2, mode="nearest")
        self.lateral_conv0 = ConvLayer(
            in_channels=int(in_channels[2]), 
            out_channels=int(in_channels[1]), 
            kernel_size=1, 
            stride=1,
            act_type=act_type
        )
        self.C3_p4 = CSPLayer(
            in_channels=int(2 * in_channels[1]),
            out_channels=int(in_channels[1]),
            n=round(3 * depth),
            shortcut=False,
            #depthwise=depthwise,
            act_type=act_type,
        )  # cat

        self.reduce_conv1 = ConvLayer(
            in_channels=int(in_channels[1]), 
            out_channels=int(in_channels[0]), 
            kernel_size=1, 
            stride=1, 
            act_type=act_type
        )
        self.C3_p3 = CSPLayer(
            in_channels=int(2 * in_channels[0]),
            out_channels=int(in_channels[0]),
            n=round(3 * depth),
            shortcut=False,
            #depthwise=depthwise,
            act_type=act_type,
        )

        # bottom-up conv
        self.bu_conv2 = Conv(
            in_channels=int(in_channels[0]), 
            out_channels=int(in_channels[0]), 
            kernel_size=3, 
            stride=2, 
            act_type=act_type
        )
        self.C3_n3 = CSPLayer(
            in_channels=int(2 * in_channels[0]),
            out_channels=int(in_channels[1]),
            n=round(3 * depth),
            shortcut=False,
            #depthwise=depthwise,
            act_type=act_type,
        )

        # bottom-up conv
        self.bu_conv1 = Conv(
            in_channels=int(in_channels[1]), 
            out_channels=int(in_channels[1]), 
            kernel_size=3, 
            stride=2, 
            act_type=act_type
        )
        self.C3_n4 = CSPLayer(
            in_channels=int(2 * in_channels[1]),
            out_channels=int(in_channels[2]),
            n=round(3 * depth),
            shortcut=False,
            #depthwise=depthwise,
            act_type=act_type,
        )

    def forward(self, inputs):
        """
        Args:
            inputs: input images.

        Returns:
            Tuple[Tensor]: FPN feature.
        """

        #  backbone
        #out_features = self.backbone(input)
        #features = [out_features[f] for f in self.in_features]
        #[x2, x1, x0] = features
        [x2, x1, x0] = inputs

        fpn_out0 = self.lateral_conv0(x0)  # 1024->512/32
        f_out0 = self.upsample(fpn_out0)  # 512/16
        f_out0 = torch.cat([f_out0, x1], 1)  # 512->1024/16
        f_out0 = self.C3_p4(f_out0)  # 1024->512/16

        fpn_out1 = self.reduce_conv1(f_out0)  # 512->256/16
        f_out1 = self.upsample(fpn_out1)  # 256/8
        f_out1 = torch.cat([f_out1, x2], 1)  # 256->512/8
        pan_out2 = self.C3_p3(f_out1)  # 512->256/8

        p_out1 = self.bu_conv2(pan_out2)  # 256->256/16
        p_out1 = torch.cat([p_out1, fpn_out1], 1)  # 256->512/16
        pan_out1 = self.C3_n3(p_out1)  # 512->512/16

        p_out0 = self.bu_conv1(pan_out1)  # 512->512/32
        p_out0 = torch.cat([p_out0, fpn_out0], 1)  # 512->1024/32
        pan_out0 = self.C3_n4(p_out0)  # 1024->1024/32

        outputs = (pan_out2, pan_out1, pan_out0)
        return outputs
