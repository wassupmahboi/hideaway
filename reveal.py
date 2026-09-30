from pathlib import Path
from PIL import Image
from stegano.lsb import generators

def reveal_bytes(path, gen):
    img = Image.open(path).convert("RGB")
    pixels = list(img.getdata())
    out = bytearray()
    buff = count = 0
    total = None
    for n in gen:
        if n >= len(pixels):
            break
        for c in pixels[n]:
            buff = (buff << 1) | (c & 1)      # most significant bit first
            count += 1
            if count == 8:
                out.append(buff)
                buff = count = 0
        if total is None and 58 in out:       # 58 is ':'
            head = bytes(out[:out.index(58)])
            if not head.isdigit():
                raise ValueError(f"Header damaged: {head!r}")
            total = len(head) + 1 + int(head)
        if total is not None and len(out) >= total:
            break
    if total is None:
        raise ValueError("No header found")
    return bytes(out[:total]).partition(b":")[2]

def reveal():
    file = Path(input("Enter the file path of image to be decrypted: "))
    return reveal_bytes(file, generators.eratosthenes()).decode("latin-1")
