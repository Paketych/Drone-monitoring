import platform
import sys
import time


def check(title, ok, detail=""):
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {title}")
    if detail:
        print(f"       {detail}")


print("=" * 55)
print("       YOLO GPU READINESS TEST")
print("=" * 55)
print(f"Python: {sys.version.split()[0]}")
print(f"OS: {platform.platform()}")

try:
    import torch
except ImportError:
    print("[FAIL] PyTorch не установлен")
    print("Установи CUDA-версию PyTorch с pytorch.org")
    sys.exit(1)

print(f"PyTorch: {torch.__version__}")
print(f"PyTorch CUDA: {torch.version.cuda}")

cuda = torch.cuda.is_available()
check("CUDA доступна", cuda)

if not cuda:
    print("\nCUDA недоступна для PyTorch.")
    print("Проверь драйвер NVIDIA и CUDA-сборку PyTorch.")
    sys.exit(1)

print(f"GPU count: {torch.cuda.device_count()}")

for i in range(torch.cuda.device_count()):
    props = torch.cuda.get_device_properties(i)
    total = props.total_memory / (1024**3)

    print(f"\n--- GPU {i} ---")
    print(f"Название: {props.name}")
    print(f"VRAM всего: {total:.2f} GB")
    print(f"Compute capability: {props.major}.{props.minor}")

device = torch.device("cuda:0")

try:
    free, total = torch.cuda.mem_get_info(device)
    print(f"\nVRAM свободно: {free / 1024**3:.2f} GB")
    print(f"VRAM занято: {(total - free) / 1024**3:.2f} GB")
except Exception as e:
    print(f"Не удалось прочитать VRAM: {e}")

print("\n--- Тест вычислений CUDA ---")

try:
    a = torch.randn(2048, 2048, device=device)
    b = torch.randn(2048, 2048, device=device)

    torch.cuda.synchronize()
    start = time.perf_counter()

    for _ in range(20):
        c = torch.matmul(a, b)

    torch.cuda.synchronize()
    elapsed = time.perf_counter() - start

    check("GPU вычисления", True, f"20 matmul: {elapsed:.3f} сек")

    del a, b, c
    torch.cuda.empty_cache()

except Exception as e:
    check("GPU вычисления", False, str(e))
    sys.exit(1)

print("\n--- Тест обучения нейросети ---")

try:
    model = torch.nn.Sequential(
        torch.nn.Linear(512, 1024), torch.nn.ReLU(), torch.nn.Linear(1024, 10)
    ).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    x = torch.randn(256, 512, device=device)
    y = torch.randint(0, 10, (256,), device=device)

    start = time.perf_counter()

    for step in range(10):
        optimizer.zero_grad()
        output = model(x)
        loss = torch.nn.functional.cross_entropy(output, y)
        loss.backward()
        optimizer.step()

    torch.cuda.synchronize()
    elapsed = time.perf_counter() - start

    check(
        "Forward + Backward + Optimizer",
        True,
        f"10 шагов: {elapsed:.3f} сек; loss={loss.item():.4f}",
    )

except Exception as e:
    check("Обучение нейросети", False, str(e))
    sys.exit(1)

print("\n--- Ultralytics YOLO ---")

try:
    import ultralytics

    check("Ultralytics установлен", True, f"Версия: {ultralytics.__version__}")
except ImportError:
    check("Ultralytics установлен", False, "pip install ultralytics")

print("\n" + "=" * 55)
print("GPU ГОТОВ К ОБУЧЕНИЮ НЕЙРОСЕТЕЙ")
print("Для YOLO потребуется Ultralytics и датасет.")
print("=" * 55)
