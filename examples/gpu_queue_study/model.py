"""A small fixed model split into chunks, built so every run computes the same thing."""
import torch

WIDTH = 2048
BLOCKS = 32
BATCH = 256
SEED = 91
CHUNK_SIZES = (1, 2, 4, 8)


def build(device="cuda"):
    """Return the blocks and one fixed input. The weights come from a seeded generator."""
    generator = torch.Generator().manual_seed(SEED)
    blocks = []
    for _ in range(BLOCKS):
        layer = torch.nn.Linear(WIDTH, WIDTH)
        with torch.no_grad():
            layer.weight.copy_(torch.randn(WIDTH, WIDTH, generator=generator) / WIDTH ** 0.5)
            layer.bias.zero_()
        blocks.append(torch.nn.Sequential(layer, torch.nn.GELU()))
    inputs = torch.randn(BATCH, WIDTH, generator=generator)
    return [b.to(device).eval() for b in blocks], inputs.to(device)


def chunks(blocks, size):
    """Group consecutive blocks into chunks of `size`. Each chunk is one unit of GPU work."""
    if size < 1 or len(blocks) % size:
        raise ValueError("the chunk size must divide the number of blocks")
    return [torch.nn.Sequential(*blocks[i:i + size]) for i in range(0, len(blocks), size)]


def run_chunk(chunk, activations):
    with torch.inference_mode():
        return chunk(activations)
