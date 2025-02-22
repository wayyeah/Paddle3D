#  Copyright (c) 2023 PaddlePaddle Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License")
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from typing import Dict, Tuple, Union

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from pprndr.apis import manager
from pprndr.cameras.rays import RaySamples
from pprndr.models.fields import BaseField

__all__ = ['NeRFField']


@manager.FIELDS.add_component
class NeRFField(BaseField):
    """
    NeRF Field. Reference: https://arxiv.org/abs/2003.08934

    Args:
        dir_encoder: Direction encoder.
        pos_encoder: Position encoder.
        density_head: Density network.
        color_head: Color network.
        use_integrated_encoding: Used integrated samples as encoding input,
            as proposed in mip-NeRF (https://arxiv.org/abs/2103.13415).
    """

    def __init__(self,
                 dir_encoder: nn.Layer,
                 pos_encoder: nn.Layer,
                 stem_net: nn.Layer,
                 density_head: nn.Layer,
                 color_head: nn.Layer,
                 density_noise: float = None,
                 density_bias: float = None,
                 rgb_padding: float = None,
                 use_integrated_encoding: bool = False):
        super(NeRFField, self).__init__()

        self.dir_encoder = dir_encoder
        self.pos_encoder = pos_encoder
        self.stem_net = stem_net
        self.density_head = density_head
        self.feat_bottleneck = nn.Linear(
            stem_net.output_dim, color_head.input_dim - dir_encoder.output_dim)
        self.color_head = color_head

        self.density_noise = density_noise
        self.density_bias = density_bias
        self.rgb_padding = rgb_padding
        self.use_integrated_encoding = use_integrated_encoding

    def get_density(
            self,
            ray_samples: Union[RaySamples, paddle.Tensor],
            which_pts: str = "mid_points",
            manipulate_pts: str = None) -> Tuple[paddle.Tensor, paddle.Tensor]:
        if isinstance(ray_samples, RaySamples):
            if self.use_integrated_encoding:
                assert (which_pts == "mid_points")
                pos_inputs = ray_samples.frustums.gaussians
            else:
                if which_pts == "mid_points":
                    pos_inputs = ray_samples.frustums.positions
                elif which_pts == "bin_points":
                    pos_inputs = ray_samples.frustums.bin_points
                else:
                    raise ValueError(
                        'which_pts should be either "mid_points" or "bin_points".'
                    )
            if manipulate_pts is not None:
                assert (manipulate_pts in ["nerf_pp_outside_warp"])
                if manipulate_pts == "nerf_pp_outside_warp":
                    assert (self.pos_encoder.input_dims == 4)
                    dis_to_center = paddle.linalg.norm(
                        pos_inputs, p=2, axis=-1, keepdim=True).clip(1.0, 1e10)
                    pos_inputs = paddle.concat(
                        [pos_inputs / dis_to_center, 1.0 / dis_to_center],
                        axis=-1)
        else:
            pos_inputs = ray_samples
        pos_embeddings = self.pos_encoder(pos_inputs)

        embeddings = self.stem_net(pos_embeddings)
        raw_density = self.density_head(embeddings)

        if self.density_noise is not None:
            raw_density += paddle.randn(
                raw_density.shape, dtype=raw_density.dtype) * self.density_noise
        if self.density_bias is not None:
            raw_density += self.density_bias

        density = F.softplus(raw_density)

        return density, embeddings

    def get_outputs(self, ray_samples: RaySamples,
                    geo_features: paddle.Tensor) -> Dict[str, paddle.Tensor]:
        embeddings = self.feat_bottleneck(geo_features)
        dir_embeddings = self.dir_encoder(ray_samples.frustums.directions)
        color = self.color_head(
            paddle.concat([dir_embeddings, embeddings], axis=-1))

        if self.rgb_padding is not None:
            color = color * (1. + 2. * self.rgb_padding) - self.rgb_padding

        return dict(rgb=color)
