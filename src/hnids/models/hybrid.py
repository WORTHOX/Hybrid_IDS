"""BiGRU -> positional encoding -> Transformer encoder -> classifier (FR-7)."""

from __future__ import annotations

import math

import torch
from pydantic import BaseModel, ConfigDict, Field
from torch import nn

from hnids.config.schema import ModelConfig
from hnids.errors import ErrorCode, InputValidationError


class ArchitectureSpec(BaseModel):
    """Everything needed to rebuild the module before loading weights (stored in the bundle)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_dim: int = Field(ge=1)
    n_classes: int = Field(ge=2)
    seq_len: int = Field(ge=2)
    model: ModelConfig


class SinusoidalPositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int) -> None:
        super().__init__()
        position = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
        div = torch.exp(
            torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10_000.0) / d_model)
        )
        table = torch.zeros(max_len, d_model)
        table[:, 0::2] = torch.sin(position * div)
        table[:, 1::2] = torch.cos(position * div[: d_model // 2])
        self.register_buffer("table", table.unsqueeze(0), persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        table: torch.Tensor = self.table  # type: ignore[assignment]
        return x + table[:, : x.size(1)].to(x.dtype)


class HybridModel(nn.Module):
    """Input ``(batch, seq_len, input_dim)`` -> logits ``(batch, n_classes)``."""

    def __init__(self, spec: ArchitectureSpec) -> None:
        super().__init__()
        cfg = spec.model
        self.spec = spec
        self.gru = nn.GRU(
            input_size=spec.input_dim,
            hidden_size=cfg.gru_hidden,
            num_layers=cfg.gru_layers,
            batch_first=True,
            bidirectional=True,
            dropout=cfg.dropout if cfg.gru_layers > 1 else 0.0,
        )
        self.project = nn.Linear(2 * cfg.gru_hidden, cfg.d_model)
        self.position = SinusoidalPositionalEncoding(cfg.d_model, spec.seq_len)
        layer = nn.TransformerEncoderLayer(
            d_model=cfg.d_model,
            nhead=cfg.n_heads,
            dim_feedforward=cfg.ff_dim,
            dropout=cfg.dropout,
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer, num_layers=cfg.transformer_layers, enable_nested_tensor=False
        )
        self.norm = nn.LayerNorm(2 * cfg.d_model)
        self.dropout = nn.Dropout(cfg.dropout)
        self.head = nn.Linear(2 * cfg.d_model, spec.n_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() != 3 or x.size(1) != self.spec.seq_len or x.size(2) != self.spec.input_dim:
            raise InputValidationError(
                f"expected (batch, {self.spec.seq_len}, {self.spec.input_dim}), "
                f"got {tuple(x.shape)}",
                code=ErrorCode.INPUT_INVALID,
            )
        sequence, _ = self.gru(x)
        encoded = self.encoder(self.position(self.project(sequence)))
        pooled = torch.cat([encoded[:, -1], encoded.mean(dim=1)], dim=-1)
        logits: torch.Tensor = self.head(self.dropout(self.norm(pooled)))
        return logits
