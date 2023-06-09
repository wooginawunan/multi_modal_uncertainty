#!/usr/bin/env python3
#
# Copyright (c) Facebook, Inc. and its affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
#

import torch
import torch.nn as nn
import torchvision
from pytorch_pretrained_bert.modeling import BertModel

class ImageEncoder(nn.Module):
    def __init__(self, args):
        super(ImageEncoder, self).__init__()
        self.args = args
        model = torchvision.models.resnet152(pretrained=True)
        modules = list(model.children())[:-2]
        self.model = nn.Sequential(*modules)

        pool_func = (
            nn.AdaptiveAvgPool2d
            if args.img_embed_pool_type == "avg"
            else nn.AdaptiveMaxPool2d
        )

        if args.num_image_embeds in [1, 2, 3, 5, 7]:
            self.pool = pool_func((args.num_image_embeds, 1))
        elif args.num_image_embeds == 4:
            self.pool = pool_func((2, 2))
        elif args.num_image_embeds == 6:
            self.pool = pool_func((3, 2))
        elif args.num_image_embeds == 8:
            self.pool = pool_func((4, 2))
        elif args.num_image_embeds == 9:
            self.pool = pool_func((3, 3))

    def forward(self, x):
        # Bx3x224x224 -> Bx2048x7x7 -> Bx2048xN -> BxNx2048
        out = self.pool(self.model(x))
        out = torch.flatten(out, start_dim=2)
        out = out.transpose(1, 2).contiguous()
        return out  # BxNx2048

class ImageBertEmbeddings(nn.Module):
    def __init__(self, args, embeddings):
        super(ImageBertEmbeddings, self).__init__()
        self.args = args
        self.img_embeddings = nn.Linear(args.img_hidden_sz, args.hidden_sz)
        self.position_embeddings = embeddings.position_embeddings
        self.token_type_embeddings = embeddings.token_type_embeddings
        self.word_embeddings = embeddings.word_embeddings
        self.LayerNorm = embeddings.LayerNorm
        self.dropout = nn.Dropout(p=args.dropout)

    def forward(self, input_imgs, token_type_ids):
        bsz = input_imgs.size(0)
        device = input_imgs.device
        seq_length = self.args.num_image_embeds + 2  # +2 for CLS and SEP Token

        cls_id = torch.LongTensor([self.args.vocab.stoi["[CLS]"]]).to(device)
        cls_id = cls_id.unsqueeze(0).expand(bsz, 1)
        cls_token_embeds = self.word_embeddings(cls_id)

        sep_id = torch.LongTensor([self.args.vocab.stoi["[SEP]"]]).to(device)
        sep_id = sep_id.unsqueeze(0).expand(bsz, 1)
        sep_token_embeds = self.word_embeddings(sep_id)

        imgs_embeddings = self.img_embeddings(input_imgs)
        token_embeddings = torch.cat(
            [cls_token_embeds, imgs_embeddings, sep_token_embeds], dim=1
        )

        position_ids = torch.arange(seq_length, dtype=torch.long).to(device)
        position_ids = position_ids.unsqueeze(0).expand(bsz, seq_length)
        position_embeddings = self.position_embeddings(position_ids)
        token_type_embeddings = self.token_type_embeddings(token_type_ids)
        embeddings = token_embeddings + position_embeddings + token_type_embeddings
        embeddings = self.LayerNorm(embeddings)
        embeddings = self.dropout(embeddings)
        return embeddings


class MultimodalBertEncoder(nn.Module):
    def __init__(self, args):
        super(MultimodalBertEncoder, self).__init__()
        self.args = args
        bert = BertModel.from_pretrained(args.bert_model)
        self.txt_embeddings = bert.embeddings

        self.img_embeddings = ImageBertEmbeddings(args, self.txt_embeddings)
        self.img_encoder = ImageEncoder(args)
        self.encoder = bert.encoder
        self.pooler = bert.pooler

    def forward(self, input_txt, attention_mask, segment, input_img):
        bsz = input_txt.size(0)
        device = input_txt.device
        attention_mask = torch.cat(
            [
                torch.ones(bsz, self.args.num_image_embeds + 2).long().to(device),
                attention_mask,
            ],
            dim=1,
        )
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        extended_attention_mask = extended_attention_mask.to(
            dtype=next(self.parameters()).dtype
        )
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        img_tok = (
            torch.LongTensor(bsz, self.args.num_image_embeds + 2)
            .fill_(0)
            .to(device)
        )
        img = self.img_encoder(input_img)  # BxNx3x224x224 -> BxNx2048
        img_embed_out = self.img_embeddings(img, img_tok)
        txt_embed_out = self.txt_embeddings(input_txt, segment)
        encoder_input = torch.cat([img_embed_out, txt_embed_out], 1)  # Bx(TEXT+IMG)xHID

        encoded_layers = self.encoder(
            encoder_input, extended_attention_mask, output_all_encoded_layers=False
        )

        return self.pooler(encoded_layers[-1])

    def forward_img_only(self, input_txt, attention_mask, segment, input_img):
        bsz = input_txt.size(0)
        device = input_txt.device
        attention_mask = torch.ones(bsz, self.args.num_image_embeds + 2).long().to(device)
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        extended_attention_mask = extended_attention_mask.to(
            dtype=next(self.parameters()).dtype
        )
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        img_tok = (
            torch.LongTensor(bsz, self.args.num_image_embeds + 2)
            .fill_(0)
            .to(device)
        )
        img = self.img_encoder(input_img)  # BxNx3x224x224 -> BxNx2048
        img_embed_out = self.img_embeddings(img, img_tok)

        encoded_layers = self.encoder(
            img_embed_out, extended_attention_mask, output_all_encoded_layers=False
        )

        return self.pooler(encoded_layers[-1])

    def forward_txt_only(self, input_txt, attention_mask, segment, input_img):
        bsz = input_txt.size(0)
        device = input_txt.device
        attention_mask = torch.cat(
            [
                torch.ones(bsz, 1).long().to(device),
                attention_mask,
            ],
            dim=1,
        )
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        extended_attention_mask = extended_attention_mask.to(
            dtype=next(self.parameters()).dtype
        )
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        img_tok = (
            torch.LongTensor(bsz, self.args.num_image_embeds + 2)
            .fill_(0)
            .to(device)
        )
        img = self.img_encoder(input_img)  # BxNx3x224x224 -> BxNx2048
        img_embed_out = self.img_embeddings(img, img_tok)
        txt_embed_out = self.txt_embeddings(input_txt, segment)
        encoder_input = torch.cat([img_embed_out[:, :1, :], txt_embed_out], 1)  # Bx(TEXT+IMG)xHID

        encoded_layers = self.encoder(
            encoder_input, extended_attention_mask, output_all_encoded_layers=False
        )

        return self.pooler(encoded_layers[-1])
    
    def forward_control(self, input_txt, attention_mask, segment, input_img, control_modal):
        bsz = input_txt.size(0)
        device = input_txt.device
        total_embeds = input_txt.size(1) + self.args.num_image_embeds + 2

        if control_modal == "image":
            num_embeds = self.args.num_image_embeds + 1 # remain CLS token
        elif control_modal == "text":
            num_embeds = input_txt.size(1)
        else:
            raise ValueError("control_modal must be either image or text")
        
        indices = torch.zeros(num_embeds+1) # keep CLS token
        ind_sampled, _ = torch.sort(torch.randperm(total_embeds-1)[:num_embeds]+1)
        indices[1:] = ind_sampled
        indices = indices.long()
        
        attention_mask = torch.cat(
            [
                torch.ones(bsz, self.args.num_image_embeds + 2).long().to(device),
                attention_mask,
            ],
            dim=1,
        )

        attention_mask = attention_mask[:, indices]
        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        extended_attention_mask = extended_attention_mask.to(
            dtype=next(self.parameters()).dtype
        )
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        img_tok = (
            torch.LongTensor(bsz, self.args.num_image_embeds + 2)
            .fill_(0)
            .to(device)
        )
        img = self.img_encoder(input_img)  # BxNx3x224x224 -> BxNx2048
        img_embed_out = self.img_embeddings(img, img_tok)
        txt_embed_out = self.txt_embeddings(input_txt, segment)
        encoder_input = torch.cat([img_embed_out, txt_embed_out], 1)  # Bx(TEXT+IMG)xHID

        encoded_layers = self.encoder(
            encoder_input[:, indices, :], 
            extended_attention_mask, 
            output_all_encoded_layers=False
        )

        return self.pooler(encoded_layers[-1])


class MultimodalBertClf(nn.Module):
    def __init__(self, args):
        super(MultimodalBertClf, self).__init__()
        self.args = args
        self.enc = MultimodalBertEncoder(args)
        self.clf = nn.Linear(args.hidden_sz, args.n_classes)
        self.loss = nn.CrossEntropyLoss()

    def forward(self, txt, mask, segment, img):
        x = self.enc(txt, mask, segment, img)
        return self.clf(x)
    
    def forward_img_only(self, txt, mask, segment, img):
        x = self.enc.forward_img_only(txt, mask, segment, img)
        return self.clf(x)

    def forward_txt_only(self, txt, mask, segment, img):
        x = self.enc.forward_txt_only(txt, mask, segment, img)
        return self.clf(x)
    
    def forward_control(self, txt, mask, segment, img, control_modal):
        x = self.enc.forward_control(txt, mask, segment, img, control_modal)
        return self.clf(x)  
    
    def compute_loss(self, y_hat, y, eval=False):
        return self.loss(y_hat, y)