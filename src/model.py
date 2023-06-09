from typing import Any
import torch
import torch.nn as nn
from src.layers import BasicBlock
from collections import OrderedDict
import math

model_configure ={
    "Vanilla": (4, 1),
    "MIMO-shuffle-instance": (4, 4),
    "MIMO-shuffle-view": (4, 4),
    "MultiHead": (4, 4),
    "MIMO-shuffle-all": (4, 4),
    "single-model-weight-sharing": (1, 1)
}

class ResNet(nn.Module):
    def __init__(self, num_channels, block, layers):
        self.inplanes = 64
        super(ResNet, self).__init__()
        self.conv1 = nn.Conv2d(num_channels, 64, kernel_size=3, stride=1, padding=1,
                               bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        # self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        #self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
        # self.layer4 = self._make_layer(block, 512, layers[3], stride=2)
        self.avgpool = nn.AvgPool2d(4)
        #self.fc = nn.Linear(256 * block.expansion, num_classes)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion,
                          kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)
   
class MultiHeadFC(nn.Module):
    def __init__(self, input_dim, num_classes, out_dim):
        super(MultiHeadFC, self).__init__()
        self.num_classes = num_classes
        self.fc = nn.Linear(input_dim, num_classes*out_dim)

    def forward(self, x):
        out = self.fc(x)
        # The shape of `outputs` is (batch_size, num_classes * ensemble_size).
        out_list = torch.split(out, self.num_classes, dim=-1) 
        out = torch.stack(out_list, dim=1) # (batch_size, ensemble_size, num_classes)
        
        return out
    
class MIMOResNet(ResNet):
    def __init__(self, num_channels, emb_dim, out_dim, num_classes):
        
        input_dim = num_channels * emb_dim
        super(MIMOResNet, self).__init__(input_dim, BasicBlock, [2, 2, 2])
        self.output_layer = MultiHeadFC(128 * BasicBlock.expansion, num_classes, out_dim)
        self.loss = torch.nn.CrossEntropyLoss()

    def forward(self, x):
        # x: B, E, C, H, W
        if len(x.shape) == 5:
            # concatenate the view/ensemble dimension as the channel dimension
            x = x.view(x.size(0), -1,  x.size(3), x.size(4)) # x: B, E*C, H, W
        else:
            # sharinng weight model
            # B*E, C, H, W
            pass 
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        
        x = self.layer1(x)
        x = self.layer2(x)
        
        x = self.avgpool(x)
        x = x.view(x.size(0), -1) 
        out = self.output_layer(x) # x: B, E, C

        return out
    
    def compute_loss(self, y_hat, y, eval=False):

        assert y.shape[0] == y_hat.shape[0]
        
        y = y.view(-1)
        if not eval:
            y_hat = y_hat.view(-1, y_hat.shape[2])
        else:
            y_hat = y_hat.mean(1)

        return self.loss(y_hat, y)

class MIMOTransfomer(nn.Module):
    def __init__(self, 
                 out_dim, 
                 num_classes, 
                 hidden_size, 
                 image_dim=14*14,
                 multimodal_num_hidden_layers=3, 
                 multimodal_num_attention_heads=3,
                 drop=0):
        
        super().__init__()

        self.image_to_mm_projection = nn.Linear(image_dim, hidden_size)
        self.mm_encoder = Transformer(width=hidden_size, 
                                      layers=multimodal_num_hidden_layers, 
                                      heads=multimodal_num_attention_heads,
                                      drop=drop)
        
        self.output_layers = nn.ModuleList([nn.Linear(hidden_size, num_classes) for i in range(out_dim)])
        self.loss = torch.nn.CrossEntropyLoss()

        self.ln_pre = nn.LayerNorm(hidden_size)
        self.ln_post = nn.LayerNorm(hidden_size)

    def forward(self, x):
        # x: B, E, C, H, W
        b, e, c, h, w = x.shape
        # concatenate the view/ensemble dimension as the channel dimension
        x = x.view(b, -1, h, w) # x: B, E*C, H, W
        x = x.view(b, e*c, -1) # x: B, E*C, H*W

        x = self.image_to_mm_projection(x)
        x = self.ln_pre(x)
        x = self.mm_encoder(x) #B, E*C, H*W
        x = self.ln_post(x)

        x = x.view(b, e, c, -1) # B, E, C, h
        x = x.mean(2) # B, E, h

        out_list = []
        for i, fc in enumerate(self.output_layers):
            out_list.append(fc(x[:, i, :]))
        
        out = torch.stack(out_list, dim=1) # (batch_size, ensemble_size, num_classes)

        return out
    
    def compute_loss(self, y_hat, y, eval=False):

        assert y.shape[0] == y_hat.shape[0]
        
        y = y.view(-1)
        if not eval:
            y_hat = y_hat.view(-1, y_hat.shape[2])
        else:
            y_hat = y_hat.mean(1)

        return self.loss(y_hat, y)


class LayerNorm(nn.LayerNorm):
    """Subclass torch's LayerNorm to handle fp16."""

    def forward(self, x: torch.Tensor):
        orig_type = x.dtype
        ret = super().forward(x.type(torch.float32))
        return ret.type(orig_type)


class QuickGELU(nn.Module):
    def forward(self, x: torch.Tensor):
        return x * torch.sigmoid(1.702 * x)


class ResidualAttentionBlock(nn.Module):
    def __init__(self, d_model: int, n_head: int, attn_mask: torch.Tensor = None, 
                 drop: float = 0.0):
        super().__init__()

        self.attn = nn.MultiheadAttention(d_model, n_head)
        self.ln_1 = LayerNorm(d_model)
        self.mlp = nn.Sequential(OrderedDict([
            ("c_fc", nn.Linear(d_model, d_model * 4)),
            ("dropout", nn.Dropout(drop)),
            ("gelu", QuickGELU()),
            ("c_proj", nn.Linear(d_model * 4, d_model)),
            ("dropout", nn.Dropout(drop))
        ]))
        self.ln_2 = LayerNorm(d_model)
        self.attn_mask = attn_mask

    def attention(self, x: torch.Tensor):
        self.attn_mask = self.attn_mask.to(dtype=x.dtype, device=x.device) if self.attn_mask is not None else None
        return self.attn(x, x, x, need_weights=False, attn_mask=self.attn_mask)[0]

    def forward(self, x: torch.Tensor):
        x = x + self.attention(self.ln_1(x))
        x = x + self.mlp(self.ln_2(x))
        return x


class Transformer(nn.Module):
    def __init__(self, width: int, layers: int, heads: int, attn_mask: torch.Tensor = None, drop: float = 0.0):
        super().__init__()
        self.width = width
        self.layers = layers
        self.resblocks = nn.Sequential(*[ResidualAttentionBlock(width, heads, attn_mask, drop) for _ in range(layers)])

    def forward(self, x: torch.Tensor):
        return self.resblocks(x)
    
class FlavaFusionTransfomer(nn.Module):
    def __init__(self, 
                 # prediction specific parameters
                 out_dim: int = 1,
                 num_classes: int = 2,
                 # Multimodal encoder specific parameters
                 image_hidden_size: int = 768,
                 text_hidden_size: int = 768,
                 # Multimodal encoder specific parameters
                 multimodal_hidden_size: int = 768,
                 multimodal_num_attention_heads: int = 3,
                 multimodal_num_hidden_layers: int = 3,
                 drop: float = 0.0,
                **kwargs: Any,):
        
        super().__init__()

        self.mm_encoder = Transformer(width=multimodal_hidden_size, 
                                      layers=multimodal_num_hidden_layers, 
                                      heads=multimodal_num_attention_heads,
                                      attn_mask=None,
                                      drop=drop)
        
        self.ln_pre = nn.LayerNorm(multimodal_hidden_size)
        self.ln_post = nn.LayerNorm(multimodal_hidden_size)

        self.image_to_mm_projection = nn.Linear(image_hidden_size, multimodal_hidden_size)
        self.text_to_mm_projection = nn.Linear(text_hidden_size, multimodal_hidden_size)

        self.output_layers = nn.ModuleList([nn.Linear(multimodal_hidden_size, num_classes) for i in range(out_dim)])
        self.loss = torch.nn.CrossEntropyLoss()
        self.avg_pool = kwargs["avg_pool"]

    def forward(self, x):
        image_features, text_features = x

        if image_features is not None:
            image_features = self.image_to_mm_projection(image_features)
        if text_features is not None:
            text_features = self.text_to_mm_projection(text_features)

        l_img, l_txt = image_features.shape[1], text_features.shape[1]

        if image_features is None:
            mm_x = text_features
        elif text_features is None:
            mm_x = image_features
        else:
            mm_x = torch.cat((image_features, text_features), dim=1)

        mm_x = self.ln_pre(mm_x)
        out = self.mm_encoder(mm_x)
        out = self.ln_post(out)
        
        # hidden_state = multimodal_features.last_hidden_state
        
        out_list = []
        if self.avg_pool:
            out_list.append(self.output_layers[0](out[:, :l_img, :].mean(1)))
            out_list.append(self.output_layers[1](out[:, l_img:(l_txt+l_img), :].mean(1)))
        else:
            for i, fc in enumerate(self.output_layers):
                out_list.append(fc(out[:, i, :]))
        
        out = torch.stack(out_list, dim=1) # (batch_size, ensemble_size, num_classes)

        return out

    def compute_loss(self, y_hat, y, eval=False):
        assert y.shape[0] == y_hat.shape[0]
        
        y = y.view(-1)
        if not eval:
            # compute loss per ensemble member
            y_hat = y_hat.view(-1, y_hat.shape[2])
        else:
            # take the ensemble mean of the predictions
            y_hat = y_hat.mean(1)

        return self.loss(y_hat, y)
    
class FlavaFusionTransfomerwithCLSToken(FlavaFusionTransfomer):
    def __init__(self, 
                 # prediction specific parameters
                 out_dim: int = 1,
                 num_classes: int = 2,
                 # Multimodal encoder specific parameters
                 image_hidden_size: int = 768,
                 text_hidden_size: int = 768,
                 # Multimodal encoder specific parameters
                 multimodal_hidden_size: int = 768,
                 multimodal_num_attention_heads: int = 3,
                 multimodal_num_hidden_layers: int = 3,
                 drop: float = 0.1,
                **kwargs: Any,):
        
        super().__init__(out_dim, num_classes, 
                        image_hidden_size, text_hidden_size, 
                        multimodal_hidden_size, multimodal_num_attention_heads,
                        multimodal_num_hidden_layers, drop,
                        **kwargs)

        scale = multimodal_hidden_size ** -0.5
        self.class_embeddings = nn.Parameter(scale * torch.randn(multimodal_hidden_size, out_dim))
        self.out_dim = out_dim

    def forward(self, x):
        image_features, text_features = x

        if image_features is not None:
            image_features = self.image_to_mm_projection(image_features)
        if text_features is not None:
            text_features = self.text_to_mm_projection(text_features)

        if image_features is None:
            mm_x = text_features
        elif text_features is None:
            mm_x = image_features
        else:
            mm_x = torch.cat((image_features, text_features), dim=1)
        
        cls  = self.class_embeddings.expand(mm_x.shape[0], -1, -1).permute(0, 2, 1).to(mm_x.device)
        mm_x = torch.cat([cls, mm_x], dim=1)

        mm_x = self.ln_pre(mm_x)
        out = self.mm_encoder(mm_x)
        out = self.ln_post(out)
        
        # hidden_state = multimodal_features.last_hidden_state
        
        out_list = []
        for i, fc in enumerate(self.output_layers):
            out_list.append(fc(out[:, i, :]))
        
        out = torch.stack(out_list, dim=1) # (batch_size, ensemble_size, num_classes)

        return out

    def compute_loss(self, y_hat, y, eval=False):
        assert y.shape[0] == y_hat.shape[0]
        
        y = y.view(-1)
        if not eval:
            # compute loss per ensemble member
            y_hat = y_hat.view(-1, y_hat.shape[2])
        else:
            # take the ensemble mean of the predictions
            y_hat = y_hat.mean(1)

        return self.loss(y_hat, y)
        
